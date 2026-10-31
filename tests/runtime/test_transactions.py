from datetime import UTC, datetime, timedelta

import pytest

from tianshu.runtime.api import CommandResult
from tianshu.runtime.commands import CommandStore, body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue, JobSpec
from tianshu.types import OriginKind, ServiceClass, TrustedContext


async def noop(**_kwargs):
    return None


async def test_rollback_removes_business_write_and_actual_procrastinate_job(database):
    jobs = JobQueue(database.config.dsn)
    jobs.register("fixture.noop", noop)
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
    jobs.register("fixture.noop", noop)
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
