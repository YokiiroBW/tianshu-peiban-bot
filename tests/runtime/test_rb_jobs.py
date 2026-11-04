"""Durable handoff and recomputation regressions, using the isolated PG fixture."""

from importlib.resources import files

import pytest
import pytest_asyncio

from tianshu.runtime.errors import AppError
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobQueue, JobSpec, RecomputeCoverage
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import ServiceClass


@pytest_asyncio.fixture
async def queue(database):
    await migrate(database, (Migration("0007_rb_runtime",
        files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),))
    jobs = JobQueue(database.config.dsn, database=database)

    async def prepare(_claim):
        async def apply(_connection, _claim):
            return RecomputeCoverage()
        return apply

    jobs.register_recompute("fixture.recompute", prepare, execution_lane=ExecutionLane.MEMORY_INDEX)
    return jobs


async def test_same_identity_requires_same_input_and_rollback_has_no_job(database, queue):
    async def handler(**_payload):
        pass
    queue.register("fixture.command", handler, execution_lane=ExecutionLane.CONTROL)
    async with database.transaction() as connection:
        current = await queue.current_generation_on(connection)
        spec = JobSpec("fixture.command", "command-1", {"value": 1}, subject_id="a", data_generation=current)
        first = await queue.enqueue(connection, spec)
        assert await queue.enqueue(connection, spec) == first
        with pytest.raises(AppError, match="job_identity_conflict"):
            await queue.enqueue(connection, JobSpec("fixture.command", "command-1", {"value": 2},
                                                     subject_id="a", data_generation=current))
        with pytest.raises(AppError, match="job_generation_expired"):
            await queue.enqueue(connection, JobSpec("fixture.command", "command-1", {"value": 1},
                                                     subject_id="a", data_generation="old"))
    with pytest.raises(RuntimeError, match="abort"):
        async with database.transaction() as connection:
            await queue.enqueue(connection, JobSpec("fixture.command", "rollback", {"value": 3}))
            raise RuntimeError("abort")
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT 1 FROM runtime.job_keys WHERE key='rollback'")).fetchone()


async def test_generation_is_bound_and_old_claim_cannot_apply(database, queue):
    async with database.transaction() as connection:
        generation = await queue.current_generation_on(connection)
        await queue.recompute_changed_on(connection, domain="memory", key="p", change_id="one",
            task="fixture.recompute", payload={"partition": "p"})
        row = await (await connection.execute("SELECT data_generation FROM runtime.job_keys")).fetchone()
        assert row["data_generation"] == generation
    claim = await queue._claim_recompute("memory", "p", 1, 60)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.installation_generation SET id='new-generation' WHERE singleton=true")
    async def never_apply(_connection, _claim):
        raise AssertionError("old generation applied")
    async with database.transaction() as connection:
        with pytest.raises(AppError, match="job_generation_expired"):
            await queue._complete_recompute_on(connection, claim, never_apply)
        with pytest.raises(AppError, match="job_generation_expired"):
            await queue.enqueue(connection, JobSpec("fixture.recompute", "old", {}, data_generation=generation))
        assert await queue.recompute_changed_on(connection, domain="memory", key="p", change_id="one",
            task="fixture.recompute", payload={"partition": "p"}) == 1
        rows = await (await connection.execute("SELECT data_generation FROM runtime.recompute_work")).fetchall()
        assert {row["data_generation"] for row in rows} == {generation, "new-generation"}


async def test_route_only_producer_enqueues_without_executable_handler(database, queue):
    queue.register_route("fixture.remote", execution_lane=ExecutionLane.DIARY)
    async with database.transaction() as connection:
        job_id = await queue.enqueue(connection, JobSpec("fixture.remote", "result-1", {"ref": "m1"}))
        job = await queue.observe_on(connection, job_id)
    assert job["execution_lane"] == "diary"
    assert "fixture.remote" not in queue.tasks
    assert "fixture.remote" not in queue.app.tasks
    async with database.transaction() as connection:
        with pytest.raises(AppError, match="task_not_recompute"):
            await queue.recompute_changed_on(connection, domain="remote", key="k", change_id="input-1",
                                               task="fixture.remote", payload={"ref": "m1"})


async def test_dynamic_producer_route_does_not_guess_a_lane(database, queue):
    queue.register_static_routes()
    async with database.transaction() as connection:
        with pytest.raises(AppError, match="dynamic_job_lane_required"):
            await queue.enqueue(connection, JobSpec("conversation.run", "autonomous-1", {"turn_id": "t1"}))
        assert not await (await connection.execute("SELECT 1 FROM runtime.job_keys WHERE key='autonomous-1'")).fetchone()
        job_id = await queue.enqueue(connection, JobSpec("conversation.run", "autonomous-2", {"turn_id": "t2"},
            execution_lane=ExecutionLane.PROACTIVE))
        assert (await queue.observe_on(connection, job_id))["execution_lane"] == "proactive"


