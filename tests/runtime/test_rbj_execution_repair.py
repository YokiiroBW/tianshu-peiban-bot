"""A repair grant is tied to the original budget and physical job history."""

from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import pytest
import pytest_asyncio
from psycopg.errors import CheckViolation
from psycopg.errors import QueryCanceled

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue, JobSpec, current_job_execution
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait


@pytest_asyncio.fixture
async def repair_queue(database):
    await migrate(database, tuple(Migration(name, files("tianshu.runtime").joinpath(name + ".sql").read_text(encoding="utf-8"))
                                  for name in ("rbj_wake", "rbj_execution_repair")))
    queue = JobQueue(database.config.dsn, database=database)
    state = {"mode": "failure", "event": {"event_id": "receipt", "action_id": "action", "result_revision": 2}}

    async def handler(event, repair_ref=None, repair_consumer=None):
        current = current_job_execution()
        if state["mode"] in {"preclose", "preunknown", "prewait"}:
            reason = {"preclose": "closed", "preunknown": "unknown", "prewait": "blocked"}[state["mode"]]
            async with database.transaction() as conn:
                if reason == "closed":
                    closed = await queue.close_execution_repair_on(conn, owner="conversation.result", identity="original-result",
                        data_generation=current["data_generation"], max_failures=4, repair_ref=repair_ref,
                        consumer=repair_consumer, observation_ref="preflight-proof")
                    assert closed["closed"]
                stop_args = dict(owner="conversation.result", identity="original-result", data_generation=current["data_generation"],
                    repair_ref=repair_ref, consumer=repair_consumer, job_id=current["job_id"],
                    native_attempt=current["native_attempt"], reason=reason, observation_ref="preflight-proof")
                stopped = await queue.record_repair_preflight_stop_on(conn, **stop_args)
                assert stopped["state"] == "preflight_stopped"
                assert await queue.record_repair_preflight_stop_on(conn, **stop_args) == stopped
                if reason == "unknown":
                    denied = await queue.begin_execution_on(conn, owner="conversation.result", identity="original-result",
                        data_generation=current["data_generation"], job_id=current["job_id"],
                        native_attempt=current["native_attempt"], max_failures=4,
                        repair_ref=repair_ref, consumer=repair_consumer)
                    assert not denied["eligible"] and denied["reason"] == "execution_preflight_stopped"
            if reason == "blocked":
                raise RecoveryWait("fixture_wait", datetime.now(UTC) + timedelta(minutes=1))
            return stopped
        async with database.transaction() as conn:
            started = await queue.begin_execution_on(conn, owner="conversation.result", identity="original-result",
                data_generation=current["data_generation"], job_id=current["job_id"],
                native_attempt=current["native_attempt"], max_failures=4,
                repair_ref=repair_ref, consumer=repair_consumer)
        if not started["eligible"]:
            return started
        if state["mode"] == "completed":
            async with database.transaction() as conn:
                return await queue.finish_execution_on(conn, owner="conversation.result", identity="original-result",
                    data_generation=current["data_generation"], job_id=current["job_id"],
                    native_attempt=current["native_attempt"], outcome="completed")
        if state["mode"] == "begin_then_stop":
            async with database.transaction() as conn:
                with pytest.raises(AppError, match="execution_repair_already_started"):
                    await queue.record_repair_preflight_stop_on(conn, owner="conversation.result", identity="original-result",
                        data_generation=current["data_generation"], repair_ref=repair_ref, consumer=repair_consumer,
                        job_id=current["job_id"], native_attempt=current["native_attempt"],
                        reason="unknown", observation_ref="too-late")
        if state["mode"] == "wait":
            error = RecoveryWait("dependency_busy", datetime.now(UTC) + timedelta(minutes=2))
            error.transaction_rollback_confirmed = True
        elif state["mode"] == "unknown":
            error = AppError("local_result_application_unconfirmed", status=409)
        elif state["mode"] == "statement_timeout":
            error = QueryCanceled("fixture")
            error.failure_phase = "transaction"
            error._tianshu_pending_rollbacks = 0
            error._tianshu_rollbacks_ok = True
        else:
            error = ValueError("fixture local failure")
        async with database.transaction() as conn:
            return await queue.finish_execution_on(conn, owner="conversation.result", identity="original-result",
                data_generation=current["data_generation"], job_id=current["job_id"],
                native_attempt=current["native_attempt"], outcome="failed", error=error)

    task = queue.register("conversation.result", handler, resource_recovery=True)
    await queue.open()
    try:
        yield queue, task, state
    finally:
        await queue.close()


