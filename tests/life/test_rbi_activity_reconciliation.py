"""Original Action observation keeps stable identity and durable future wakes."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from test_rbf_planning_recovery import make_life
from tianshu.conversation.codec import document
from tianshu.life.worker import LifeWorker
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire


async def add_waiting(value, name="watch", *, state="dormant", deadline=None):
    deadline = deadline or datetime.now(UTC)+timedelta(hours=1)
    action = "action:"+name
    frozen = {"action_id":action,"deadline_at":deadline.isoformat(),"context":wire(value.context)}
    checkpoint = {"capability_id":"content.read","frozen_action":frozen}
    async with value.worker.life.database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,resume_condition,"
            "action_id,checkpoint_data,scope_ids,context,next_due_at) "
            "VALUES (%s,'actor','observe','reading',%s,'outcome_unknown','reconcile_original_action',%s,%s,"
            "ARRAY['actor-scope'],%s,%s)",
            (name,state,action,document(checkpoint),document(value.context),deadline))
    return action,frozen,deadline


async def activity(value, name="watch"):
    async with value.worker.life.database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM life.activities WHERE id=%s",(name,))).fetchone()


async def wakes(value, name="watch"):
    async with value.worker.life.database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT id,status,args,scheduled_at,queueing_lock FROM procrastinate_jobs "
            "WHERE task_name='life.activity_reconcile' AND args->>'activity_id'=%s "
            "AND args ? 'data_generation' ORDER BY id",(name,))).fetchall()


async def test_old_sequence_key_restart_and_refreshed_context_never_reinvoke(database):
    value = await make_life(database)
    action,frozen,deadline = await add_waiting(value)
    old_payload = {"activity_id":"watch","context":wire(value.context),"sequence":1}
    async with database.transaction() as connection:
        old = await value.jobs.enqueue(connection,JobSpec("life.activity_reconcile",
            "activity-reconcile:"+action+":1",old_payload,subject_id="actor",data_generation=value.data_generation))
        await connection.execute("UPDATE procrastinate_jobs SET status='succeeded' WHERE id=%s",(old,))
    value.worker.life.identity.epoch = 2
    value.actions.get = AsyncMock(side_effect=AppError("action_not_found",status=404))
    value.actions.invoke = AsyncMock()
    restarted = LifeWorker(value.worker.life,value.worker.models,value.worker.assembler,
        value.worker.catalog,value.actions,None)
    await restarted.recover()
    jobs = await wakes(value)
    assert len([job for job in jobs if job["status"] == "todo"]) == 1
    assert jobs[-1]["args"] == {"activity_id":"watch","action_id":action,"data_generation":value.data_generation}
    # This legacy delivery previously reused the already-completed :1 key with
    # a refreshed context. It can now only schedule the original observation.
    await restarted.reconcile_activity("watch",wire(value.context),0)
    jobs = await wakes(value)
    assert len([job for job in jobs if job["status"] == "todo"]) == 1
    value.actions.invoke.assert_not_awaited()
    current = await activity(value)
    assert current["action_id"] == action and current["next_due_at"] == deadline
    assert current["checkpoint_data"]["frozen_action"] == frozen
    assert current["checkpoint_data"]["reconciliation"]["action_id"] == action


async def test_early_duplicate_and_doing_job_keep_one_successor_and_original_deadline(database):
    value = await make_life(database)
    action,frozen,deadline = await add_waiting(value)
    row = await activity(value)
    future = datetime.now(UTC)+timedelta(minutes=4)
    async def schedule(when):
        return await value.worker._queue_activity_observation(value.context,row,when,reason="notification")
    await asyncio.gather(schedule(future),schedule(future+timedelta(minutes=1)),schedule(future))
    pending = [job for job in await wakes(value) if job["status"] == "todo"]
    assert len(pending) == 1 and pending[0]["scheduled_at"] == future
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s",(pending[0]["id"],))
    outcome = await value.worker.reconcile_due(limit=8)
    assert outcome["woken"] == 1
    jobs = await wakes(value)
    assert sum(job["status"] == "doing" for job in jobs) == 1
    assert sum(job["status"] == "todo" for job in jobs) == 1
    assert (await value.worker.reconcile_due(limit=8))["woken"] == 0
    current = await activity(value)
    assert current["next_due_at"] == deadline and current["checkpoint_data"]["frozen_action"] == frozen
    assert current["checkpoint_data"]["reconciliation"]["due_at"] == future.isoformat()


async def test_failed_wake_is_repaired_without_erasing_valid_future_due(database):
    value = await make_life(database)
    action,frozen,deadline = await add_waiting(value)
    future = datetime.now(UTC)+timedelta(minutes=10)
    row = await activity(value)
    first = await value.worker._queue_activity_observation(value.context,row,future,reason="waiting")
    assert (await value.worker.reconcile_due(limit=8))["woken"] == 0
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s",(first,))
    repaired = await value.worker.reconcile_due(limit=8)
    assert repaired["scanned"] == 1 and repaired["woken"] == 1 and repaired["complete"]
    pending = [job for job in await wakes(value) if job["status"] == "todo"]
    assert len(pending) == 1 and pending[0]["id"] != first and pending[0]["scheduled_at"] == future
    current = await activity(value)
    assert current["next_due_at"] == deadline and current["checkpoint_data"]["frozen_action"] == frozen
    assert current["action_id"] == action


async def test_dependency_wait_schedules_original_after_retry_without_extending_deadline(database):
    value = await make_life(database)
    action,frozen,deadline = await add_waiting(value)
    when = datetime.now(UTC)+timedelta(minutes=2)
    value.actions.get = AsyncMock(side_effect=RecoveryWait("recovery_dependency_cooling",when))
    value.actions.invoke = AsyncMock()
    await value.worker.reconcile_activity("watch",action_id=action,data_generation=value.data_generation)
    current = await activity(value)
    assert current["next_due_at"] == deadline and current["checkpoint_data"]["frozen_action"] == frozen
    assert datetime.fromisoformat(current["checkpoint_data"]["reconciliation"]["due_at"]) >= when
    assert len([job for job in await wakes(value) if job["status"] == "todo"]) == 1
    value.actions.invoke.assert_not_awaited()


async def test_404_during_original_admission_is_unknown_and_never_reinvoked(database):
    value = await make_life(database)
    action,frozen,deadline = await add_waiting(value,state="running")
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET waiting_reason='capability_queued' WHERE id='watch'")
    value.actions.get = AsyncMock(side_effect=AppError("action_not_found",status=404))
    value.actions.invoke = AsyncMock()
    await value.worker.reconcile_activity("watch",action_id=action,data_generation=value.data_generation)
    current = await activity(value)
    assert current["waiting_reason"] == "outcome_unknown" and current["action_id"] == action
    assert current["next_due_at"] == deadline and current["checkpoint_data"]["frozen_action"] == frozen
    assert len([job for job in await wakes(value) if job["status"] == "todo"]) == 1
    value.actions.invoke.assert_not_awaited()


async def test_maintenance_rotates_pages_and_stale_or_paused_binding_never_wakes(database):
    value = await make_life(database)
    for name in ("a","b","c"):
        await add_waiting(value,name)
    first = await value.worker.reconcile_due(limit=2)
    second = await value.worker.reconcile_due(limit=2)
    assert first == {"scanned":2,"woken":2,"complete":False}
    assert second == {"scanned":1,"woken":1,"complete":True}
    for name in ("a","b","c"):
        assert len([job for job in await wakes(value,name) if job["status"] == "todo"]) == 1
    value.actions.get = AsyncMock()
    value.actions.invoke = AsyncMock()
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET action_id='replacement',state='paused' WHERE id='a'")
    await value.worker.reconcile_activity("a",action_id="action:a",data_generation=value.data_generation)
    value.actions.get.assert_not_awaited()
    value.actions.invoke.assert_not_awaited()


async def test_old_generation_cannot_observe_or_rebind_a_current_activity(database):
    value = await make_life(database)
    action,_,_ = await add_waiting(value)
    value.actions.get = AsyncMock()
    with pytest.raises(AppError,match="job_generation_expired"):
        await value.worker.reconcile_activity("watch",action_id=action,data_generation="old-generation")
    value.actions.get.assert_not_awaited()
    assert await wakes(value) == []


async def test_legacy_job_cannot_observe_activity_with_old_generation_metadata(database):
    value = await make_life(database)
    action,_,_ = await add_waiting(value)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET checkpoint_data=checkpoint_data||%s WHERE id='watch'",
            (document({"reconciliation":{"action_id":action,"data_generation":"old-generation"}}),))
    value.actions.get = AsyncMock()
    await value.worker.reconcile_activity("watch",wire(value.context),0)
    assert (await value.worker.reconcile_due(limit=8))["woken"] == 0
    value.actions.get.assert_not_awaited()
    assert await wakes(value) == []

async def test_native_retry_and_coalesced_successor_observe_only_original_action(database):
    value = await make_life(database)
    action,frozen,deadline = await add_waiting(value)
    row = await activity(value)
    future = datetime.now(UTC)+timedelta(minutes=2)
    first = await value.worker._queue_activity_observation(value.context,row,future,reason="first")
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s",(first,))
    await value.worker._queue_activity_observation(value.context,row,future,reason="notification")
    # The native retry releases its queueing lock while a separately coalesced
    # successor keeps the stable lock. Both still refer to the same Action.
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='todo',queueing_lock=NULL WHERE id=%s",(first,))
    jobs = await wakes(value)
    assert len([job for job in jobs if job["status"] == "todo"]) == 2
    assert sum(bool(job["queueing_lock"]) for job in jobs if job["status"] == "todo") == 1
    value.actions.get = AsyncMock(side_effect=AppError("action_not_found",status=404))
    value.actions.invoke = AsyncMock()
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s",(first,))
    await value.worker.reconcile_activity(**next(job["args"] for job in jobs if job["id"] == first))
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='succeeded' WHERE id=%s",(first,))
    assert sum(job["status"] == "todo" for job in await wakes(value)) == 1
    current = await activity(value)
    assert current["action_id"] == action and current["checkpoint_data"]["frozen_action"] == frozen
    assert current["next_due_at"] == deadline
    value.actions.invoke.assert_not_awaited()
