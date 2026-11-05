"""Coalesced early notifications cannot consume a later plan deadline."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from test_planner_execution import mark
from test_rbf_planning_recovery import make_life, run_plan
from tianshu.conversation.generation import attempt_for
from tianshu.life import clock, planning_engine
from tianshu.life.planning import wake_lock, wake_payload
from tianshu.runtime.jobs import JobSpec


@pytest.fixture
def awake(monkeypatch):
    monkeypatch.setattr(planning_engine,"awake_window",lambda _routine,now:(now,now+timedelta(hours=6)))
    monkeypatch.setattr(clock,"awake_and_due",lambda _routine,now:(True,now+timedelta(hours=6)))


async def unknown_retry(value):
    await run_plan(value)
    old = await mark(value.worker.life.database)
    attempt = attempt_for(old["generation_key"])
    value.broker.receipts[attempt] = replace(value.broker.receipts[attempt],status="unknown",dispatch="unknown",
        finished_at=datetime.now(UTC),error_code="timeout_unknown")
    await run_plan(value)
    current = await mark(value.worker.life.database)
    assert current["generation_consumed"] and current["repair_due_at"] > datetime.now(UTC)
    return current,attempt


async def take_pending(database):
    async with database.transaction() as connection:
        row = await (await connection.execute("SELECT id,scheduled_at FROM procrastinate_jobs "
            "WHERE task_name='life.plan' AND status='todo' ORDER BY id LIMIT 1 FOR UPDATE")).fetchone()
        assert row is not None
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s",(row["id"],))
        return row


async def pending(database):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT id,scheduled_at,args FROM procrastinate_jobs "
            "WHERE task_name='life.plan' AND status='todo' ORDER BY id")).fetchall()


async def test_merged_early_observation_rearms_retry_and_due_wake_creates_one_successor(database,awake):
    value = await make_life(database)
    saved,original = await unknown_retry(value)
    async with database.transaction() as connection:
        await value.jobs.enqueue(connection,JobSpec("life.plan","rbh-earlier-observe",
            wake_payload("actor",value.data_generation),scheduled_at=datetime.now(UTC)+timedelta(seconds=1),
            subject_id="actor",data_generation=value.data_generation),queueing_lock=wake_lock("actor"))
    early = await take_pending(database)
    assert early["scheduled_at"] is None or early["scheduled_at"] < saved["repair_due_at"]
    await run_plan(value)
    jobs = await pending(database)
    assert len(jobs) == 1 and jobs[0]["scheduled_at"] == saved["repair_due_at"]
    assert list(value.broker.requests) == [original]
    async with database.transaction() as connection:
        past = datetime.now(UTC)-timedelta(seconds=1)
        await connection.execute("UPDATE life.plan_watermarks SET repair_due_at=%s WHERE actor_id='actor'",(past,))
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s",(past,))
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s",(past,))
        await connection.execute("UPDATE procrastinate_jobs SET scheduled_at=%s WHERE id=%s",(past,jobs[0]["id"]))
    await take_pending(database)
    await asyncio.gather(run_plan(value),run_plan(value))
    current = await mark(database)
    assert current["generation_key"] != saved["generation_key"]
    assert len(value.broker.requests) == 2 and value.broker.receipts[original].status == "unknown"


async def test_retry_wakes_at_its_due_time_without_another_notification(database,awake):
    value = await make_life(database)
    await run_plan(value)
    original = attempt_for((await mark(database))["generation_key"])
    value.broker.receipts[original] = replace(value.broker.receipts[original],status="unknown",dispatch="unknown",
        finished_at=datetime.now(UTC),error_code="timeout_unknown")
    await take_pending(database)
    await run_plan(value)
    saved = await mark(database)
    jobs = await pending(database)
    assert len(jobs) == 1 and jobs[0]["scheduled_at"] == saved["repair_due_at"]
    async with database.transaction() as connection:
        past = datetime.now(UTC)-timedelta(seconds=1)
        await connection.execute("UPDATE life.plan_watermarks SET repair_due_at=%s WHERE actor_id='actor'",(past,))
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s",(past,))
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s",(past,))
    await take_pending(database)
    await run_plan(value)
    assert len(value.broker.requests) == 2 and value.broker.receipts[original].status == "unknown"
    assert (await mark(database))["generation_key"] != saved["generation_key"]


async def test_concurrent_notification_can_coalesce_but_not_erase_future_retry(database,awake):
    value = await make_life(database)
    saved,original = await unknown_retry(value)
    await take_pending(database)
    # A duplicate model-completion wake and capability notification race with
    # the earlier observation while all still refer to the same actor pointer.
    await asyncio.gather(run_plan(value),run_plan(value),
        value.worker.life.signal(value.context,"capability-observation:rbh-concurrent"))
    first = await pending(database)
    assert len(first) == 1
    if first[0]["scheduled_at"] is None or first[0]["scheduled_at"] < saved["repair_due_at"]:
        await take_pending(database)
        await run_plan(value)
    final = await pending(database)
    assert len(final) == 1 and final[0]["scheduled_at"] == saved["repair_due_at"]
    assert (await mark(database))["generation_key"] == saved["generation_key"]
    assert list(value.broker.requests) == [original]


async def test_sleeping_actor_does_not_wake_before_its_next_real_wake(database,awake,monkeypatch):
    value = await make_life(database)
    saved,original = await unknown_retry(value)
    await take_pending(database)
    wake_at = saved["repair_due_at"]+timedelta(hours=1)
    monkeypatch.setattr(clock,"awake_and_due",lambda _routine,_now:(False,wake_at))
    await run_plan(value)
    jobs = await pending(database)
    assert len(jobs) == 1 and jobs[0]["scheduled_at"] == wake_at
    assert list(value.broker.requests) == [original]


@pytest.mark.parametrize("blocked",["routine_disabled","actor_disabled","planner_cancelled","actor_resting"])
async def test_blocked_actor_does_not_rearm_a_stale_deadline(database,awake,blocked):
    value = await make_life(database)
    await unknown_retry(value)
    await take_pending(database)
    if blocked == "actor_disabled":
        async def disabled(_context):
            return {"enabled":False,"persona_id":None}
        value.worker.life.companion.actor = disabled
    else:
        async with database.transaction() as connection:
            if blocked == "routine_disabled":
                await connection.execute("UPDATE life.routines SET enabled=false WHERE actor_id='actor'")
            await connection.execute("UPDATE life.plan_watermarks SET reason=%s WHERE actor_id='actor'",(blocked,))
    await run_plan(value)
    assert await pending(database) == []
