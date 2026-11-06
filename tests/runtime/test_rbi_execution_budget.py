"""Native consumption attempts share one durable failure budget across job keys."""

from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import pytest
import pytest_asyncio
from psycopg import OperationalError

from tianshu.runtime.database import classify_database_failure
from tianshu.runtime.errors import AppError
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobQueue, JobSpec, current_job_execution
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait


@pytest_asyncio.fixture
async def queue(database):
    await migrate(database, (Migration("rbi_execution_budget",
        files("tianshu.runtime").joinpath("rbi_execution_budget.sql").read_text(encoding="utf-8")),))
    jobs = JobQueue(database.config.dsn, database=database)
    await jobs.open()
    try:
        yield jobs
    finally:
        await jobs.close()


def identity(generation):
    return f"conversation.result:{generation}:receipt:action:revision-2"


def budget_args(generation):
    return {"owner": "conversation.result", "identity": identity(generation),
            "data_generation": generation, "max_failures": 4}


async def new_doing_job(conn, queue, name, key, payload=None):
    job_id = await queue.enqueue(conn, JobSpec(name, key, payload or {}))
    await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    return job_id


def context(job_id, attempt=0):
    return SimpleNamespace(job=SimpleNamespace(id=job_id, attempts=attempt, queueing_lock=None))


async def test_incomplete_history_wait_and_four_failures_across_new_jobs(database, queue):
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        args = budget_args(generation)
        pending = await queue.seed_execution_history_on(conn, **args, history=[], complete=False)
        assert not pending["ready"] and pending["reason"] == "history_unknown"
        assert pending["total_executions"] is None and pending["failed_executions"] is None
        assert not (await queue.inspect_execution_on(conn, **args))["eligible"]
        initialized = await queue.seed_execution_history_on(conn, **args, history=[], complete=True)
        assert initialized["ready"] and initialized["eligible"] and initialized["remaining"] == 4

    async def handle(kind):
        current = current_job_execution()
        assert current is not None and current["native_attempt"] == 0
        async with database.transaction() as conn:
            begun = await queue.begin_execution_on(conn, **args, job_id=current["job_id"],
                                                     native_attempt=current["native_attempt"])
        if not begun["eligible"]:
            return begun
        if kind == "wait":
            wait = RecoveryWait("resource_busy", datetime.now(UTC) + timedelta(minutes=3), dependency="db")
            wait.transaction_rollback_confirmed = True
            error = wait
        else:
            error = ValueError("fixture definitive failure")
        async with database.transaction() as conn:
            return await queue.finish_execution_on(conn, owner=args["owner"], identity=args["identity"],
                data_generation=args["data_generation"], job_id=current["job_id"], native_attempt=current["native_attempt"],
                outcome="failed", error=error)

    task = queue.register("fixture.rbi_execution", handle, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        wait_id = await new_doing_job(conn, queue, "fixture.rbi_execution", "budget-wait", {"kind": "wait"})
    waiting = await task(context(wait_id), kind="wait")
    assert waiting["outcome"] == "wait" and waiting["failed_executions"] == 0
    assert waiting["reason"] == "execution_wait" and waiting["retry_at"] is not None
    assert current_job_execution() is None
    async with database.transaction() as conn:
        assert (await queue.inspect_execution_on(conn, **args))["reason"] == "execution_wait"
        await conn.execute("UPDATE runtime.rbi_execution_facts SET retry_at=now()-interval '1 second' WHERE job_id=%s", (wait_id,))
        await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (wait_id,))
    for number in range(1, 5):
        async with database.transaction() as conn:
            assert (await queue.inspect_execution_on(conn, **args))["eligible"]
            job_id = await new_doing_job(conn, queue, "fixture.rbi_execution", f"budget-failed-{number}", {"kind": "failure"})
        result = await task(context(job_id), kind="failure")
        assert result["outcome"] == "failed" and result["failed_executions"] == number
        async with database.transaction() as conn:
            await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (job_id,))
    async with database.transaction() as conn:
        exhausted = await queue.inspect_execution_on(conn, **args)
        assert not exhausted["eligible"] and exhausted["reason"] == "execution_failure_budget_exhausted"
        assert (exhausted["total_executions"], exhausted["failed_executions"], exhausted["remaining"]) == (5, 4, 0)
        another = await new_doing_job(conn, queue, "fixture.rbi_execution", "budget-bypass", {"kind": "failure"})
    rejected = await task(context(another), kind="failure")
    assert not rejected["eligible"] and rejected["reason"] == "execution_failure_budget_exhausted"
    async with database.transaction() as conn:
        assert (await queue.inspect_execution_on(conn, **args))["total_executions"] == 5
        with pytest.raises(AppError, match="execution_budget_identity_conflict"):
            await queue.inspect_execution_on(conn, **dict(args, owner="other"))
        with pytest.raises(AppError, match="execution_budget_identity_conflict"):
            await queue.inspect_execution_on(conn, **dict(args, max_failures=5))


