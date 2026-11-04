"""Disposable-PG cases for asynchronous, profile-isolated Memory projections."""

from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from tianshu.memory.domain_utils import lexical
from tianshu.memory.embedding import Embeddings
from tianshu.memory.indexing import put_learning_reference_on, purge_learning_references_on
from tianshu.memory.steps import ModelSteps
from tianshu.memory.types import MemoryQueryPlan
from tianshu.models.types import ModelReceipt, ModelResult
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget


class SubmittedModels:
    def __init__(self):
        self.profile = {"dimensions": 1024, "normalization": "none", "model_version": "model-a", "quota_group": "quota"}
        self.requests, self.completions, self.receipts, self.results = [], [], {}, {}

    async def embedding_profile(self):
        return dict(self.profile)

    async def status(self, attempt_id):
        return self.receipts.get(attempt_id)

    async def submit(self, request, *, completion=None):
        previous = self.receipts.get(request.attempt_id)
        if previous:
            return previous
        self.requests.append(request)
        self.completions.append(completion)
        receipt = ModelReceipt(request.attempt_id, request.intent_id, "queued", "not_started",
                               "route", 1, "provider", "model", "openai.embedding", "quota",
                               data_generation=request.data_generation)
        self.receipts[request.attempt_id] = receipt
        return receipt

    async def result(self, attempt_id, *, context):
        return self.results.get(attempt_id)

    def succeed(self, request):
        receipt = ModelReceipt(request.attempt_id, request.intent_id, "succeeded", "submitted",
                               "route", 1, "provider", "model", "openai.embedding", "quota",
                               finished_at=datetime.now(UTC), data_generation=request.data_generation)
        self.receipts[request.attempt_id] = receipt
        self.results[request.attempt_id] = ModelResult((), receipt,
            native_response={"data": [{"embedding": [1.0] + [0.0] * 1023}]})

    def fail(self, request, *, status="failed", code="upstream_http_500"):
        receipt = ModelReceipt(request.attempt_id, request.intent_id, status, "submitted",
                               "route", 1, "provider", "model", "openai.embedding", "quota", error_code=code,
                               finished_at=datetime.now(UTC), data_generation=request.data_generation)
        self.receipts[request.attempt_id] = receipt


async def fact(core, context, key):
    refs = await core.sources.record_event(context, producer="web", event_id=key, revision=1,
        occurred_at=datetime.now(UTC), parts=({"kind": "text", "text": "我喜欢咖啡"},),
        origin_kind=OriginKind.HUMAN)
    async with core.database.transaction() as conn:
        return await core.claims.put_on(conn, context, {"predicate": "interest", "value": key,
            "assertion": "reported", "source_refs": refs}, automatic=True)


def bind_models(core):
    models = SubmittedModels()
    steps = ModelSteps(core.database, models, core.sources)
    embeddings = Embeddings(core.database, models, steps)
    core.workers.embeddings, core.workers.steps = embeddings, steps
    return models, embeddings


async def find_job(core, value, profile_key):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute("SELECT * FROM memory.index_jobs WHERE kind='claim' AND object_id=%s "
            "AND revision=%s AND profile_key=%s", (value.claim_id, value.revision, profile_key))).fetchone()


async def generation(core):
    async with core.database.transaction(read_only=True) as conn:
        return await core.memory.jobs.current_generation_on(conn)


async def index_claim(core, value):
    return await core.workers.index_jobs.index_object("claim", value.claim_id, value.revision, await generation(core))


