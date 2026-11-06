"""Completion wakes are durable, target-fenced, and do not replay an Action."""
import asyncio
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest

from test_rbf_planning_recovery import make_life
from tianshu.conversation.codec import document
from tianshu.life.rbi_activity_wake import enqueue_on
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.types import Receipt


async def fixture(database):
    value = await make_life(database)
    await migrate(database,(Migration("rbi_activity_reconciliation",
        files("tianshu.life").joinpath("rbi_activity_reconciliation.sql").read_text(encoding="utf-8")),))
    value.jobs.register("life.activity_completed_wake",value.worker.activity_completed_wake,
        execution_lane=ExecutionLane.LIFE,resource_recovery=True)
    original = value.worker.life.sources.prepare_event_writes
    async def scoped(connection,context,events,*,additional_refs=(),scoped_refs=()):
        await original(connection,context,events,additional_refs=additional_refs)
        for target_context,refs in scoped_refs:
            assert await value.worker.life.sources.validate_persistence(connection,target_context,refs)
    value.worker.life.sources.prepare_event_writes = scoped
    return value


async def target(value,identifier,*,due=None):
    async with value.worker.life.database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,"
            "wake_condition,scope_ids,context,next_due_at) VALUES (%s,'actor','later','reading','dormant',"
            "'waiting_for_event',%s,ARRAY['actor-scope'],%s,%s)",
            (identifier,document({"kind":"activity_completed","subject_id":"original-activity"}),
             document(value.context),due))


async def emit(value):
    result = Receipt("original-receipt","original-action","attempt","succeeded","none","not_requested")
    async with value.worker.life.database.transaction() as connection:
        return await enqueue_on(value.worker.life,connection,context=value.context,activity_id="original-activity",
            result=result,result_revision=2,refs=(),data_generation=value.data_generation)


async def intent(database,identifier):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM life.rbi_activity_wake_intents WHERE id=%s",(identifier,))).fetchone()


async def test_wake_captures_targets_and_preserves_earliest_due(database):
    value = await fixture(database)
    due = datetime.now(UTC)-timedelta(minutes=1)
    await target(value,"before",due=due)
    identifier = await emit(value)
    await target(value,"created-after-event")
    await asyncio.gather(value.worker.activity_completed_wake(identifier,value.data_generation),
        value.worker.activity_completed_wake(identifier,value.data_generation))
    record = await intent(database,identifier)
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT id,state,next_due_at,wake_condition FROM life.activities ORDER BY id")).fetchall()
    by_id = {row["id"]:row for row in rows}
    assert record["state"] == "done" and record["next_index"] == 1
    assert by_id["before"]["state"] == "planned" and by_id["before"]["next_due_at"] == due
    assert by_id["created-after-event"]["state"] == "dormant"
    assert not value.actions.calls


async def test_failed_native_wake_is_repaired_and_stale_target_is_not_woken(database):
    value = await fixture(database)
    await target(value,"changed")
    identifier = await emit(value)
    before = await intent(database,identifier)
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s",(before["job_id"],))
        await connection.execute("UPDATE life.activities SET state='paused',revision=revision+1 WHERE id='changed'")
    assert (await value.worker.reconcile_due(limit=8))["woken"] == 1
    after = await intent(database,identifier)
    assert after["job_id"] != before["job_id"] and after["state"] == "pending"
    await value.worker.activity_completed_wake(identifier,value.data_generation)
    record = await intent(database,identifier)
    assert record["state"] == "done" and record["skipped"][0]["reason"] == "target_changed"
    assert not value.actions.calls


async def test_rolled_back_result_transaction_does_not_leave_wake_or_job(database):
    value = await fixture(database)
    await target(value,"before")
    result = Receipt("original-receipt","original-action","attempt","succeeded","none","not_requested")
    with pytest.raises(RuntimeError,match="rollback"):
        async with database.transaction() as connection:
            await enqueue_on(value.worker.life,connection,context=value.context,activity_id="original-activity",
                result=result,result_revision=2,refs=(),data_generation=value.data_generation)
            raise RuntimeError("rollback")
    async with database.transaction(read_only=True) as connection:
        saved = await (await connection.execute("SELECT count(*) AS n FROM life.rbi_activity_wake_intents")).fetchone()
        jobs = await (await connection.execute("SELECT count(*) AS n FROM procrastinate_jobs "
            "WHERE task_name='life.activity_completed_wake'")).fetchone()
    assert saved["n"] == jobs["n"] == 0


async def test_typed_source_wait_keeps_original_target_pending(database):
    value = await fixture(database)
    await target(value,"before")
    identifier = await emit(value)
    retry = datetime.now(UTC)+timedelta(minutes=2)
    value.worker.life.sources.prepare_event_writes = AsyncMock(side_effect=RecoveryWait("source_unavailable",retry))
    with pytest.raises(RecoveryWait) as caught:
        await value.worker.activity_completed_wake(identifier,value.data_generation)
    record = await intent(database,identifier)
    assert caught.value.retry_at == retry and record["state"] == "pending" and record["next_index"] == 0
    assert record["skipped"] == []