async def test_unknown_commit_requires_observation_and_preserves_original_fact(database, queue):
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        args = budget_args(generation)
        await queue.seed_execution_history_on(conn, **args, history=[], complete=True)

    async def handle():
        current = current_job_execution()
        async with database.transaction() as conn:
            assert (await queue.begin_execution_on(conn, **args, job_id=current["job_id"],
                                                   native_attempt=current["native_attempt"]))["eligible"]
        error = OperationalError("fixture uncertain commit")
        error.database_failure = classify_database_failure(error, phase="commit", rollback_confirmed=False)
        async with database.transaction() as conn:
            return await queue.finish_execution_on(conn, owner=args["owner"], identity=args["identity"],
                data_generation=generation, job_id=current["job_id"], native_attempt=current["native_attempt"],
                outcome="failed", error=error)

    task = queue.register("fixture.rbi_unknown", handle, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        job_id = await new_doing_job(conn, queue, "fixture.rbi_unknown", "unknown-job")
    unknown = await task(context(job_id))
    assert unknown["outcome"] == "unknown" and unknown["failed_executions"] == 0
    assert unknown["reason"] == "execution_outcome_unknown" and unknown["unknown_held"] == 1
    async with database.transaction() as conn:
        active = await queue.resolve_execution_on(conn, owner=args["owner"], identity=args["identity"],
            data_generation=generation, job_id=job_id, native_attempt=0, outcome="failed", observation_ref="native-proof:1")
        assert not active["recorded"] and active["reason"] == "execution_in_progress"
        await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (job_id,))
        resolved = await queue.resolve_execution_on(conn, owner=args["owner"], identity=args["identity"],
            data_generation=generation, job_id=job_id, native_attempt=0, outcome="failed", observation_ref="native-proof:1")
        assert resolved["recorded"] and resolved["failed_executions"] == 1 and resolved["eligible"]
        duplicate = await queue.resolve_execution_on(conn, owner=args["owner"], identity=args["identity"],
            data_generation=generation, job_id=job_id, native_attempt=0, outcome="failed", observation_ref="native-proof:1")
        assert not duplicate["recorded"] and duplicate["failed_executions"] == 1
        original = await (await conn.execute("SELECT outcome,resolved_outcome,observation_ref FROM "
            "runtime.rbi_execution_facts WHERE job_id=%s AND native_attempt=0", (job_id,))).fetchone()
        assert original == {"outcome": "unknown", "resolved_outcome": "failed", "observation_ref": "native-proof:1"}
        with pytest.raises(AppError, match="execution_attempt_conflict"):
            await queue.resolve_execution_on(conn, owner=args["owner"], identity=args["identity"],
                data_generation=generation, job_id=job_id, native_attempt=0, outcome="completed", observation_ref="other")