async def test_index_worker_yields_while_queued_and_completion_applies_only_once(core, ordinary_users):
    context, _ = ordinary_users
    value = await fact(core, context, "yield-and-complete")
    models, embeddings = bind_models(core)
    profile_key = (await embeddings.profile())[1]
    await index_claim(core, value)
    job = await find_job(core, value, profile_key)
    assert job["state"] == "observing" and job["lease_owner"] is None
    assert len(models.requests) == len(models.completions) == 1
    reference = {"kind": "claim", "id": value.claim_id, "revision": value.revision}
    assert (await core.retrieval.index_readiness(context, (reference,), profile_key=profile_key))[0]["state"] == "pending"
    request, completion = models.requests[0], models.completions[0]
    assert request.data_generation == job["generation"]
    assert completion.task == "memory.index_retry"
    assert completion.payload["completed_attempt_id"] == request.attempt_id
    assert completion.data_generation == completion.payload["generation"] == job["generation"]
    models.succeed(request)
    await core.workers.index_jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    await core.workers.index_jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    async with core.database.transaction(read_only=True) as conn:
        vectors = await (await conn.execute("SELECT profile_key,kind,object_id,revision FROM memory.search_vectors "
            "WHERE object_id=%s", (value.claim_id,))).fetchall()
    assert vectors == [{"profile_key": profile_key, "kind": "claim", "object_id": value.claim_id,
                        "revision": value.revision}]
    assert len(models.requests) == 1
    assert (await core.retrieval.index_readiness(context, (reference,), profile_key=profile_key))[0]["state"] == "ready"


async def test_profile_switch_keeps_old_vectors_but_never_queries_them_as_the_new_model(core, ordinary_users):
    context, _ = ordinary_users
    value = await fact(core, context, "separate-spaces")
    models, embeddings = bind_models(core)
    first_key = (await embeddings.profile())[1]
    await index_claim(core, value)
    first_job = await find_job(core, value, first_key)
    first_request = models.requests[0]
    models.succeed(first_request)
    await core.workers.index_jobs.run(first_job["index_id"], generation=first_job["generation"], completed_attempt_id=first_request.attempt_id)
    models.profile["model_version"] = "model-b"
    second_key = (await embeddings.profile())[1]
    assert first_key != second_key
    plan = MemoryQueryPlan(query="咖啡", evidence_kinds=("claim",))
    budget = RequestBudget(datetime.now(UTC) + timedelta(seconds=5))
    assert await core.retrieval.store.has_vectors(context, plan, first_key, budget)
    assert not await core.retrieval.store.has_vectors(context, plan, second_key, budget)
    await core.workers.index_jobs.recover(await generation(core))
    assert await find_job(core, value, second_key) is not None


async def test_missing_derived_vector_is_restored_from_the_original_success_receipt(core, ordinary_users):
    context, _ = ordinary_users
    value = await fact(core, context, "rebuild-from-receipt")
    models, embeddings = bind_models(core)
    profile_key = (await embeddings.profile())[1]
    await index_claim(core, value)
    job, request = await find_job(core, value, profile_key), models.requests[0]
    models.succeed(request)
    await core.workers.index_jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    async with core.database.transaction() as conn:
        await conn.execute("DELETE FROM memory.search_vectors WHERE profile_key=%s AND kind='claim' AND object_id=%s AND revision=%s",
                           (profile_key, value.claim_id, value.revision))
    reference = {"kind": "claim", "id": value.claim_id, "revision": value.revision}
    assert (await core.retrieval.index_readiness(context, (reference,), profile_key=profile_key))[0]["state"] == "pending"
    await core.workers.index_jobs.recover(await generation(core))
    assert (await find_job(core, value, profile_key))["state"] == "observing"
    await core.workers.index_jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT count(*) AS n FROM memory.search_vectors WHERE profile_key=%s "
            "AND kind='claim' AND object_id=%s AND revision=%s", (profile_key, value.claim_id, value.revision))).fetchone()
    assert row["n"] == 1 and len(models.requests) == 1


