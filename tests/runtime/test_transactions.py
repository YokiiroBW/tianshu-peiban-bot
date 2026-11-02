from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest

from tianshu.runtime.api import CommandResult
from tianshu.runtime.commands import CommandStore, body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import ExecutionLane, JobQueue, JobSpec
from tianshu.types import OriginKind, ServiceClass, TrustedContext


async def noop(**_kwargs):
    return None


async def test_rollback_removes_business_write_and_actual_procrastinate_job(database):
    jobs = JobQueue(database.config.dsn)
    jobs.register("fixture.noop", noop, execution_lane=ExecutionLane.MAINTENANCE)
    with pytest.raises(RuntimeError):
        async with database.transaction() as conn:
            await conn.execute("INSERT INTO runtime.projection_cursors(consumer,subject_id) "
                               "VALUES('fixture','subject')")
            await jobs.enqueue(conn, JobSpec("fixture.noop", "change-1", {}, ServiceClass.ELASTIC))
            raise RuntimeError("rollback")
    async with database.transaction(read_only=True) as conn:
        assert (await (await conn.execute("SELECT count(*) AS count FROM procrastinate_jobs")).fetchone())["count"] == 0
        assert await (await conn.execute("SELECT * FROM runtime.projection_cursors")).fetchone() is None


async def test_duplicate_change_enqueues_once_and_cancellation_is_transactional(database):
    jobs = JobQueue(database.config.dsn)
    jobs.register("fixture.noop", noop, execution_lane=ExecutionLane.MAINTENANCE)
    spec = JobSpec("fixture.noop", "same-change", {}, ServiceClass.DUE,
                   datetime.now(UTC)+timedelta(hours=1), subject_id="person")
    async with database.transaction() as conn:
        first = await jobs.enqueue(conn, spec)
        second = await jobs.enqueue(conn, spec)
        assert first == second
        await jobs.cancel(conn, spec.key)
    async with database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT status FROM procrastinate_jobs WHERE id=%s", (first,))).fetchone()
        assert row["status"] == "cancelled"


async def test_coalesced_signal_keeps_original_job_moves_earlier_and_retains_cancelled_audit(database, monkeypatch):
    jobs = JobQueue(database.config.dsn)
    jobs.register("fixture.wake", noop, execution_lane=ExecutionLane.MAINTENANCE)
    later = datetime.now(UTC) + timedelta(hours=1)
    earlier = later - timedelta(minutes=10)
    base = JobSpec("fixture.wake", "legacy-one", {"partition_id": "one"}, scheduled_at=later)
    notifications = []
    notify = jobs._notify_pending

    async def notified(connection, job_id, queue):
        notifications.append((job_id, queue))
        await notify(connection, job_id, queue)

    monkeypatch.setattr(jobs, "_notify_pending", notified)
    async with database.transaction() as conn:
        first = await jobs.enqueue(conn, base)
        duplicate = await jobs.enqueue(conn, replace(base, key="legacy-two", scheduled_at=earlier))
        other = await jobs.enqueue(conn, replace(base, key="other-partition", payload={"partition_id": "other"}))
        await conn.execute("UPDATE procrastinate_jobs SET attempts=2 WHERE id=%s", (duplicate,))
        keys = await (await conn.execute("SELECT * FROM runtime.job_keys ORDER BY key")).fetchall()
        retained = await jobs.enqueue(conn, replace(base, key="new-unused-signal"), queueing_lock="wake:one")
        assert retained == first
        rows = {row["id"]: row for row in await (await conn.execute("SELECT * FROM procrastinate_jobs")).fetchall()}
        assert rows[first]["status"] == "todo" and rows[first]["scheduled_at"] == earlier
        assert rows[first]["queueing_lock"] == "wake:one"
        assert rows[duplicate]["status"] == "cancelled" and rows[duplicate]["attempts"] == 2
        assert rows[duplicate]["abort_requested"] is False and rows[other]["status"] == "todo"
        assert await (await conn.execute("SELECT * FROM runtime.job_keys ORDER BY key")).fetchall() == keys
        events = await (await conn.execute("SELECT type FROM procrastinate_events WHERE job_id=%s ORDER BY id",
            (duplicate,))).fetchall()
        assert events[0]["type"] == "deferred" and events[-1]["type"] == "cancelled"
        assert notifications == [(first, "maintenance")]
        # A later request cannot postpone the earliest promised wake or emit noise.
        assert await jobs.enqueue(conn, replace(base, key="later-unused"), queueing_lock="wake:one") == first
        assert notifications == [(first, "maintenance")]
        with pytest.raises(AppError, match="job_identity_conflict"):
            await jobs.enqueue(conn, replace(base, key="wrong-payload", payload={"partition_id": "wrong"}),
                queueing_lock="wake:one")
        await conn.execute("INSERT INTO runtime.projection_cursors(consumer,subject_id) VALUES('still-valid','one')")