async def test_wait_without_confirmed_remote_rollback_remains_unknown(database, queue):
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        args = budget_args(generation)
        await queue.seed_execution_history_on(conn, **args, history=[], complete=True)

    async def handle():
        current = current_job_execution()
        async with database.transaction() as conn:
            assert (await queue.begin_execution_on(conn, **args, job_id=current["job_id"],
                                                    native_attempt=current["native_attempt"]))["eligible"]
        wait = RecoveryWait("resource_busy", datetime.now(UTC) + timedelta(minutes=1))
        wait.remote_rollback_confirmed = False
        async with database.transaction() as conn:
            return await queue.finish_execution_on(conn, owner=args["owner"], identity=args["identity"],
                data_generation=generation, job_id=current["job_id"], native_attempt=current["native_attempt"],
                outcome="failed", error=wait)

    task = queue.register("fixture.rbi_wait_unknown", handle, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        job_id = await new_doing_job(conn, queue, "fixture.rbi_wait_unknown", "wait-unknown")
    result = await task(context(job_id))
    assert result["outcome"] == "unknown" and result["reason"] == "execution_outcome_unknown"
    assert result["failed_executions"] == 0 and result["unknown_held"] == 1


async def test_native_history_ordinal_and_incremental_import_preserve_unknown(database, queue):
    async def handler():
        pass

    queue.register("fixture.rbi_history", handler, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        args = budget_args(generation)
        assert (await queue.seed_execution_history_on(conn, **args, history=[], complete=True))["eligible"]
        job_id = await new_doing_job(conn, queue, "fixture.rbi_history", "historical-job")
    await queue.app.job_manager.retry_job(context(job_id).job, retry_at=datetime.now(UTC))
    async with database.transaction() as conn:
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (job_id,))
        history = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                           task_name="fixture.rbi_history")
        assert history["complete"] and history["reason"] is None
        assert [(event["native_attempt"], event["outcome"]) for event in history["executions"]] == [(0, "unknown"), (1, "failed")]
        assert all(event["evidence_ref"].startswith("native-job-event:") for event in history["executions"])
        imported = await queue.seed_execution_history_on(conn, **args, history=history["executions"], complete=True)
        assert imported["ready"] and not imported["eligible"] and imported["reason"] == "execution_outcome_unknown"
        assert (imported["total_executions"], imported["failed_executions"], imported["unknown_held"]) == (2, 1, 1)
        again = await queue.seed_execution_history_on(conn, **args, history=history["executions"], complete=True)
        assert again == imported
        resolved = await queue.resolve_execution_on(conn, owner=args["owner"], identity=args["identity"],
            data_generation=generation, job_id=job_id, native_attempt=0, outcome="failed", observation_ref="native-proof:retry")
        assert resolved["eligible"] and resolved["failed_executions"] == 2
        imported_again = await queue.seed_execution_history_on(conn, **args, history=history["executions"], complete=True)
        assert imported_again["eligible"] and imported_again["total_executions"] == 2
        original = await (await conn.execute("SELECT outcome,resolved_outcome FROM runtime.rbi_execution_facts "
            "WHERE job_id=%s AND native_attempt=0", (job_id,))).fetchone()
        assert original == {"outcome": "unknown", "resolved_outcome": "failed"}


async def test_history_import_finds_a_job_that_failed_before_begin(database, queue):
    async def handler():
        pass

    queue.register("fixture.rbi_precheck", handler, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        args = budget_args(generation)
        await queue.seed_execution_history_on(conn, **args, history=[], complete=True)
        job_id = await new_doing_job(conn, queue, "fixture.rbi_precheck", "precheck-failed")
        await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (job_id,))
        # The worker failed before it could call begin; native job history is the proof.
        history = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                           task_name="fixture.rbi_precheck")
        assert history["complete"] and len(history["executions"]) == 1
        assert history["executions"][0]["native_attempt"] == 0 and history["executions"][0]["outcome"] == "failed"
        imported = await queue.seed_execution_history_on(conn, **args, history=history["executions"], complete=True)
        assert imported["failed_executions"] == 1 and imported["total_executions"] == 1
        again = await queue.seed_execution_history_on(conn, **args, history=history["executions"], complete=True)
        assert again == imported
        missing = await queue.seed_execution_history_on(conn, **args, history=[], complete=False)
        assert not missing["ready"] and missing["reason"] == "history_unknown" and missing["failed_executions"] is None
        assert (await queue.inspect_execution_on(conn, **args))["reason"] == "history_unknown"
        restored = await queue.seed_execution_history_on(conn, **args, history=history["executions"], complete=True)
        assert restored["failed_executions"] == 1 and restored["eligible"]