def context(job_id, attempt=0):
    return SimpleNamespace(job=SimpleNamespace(id=job_id, attempts=attempt, queueing_lock=None))


def args(generation):
    return {"owner": "conversation.result", "identity": "original-result", "data_generation": generation, "max_failures": 4}


async def exhaust_normal(conn, queue, event):
    generation = await queue.current_generation_on(conn)
    history = []
    for index in range(4):
        job_id = await queue.enqueue(conn, JobSpec("conversation.result", f"normal-{index}", {"event": event},
                                                   data_generation=generation))
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'failed',false)", (job_id,))
        observed = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                            task_name="conversation.result")
        assert observed["complete"]
        history.extend(observed["executions"])
    result = await queue.seed_execution_history_on(conn, **args(generation), history=history, complete=True)
    assert result["failed_executions"] == 4 and result["reason"] == "execution_failure_budget_exhausted"
    return generation, result["failure_digest"]


async def grant_on(conn, queue, generation, failure_digest, *, consumer="life_activity"):
    registration = dict(owner="conversation.result", repair_ref="fix:source-eval", repair_revision="revision-1",
                        defect_key="source-eval", consumer=consumer)
    await queue.register_execution_repair_on(conn, **registration)
    return await queue.authorize_execution_repair_on(conn, **args(generation), repair_ref=registration["repair_ref"],
        repair_revision=registration["repair_revision"], defect_key=registration["defect_key"],
        failure_digest=failure_digest, consumer=consumer)


async def enqueue_repair(conn, queue, generation, event, *, consumer="life_activity", key="repair-1"):
    payload = {"event": event, "repair_ref": "fix:source-eval", "repair_consumer": consumer}
    job_id = await queue.enqueue(conn, JobSpec("conversation.result", key, payload, data_generation=generation),
                                 queueing_lock="repair:" + consumer, wake_object_key="result-object")
    bound = await queue.bind_execution_repair_job_on(conn, **args(generation), repair_ref="fix:source-eval",
                                                      consumer=consumer, job_id=job_id)
    assert bound["bound"]
    return job_id, payload


async def test_repair_failure_is_distinct_and_cannot_renew_normal_budget(database, repair_queue):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        missing = await queue.authorize_execution_repair_on(conn, **args(generation), repair_ref="unregistered",
            repair_revision="revision-1", defect_key="source-eval", failure_digest=digest, consumer="life_activity")
        assert not missing["eligible"] and missing["reason"] == "repair_not_registered"
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        with pytest.raises(AppError, match="execution_repair_registration_conflict"):
            await queue.register_execution_repair_on(conn, owner="conversation.result", repair_ref="random-other",
                repair_revision="new-sha", defect_key="source-eval", consumer="life_activity")
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        again = await queue.bind_execution_repair_job_on(conn, **args(generation), repair_ref="fix:source-eval",
                                                          consumer="life_activity", job_id=job_id)
        assert again["bound"] and again["reason"] == "already_bound"
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    result = await task(context(job_id), **payload)
    assert result["outcome"] == "failed" and result["failed_executions"] == 4
    assert result["repair"]["reason"] == "repair_failure_budget_exhausted"
    async with database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'failed',false)", (job_id,))
        observed = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                             task_name="conversation.result")
        await queue.seed_execution_history_on(conn, **args(generation), history=observed["executions"], complete=True)
        status = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert status["reason"] == "execution_failure_budget_exhausted" and status["failed_executions"] == 4
        assert status["repair_failed_executions"] == 1 and status["total_failed_executions"] == 5
        assert status["repair"]["remaining"] == 0 and not status["repair"]["eligible"]