async def test_coalesced_signal_keeps_doing_work_and_cancelled_keys_do_not_block_a_fresh_successor(database):
    jobs = JobQueue(database.config.dsn)
    jobs.register("fixture.wake", noop, execution_lane=ExecutionLane.MAINTENANCE)
    base = JobSpec("fixture.wake", "first", {"partition_id": "one"})
    async with database.transaction() as conn:
        doing = await jobs.enqueue(conn, base, queueing_lock="wake:one")
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (doing,))
        before = await (await conn.execute("SELECT * FROM procrastinate_jobs WHERE id=%s", (doing,))).fetchone()
        pending = await jobs.enqueue(conn, replace(base, key="next"), queueing_lock="wake:one")
        assert pending != doing
        assert await jobs.enqueue(conn, replace(base, key="merged"), queueing_lock="wake:one") == pending
        assert await (await conn.execute("SELECT * FROM procrastinate_jobs WHERE id=%s", (doing,))).fetchone() == before
        await jobs.cancel(conn, "next")
        assert (await (await conn.execute("SELECT cancelled_at FROM runtime.job_keys WHERE key='next'")).fetchone())["cancelled_at"]
        fresh = await jobs.enqueue(conn, replace(base, key="after-cancellation"), queueing_lock="wake:one")
        assert fresh not in {doing, pending}
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (doing,))
        assert (await (await conn.execute("SELECT status FROM procrastinate_jobs WHERE id=%s", (fresh,))).fetchone())["status"] == "todo"


async def test_coalesced_signal_cancellation_and_earlier_schedule_rollback_with_business_changes(database):
    jobs = JobQueue(database.config.dsn)
    jobs.register("fixture.wake", noop, execution_lane=ExecutionLane.MAINTENANCE)
    future = datetime.now(UTC) + timedelta(hours=1)
    base = JobSpec("fixture.wake", "first", {"partition_id": "one"}, scheduled_at=future)
    async with database.transaction() as conn:
        await jobs.enqueue(conn, base)
        await jobs.enqueue(conn, replace(base, key="duplicate"))
        before = await (await conn.execute("SELECT * FROM procrastinate_jobs ORDER BY id")).fetchall()
        events = await (await conn.execute("SELECT * FROM procrastinate_events ORDER BY id")).fetchall()
    with pytest.raises(RuntimeError, match="rollback"):
        async with database.transaction() as conn:
            await jobs.enqueue(conn, replace(base, key="uncommitted", scheduled_at=None), queueing_lock="wake:one")
            await conn.execute("INSERT INTO runtime.projection_cursors(consumer,subject_id) VALUES('coalesced','one')")
            raise RuntimeError("rollback")
    async with database.transaction(read_only=True) as conn:
        assert await (await conn.execute("SELECT * FROM procrastinate_jobs ORDER BY id")).fetchall() == before
        assert await (await conn.execute("SELECT * FROM procrastinate_events ORDER BY id")).fetchall() == events
        assert await (await conn.execute("SELECT * FROM runtime.projection_cursors WHERE consumer='coalesced'")).fetchone() is None


async def test_command_replay_and_semantic_conflict_share_business_transaction(database):
    commands = CommandStore(database)
    context = TrustedContext("principal", None, None, None, (), (), 1, OriginKind.HUMAN)

    async def apply(conn):
        await conn.execute("INSERT INTO runtime.projection_cursors(consumer,subject_id) VALUES('command','one')")
        return CommandResult("request-1", data={"saved": True}, revision=1)

    first = await commands.execute(context, "fixture.save", "request-1", body_hash({"name": "one"}), apply)
    second = await commands.execute(context, "fixture.save", "request-1", body_hash({"name": "one"}), apply)
    assert first.revision == second.revision
    assert second.data is None  # A replay cannot return an old private projection.
    recovered = await commands.find(context, "fixture.save", "request-1")
    assert recovered.client_request_id == "request-1"
    assert recovered.revision == 1 and recovered.data is None and recovered.receipt is None
    another = TrustedContext("another", None, None, None, (), (), 1, OriginKind.HUMAN)
    assert await commands.find(another, "fixture.save", "request-1") is None
    with pytest.raises(AppError, match="client_request_id_conflict"):
        await commands.execute(context, "fixture.save", "request-1", body_hash({"name": "different"}), apply)