async def test_native_history_pages_are_bounded_durable_and_resume_after_new_events(database, queue):
    async def handler():
        pass

    queue.register("fixture.rbi_paged", handler, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        args = budget_args(generation)
        job_id = await new_doing_job(conn, queue, "fixture.rbi_paged", "paged-history")
    await queue.app.job_manager.retry_job(context(job_id).job, retry_at=datetime.now(UTC))
    async with database.transaction() as conn:
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (job_id,))

    with pytest.raises(RuntimeError, match="rollback page"):
        async with database.transaction() as conn:
            page = await queue.advance_execution_history_on(conn, **args, job_id=job_id,
                                                             task_name="fixture.rbi_paged", limit=128)
            assert page["complete"] and len(page["executions"]) == 2
            raise RuntimeError("rollback page")
    async with database.transaction(read_only=True) as conn:
        cursor = await (await conn.execute("SELECT 1 FROM runtime.rbi_execution_history_cursors WHERE job_id=%s",
                                            (job_id,))).fetchone()
        facts = await (await conn.execute("SELECT 1 FROM runtime.rbi_execution_facts WHERE job_id=%s",
                                           (job_id,))).fetchone()
        assert cursor is None and facts is None

    async def reach_tail():
        observations = []
        for _ in range(16):
            async with database.transaction() as conn:
                page = await queue.advance_execution_history_on(conn, **args, job_id=job_id,
                                                                 task_name="fixture.rbi_paged", limit=1)
                assert page["events_read"] <= 1
                observations.extend(page["executions"])
                assert (await queue.inspect_execution_on(conn, **args))["reason"] == "history_unknown"
                if page["complete"]:
                    assert page["reason"] is None and page["job_status"] == "failed"
                    return observations
                assert page["reason"] == "execution_history_more"
        pytest.fail("native event cursor did not reach the tail")

    observations = await reach_tail()
    assert [(item["native_attempt"], item["outcome"]) for item in observations] == [(0, "unknown"), (1, "failed")]
    async with database.transaction() as conn:
        first = await queue.seed_execution_history_on(conn, **args, history=[], complete=True)
        assert (first["total_executions"], first["failed_executions"], first["unknown_held"]) == (2, 1, 1)
    # A failed job may be explicitly retried later. The saved cursor must see
    # only the appended events and must not turn the earlier unknown into zero.
    await queue.app.job_manager.retry_job(context(job_id, 1).job, retry_at=datetime.now(UTC))
    async with database.transaction() as conn:
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (job_id,))
    observations = await reach_tail()
    assert [(item["native_attempt"], item["outcome"]) for item in observations] == [(2, "failed")]
    async with database.transaction() as conn:
        final = await queue.seed_execution_history_on(conn, **args, history=[], complete=True)
        assert (final["total_executions"], final["failed_executions"], final["unknown_held"]) == (3, 2, 1)
        missing = await queue.advance_execution_history_on(conn, **args, job_id=job_id + 10_000_000,
                                                           task_name="fixture.rbi_paged", limit=1)
        assert not missing["complete"] and missing["reason"] == "execution_history_job_missing_or_changed"
        assert missing["job_status"] is None and (await queue.inspect_execution_on(conn, **args))["reason"] == "history_unknown"


async def test_history_does_not_guess_missing_events_or_exclude_someone_elses_active_job(database, queue):
    async def handler():
        pass

    queue.register("fixture.rbi_missing", handler, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        job_id = await new_doing_job(conn, queue, "fixture.rbi_missing", "active-history")
        active = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                          task_name="fixture.rbi_missing")
        assert active["complete"] and active["executions"][0]["outcome"] == "unknown"
        with pytest.raises(AppError, match="execution_current_job_mismatch"):
            await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                task_name="fixture.rbi_missing", current_job={"job_id": job_id, "native_attempt": 0,
                                                               "data_generation": generation})
        await conn.execute("DELETE FROM procrastinate_events WHERE job_id=%s AND type='started'", (job_id,))
        missing = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                           task_name="fixture.rbi_missing")
        assert not missing["complete"] and missing["reason"] == "execution_history_incomplete"


async def test_wrapper_context_can_exclude_only_its_own_current_execution(database, queue):
    seen = []

    async def handler():
        current = current_job_execution()
        seen.append(current)
        async with database.transaction() as conn:
            history = await queue.observe_execution_history_on(conn, job_ids=[current["job_id"]],
                data_generation=current["data_generation"], task_name="fixture.rbi_context", current_job=current)
            assert history == {"complete": True, "reason": None, "executions": []}
            args = budget_args(current["data_generation"])
            page = await queue.advance_execution_history_on(conn, **args, job_id=current["job_id"],
                task_name="fixture.rbi_context", current_job=current)
            assert page["complete"] and page["executions"] == [] and page["job_status"] == "doing"
            await queue.seed_execution_history_on(conn, **args, history=[], complete=True)
            begun = await queue.begin_execution_on(conn, **args, job_id=current["job_id"],
                                                    native_attempt=current["native_attempt"])
            assert begun["eligible"]
        async with database.transaction() as conn:
            await queue.finish_execution_on(conn, owner=args["owner"], identity=args["identity"],
                data_generation=args["data_generation"], job_id=current["job_id"], native_attempt=current["native_attempt"],
                outcome="completed")

    task = queue.register("fixture.rbi_context", handler, execution_lane=ExecutionLane.MAINTENANCE)
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        job_id = await new_doing_job(conn, queue, "fixture.rbi_context", "context-job")
    assert current_job_execution() is None
    await task(context(job_id))
    assert seen == [{"job_id": job_id, "native_attempt": 0, "data_generation": generation}]
    assert current_job_execution() is None