async def test_same_episode_revision_with_changed_material_keeps_original_success_receipt(core, ordinary_users):
    context, _ = ordinary_users
    occurred = datetime.now(UTC)
    refs = await core.sources.record_event(context, producer="web", event_id="episode-content-change", revision=1,
        occurred_at=occurred, parts=({"kind": "text", "text": "原始文字"},), origin_kind=OriginKind.HUMAN)
    episode = await core.memory.observe_episode(context, {"event_id": "episode-content-change", "summary": "最初摘要",
        "occurred_at": occurred, "origin_kind": "human", "source_refs": refs})
    models, embeddings = bind_models(core)
    profile_key = (await embeddings.profile())[1]
    frozen_generation = await generation(core)
    jobs = core.workers.index_jobs
    await jobs.index_object("episode", episode.episode_id, episode.revision, frozen_generation)
    first_request = models.requests[0]
    async with core.database.transaction(read_only=True) as conn:
        first_job = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE kind='episode' AND object_id=%s "
            "AND revision=%s AND profile_key=%s", (episode.episode_id, episode.revision, profile_key))).fetchone()
    models.succeed(first_request)
    await jobs.run(first_job["index_id"], generation=frozen_generation, completed_attempt_id=first_request.attempt_id)
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET summary=%s WHERE episode_id=%s", ("清理后的原文", episode.episode_id))
    await jobs.index_object("episode", episode.episode_id, episode.revision, frozen_generation)
    assert len(models.requests) == 2 and models.requests[1].attempt_id != first_request.attempt_id
    async with core.database.transaction(read_only=True) as conn:
        rows = await (await conn.execute("SELECT index_id,content_hash,state FROM memory.index_jobs WHERE kind='episode' "
            "AND object_id=%s AND revision=%s AND profile_key=%s", (episode.episode_id, episode.revision, profile_key))).fetchall()
        original = await (await conn.execute("SELECT status,model_attempt_id FROM memory.index_attempts WHERE index_id=%s",
            (first_job["index_id"],))).fetchone()
    assert len(rows) == 2 and len({row["content_hash"] for row in rows}) == 2
    assert original == {"status": "succeeded", "model_attempt_id": first_request.attempt_id}


async def test_missing_media_rebuilds_original_text_search_for_current_and_history(core, ordinary_users):
    context, _ = ordinary_users
    occurred = datetime.now(UTC)
    refs = await core.sources.record_event(context, producer="web", event_id="original-recall", revision=1,
        occurred_at=occurred, parts=({"kind": "text", "text": "原始文字可以找回"},), origin_kind=OriginKind.HUMAN)
    episode = await core.memory.observe_episode(context, {"event_id": "original-recall", "summary": "图片摘要",
        "occurred_at": occurred, "origin_kind": "human", "source_refs": refs})
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET summary='',lexical='',supplement_refs='[]',"
            "extraction_missing=ARRAY['media_unavailable'] WHERE episode_id=%s", (episode.episode_id,))
        await conn.execute("UPDATE memory.episode_revisions SET summary='',supplement_refs='[]' "
            "WHERE episode_id=%s AND revision=%s", (episode.episode_id, episode.revision))
    bind_models(core)
    await core.workers.index_jobs.index_object("episode", episode.episode_id, episode.revision, await generation(core))
    budget = lambda: RequestBudget(datetime.now(UTC) + timedelta(seconds=10))
    current = await core.retrieval.query(context, MemoryQueryPlan(query="原始文字", evidence_kinds=("episode",)), budget())
    history = await core.retrieval.query(context, MemoryQueryPlan(query="原始文字", evidence_kinds=("episode",),
        time_mode="known_at", known_at=datetime.now(UTC), coverage_mode="list"), budget())
    assert current.items[0].content == history.items[0].content == "原始文字可以找回"
    assert "media_unavailable" in current.items[0].coverage.missing
    async with core.database.transaction(read_only=True) as conn:
        projection = await (await conn.execute("SELECT source_refs,search_vector::text AS terms "
            "FROM memory.episode_original_terms WHERE episode_id=%s AND revision=%s",
            (episode.episode_id, episode.revision))).fetchone()
    assert projection["source_refs"] == wire(refs) and projection["terms"]


async def test_a_late_embedding_cannot_reactivate_a_withdrawn_source(core, ordinary_users):
    context, _ = ordinary_users
    value = await fact(core, context, "withdraw-before-index-result")
    models, embeddings = bind_models(core)
    profile_key = (await embeddings.profile())[1]
    await index_claim(core, value)
    job = await find_job(core, value, profile_key)
    request = models.requests[0]
    await core.sources.invalidate(context, {"source_id": value.source_refs[0].source_id,
        "expected_revision": 1, "client_request_id": "withdraw-before-index-result"}, forgotten=True)
    models.succeed(request)
    await core.workers.index_jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT count(*) AS n FROM memory.search_vectors WHERE object_id=%s",
                                       (value.claim_id,))).fetchone()
    assert row["n"] == 0