async def test_coalesced_identities_remain_replayable_and_cancel_independently(database, queue):
    async def wake(**_payload):
        pass
    queue.register("fixture.wake", wake, execution_lane=ExecutionLane.MEMORY_INDEX)
    first = JobSpec("fixture.wake", "wake-1", {"partition": "p"}, subject_id="a")
    second = JobSpec("fixture.wake", "wake-2", {"partition": "p"}, subject_id="a")
    async with database.transaction() as connection:
        job_id = await queue.enqueue(connection, first, queueing_lock="partition:p")
        assert await queue.enqueue(connection, second, queueing_lock="partition:p") == job_id
        assert await queue.cancel(connection, "wake-1")
        state = await (await connection.execute("SELECT status FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
        assert state["status"] == "todo"
        with pytest.raises(AppError, match="job_identity_conflict"):
            await queue.enqueue(connection, JobSpec("fixture.wake", "wake-2", {"partition": "other"}, subject_id="a"))
        assert await queue.enqueue(connection, second) == job_id
        assert await queue.cancel(connection, "wake-2")
        state = await (await connection.execute("SELECT status FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
        assert state["status"] == "cancelled"


async def test_wake_never_absorbs_unlabelled_input(database, queue):
    async def handler(**_payload):
        pass
    queue.register("fixture.input", handler, execution_lane=ExecutionLane.MEMORY_INDEX)
    async with database.transaction() as connection:
        first = await queue.enqueue(connection, JobSpec("fixture.input", "input", {"id": "p"}))
        wake = await queue.enqueue(connection, JobSpec("fixture.input", "wake", {"id": "p"}),
                                   queueing_lock="wake:p")
        assert first != wake


async def test_subject_gets_turn_despite_busy_high_priority_subject(database, queue):
    async def handler(**_payload):
        pass
    queue.register("fixture.fair", handler, execution_lane=ExecutionLane.MEMORY_INDEX)
    async with database.transaction() as connection:
        ids = []
        for number in range(4):
            ids.append(await queue.enqueue(connection, JobSpec("fixture.fair", f"a-{number}",
                {"number": number}, ServiceClass.DUE, subject_id="a")))
        other = await queue.enqueue(connection, JobSpec("fixture.fair", "b-0", {"number": 0},
            ServiceClass.ELASTIC, subject_id="b"))
    await queue.open()
    try:
        worker_id = await queue.app.job_manager.register_worker()
        first = await queue.app.job_manager.fetch_job(["memory_index"], worker_id)
        second = await queue.app.job_manager.fetch_job(["memory_index"], worker_id)
        assert first.id == ids[0]
        assert second.id == other
    finally:
        await queue.close()


async def test_change_during_claim_and_gap_repair_do_not_fake_coverage(database, queue):
    async with database.transaction() as connection:
        args = dict(domain="diary", key="actor:day", task="fixture.recompute", payload={"actor": "a"})
        assert await queue.recompute_changed_on(connection, **args, change_id="event-a") == 1
        assert await queue.recompute_changed_on(connection, **args, change_id="event-a") == 1
    first = await queue._claim_recompute("diary", "actor:day", 1, 60)
    async with database.transaction() as connection:
        assert await queue.recompute_changed_on(connection, **args, change_id="event-b") == 2

    applied = []
    async def stale_apply(_connection, claim):
        applied.append(claim.revision)

    async with database.transaction() as connection:
        assert not await queue._complete_recompute_on(connection, first, stale_apply)
    assert applied == []
    second = await queue._claim_recompute("diary", "actor:day", 2, 60)

    async def partial(connection, claim):
        await connection.execute("INSERT INTO runtime.projection_cursors(consumer,subject_id) VALUES('fixture','a')")
        return RecomputeCoverage(isolated={"bad-material": "missing_input"})

    async with database.transaction() as connection:
        assert await queue._complete_recompute_on(connection, second, partial)
        state = await (await connection.execute("SELECT * FROM runtime.recompute_work")).fetchone()
        assert (state["dirty_revision"], state["processed_revision"], state["covered_revision"]) == (2, 2, 1)
        assert await queue.recompute_changed_on(connection, **args, change_id="repair-a") == 3
    third = await queue._claim_recompute("diary", "actor:day", 3, 60)

    async def repaired(_connection, _claim):
        return RecomputeCoverage(repaired=("bad-material",))

    async with database.transaction() as connection:
        assert await queue._complete_recompute_on(connection, third, repaired)
        state = await (await connection.execute("SELECT * FROM runtime.recompute_work")).fetchone()
        gap = await (await connection.execute("SELECT * FROM runtime.recompute_gaps")).fetchone()
        assert state["covered_revision"] == state["processed_revision"] == 3
        assert gap["repaired_at"] is not None


async def test_expired_claim_is_fenced_and_due_work_can_be_recovered(database, queue):
    async with database.transaction() as connection:
        await queue.recompute_changed_on(connection, domain="memory", key="p", change_id="source-1",
            task="fixture.recompute", payload={"partition": "p"})
    old = await queue._claim_recompute("memory", "p", 1, 60)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recompute_work SET claim_expires_at=now()-interval '1 second'")
    assert await queue.recover_recompute_due() == 1
    async def never_apply(_connection, _claim):
        raise AssertionError("expired owner applied")
    async with database.transaction() as connection:
        with pytest.raises(AppError, match="recompute_claim_lost"):
            await queue._complete_recompute_on(connection, old, never_apply)
        row = await (await connection.execute("SELECT * FROM runtime.recompute_work")).fetchone()
        assert row["queue_generation"] == 2 and row["queued_job_key"]
        attempt = await (await connection.execute("SELECT outcome FROM runtime.recompute_attempts WHERE claim_token=%s",
                                                   (old.token,))).fetchone()
        assert attempt["outcome"] == "expired"