async def test_wait_preserves_one_grant_but_unknown_requires_original_proof(database, repair_queue):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    state["mode"] = "wait"
    first = await task(context(job_id), **payload)
    assert first["outcome"] == "wait" and first["repair"]["remaining"] == 1
    await queue.app.job_manager.retry_job(context(job_id).job, retry_at=datetime.now(UTC))
    async with database.transaction() as conn:
        await conn.execute("UPDATE runtime.rbi_execution_facts SET retry_at=now()-interval '1 second' WHERE job_id=%s", (job_id,))
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    state["mode"] = "unknown"
    second = await task(context(job_id, 1), **payload)
    assert second["outcome"] == "unknown" and second["repair"]["reason"] == "execution_outcome_unknown"
    async with database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'failed',false)", (job_id,))
        blocked = await queue.close_execution_repair_on(conn, **args(generation), repair_ref="fix:source-eval",
            consumer="life_activity", observation_ref="life-proof:receipt")
        assert not blocked["closed"] and blocked["reason"] == "execution_outcome_unknown"
        inspected = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert inspected["unresolved_executions"][0]["purpose"] == "repair"
        assert inspected["unresolved_executions"][0]["repair_ref"] == "fix:source-eval"
        resolved = await queue.resolve_execution_on(conn, owner="conversation.result", identity="original-result",
            data_generation=generation, job_id=job_id, native_attempt=1, outcome="completed", observation_ref="life-proof:receipt")
        assert resolved["recorded"] and resolved["repair"]["reason"] == "repair_closed"
        closed = await queue.close_execution_repair_on(conn, **args(generation), repair_ref="fix:source-eval",
            consumer="life_activity", observation_ref="life-proof:receipt")
        assert closed["closed"] and closed["reason"] == "already_closed"


async def test_pre_begin_repair_history_and_statement_timeout_spend_repair_not_normal(database, repair_queue):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, _payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'failed',false)", (job_id,))
        history = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                            task_name="conversation.result")
        await queue.seed_execution_history_on(conn, **args(generation), history=history["executions"], complete=True)
        fact = await (await conn.execute("SELECT purpose,repair_grant_id FROM runtime.rbi_execution_facts "
                                          "WHERE job_id=%s AND native_attempt=0", (job_id,))).fetchone()
        assert fact["purpose"] == "repair" and fact["repair_grant_id"] is not None
        status = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert status["failed_executions"] == 4 and status["repair_failed_executions"] == 1
    from tianshu.runtime.rbi_execution_budget import _classify_outcome
    error = QueryCanceled("fixture")
    error.failure_phase = "transaction"
    error._tianshu_pending_rollbacks = 0
    error._tianshu_rollbacks_ok = True
    assert _classify_outcome("failed", error) == ("failed", None)


async def test_closed_grant_does_not_create_execution_and_unbound_job_cannot_commit(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        first = await queue.close_execution_repair_on(conn, **args(generation), repair_ref="fix:source-eval",
            consumer="life_activity", observation_ref="target-proof:non-application")
        second = await queue.close_execution_repair_on(conn, **args(generation), repair_ref="fix:source-eval",
            consumer="life_activity", observation_ref="target-proof:non-application")
        assert first["closed"] and first["reason"] == "closed"
        assert second["closed"] and second["reason"] == "already_closed"
        with pytest.raises(AppError, match="execution_repair_close_conflict"):
            await queue.close_execution_repair_on(conn, **args(generation), repair_ref="fix:source-eval",
                consumer="life_activity", observation_ref="different-non-application-proof")
        status = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert status["total_executions"] == 4 and status["repair"]["reason"] == "repair_closed"
        assert not (await grant_on(conn, queue, generation, digest))["eligible"]
    with pytest.raises(CheckViolation, match="execution_repair_job_unbound"):
        async with database.transaction() as conn:
            payload = {"event": state["event"], "repair_ref": "fix:source-eval", "repair_consumer": "life_activity"}
            await queue.enqueue(conn, JobSpec("conversation.result", "unbound-repair", payload, data_generation=generation),
                                queueing_lock="unbound-repair", wake_object_key="result-object")


async def test_covered_repair_native_success_is_not_a_repair_completion(database, repair_queue):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        repair_job, repair_payload = await enqueue_repair(conn, queue, generation, state["event"])
        target_payload = {"event": state["event"]}
        target = await queue.enqueue(conn, JobSpec("conversation.result", "cover-target", target_payload,
            data_generation=generation), queueing_lock="normal-target", wake_object_key="result-object")
        result = await queue.cover_wake_on(conn, repair_job, expected_task="conversation.result", expected_generation=generation,
            expected_payload=repair_payload, covered_by_job_id=target, expected_covering_payload=target_payload,
            wake_key="normal-target", coverage_ref="fixture:domain-verified")
        assert result["state"] == "covered"
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (repair_job,))
    exited = await task(context(repair_job), **repair_payload)
    assert exited == {"state": "covered", "covered_by": target}
    async with database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (repair_job,))
        history = await queue.observe_execution_history_on(conn, job_ids=[repair_job], data_generation=generation,
                                                            task_name="conversation.result")
        assert history["complete"] and history["executions"] == []
        await queue.seed_execution_history_on(conn, **args(generation), history=history["executions"], complete=True)
        status = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert status["repair"]["eligible"] and status["repair"]["remaining"] == 1
        assert status["repair_failed_executions"] == 0 and status["total_executions"] == 4