async def test_episode_known_at_uses_saved_revision_and_checks_current_and_historical_sources(core, ordinary_users):
    context, _ = ordinary_users
    happened = datetime.now(UTC) - timedelta(hours=1)
    first_refs = await core.sources.record_event(context, producer="web", event_id="episode-first", revision=1,
        occurred_at=happened, parts=({"kind": "text", "text": "第一版经历"},), origin_kind=OriginKind.HUMAN)
    second_refs = await core.sources.record_event(context, producer="web", event_id="episode-second", revision=1,
        occurred_at=happened, parts=({"kind": "text", "text": "修订后的经历"},), origin_kind=OriginKind.HUMAN)
    episode = {"event_id": "semantic-episode", "summary": "第一版经历", "occurred_at": happened,
               "origin_kind": "human", "source_refs": first_refs}
    initial = await core.memory.observe_episode(context, episode)
    async with core.database.transaction(read_only=True) as conn:
        historical = await (await conn.execute("SELECT recorded_at FROM memory.episode_revisions "
            "WHERE episode_id=%s AND revision=%s", (initial.episode_id, initial.revision))).fetchone()
    await core.memory.observe_episode(context, {**episode, "revision": 2, "summary": "修订后的经历",
                                                 "source_refs": second_refs})
    history_plan = MemoryQueryPlan(time_mode="known_at", known_at=historical["recorded_at"],
                                   evidence_kinds=("episode",), coverage_mode="list")
    valid_plan = MemoryQueryPlan(time_mode="valid_at", valid_at=happened + timedelta(minutes=1),
                                 evidence_kinds=("episode",), coverage_mode="list")
    budget = lambda: RequestBudget(datetime.now(UTC) + timedelta(seconds=10))
    history = await core.retrieval.query(context, history_plan, budget())
    valid = await core.retrieval.query(context, valid_plan, budget())
    assert [item.content for item in history.items] == ["第一版经历"]
    assert [item.content for item in valid.items] == ["修订后的经历"]
    assert "episode_valid_at_uses_occurrence_time" in valid.coverage.missing
    await core.sources.invalidate(context, {"source_id": first_refs[0].source_id, "expected_revision": 1,
        "client_request_id": "forget-historical-episode"}, forgotten=True)
    assert not (await core.retrieval.query(context, history_plan, budget())).items


async def test_native_learning_search_revalidates_growth_and_preserves_its_reference(core, ordinary_users):
    context, _ = ordinary_users
    refs = await core.sources.record_event(context, producer="web", event_id="learning-search", revision=1,
        occurred_at=datetime.now(UTC), parts=({"kind": "text", "text": "以后早上别叫我喝咖啡"},),
        origin_kind=OriginKind.HUMAN)
    async with core.database.transaction() as conn:
        for revision in (1, 2):
            await put_learning_reference_on(conn, context, rule_id="rule-coffee", revision=revision,
                semantic_revision=1, kind="preference", source_refs=refs, scope_ids=context.scope_ids,
                search_text="早上不要叫我喝咖啡")
    eligible = True

    async def resolve(_context, selected, *, purpose):
        assert selected[0]["rule_id"] == "rule-coffee" and purpose == "tool"
        return ({"rule_id": "rule-coffee", "revision": 3, "semantic_revision": 1,
                 "state": "active" if eligible else "retracted", "eligible": eligible, "compatible": eligible,
                 "source_refs": refs, "scope_ids": context.scope_ids, "text": "早上不要叫我喝咖啡", "conditions": {}},)

    core.retrieval.bind_behavior_resolver(resolve)
    plan = MemoryQueryPlan(query="咖啡", evidence_kinds=("learning",))
    budget = lambda: RequestBudget(datetime.now(UTC) + timedelta(seconds=10))
    page = await core.retrieval.query(context, plan, budget(), purpose="tool")
    assert page.items[0].behavior_ref == {"rule_id": "rule-coffee", "revision": 2, "semantic_revision": 1}
    eligible = False
    assert not (await core.retrieval.query(context, plan, budget(), purpose="tool")).items
    await core.sources.invalidate(context, {"source_id": refs[0].source_id, "expected_revision": 1,
        "client_request_id": "forget-learning-source"}, forgotten=True)
    async with core.database.transaction() as conn:
        await purge_learning_references_on(conn, refs, "forgotten", actor_id=context.actor_id)
        rows = await (await conn.execute("SELECT revision,search_vector::text AS words FROM memory.learning_index_refs "
            "WHERE rule_id='rule-coffee' ORDER BY revision")).fetchall()
    assert [row["revision"] for row in rows] == [1, 2]
    assert all(row["words"] == "" for row in rows)


