"""Persistent pure-computation retries retain original evidence and model receipts."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest
from psycopg.types.json import Jsonb

from tianshu.companion.types import ReflectionProposal
from tianshu.memory.embedding import Embeddings
from tianshu.memory.index_jobs import IndexJobs, SCAN_LIMIT
from tianshu.memory.steps import ModelSteps
from tianshu.models.codec import request_from_wire
from tianshu.models.types import ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass


class IndexModels:
    def __init__(self, outcome="succeeded", error=None):
        self.profile = {"dimensions": 1024, "normalization": "none", "model_version": "synthetic-v1"}
        self.outcome, self.error = outcome, error
        self.requests, self.reads, self.status_reads = [], [], []
        self.results, self.receipts = {}, {}
        self.after_generate = None
        self.after_read = None

    async def embedding_profile(self):
        return self.profile

    def result_for(self, request, status, error=None):
        receipt = ModelReceipt(request.attempt_id, request.intent_id, status, "submitted", "route", 1,
            "provider", "model", "openai.embedding", "quota", error_code=error,
            finished_at=datetime.now(UTC) if status not in {"queued", "running"} else None,
            data_generation=request.data_generation)
        return ModelResult((), receipt, native_response={"data": [{"embedding": [1.0] + [0.0] * 1023}]}
                           if status == "succeeded" else None)

    async def generate(self, request):
        self.requests.append(request)
        result = self.result_for(request, self.outcome, self.error)
        self.results[request.attempt_id], self.receipts[request.attempt_id] = result, result.receipt
        if self.after_generate:
            await self.after_generate(request)
        return result

    async def submit(self, request, *, completion=None):
        if request.attempt_id in self.receipts:
            return self.receipts[request.attempt_id]
        return (await self.generate(request)).receipt

    async def result(self, attempt_id, **_kwargs):
        self.reads.append(attempt_id)
        if self.after_read:
            await self.after_read(attempt_id)
        result = self.results.get(attempt_id)
        return None if result and result.receipt.status in {"queued", "running"} else result

    async def status(self, attempt_id):
        self.status_reads.append(attempt_id)
        return self.receipts.get(attempt_id)


def configured(core, outcome="succeeded", error=None):
    models = IndexModels(outcome, error)
    steps = ModelSteps(core.database, models, core.sources)
    core.workers.steps = steps
    core.workers.embeddings = Embeddings(core.database, models, steps)
    return models


async def fact(core, context, key, *, hypothesis=False):
    refs = await core.sources.record_event(context, producer="web", event_id=key, revision=1,
        occurred_at=datetime.now(UTC), parts=({"kind": "text", "text": "合成索引材料 " + key},),
        origin_kind=OriginKind.HUMAN)
    async with core.database.transaction() as conn:
        return await core.claims.put_on(conn, context, {"predicate": "interest", "value": key,
            "source_refs": refs, "memory_kind": "hypothesis" if hypothesis else "fact",
            "assertion": "inferred" if hypothesis else "reported"}, automatic=True)


async def job_for(core, value):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute("SELECT * FROM memory.index_jobs WHERE object_id=%s AND revision=%s "
            "ORDER BY created_at DESC,index_id LIMIT 1", (value.claim_id, value.revision))).fetchone()


async def deliver(core, index_id, *, due=True):
    # Simulate claiming the existing native delivery, without running a worker
    # or sleeping through the persistent backoff in a disposable PG test.
    async with core.database.transaction() as conn:
        job = await (await conn.execute("SELECT generation FROM memory.index_jobs WHERE index_id=%s", (index_id,))).fetchone()
        if due:
            await conn.execute("UPDATE memory.index_jobs SET next_due=now()-interval '1 second' WHERE index_id=%s", (index_id,))
        rows = await (await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE task_name='memory.index_retry' "
            "AND args->>'index_id'=%s AND status='todo' RETURNING id", (index_id,))).fetchall()
    await core.workers.index_retry(index_id, generation=job["generation"])
    async with core.database.transaction() as conn:
        if rows:
            await conn.execute("UPDATE procrastinate_jobs SET status='succeeded' WHERE id=ANY(%s) AND status='doing'",
                               ([row["id"] for row in rows],))


@pytest.mark.parametrize("with_policy", [False, True])
async def test_transient_index_yields_to_later_objects_then_retries_once_after_restart(core, ordinary_users, with_policy):
    current, _ = ordinary_users
    first, later = await fact(core, current, "first-timeout"), await fact(core, current, "later-success")
    models = configured(core, "unknown", "timeout_unknown")
    policy = None
    if with_policy:
        from tianshu.runtime.recovery_policy import RecoveryPolicy
        models.profile["quota_group"] = "quota"
        policy = RecoveryPolicy()
        core.workers.index_jobs.bind_observability(recovery_policy=policy)
    async with core.database.transaction(read_only=True) as conn:
        generation = await core.memory.jobs.current_generation_on(conn)
    await core.workers.index_object("claim", first.claim_id, first.revision, generation=generation)
    held = await job_for(core, first)
    assert held["state"] in {"observing", "retry_wait"} and held["attempt_count"] == 1
    assert 24 <= (held["next_due"] - held["updated_at"]).total_seconds() <= 36
    assert held["lease_owner"] is held["lease_until"] is None
    original = models.requests[0]
    async with core.database.transaction(read_only=True) as conn:
        before = await (await conn.execute("SELECT request,request_timeout_seconds,state,error_code FROM memory.model_steps "
            "WHERE step_id=%s", (original.intent_id + ":" + held["profile_key"],))).fetchone()
        row = await (await conn.execute("SELECT lexical FROM memory.claim_revisions "
            "WHERE claim_id=%s AND revision=%s", (first.claim_id, first.revision))).fetchone()
        vector_before = await (await conn.execute("SELECT 1 FROM memory.search_vectors WHERE object_id=%s "
            "AND revision=%s AND generation=%s", (first.claim_id, first.revision, generation))).fetchone()
    assert row["lexical"] and vector_before is None
    assert before is not None and before["state"] == "unknown"
    models.outcome, models.error = "succeeded", None
    await core.workers.index_object("claim", later.claim_id, later.revision, generation=generation)
    later_job = await job_for(core, later)
    assert later_job["state"] == "succeeded", later_job["last_error"]
    # An early signal must not consume another model attempt or erase the due.
    await core.workers.index_retry(held["index_id"], generation=generation)
    await core.workers.index_retry(held["index_id"], completed_attempt_id=original.attempt_id, generation=generation)
    assert len(models.requests) == 2 and (await job_for(core, first))["next_due"] == held["next_due"]
    core.workers.index_jobs = IndexJobs(core.workers)
    if policy:
        core.workers.index_jobs.bind_observability(recovery_policy=policy)
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE lineage_id=%s",
                               (held["recovery_lineage_id"],))
    core.workers.embeddings.steps = ModelSteps(core.database, models, core.sources)
    previous_reads = models.status_reads.count(original.attempt_id)
    await deliver(core, held["index_id"])
    done = await job_for(core, first)
    assert done["state"] == "succeeded" and done["attempt_count"] == 2 and done["next_due"] is None
    assert models.status_reads.count(original.attempt_id) > previous_reads
    assert len(models.requests) == 3 and len({request.attempt_id for request in models.requests}) == 3
    assert all(request.execution_timeout_seconds == 30.0 and request.business_deadline_at is None
               and request.max_queue_wait is None for request in models.requests)
    assert models.results[original.attempt_id].receipt.status == "unknown"
    async with core.database.transaction(read_only=True) as conn:
        after = await (await conn.execute("SELECT request,request_timeout_seconds,state,error_code FROM memory.model_steps "
            "WHERE step_id=%s", (original.intent_id + ":" + held["profile_key"],))).fetchone()
        chain = await (await conn.execute("SELECT * FROM memory.index_attempts WHERE index_id=%s ORDER BY number", (held["index_id"],))).fetchall()
        policy_chain = (await (await conn.execute("SELECT status FROM runtime.recovery_attempts WHERE lineage_id=%s "
            "ORDER BY attempt_number", (held["recovery_lineage_id"],))).fetchall()) if policy else []
        vector = await (await conn.execute("SELECT embedding::text AS value FROM memory.search_vectors "
            "WHERE object_id=%s AND revision=%s AND generation=%s AND profile_key=%s",
            (first.claim_id, first.revision, generation, held["profile_key"]))).fetchone()
    assert vector is not None and after == before
    assert [attempt["status"] for attempt in chain] == ["unknown", "succeeded"]
    if policy:
        assert [attempt["status"] for attempt in policy_chain] == ["unknown", "succeeded"]
    # A late original success cannot replace the already selected successor.
    late = models.result_for(original, "succeeded")
    models.results[original.attempt_id] = late
    models.receipts[original.attempt_id] = late.receipt
    reads, status_reads = list(models.reads), list(models.status_reads)
    await core.workers.index_retry(held["index_id"], generation=generation)
    async with core.database.transaction(read_only=True) as conn:
        assert await (await conn.execute("SELECT embedding::text AS value FROM memory.search_vectors "
            "WHERE object_id=%s AND revision=%s AND generation=%s AND profile_key=%s",
            (first.claim_id, first.revision, generation, held["profile_key"]))).fetchone() == vector
    assert models.reads == reads and models.status_reads == status_reads and len(models.requests) == 3


@pytest.mark.parametrize("later_status", ["succeeded", "missing", "unavailable", "queued", "running",
                                          "unfinished", "naive_time", "cancel_reason", "cancelled",
                                          "mismatched", "old_generation", "forbidden"])
@pytest.mark.parametrize("with_policy", [False, True])
async def test_unknown_index_rechecks_original_before_selecting_successor(core, ordinary_users, later_status, with_policy):
    context, _ = ordinary_users
    value = await fact(core, context, "original-status-changes-" + later_status)
    models = configured(core, "unknown", "timeout_unknown")
    if with_policy:
        from tianshu.runtime.recovery_policy import RecoveryPolicy
        models.profile["quota_group"] = "quota"
        core.workers.index_jobs.bind_observability(recovery_policy=RecoveryPolicy())
    async with core.database.transaction(read_only=True) as conn:
        generation = await core.memory.jobs.current_generation_on(conn)
    await core.workers.index_object("claim", value.claim_id, value.revision, generation=generation)
    job, original = await job_for(core, value), models.requests[0]
    read_status = models.status
    reads = 0

    async def changing_status(attempt_id):
        nonlocal reads
        if attempt_id == original.attempt_id:
            reads += 1
            if reads == 2:
                if later_status == "missing":
                    return None
                if later_status == "unavailable":
                    raise AppError("internal_error", status=503)
                if later_status == "forbidden":
                    raise AppError("actor_forbidden", status=403)
                if later_status in {"queued", "running"}:
                    return replace(models.receipts[attempt_id], status=later_status, finished_at=None)
                if later_status == "unfinished":
                    return replace(models.receipts[attempt_id], finished_at=None)
                if later_status == "naive_time":
                    return replace(models.receipts[attempt_id], finished_at=datetime(2026, 11, 5))
                if later_status == "cancel_reason":
                    return replace(models.receipts[attempt_id], cancellation_reason="consumer_interrupted")
                if later_status == "cancelled":
                    return replace(models.receipts[attempt_id], status="cancelled", error_code="cancelled",
                                   cancellation_reason="explicit_cancel")
                if later_status == "mismatched":
                    return replace(models.receipts[attempt_id], attempt_id="different-attempt")
                if later_status == "old_generation":
                    return replace(models.receipts[attempt_id], data_generation="old-generation")
                result = models.result_for(original, "succeeded")
                models.results[attempt_id], models.receipts[attempt_id] = result, result.receipt
        return await read_status(attempt_id)

    models.status = changing_status
    await deliver(core, job["index_id"])
    observed = await job_for(core, value)
    assert observed["state"] == ("cancelled" if later_status == "cancelled" else
                                  "failed" if later_status in {"mismatched", "old_generation"} else
                                  "revoked" if later_status == "forbidden" else "observing")
    assert observed["attempt_count"] == 1
    assert len(models.requests) == 1
    if with_policy:
        async with core.database.transaction(read_only=True) as conn:
            chain = await (await conn.execute("SELECT status FROM runtime.recovery_attempts WHERE lineage_id=%s",
                                               (job["recovery_lineage_id"],))).fetchall()
        assert len(chain) == 1
    if later_status == "succeeded":
        await core.workers.index_retry(job["index_id"], generation=generation)
        assert (await job_for(core, value))["state"] == "succeeded"
        assert len(models.requests) == 1


async def test_repeated_429_retains_a_capped_durable_backoff_without_immediate_replay(core, ordinary_users):
    current, _ = ordinary_users
    value = await fact(core, current, "rate-limited")
    models = configured(core, "failed", "upstream_http_429")
    await core.workers.index_object("claim", value.claim_id, value.revision)
    for number, seconds in enumerate((30, 60, 120, 240, 480, 900, 900), 1):
        job = await job_for(core, value)
        assert job["state"] == "retry_wait" and job["attempt_count"] == number
        assert (job["next_due"] - job["updated_at"]).total_seconds() == seconds
        assert len(models.requests) == number and job["lease_owner"] is None
        if number < 7:
            core.workers.index_jobs = IndexJobs(core.workers)
            await deliver(core, job["index_id"])
    assert len({request.attempt_id for request in models.requests}) == 7


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_live_original_is_observed_without_allocating_a_successor(core, ordinary_users, status):
    current, _ = ordinary_users
    value = await fact(core, current, "live-" + status)
    models = configured(core, status)
    await core.workers.index_object("claim", value.claim_id, value.revision)
    job = await job_for(core, value)
    assert job["state"] == "observing"
    await deliver(core, job["index_id"])
    assert len(models.requests) == 1 and (await job_for(core, value))["attempt_count"] == 1
    request = models.requests[0]
    result = models.result_for(request, "succeeded")
    models.results[request.attempt_id], models.receipts[request.attempt_id] = result, result.receipt
    await deliver(core, job["index_id"])
    assert (await job_for(core, value))["state"] == "succeeded" and len(models.requests) == 1


@pytest.mark.parametrize("status,error", [("cancelled", "model_cancelled"), ("failed", "model_function_not_configured")])
async def test_explicit_cancellation_and_permanent_configuration_do_not_retry(core, ordinary_users, status, error):
    current, _ = ordinary_users
    value = await fact(core, current, "permanent-" + status)
    models = configured(core, status, error)
    await core.workers.index_object("claim", value.claim_id, value.revision)
    job = await job_for(core, value)
    assert job["state"] == status and job["next_due"] is None and job["last_error"] == error
    await core.workers.index_jobs.recover()
    await core.workers.index_retry(job["index_id"])
    assert len(models.requests) == 1


@pytest.mark.parametrize("change", ["source", "revision", "profile"])
async def test_writeback_rechecks_sources_revision_and_current_profile(core, ordinary_users, change):
    current, _ = ordinary_users
    value = await fact(core, current, "changed-" + change)
    models = configured(core)

    async def changed(_request):
        if change == "source":
            await core.sources.invalidate(current, {"source_id": value.source_refs[0].source_id,
                "expected_revision": 1, "client_request_id": "withdraw-index"}, forgotten=True)
        elif change == "revision":
            await core.claims.correct(current, {"claim_id": value.claim_id, "expected_revision": value.revision,
                "value": "new current revision", "client_request_id": "correct-during-index"})
        else:
            models.profile = {**models.profile, "model_version": "synthetic-v2"}

    models.after_generate = changed
    await core.workers.index_object("claim", value.claim_id, value.revision)
    job = await job_for(core, value)
    assert job["state"] == ("revoked" if change == "source" else "obsolete") and job["next_due"] is None
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT embedding IS NULL AS missing FROM memory.claim_revisions WHERE claim_id=%s AND revision=%s",
            (value.claim_id, value.revision))).fetchone()
    assert row["missing"] and len(models.requests) == 1


async def test_recover_adopts_old_unknown_nonempty_lexical_indexes_and_coalesces_each_wake(core, ordinary_users):
    current, _ = ordinary_users
    models = configured(core, "unknown", "timeout_unknown")
    values = [await fact(core, current, "old-timeout-" + str(index)) for index in range(7)]
    embeddings = core.workers.embeddings
    profile = await embeddings.profile()
    for value in values:
        loaded = await core.workers.index_jobs._material("claim", value.claim_id, value.revision)
        with pytest.raises(AppError, match="timeout_unknown"):
            await embeddings.vector(loaded[1], loaded[3], RequestBudget(datetime.now(UTC) + timedelta(seconds=120)),
                intent=f"memory-index:claim:{value.claim_id}:{value.revision}", source_refs=loaded[2], profile_snapshot=profile)
    originals = list(models.requests)
    # No logical retry rows existed when those old requests settled unknown.
    await asyncio.gather(core.workers.index_jobs.recover(), core.workers.index_jobs.recover())
    await core.workers.index_jobs.recover()
    async with core.database.transaction(read_only=True) as conn:
        jobs = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE kind='claim' ORDER BY object_id")).fetchall()
        queued = await (await conn.execute("SELECT args->>'index_id' AS index_id,count(*) AS count FROM procrastinate_jobs "
            "WHERE task_name='memory.index_retry' AND status='todo' GROUP BY args->>'index_id'")).fetchall()
    assert len(jobs) == len(values) == len(queued) == 7 and all(row["count"] == 1 for row in queued)
    assert core.memory.jobs.task_lanes["memory.index_retry"] == ExecutionLane.MEMORY_INDEX
    for job in jobs:
        await deliver(core, job["index_id"])
    assert len(models.requests) == 7 and set(models.reads) == {request.attempt_id for request in originals}
    models.outcome, models.error = "succeeded", None
    for job in jobs:
        await deliver(core, job["index_id"])
    async with core.database.transaction(read_only=True) as conn:
        assert (await (await conn.execute("SELECT count(*) AS count FROM memory.index_jobs WHERE state='succeeded'")).fetchone())["count"] == 7
        rows = await (await conn.execute("SELECT state,request FROM memory.model_steps WHERE step_id=ANY(%s)",
            ([request.intent_id + ":" + profile[1] for request in originals],))).fetchall()
    assert all(row["state"] == "unknown" for row in rows)
    assert len(models.requests) == 14 and len({request.attempt_id for request in models.requests}) == 14


async def test_hypothesis_aliases_share_one_logical_work_and_old_claim_step(core, ordinary_users):
    current, _ = ordinary_users
    value = await fact(core, current, "one-hypothesis", hypothesis=True)
    models = configured(core, "unknown", "timeout_unknown")
    loaded = await core.workers.index_jobs._material("claim", value.claim_id, value.revision)
    profile = await core.workers.embeddings.profile()
    with pytest.raises(AppError, match="timeout_unknown"):
        await core.workers.embeddings.vector(loaded[1], loaded[3], RequestBudget(datetime.now(UTC) + timedelta(seconds=120)),
            intent=f"memory-index:claim:{value.claim_id}:{value.revision}", source_refs=loaded[2], profile_snapshot=profile)
    await asyncio.gather(core.workers.index_object("claim", value.claim_id, value.revision),
                         core.workers.index_object("hypothesis", value.claim_id, value.revision))
    await core.workers.index_jobs.recover()
    async with core.database.transaction(read_only=True) as conn:
        jobs = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE object_id=%s", (value.claim_id,))).fetchall()
        attempts = await (await conn.execute("SELECT * FROM memory.index_attempts WHERE index_id=%s", (jobs[0]["index_id"],))).fetchall()
        wakes = await (await conn.execute("SELECT count(*) AS count FROM procrastinate_jobs WHERE task_name='memory.index_retry' "
            "AND status='todo' AND args->>'index_id'=%s", (jobs[0]["index_id"],))).fetchone()
    assert len(jobs) == len(attempts) == len(models.requests) == wakes["count"] == 1
    assert jobs[0]["kind"] == "hypothesis" and attempts[0]["intent_id"] == models.requests[0].intent_id


async def test_old_wire_without_session_scope_observes_conversation_and_new_retry_uses_intent(core, ordinary_users):
    current, _ = ordinary_users
    value = await fact(core, current, "old-provider-scope")
    models = configured(core, "unknown", "timeout_unknown")
    loaded = await core.workers.index_jobs._material("claim", value.claim_id, value.revision)
    profile = await core.workers.embeddings.profile()
    intent = f"memory-index:claim:{value.claim_id}:{value.revision}"
    step_id = intent + ":" + profile[1]
    request = ModelRequest(intent, str(uuid5(NAMESPACE_URL, step_id)), "embed", ServiceClass.ELASTIC, loaded[1],
        RequestBudget(datetime.now(UTC) + timedelta(seconds=120)), (), required_capabilities=frozenset({"embed"}),
        max_output_tokens=0, native_payload={"input": [loaded[3]]}, source_refs=loaded[2], execution_timeout_seconds=30.0)
    raw = wire(request)
    raw.pop("provider_session_scope", None)
    original = request_from_wire(raw)
    assert original.provider_session_scope == "conversation"
    result = models.result_for(original, "unknown", "timeout_unknown")
    models.results[original.attempt_id], models.receipts[original.attempt_id] = result, result.receipt
    async with core.database.transaction() as conn:
        await conn.execute("INSERT INTO memory.model_steps(step_id,request,state,error_code,request_timeout_seconds) "
            "VALUES(%s,%s,'unknown','timeout_unknown',30)", (step_id, Jsonb(raw)))
    await core.workers.index_object("claim", value.claim_id, value.revision)
    job = await job_for(core, value)
    assert job["state"] == "retry_wait" and not models.requests
    models.outcome, models.error = "succeeded", None
    await deliver(core, job["index_id"])
    assert len(models.requests) == 1 and models.requests[0].provider_session_scope == "intent"
    async with core.database.transaction(read_only=True) as conn:
        frozen = await (await conn.execute("SELECT request,state FROM memory.model_steps WHERE step_id=%s", (step_id,))).fetchone()
    assert frozen["request"] == raw and frozen["state"] == "unknown"
    assert (await job_for(core, value))["state"] == "succeeded"


async def test_later_explicit_cancel_overrides_a_retryable_interrupted_unknown_without_changing_it(core, ordinary_users):
    current, _ = ordinary_users
    value = await fact(core, current, "explicit-after-cleanup")
    models = configured(core, "unknown", "consumer_interrupted")
    await core.workers.index_object("claim", value.claim_id, value.revision)
    job = await job_for(core, value)
    assert job["state"] == "retry_wait"
    original = models.requests[0]
    models.receipts[original.attempt_id] = replace(models.receipts[original.attempt_id], cancellation_reason="explicit_cancel")
    # The original result/error remains unknown; status carries the later
    # authoritative cancellation, so classifying only that old error is unsafe.
    models.outcome, models.error = "succeeded", None
    await deliver(core, job["index_id"])
    after = await job_for(core, value)
    assert after["state"] == "cancelled" and after["last_error"] == "explicit_cancel" and after["next_due"] is None
    assert after["attempt_count"] == len(models.requests) == 1
    assert models.results[original.attempt_id].receipt.status == "unknown"
    assert models.results[original.attempt_id].receipt.error_code == "consumer_interrupted"
    async with core.database.transaction(read_only=True) as conn:
        attempt = await (await conn.execute("SELECT status,error_code FROM memory.index_attempts WHERE index_id=%s", (job["index_id"],))).fetchone()
    assert attempt == {"status": "unknown", "error_code": "consumer_interrupted"}


async def test_lost_owner_cannot_select_retry_or_release_new_owner(core, ordinary_users):
    current, _ = ordinary_users
    value = await fact(core, current, "owner-changed")
    models = configured(core, "unknown", "timeout_unknown")
    await core.workers.index_object("claim", value.claim_id, value.revision)
    job = await job_for(core, value)

    async def takeover(_attempt_id):
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE memory.index_jobs SET lease_owner='replacement',lease_until=now()+interval '5 minutes',"
                "state='running' WHERE index_id=%s", (job["index_id"],))

    models.after_read = takeover
    await deliver(core, job["index_id"])
    after = await job_for(core, value)
    assert after["lease_owner"] == "replacement" and after["state"] == "running" and after["attempt_count"] == 1
    assert len(models.requests) == 1


async def test_missing_vector_scan_is_bounded_and_does_not_reset_to_the_same_prefix(core, ordinary_users):
    current, _ = ordinary_users
    configured(core)
    values = [await fact(core, current, "scan-" + str(number)) for number in range(SCAN_LIMIT + 3)]
    await core.workers.index_jobs.recover()
    async with core.database.transaction(read_only=True) as conn:
        first = await (await conn.execute("SELECT count(*) AS count FROM memory.index_jobs WHERE kind='claim'")).fetchone()
        cursor = await (await conn.execute("SELECT * FROM memory.index_scan WHERE kind='claim'")).fetchone()
    assert first["count"] == SCAN_LIMIT and cursor["after_id"]
    await core.workers.index_jobs.recover()
    async with core.database.transaction(read_only=True) as conn:
        count = await (await conn.execute("SELECT count(*) AS count FROM memory.index_jobs WHERE kind='claim'")).fetchone()
        pending = await (await conn.execute("SELECT count(*) AS count FROM procrastinate_jobs WHERE task_name='memory.index_retry' AND status='todo'")).fetchone()
    assert count["count"] == pending["count"] == len(values)


async def test_ordinary_recovery_indexes_all_semantic_kinds_through_the_same_writer(core, ordinary_users):
    current, _ = ordinary_users
    value = await fact(core, current, "all-index-kinds")
    hypothesis = await fact(core, current, "all-index-hypothesis", hypothesis=True)
    episode = await core.memory.observe_episode(current, {"event_id": "index-episode", "summary": "合成经历",
        "occurred_at": datetime.now(UTC), "origin_kind": "human", "source_refs": value.source_refs})
    reflection = await core.memory.record_reflection(current, ReflectionProposal("index-reflection", core.actor,
        (episode.episode_id,), "合成反思", "interest", datetime.now(UTC), value.source_refs, value.scope_ids))
    matter = (await core.claims.save_matter(current, {"matter_id": "index-matter", "title": "合成约定",
        "state": "planned", "expected_revision": 0, "source_refs": value.source_refs, "client_request_id": "index-matter"})).data
    models = configured(core)
    expected = {("claim", value.claim_id), ("hypothesis", hypothesis.claim_id), ("episode", episode.episode_id),
                ("reflection", reflection.proposal_id), ("matter", matter.matter_id)}
    await core.workers.index_jobs.recover()
    async with core.database.transaction(read_only=True) as conn:
        jobs = await (await conn.execute("SELECT * FROM memory.index_jobs")).fetchall()
    selected = [job for job in jobs if (job["kind"], job["object_id"]) in expected]
    assert len(selected) == len(expected)
    for job in selected:
        await deliver(core, job["index_id"])
    async with core.database.transaction(read_only=True) as conn:
        settled = await (await conn.execute("SELECT kind,object_id,state FROM memory.index_jobs WHERE index_id=ANY(%s)",
            ([job["index_id"] for job in selected],))).fetchall()
    assert {(job["kind"], job["object_id"]) for job in settled if job["state"] == "succeeded"} == expected
    assert len(models.requests) == len(expected)