@pytest.mark.parametrize("mode", ["preclose", "preunknown"])
async def test_repair_doing_can_stop_before_begin_without_inventing_execution(database, repair_queue, mode):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    state["mode"] = mode
    stopped = await task(context(job_id), **payload)
    assert stopped["state"] == "preflight_stopped"
    async with database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (job_id,))
        history = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                            task_name="conversation.result")
        assert history["complete"] and history["executions"] == []
        await queue.seed_execution_history_on(conn, **args(generation), history=history["executions"], complete=True)
        status = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert status["total_executions"] == 4 and status["repair_failed_executions"] == 0
        if mode == "preclose":
            assert status["repair"]["reason"] == "repair_closed"
        else:
            assert status["repair"]["eligible"]


async def test_preflight_wait_commits_stop_before_native_retry_without_spending_grant(database, repair_queue):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    state["mode"] = "prewait"
    with pytest.raises(RecoveryWait) as captured:
        await task(context(job_id), **payload)
    await queue.app.job_manager.retry_job(context(job_id).job, retry_at=captured.value.retry_at)
    async with database.transaction() as conn:
        history = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                            task_name="conversation.result")
        assert history["complete"] and history["executions"] == []
        await queue.seed_execution_history_on(conn, **args(generation), history=history["executions"], complete=True)
        status = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert status["repair"]["remaining"] == 1 and status["repair_failed_executions"] == 0


async def test_native_success_without_begin_or_exit_proof_stays_unknown(database, repair_queue):
    queue, _task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, _payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (job_id,))
        history = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                            task_name="conversation.result")
        await queue.seed_execution_history_on(conn, **args(generation), history=history["executions"], complete=True)
        status = await queue.inspect_execution_on(conn, **args(generation), repair_ref="fix:source-eval", consumer="life_activity")
        assert status["repair"]["reason"] == "execution_outcome_unknown" and status["repair"]["remaining"] == 1
        assert status["failed_executions"] == 4 and status["repair_failed_executions"] == 0


async def test_preflight_stop_requires_the_current_worker_and_cannot_overwrite_begin(database, repair_queue):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        with pytest.raises(AppError, match="execution_current_job_mismatch"):
            await queue.record_repair_preflight_stop_on(conn, owner="conversation.result", identity="original-result",
                data_generation=generation, repair_ref="fix:source-eval", consumer="life_activity",
                job_id=job_id, native_attempt=0, reason="unknown", observation_ref="not-the-worker")
    state["mode"] = "begin_then_stop"
    await task(context(job_id), **payload)
    async with database.transaction() as conn:
        assert not await (await conn.execute("SELECT 1 FROM runtime.rbj_wake_exits WHERE job_id=%s", (job_id,))).fetchone()
        fact = await (await conn.execute("SELECT purpose,outcome FROM runtime.rbi_execution_facts WHERE job_id=%s", (job_id,))).fetchone()
        assert fact["purpose"] == "repair" and fact["outcome"] == "failed"


async def test_completed_repair_binds_domain_proof_without_replacing_execution_close(database, repair_queue):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    state["mode"] = "completed"
    result = await task(context(job_id), **payload)
    assert result["outcome"] == "completed" and result["repair"]["reason"] == "repair_closed"
    async with database.transaction() as conn:
        close_args = {**args(generation), "repair_ref": "fix:source-eval", "consumer": "life_activity"}
        original_close = await (await conn.execute(
            "SELECT closed_at,close_observation_ref FROM runtime.rbj_execution_repair_grants "
            "WHERE data_generation=%s AND repair_ref=%s AND consumer=%s",
            (generation, "fix:source-eval", "life_activity"))).fetchone()
        first = await queue.close_execution_repair_on(conn, **close_args, observation_ref="life-proof:original")
        again = await queue.close_execution_repair_on(conn, **close_args, observation_ref="life-proof:original")
        assert first["closed"] and first["reason"] == "already_closed" and again == first
        with pytest.raises(AppError, match="execution_repair_close_conflict"):
            await queue.close_execution_repair_on(conn, **close_args, observation_ref="life-proof:different")
        with pytest.raises(AppError, match="execution_repair_close_conflict"):
            await queue.close_execution_repair_on(conn, **close_args,
                                                  observation_ref=f"execution:{job_id}:0:completed")
        saved = await (await conn.execute(
            "SELECT g.closed_at,g.close_observation_ref,f.outcome,f.observation_ref FROM runtime.rbj_execution_repair_grants g "
            "JOIN runtime.rbi_execution_facts f ON f.repair_grant_id=g.id WHERE f.data_generation=%s "
            "AND f.job_id=%s AND f.native_attempt=0", (generation, job_id))).fetchone()
        assert saved == {"closed_at": original_close["closed_at"],
                         "close_observation_ref": f"execution:{job_id}:0:completed", "outcome": "completed",
                         "observation_ref": "life-proof:original"}
        assert original_close["close_observation_ref"] == saved["close_observation_ref"]