async def test_chinese_name_outranks_a_shared_character_and_a_single_character_can_recall(core, ordinary_users):
    context, _ = ordinary_users
    exact = await fact(core, context, "智乃")
    similar = await fact(core, context, "智明")
    budget = lambda: RequestBudget(datetime.now(UTC) + timedelta(seconds=10))
    named = await core.retrieval.query(context, MemoryQueryPlan(query="智乃", evidence_kinds=("claim",)), budget())
    single = await core.retrieval.query(context, MemoryQueryPlan(query="智", evidence_kinds=("claim",)), budget())
    assert named.items[0].id == exact.claim_id
    assert {item.id for item in single.items} >= {exact.claim_id, similar.claim_id}


async def test_unknown_index_receipt_is_observed_without_allocating_a_new_attempt(core, ordinary_users):
    context, _ = ordinary_users
    value = await fact(core, context, "unknown-does-not-replay")
    models, embeddings = bind_models(core)
    profile_key = (await embeddings.profile())[1]
    await index_claim(core, value)
    job, request = await find_job(core, value, profile_key), models.requests[0]
    models.fail(request, status="unknown", code="timeout_unknown")
    await core.workers.index_jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    after = await find_job(core, value, profile_key)
    assert after["state"] == "observing" and after["attempt_count"] == 1
    models.succeed(request)
    await core.workers.index_jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    assert (await find_job(core, value, profile_key))["state"] == "succeeded"
    assert len(models.requests) == 1


async def test_recovery_statistics_backfill_uses_original_attempt_identity(core, ordinary_users):
    context, _ = ordinary_users
    value = await fact(core, context, "statistics-replay")
    models, embeddings = bind_models(core)

    class Statistics:
        def __init__(self):
            self.events, self.producers = {}, []

        async def record_safely_on(self, _conn, event):
            key = event["family"], event["event_id"]
            if key not in self.events or event["revision"] > self.events[key]["revision"]:
                self.events[key] = event
            return True

        async def mark_producer_on(self, _conn, producer, **kwargs):
            self.producers.append((producer, kwargs))

    stats = Statistics()
    core.workers.index_jobs.bind_observability(statistics=stats)
    profile_key = (await embeddings.profile())[1]
    await index_claim(core, value)
    job, request = await find_job(core, value, profile_key), models.requests[0]
    models.fail(request)
    await core.workers.index_jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    assert (await find_job(core, value, profile_key))["state"] == "retry_wait"
    assert stats.events[("recovery", "memory-index:" + request.attempt_id)]["metrics"]["recovery_eligible"] == 1
    await core.workers.index_jobs.backfill_statistics(await generation(core))
    assert len(stats.events) == 1 and stats.producers[-1][1]["complete"]


