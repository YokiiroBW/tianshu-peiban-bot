"""A missing Action can race admission and must not be reinvoked."""
from unittest.mock import AsyncMock

from test_rbi_result_atomicity import result_fixture
from tianshu.conversation.codec import document
from tianshu.life import reconciliation
from tianshu.runtime.errors import AppError


async def test_missing_commitment_action_remains_unknown_and_has_original_wake(database):
    value = await result_fixture(database,"commitment")
    frozen = {"action_id":value.action.action_id,"arguments":{"original":True}}
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.commitments SET frozen_action=%s WHERE id='promise'",(document(frozen),))
        row = await (await connection.execute("SELECT revision FROM life.commitments WHERE id='promise'")).fetchone()
    value.actions.get = AsyncMock(side_effect=AppError("action_not_found",status=404))
    value.actions.invoke = AsyncMock()
    value.jobs.enqueue = AsyncMock(return_value=123)
    result = await reconciliation.commitment(value.worker,"promise",row["revision"])
    assert result["state"] == "waiting" and result["reason"] == "original_action_not_found"
    assert result["action_id"] == value.action.action_id and result["job_id"] == 123
    async with database.transaction(read_only=True) as connection:
        current = await (await connection.execute("SELECT state,reason,action_id,frozen_action FROM life.commitments "
            "WHERE id='promise'")).fetchone()
    assert current == {"state":"unknown","reason":"original_action_unresolved",
        "action_id":value.action.action_id,"frozen_action":frozen}
    value.actions.invoke.assert_not_awaited()


async def test_pending_commitment_gets_original_revision_wake(database):
    value = await result_fixture(database,"commitment")
    value.jobs.enqueue = AsyncMock(return_value=124)
    outcome = await reconciliation.queue_commitment_observation(value.worker,value.context,
        value.action.action_id,value.data_generation)
    assert outcome["job_id"] == 124
    spec = value.jobs.enqueue.await_args.args[1]
    assert spec.task == "life.commitment_reconcile"
    assert spec.payload == {"commitment_id":"promise","expected_revision":1}
    assert spec.data_generation == value.data_generation
    assert value.jobs.enqueue.await_args.kwargs["queueing_lock"].startswith("life-commitment-observe:")
