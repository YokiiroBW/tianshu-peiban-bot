"""Coalesced future wakes coexist with an original native retry."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobQueue, JobSpec


@pytest.mark.parametrize("resource_recovery", [False, True])
async def test_native_retry_preserves_original_key_and_earliest_pending_successor(database, resource_recovery):
    queue = JobQueue(database.config.dsn, database=database)

    async def fails(**_payload):
        raise ValueError("fixture failure")

    task = queue.register("fixture.rbi_retry", fails, retryable=True,
                          resource_recovery=resource_recovery, execution_lane=ExecutionLane.MAINTENANCE)
    future = datetime.now(UTC) + timedelta(minutes=10)
    earlier = future - timedelta(minutes=5)
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        initial = JobSpec("fixture.rbi_retry", "retry-original", {"subject": "a"}, subject_id="a",
                          data_generation=generation)
        original = await queue.enqueue(conn, initial, queueing_lock="rbi-subject")
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (original,))
        following = await queue.enqueue(conn, JobSpec("fixture.rbi_retry", "retry-future", {"subject": "a"},
            subject_id="a", data_generation=generation, scheduled_at=future), queueing_lock="rbi-subject")
        merged = await queue.enqueue(conn, JobSpec("fixture.rbi_retry", "retry-earlier", {"subject": "a"},
            subject_id="a", data_generation=generation, scheduled_at=earlier), queueing_lock="rbi-subject")
        assert merged == following and following != original
    await queue.open()
    try:
        context = SimpleNamespace(job=SimpleNamespace(id=original, queueing_lock="rbi-subject", attempts=0))
        with pytest.raises(ValueError) as failure:
            await task(context, subject="a")
        decision = task.retry_strategy.get_retry_decision(exception=failure.value, job=context.job)
        assert decision is not None
        await queue.app.job_manager.retry_job(context.job, retry_at=decision.retry_at)
        async with database.transaction() as conn:
            retry = await (await conn.execute("SELECT status,queueing_lock,args,attempts FROM procrastinate_jobs WHERE id=%s",
                                               (original,))).fetchone()
            pending = await (await conn.execute("SELECT status,queueing_lock,args,scheduled_at FROM procrastinate_jobs WHERE id=%s",
                                                 (following,))).fetchone()
            assert await queue.enqueue(conn, initial) == original  # Ordinary permanent keys remain stable.
        assert retry == {"status": "todo", "queueing_lock": None, "args": {"subject": "a"}, "attempts": 1}
        assert pending["status"] == "todo" and pending["queueing_lock"] is not None
        assert pending["args"] == {"subject": "a"} and pending["scheduled_at"] == earlier
    finally:
        await queue.close()


async def test_exhausted_native_strategy_does_not_release_doing_lock(database):
    queue = JobQueue(database.config.dsn, database=database)

    async def fails():
        raise ValueError("fixture failure")

    task = queue.register("fixture.rbi_exhausted", fails, retryable=True, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        job_id = await queue.enqueue(conn, JobSpec("fixture.rbi_exhausted", "exhausted-original", {}),
                                     queueing_lock="exhausted-subject")
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    await queue.open()
    try:
        context = SimpleNamespace(job=SimpleNamespace(id=job_id, queueing_lock="exhausted-subject", attempts=3))
        with pytest.raises(ValueError) as failure:
            await task(context)
        assert task.retry_strategy.get_retry_decision(exception=failure.value, job=context.job) is None
        async with database.transaction(read_only=True) as conn:
            saved = await (await conn.execute("SELECT status,queueing_lock FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
        assert saved["status"] == "doing" and saved["queueing_lock"] is not None
    finally:
        await queue.close()