async def test_operations_index_recovery_guard_and_version_are_checked_in_enqueue_transaction(core, ordinary_users):
    context, _ = ordinary_users
    value = await fact(core, context, "guarded-index-rebuild")
    models, embeddings = bind_models(core)
    guards = []

    async def guard(conn, attempt_id, owner_token):
        guards.append((conn, attempt_id, owner_token))

    class Policy:
        async def register_on(self, _conn, **_kwargs):
            return {"lineage_id": "original-lineage"}

        async def reserve_attempt_on(self, _conn, _lineage, **_kwargs):
            return {"probe_token": None}

        async def fail_attempt_on(self, _conn, _attempt_id, **_kwargs):
            return None

        async def succeed_attempt_on(self, _conn, _attempt_id, **_kwargs):
            return None

        async def claim_lineage_due_on(self, _conn, _lineage_id, **_kwargs):
            return {"lineage_id": "original-lineage", "claim_token": "claim"}

    jobs = core.workers.index_jobs
    jobs.bind_observability(recovery_policy=Policy(), guard_on=guard)
    recovery = {"owner_token": "owner-token"}
    target = {"kind": "claim", "id": value.claim_id, "revision": value.revision}
    profile_key = (await embeddings.profile())[1]
    await index_claim(core, value)
    job, first_request = await find_job(core, value, profile_key), models.requests[0]
    models.fail(first_request)
    await jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=first_request.attempt_id)
    facts = await jobs.recovery_inspect(recovery, target)
    assert facts["eligible"] and facts["lineage_id"] == "original-lineage" and facts["dependency"] == "quota"
    candidates = await jobs.recovery_candidates()
    assert candidates[0]["target"] == target and candidates[0]["error_family"] == "memory_index_failed"
    assert candidates[0]["material_id"] == facts["material_id"] and candidates[0]["due_at"] is not None
    result = await jobs.recovery_invoke(recovery, target, facts, "ops-attempt")
    assert result["state"] == "accepted" and guards[0][1:] == ("ops-attempt", "owner-token")
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.index_jobs SET next_due=now()-interval '1 second' WHERE index_id=%s", (job["index_id"],))
    await jobs.run(job["index_id"], generation=job["generation"])
    request = models.requests[1]
    models.succeed(request)
    await jobs.run(job["index_id"], generation=job["generation"], completed_attempt_id=request.attempt_id)
    assert (await jobs.recovery_observe(recovery, target, facts, "ops-attempt"))["state"] == "verified"


async def test_withdrawn_supplement_keeps_original_episode_text_without_stale_image_summary(core, ordinary_users):
    context, _ = ordinary_users
    occurred = datetime.now(UTC) - timedelta(minutes=1)
    original = await core.sources.record_event(context, producer="web", event_id="text-part", revision=1,
        occurred_at=occurred, parts=({"kind": "text", "text": "原始文字"},), origin_kind=OriginKind.HUMAN)
    supplemental = await core.sources.record_event(context, producer="web", event_id="image-supplement", revision=1,
        occurred_at=occurred, parts=({"kind": "text", "text": "秘密花园"},), origin_kind=OriginKind.HUMAN)
    episode = await core.memory.observe_episode(context, {"event_id": "multimodal-episode", "summary": "原始文字",
        "occurred_at": occurred, "origin_kind": "human", "source_refs": original})
    summary = "原始文字。图片内容：秘密花园"
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET summary=%s,lexical=%s,supplement_refs=%s WHERE episode_id=%s",
                           (summary, lexical(summary), Jsonb(wire(supplemental)), episode.episode_id))
        await conn.execute("UPDATE memory.episode_revisions SET summary=%s,supplement_refs=%s "
                           "WHERE episode_id=%s AND revision=%s",
                           (summary, Jsonb(wire(supplemental)), episode.episode_id, episode.revision))

    class SourcesProxy:
        blocked = False
        block_after = None
        supplement_checks = 0

        def __getattr__(self, name):
            return getattr(core.sources, name)

        async def check_many_on(self, conn, trusted, groups):
            result = await core.sources.check_many_on(conn, trusted, groups)
            checked = []
            for group, valid in zip(groups, result, strict=True):
                contains_supplement = any(ref.source_id == supplemental[0].source_id for ref in group)
                if contains_supplement:
                    self.supplement_checks += 1
                denied = self.blocked or (self.block_after is not None and self.supplement_checks >= self.block_after)
                checked.append(valid and not (contains_supplement and denied))
            return tuple(checked)

    proxy = SourcesProxy()
    core.memory.sources = proxy
    plan = MemoryQueryPlan(evidence_kinds=("episode",), coverage_mode="list")
    budget = lambda: RequestBudget(datetime.now(UTC) + timedelta(seconds=10))
    before = await core.retrieval.query(context, plan, budget())
    assert before.items[0].content == summary
    assert supplemental[0] in before.items[0].source_refs
    proxy.blocked = True
    after = await core.retrieval.query(context, plan, budget())
    assert after.items[0].content == "原始文字" and "秘密花园" not in after.items[0].content
    assert "media_unavailable" in after.items[0].coverage.missing
    assert supplemental[0] not in after.items[0].source_refs
    proxy.blocked, proxy.block_after, proxy.supplement_checks = False, 2, 0
    concurrent = await core.retrieval.query(context, plan, budget())
    assert concurrent.items[0].content == "原始文字" and "media_unavailable" in concurrent.items[0].coverage.missing
