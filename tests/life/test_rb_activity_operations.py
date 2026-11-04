from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document
from tianshu.runtime.serialization import wire
from tianshu.types import Receipt
from tianshu.runtime.errors import AppError
from tianshu.runtime.execution import ExecutionLane
from test_planner_execution import setup


@pytest.mark.asyncio
async def test_operations_reconciles_only_original_action_with_commit_fence(database):
    value = await setup(database, "{}")
    value.jobs.register("life.recovery_observe_activity", value.worker.recovery_observe_activity, execution_lane=ExecutionLane.LIFE)
    guard = AsyncMock()
    action = value.worker.operations_recovery(guard)
    assert action["observation_only"] is True
    target = {"kind": "life.activity", "id": "activity", "revision": 1}
    checkpoint = {"capability_id": "content.read", "frozen_action": {"action_id": "original"}}
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,action_id,checkpoint_data,scope_ids,context) "
            "VALUES ('activity','actor','read','reading','dormant','outcome_unknown','original',%s,ARRAY['actor-scope'],%s)",
            (document(checkpoint), document(value.context)))
    value.actions.get = AsyncMock(return_value={"action": {"capability_id": "content.read"},
        "receipt": wire(Receipt("receipt", "original", "attempt", "pending", "none", "not_requested")),
        "result_revision": 1, "result_observed_at": datetime.now(UTC)})
    facts = await action["inspect"]({}, target)
    assert facts["eligible"] and facts["material_id"] == "original"
    outcome = await action["invoke"]({"owner_token": "lease"}, target, facts, "recovery-attempt")
    assert outcome["state"] == "accepted" and guard.await_count == 1
    await value.worker.recovery_observe_activity("activity", 1, wire(value.context), "recovery-attempt", "lease")
    assert guard.await_count == 2 and not value.actions.calls
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT * FROM life.activities WHERE id='activity'")).fetchone()
    assert row["action_id"] == "original" and row["waiting_reason"] == "capability_running"
    assert (await action["observe"]({}, target, facts, "recovery-attempt"))["state"] == "pending"


@pytest.mark.asyncio
async def test_missing_unknown_action_is_not_reissued(database):
    value = await setup(database, "{}")
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,action_id,checkpoint_data,scope_ids,context) "
            "VALUES ('activity','actor','read','reading','dormant','outcome_unknown','original',%s,ARRAY['actor-scope'],%s)",
            (document({"capability_id": "content.read", "frozen_action": {"action_id": "original"}}), document(value.context)))
    value.actions.get = AsyncMock(side_effect=AppError("not_found", status=404))
    await value.worker.reconcile_activity("activity", wire(value.context), 0)
    assert not value.actions.calls