@pytest.mark.parametrize("mode", ["failure", "wait"])
async def test_noncompleted_repair_cannot_turn_matching_string_into_completion_proof(database, repair_queue, mode):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    state["mode"] = mode
    result = await task(context(job_id), **payload)
    assert result["outcome"] == ("failed" if mode == "failure" else "wait")
    async with database.transaction() as conn:
        close_args = {**args(generation), "repair_ref": "fix:source-eval", "consumer": "life_activity"}
        mechanical_lookalike = f"execution:{job_id}:0:completed"
        manual = await queue.close_execution_repair_on(conn, **close_args, observation_ref=mechanical_lookalike)
        assert manual["closed"] and manual["reason"] == "closed"
        with pytest.raises(AppError, match="execution_repair_close_conflict"):
            await queue.close_execution_repair_on(conn, **close_args, observation_ref="different-domain-proof")
        fact = await (await conn.execute("SELECT outcome,observation_ref FROM runtime.rbi_execution_facts "
                                         "WHERE data_generation=%s AND job_id=%s", (generation, job_id))).fetchone()
        assert fact["outcome"] == result["outcome"] and fact["observation_ref"] is None


@pytest.mark.parametrize("blocker", ["unknown", "started", "history_unknown"])
async def test_completed_repair_does_not_bind_proof_while_history_is_unresolved(database, repair_queue, blocker):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    state["mode"] = "completed"
    assert (await task(context(job_id), **payload))["outcome"] == "completed"
    async with database.transaction() as conn:
        if blocker == "history_unknown":
            await queue.seed_execution_history_on(conn, **args(generation), history=[], complete=False)
        else:
            other = await queue.enqueue(conn, JobSpec("conversation.result", "other-unresolved", {"event": state["event"]},
                                                       data_generation=generation))
            await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (other,))
            await conn.execute(
                "INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,job_id,"
                "native_attempt,outcome) SELECT data_generation,identity_digest,owner,max_failures,%s,0,%s "
                "FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s AND native_attempt=0",
                (other, blocker, generation, job_id))
        closed = await queue.close_execution_repair_on(conn, **args(generation), repair_ref="fix:source-eval",
            consumer="life_activity", observation_ref="life-proof:cannot-close-unresolved")
        expected = {"unknown": "execution_outcome_unknown", "started": "execution_in_progress",
                    "history_unknown": "history_unknown"}[blocker]
        assert not closed["closed"] and closed["reason"] == expected
        fact = await (await conn.execute("SELECT observation_ref FROM runtime.rbi_execution_facts "
                                         "WHERE data_generation=%s AND job_id=%s", (generation, job_id))).fetchone()
        assert fact["observation_ref"] is None


async def test_completed_execution_of_another_grant_cannot_authorize_a_proof_binding(database, repair_queue):
    queue, task, state = repair_queue
    async with database.transaction() as conn:
        generation, digest = await exhaust_normal(conn, queue, state["event"])
        assert (await grant_on(conn, queue, generation, digest))["eligible"]
        job_id, payload = await enqueue_repair(conn, queue, generation, state["event"])
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    state["mode"] = "completed"
    assert (await task(context(job_id), **payload))["outcome"] == "completed"
    async with database.transaction() as conn:
        assert (await grant_on(conn, queue, generation, digest, consumer="life_commitment"))["eligible"]
        close_args = {**args(generation), "repair_ref": "fix:source-eval", "consumer": "life_commitment"}
        lookalike = f"execution:{job_id}:0:completed"
        assert (await queue.close_execution_repair_on(conn, **close_args, observation_ref=lookalike))["closed"]
        with pytest.raises(AppError, match="execution_repair_close_conflict"):
            await queue.close_execution_repair_on(conn, **close_args, observation_ref="other-target-proof")
        fact = await (await conn.execute("SELECT observation_ref FROM runtime.rbi_execution_facts "
                                         "WHERE data_generation=%s AND job_id=%s", (generation, job_id))).fetchone()
        assert fact["observation_ref"] is None
