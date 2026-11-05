"""RB-15: one scene/day, independent submissions and saved-body recovery."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest

from tianshu.identity.service import IdentityService
from tianshu.life.diary_automation import diary_experience
from tianshu.life.diary_materials import episode_observation, episode_versions
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.models.ledger import submission_digest
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind
from test_diary_automation import diary_state, fixture


class Broker:
    """A receipt ledger: submit returns before a result exists."""

    def __init__(self, profile):
        self._profile = profile
        self.requests = {}
        self.receipts = {}
        self.results = {}
        self.completions = {}
        self.selected_route = "fixture-route"
        self.quota_groups = {"fixture-route":"fixture-quota", "backup-route":"backup-quota"}

    async def generation_profile(self, request):
        route = request.route_id or self.selected_route
        return {**await self._profile(request), "route_id":route,
            "route_revision":request.route_revision or 1,"quota_group":self.quota_groups[route]}

    async def submit(self, request, *, completion=None):
        if request.attempt_id in self.requests:
            assert self.requests[request.attempt_id] == request
            return self.receipts[request.attempt_id]
        self.requests[request.attempt_id] = request
        self.completions[request.attempt_id] = completion
        route = request.route_id or self.selected_route
        receipt = ModelReceipt(request.attempt_id, request.intent_id, "queued", "not_started",
            route, request.route_revision or 1, "provider", "model", "openai.chat", self.quota_groups[route],
            queued_at=datetime.now(UTC),request_digest=submission_digest(request),data_generation=request.data_generation)
        self.receipts[request.attempt_id] = receipt
        return receipt

    async def status(self, attempt_id, *, phase="observe"):
        assert phase in {"pre_submit", "observe"}
        return self.receipts.get(attempt_id)

    async def result(self, attempt_id, *, context):
        result = self.results.get(attempt_id)
        return replace(result,receipt=self.receipts[attempt_id]) if result is not None else None

    async def reusable_result(self, attempt_id, *, context, expected_request_digest=None, expected_data_generation=None):
        from tianshu.models.results import validate_reusable_receipt
        current = await self.status(attempt_id)
        if current is None:
            raise AppError("attempt_not_found", status=404)
        validate_reusable_receipt(current,attempt_id,expected_request_digest=expected_request_digest,
            expected_data_generation=expected_data_generation)
        result = await self.result(attempt_id,context=context)
        if result is None:
            raise AppError("model_result_unavailable",status=409)
        return result

    async def cancel(self, attempt_id, *, reason="explicit_cancel"):
        if attempt_id in self.receipts:
            self.receipts[attempt_id] = replace(self.receipts[attempt_id], status="cancelled",
                cancellation_reason=reason)
        return self.receipts.get(attempt_id)

    def complete(self, attempt_id, content="今天做了实际记录。"):
        receipt = replace(self.receipts[attempt_id], status="succeeded", dispatch="submitted",finished_at=datetime.now(UTC))
        self.receipts[attempt_id] = receipt
        self.results[attempt_id] = ModelResult((ModelPart("text", text=content),), receipt,
            completion=ModelCompletion("complete", ("stop",)))


async def rb_fixture(database):
    value = await fixture(database)
    # Use the real shared scene contract while keeping this fixture's isolated
    # authority for source and epoch checks.
    value.identity.scene_id = IdentityService.scene_id
    async with database.transaction(read_only=True) as connection:
        column = await (await connection.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='life' "
            "AND table_name='diaries' AND column_name='scene_key'")).fetchone()
        runtime = await (await connection.execute("SELECT to_regclass('runtime.recovery_lineages') AS name")).fetchone()
    if not runtime["name"]:
        await migrate(database, (Migration("rb_runtime_experience_test",
            files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),))
    if not column:
        await migrate(database, (Migration("rb_experience_test",
            files("tianshu.life").joinpath("rb_experience.sql").read_text()),))
    from tianshu.runtime.jobs import JobQueue
    value.jobs.current_generation_on = JobQueue.current_generation_on
    value.jobs.assert_generation_on = JobQueue.assert_generation_on
    async def current_generation():
        async with database.transaction(read_only=True) as connection:
            return await value.jobs.current_generation_on(connection)
    value.jobs.current_generation = current_generation
    broker = Broker(value.models.generation_profile)
    value.diaries.models = broker
    value.diaries.generations.models = broker
    value.models = broker
    from tianshu.runtime.recovery_policy import RecoveryPolicy
    value.diaries.bind_observability(recovery_policy=RecoveryPolicy(),statistics=None)
    return value


async def collect_current(database, value):
    diary = await diary_state(database)
    await value.diaries.collect(diary["id"],diary["generation_id"],None,
        data_generation=await value.jobs.current_generation())
    return await diary_state(database)


async def begin(value, database, day="2026-11-02"):
    key = value.identity.scene_id(value.context)
    await value.diaries.day_closed(value.context, day)
    await value.diaries.review_day(value.context.actor_id, key, day, 1,await value.jobs.current_generation())
    return await collect_current(database, value)


@pytest.mark.asyncio
async def test_old_generation_cannot_collect_after_installation_reset(database):
    from uuid import uuid4

    value = await rb_fixture(database)
    await value.diaries.day_closed(value.context,"2026-11-02")
    original = await value.jobs.current_generation()
    await value.diaries.review_day("actor",value.identity.scene_id(value.context),"2026-11-02",1,original)
    diary = await diary_state(database)
    assert value.jobs.items[-1].data_generation == original
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton=true",(str(uuid4()),))
    with pytest.raises(AppError,match="job_generation_expired"):
        await value.diaries.collect(diary["id"],diary["generation_id"],None,data_generation=original)
    current = await diary_state(database)
    assert current["state"] == "collecting" and current["current_revision"] == 0


@pytest.mark.asyncio
async def test_episode_notification_cannot_rebind_to_new_installation(database):
    from uuid import uuid4

    value = await rb_fixture(database)
    await value.diaries.day_closed(value.context,"2026-11-02")
    original = await value.jobs.current_generation()
    async with database.transaction() as connection:
        before = await (await connection.execute("SELECT revision FROM life.diary_days")).fetchone()
        await connection.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton=true",(str(uuid4()),))
    with pytest.raises(AppError,match="job_generation_expired"):
        await value.diaries.consume_episode_change(wire(value.context),value.episode.episode_id,
            value.episode.revision,generation=original)
    async with database.transaction(read_only=True) as connection:
        after = await (await connection.execute("SELECT revision FROM life.diary_days")).fetchone()
    assert after == before


@pytest.mark.asyncio
async def test_scope_change_preserves_one_day_and_reconciles_original_body(database):
    value = await rb_fixture(database)
    diary = await begin(value, database)
    await value.diaries.generate(diary["id"], diary["generation_id"])
    assert len(value.models.requests) == 1
    assert value.models.completions[diary["generation_id"]].task == "life.diary_generate"
    assert value.models.requests[diary["generation_id"]].data_generation == await value.jobs.current_generation()
    assert (await diary_state(database))["current_revision"] == 0
    await value.diaries.generate(diary["id"], diary["generation_id"])
    assert len(value.models.requests) == 1
    value.models.complete(diary["generation_id"])
    await value.diaries.generate(diary["id"], diary["generation_id"])
    saved = await diary_state(database)
    async with database.transaction(read_only=True) as connection:
        body_before = await (await connection.execute(
            "SELECT * FROM life.diary_revisions WHERE diary_id=%s", (diary["id"],))).fetchone()

    wider = replace(value.context, scope_ids=("actor-scope", "new-scope"),
        audience=("actor-scope", "new-scope"))
    assert value.identity.scene_id(wider) == value.identity.scene_id(value.context)
    await value.diaries.day_closed(wider, "2026-11-02")
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diary_days SET last_error='old_error',blocked_revision=revision")
    outcome = await value.diaries.review_day("actor", value.identity.scene_id(wider), "2026-11-02", 2,await value.jobs.current_generation())
    assert outcome["state"] == "reviewed"
    async with database.transaction(read_only=True) as connection:
        days = await (await connection.execute("SELECT * FROM life.diary_days")).fetchall()
        diaries = await (await connection.execute("SELECT * FROM life.diaries")).fetchall()
        body_after = await (await connection.execute(
            "SELECT * FROM life.diary_revisions WHERE diary_id=%s", (diary["id"],))).fetchone()
    assert len(days) == len(diaries) == 1
    assert days[0]["reviewed_revision"] == days[0]["revision"] and days[0]["last_error"] is None
    assert diaries[0]["id"] == saved["id"] and body_after == body_before
    assert len(value.models.requests) == 1


@pytest.mark.asyncio
async def test_manual_same_material_retains_the_original_attempt_and_saved_body(database):
    value = await rb_fixture(database)
    diary = await begin(value, database)
    await value.diaries.generate(diary["id"], diary["generation_id"])
    value.models.complete(diary["generation_id"])
    await value.diaries.generate(diary["id"], diary["generation_id"])
    saved = await diary_state(database)
    await value.diaries.request(value.context, {"client_request_id":"same-material",
        "day":"2026-11-02", "expected_revision":saved["revision"]})
    collected = await collect_current(database, value)
    assert collected["generation_id"] == diary["generation_id"]
    assert collected["current_revision"] == 1 and collected["state"] == "draft"
    assert len(value.models.requests) == 1


@pytest.mark.asyncio
async def test_group_member_change_reuses_the_saved_day_and_automatic_binding(database):
    value = await rb_fixture(database)
    first_context = replace(value.context, person_id="member-a",
        conversation_id="group-1", conversation_kind="group")
    second_context = replace(first_context, person_id="member-b", principal_id="another-author")
    value.context = first_context
    assert value.identity.scene_id(first_context) == value.identity.scene_id(second_context)
    diary = await begin(value, database)
    await value.diaries.generate(diary["id"], diary["generation_id"])
    value.models.complete(diary["generation_id"])
    await value.diaries.generate(diary["id"], diary["generation_id"])
    saved = await diary_state(database)

    command = await value.diaries.request(second_context, {"client_request_id":"other-group-member",
        "day":"2026-11-02", "expected_revision":saved["revision"]})
    assert command.data["id"] == saved["id"]
    await collect_current(database, value)
    await value.diaries.day_closed(second_context, "2026-11-02")
    async with database.transaction(read_only=True) as connection:
        diaries = await (await connection.execute("SELECT * FROM life.diaries")).fetchall()
        days = await (await connection.execute("SELECT * FROM life.diary_days")).fetchall()
    assert len(diaries) == len(days) == 1
    assert diaries[0]["id"] == saved["id"] and diaries[0]["current_revision"] == 1
    assert days[0]["generation_id"] is None and len(value.models.requests) == 1


@pytest.mark.asyncio
async def test_same_actor_day_different_scenes_do_not_choose_or_overwrite_each_other(database):
    value = await rb_fixture(database)
    first = await begin(value, database)
    other = replace(value.context, conversation_id="other-scene", conversation_kind="group")
    assert value.identity.scene_id(other) != value.identity.scene_id(value.context)
    await value.diaries.day_closed(other, "2026-11-02")
    await value.diaries.review_day("actor", value.identity.scene_id(other), "2026-11-02", 1,await value.jobs.current_generation())
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT * FROM life.diaries ORDER BY scene_key")).fetchall()
    assert len(rows) == 2 and len({row["id"] for row in rows}) == 2
    assert first["id"] in {row["id"] for row in rows}


@pytest.mark.asyncio
async def test_unfinished_day_does_not_enter_next_day_request(database):
    value = await rb_fixture(database)
    first = await begin(value, database)
    await value.diaries.generate(first["id"], first["generation_id"])
    next_episode = replace(value.episode, episode_id="next", summary="第二天的实际材料",
        occurred_at=value.episode.occurred_at + timedelta(days=1))
    value.memory.episodes += (next_episode,)
    command = await value.diaries.request(value.context, {"client_request_id":"next-day",
        "day":"2026-11-03", "expected_revision":0})
    await value.diaries.collect(command.data["id"], command.attempt_id, None)
    await value.diaries.generate(command.data["id"], command.attempt_id)
    assert len(value.models.requests) == 2
    second = value.models.requests[command.attempt_id]
    payload = str(wire(second.messages))
    assert "第二天的实际材料" in payload and value.episode.summary not in payload
    assert second.provider_session_scope == "intent"
    assert await value.models.status(first["generation_id"]) is not None


@pytest.mark.asyncio
async def test_persisted_notification_checks_the_original_episode_and_is_idempotent(database):
    value = await rb_fixture(database)
    await value.diaries.day_closed(value.context, "2026-11-02")
    payload = wire(value.context)
    generation = await value.jobs.current_generation()
    first = await value.diaries.consume_episode_change(payload, value.episode.episode_id, 1, "e:1",generation=generation)
    await value.diaries.consume_episode_change(payload, value.episode.episode_id, 1, "e:1",generation=generation)
    async with database.transaction(read_only=True) as connection:
        day = await (await connection.execute("SELECT * FROM life.diary_days")).fetchone()
    assert first["state"] == "observed" and day["material_revisions"] == {value.episode.episode_id: 1}
    assert day["revision"] == 2
    stale = await value.diaries.consume_episode_change(payload, value.episode.episode_id, 2, "e:2",generation=generation)
    assert stale["state"] == "unavailable_or_revised"


@pytest.mark.asyncio
async def test_episode_revalidation_batches_without_omitting_the_day():
    from types import SimpleNamespace

    calls = []
    class Memory:
        async def episodes_by_refs(self, context, refs, *, connection):
            calls.append(refs)
            return SimpleNamespace(items=[SimpleNamespace(episode_id=key, revision=revision) for key, revision in refs])
    versions = tuple((f"episode-{index}", 1) for index in range(300))
    actual = await episode_versions(Memory(), None, versions, connection=object())
    assert actual == set(versions) and [len(batch) for batch in calls] == [128, 128, 44]


@pytest.mark.asyncio
async def test_day_over_128_experiences_is_collected_and_revalidated(database):
    value = await rb_fixture(database)
    value.memory.episodes = tuple(replace(value.episode, episode_id=f"event-{index}",
        summary=f"想象中的时段 {index}") for index in range(130))
    diary = await begin(value, database)
    assert len(diary["generation_recipe"]["episodes"]) == 130
    await value.diaries.generate(diary["id"], diary["generation_id"])
    value.models.complete(diary["generation_id"])
    await value.diaries.generate(diary["id"], diary["generation_id"])
    assert (await diary_state(database))["current_revision"] == 1
    request = value.models.requests[diary["generation_id"]]
    text = str(wire(request.messages))
    assert "imagined_activity" in text and "source_read" not in text


@pytest.mark.asyncio
async def test_explicit_cancel_does_not_save_a_late_diary_result(database):
    value = await rb_fixture(database)
    diary = await begin(value, database)
    await value.diaries.generate(diary["id"], diary["generation_id"])
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diaries SET state='cancelling',generation_cancel_requested=true WHERE id=%s", (diary["id"],))
    await value.diaries.control_generation(diary["id"], diary["generation_id"], value.context, "cancel")
    value.models.complete(diary["generation_id"], "迟到的正文")
    await value.diaries.generate(diary["id"], diary["generation_id"])
    assert (await diary_state(database))["current_revision"] == 0
    assert (await value.models.status(diary["generation_id"])).cancellation_reason == "explicit_cancel"


@pytest.mark.asyncio
async def test_unknown_attempt_is_observed_then_definitive_failure_retries_same_lineage(database):
    from datetime import UTC, datetime, timedelta

    value = await rb_fixture(database)
    diary = await begin(value, database)
    attempt = diary["generation_id"]
    await value.diaries.generate(diary["id"], attempt)
    value.models.receipts[attempt] = replace(value.models.receipts[attempt],status="unknown",dispatch="unknown",
        error_code="transport_unknown",finished_at=datetime.now(UTC))
    await value.diaries.generate(diary["id"], attempt)
    await value.diaries.generate(diary["id"], attempt)
    async with database.transaction(read_only=True) as connection:
        first = await (await connection.execute("SELECT * FROM runtime.recovery_attempts WHERE attempt_id=%s",(attempt,))).fetchone()
        lineage = await (await connection.execute("SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s",(first["lineage_id"],))).fetchone()
    assert first["status"] == "unknown" and lineage["attempts"] == 1
    assert len(value.models.requests) == 1 and (await diary_state(database))["generation_id"] == attempt

    value.models.receipts[attempt] = replace(value.models.receipts[attempt],status="failed",dispatch="submitted",
        error_code="upstream_http_503")
    await value.diaries.generate(diary["id"], attempt)
    value.models.selected_route = "backup-route"
    past = datetime.now(UTC)-timedelta(seconds=1)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s WHERE lineage_id=%s",(past,lineage["lineage_id"]))
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s",(past,))
        await connection.execute("UPDATE life.diaries SET generation_retry_due_at=%s WHERE id=%s",(past,diary["id"]))
    await value.diaries.generate(diary["id"], attempt)
    second = await diary_state(database)
    assert second["generation_id"] != attempt
    await value.diaries.generate(diary["id"], second["generation_id"])
    assert value.models.requests[second["generation_id"]].data_generation == value.models.requests[attempt].data_generation
    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute("SELECT lineage_id,status,dependency FROM runtime.recovery_attempts ORDER BY started_at")).fetchall()
        current = await (await connection.execute("SELECT attempts,dependency FROM runtime.recovery_lineages WHERE lineage_id=%s",(lineage["lineage_id"],))).fetchone()
    assert len(attempts) == 2 and {item["lineage_id"] for item in attempts} == {lineage["lineage_id"]}
    assert current["attempts"] == 2 and current["dependency"] == "models:quota:backup-quota"
    assert {item["dependency"] for item in attempts} == {"models:quota:fixture-quota", "models:quota:backup-quota"}


@pytest.mark.asyncio
async def test_author_edit_and_new_request_do_not_reset_exhausted_generation_budget(database):
    from tianshu.runtime.errors import AppError

    value = await rb_fixture(database)
    diary = await begin(value, database)
    attempt = diary["generation_id"]
    await value.diaries.generate(diary["id"], attempt)
    value.models.receipts[attempt] = replace(value.models.receipts[attempt],status="failed",dispatch="submitted",
        error_code="upstream_http_503")
    await value.diaries.generate(diary["id"], attempt)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_lineages SET attempts=max_attempts,due_at=NULL")
        await connection.execute("UPDATE life.diary_days SET generation_id=NULL,generation_revision=NULL,blocked_revision=revision")
        await connection.execute("UPDATE life.diaries SET state='failed',generation_retry_due_at=NULL")
    failed = await diary_state(database)
    edited = await value.diaries.revise(value.context,{"id":diary["id"],"expected_revision":failed["revision"],
        "client_request_id":"author-edit-after-failure","content":"作者手写正文"})
    request = await value.diaries.request(value.context,{"day":"2026-11-02","expected_revision":edited.revision,
        "client_request_id":"new-request-after-failure"})
    await value.diaries.collect(request.data["id"],request.attempt_id,None)
    with pytest.raises(AppError,match="recovery_budget_exhausted"):
        await value.diaries.generate(request.data["id"],request.attempt_id)
    async with database.transaction(read_only=True) as connection:
        lineages = await (await connection.execute("SELECT attempts,max_attempts FROM runtime.recovery_lineages")).fetchall()
    assert len(value.models.requests) == 1 and len(lineages) == 1
    assert lineages[0]["attempts"] == lineages[0]["max_attempts"] == 3


@pytest.mark.asyncio
async def test_saved_diary_statistics_are_replayed_from_immutable_versions(database):
    class Statistics:
        available = False
        events = {}
        marks = []
        async def record_safely_on(self, connection, event):
            if not self.available:
                return False
            key = (event["family"],event["event_id"])
            old = self.events.get(key)
            if old is None or old["revision"] < event["revision"]:
                self.events[key] = event
            return True
        async def mark_producer_on(self, connection, producer, **fields):
            self.marks.append((producer,fields["complete"]))

    value = await rb_fixture(database)
    stats = Statistics()
    value.diaries.statistics = stats
    diary = await begin(value, database)
    await value.diaries.generate(diary["id"], diary["generation_id"])
    value.models.complete(diary["generation_id"])
    await value.diaries.generate(diary["id"], diary["generation_id"])
    saved = await diary_state(database)
    before = await value.diaries.backfill_statistics(data_generation=await value.jobs.current_generation())
    assert saved["current_revision"] == 1 and before["pending"] == 1 and not before["complete"]
    stats.available = True
    after = await value.diaries.backfill_statistics(data_generation=await value.jobs.current_generation())
    assert after["complete"] and after["processed"] == 1
    event = next(iter(stats.events.values()))
    assert event["event_id"] == "life.diary:"+diary["id"] and event["metrics"] == {"business_success":1}
    await value.diaries.revise(value.context,{"id":diary["id"],"expected_revision":saved["revision"],
        "client_request_id":"stats-edit","content":"修改后的正文"})
    assert len(stats.events) == 1 and next(iter(stats.events.values()))["revision"] == 2
    from tianshu.life.generation_control import GenerationControl
    from tianshu.life.writing import WritingService
    writing = WritingService(value.life,value.models,value.diaries.assembler)
    writing.bind_observability(recovery_policy=value.diaries.recovery.policy,statistics=stats)
    coverage = await GenerationControl(value.life,writing,value.diaries).backfill_statistics(await value.jobs.current_generation())
    assert coverage["complete"] and stats.marks[-1] == ("life.experience", True)


def test_semantic_types_do_not_turn_discovery_or_uncertain_images_into_reading():
    from tianshu.memory.types import Episode
    from tianshu.types import Coverage, SourceRef
    from datetime import UTC, datetime

    episode = Episode("id", "event", 1, "actor", OriginKind.TOOL_RESULT,
        datetime(2026,11,2,tzinfo=UTC), "生成了一张图片", (SourceRef("source",1),),
        ("scope",), {"capability_id":"images.generate", "execution":"unknown", "description":"尚未证实的画面"}, Coverage(True), 1, "e:1")
    image = episode_observation(wire(episode))
    assert image["semantic_kind"] == "action_outcome_unknown"
    assert image["summary"] != episode.summary
    assert "description" not in image["actual_result"]
    assert "image_contents_not_described" in image["diary_coverage"]["missing"]
    discovery = episode_observation(wire(replace(episode, actual_result={
        "capability_id":"content.discover", "execution":"succeeded", "candidates":[{}, {}]})))
    assert discovery["semantic_kind"] == "discovery" and not discovery["actual_result"]["discovery"]["body_read"]
    imagined = episode_observation(wire(replace(episode, origin_kind=OriginKind.ACTOR,
        actual_result={"imagined":True,"representation":"fictional_world"})))
    assert imagined["semantic_kind"] == "imagined_activity"
    assert not diary_experience(replace(episode, actual_result={"technical_backfill":True}))
    assert not diary_experience(replace(episode, actual_result={"derived_kind":"reflection"}))


@pytest.mark.asyncio
async def test_author_review_is_versioned_and_new_revision_clears_it(database):
    value = await rb_fixture(database)
    diary = await begin(value, database)
    await value.diaries.generate(diary["id"], diary["generation_id"])
    value.models.complete(diary["generation_id"])
    await value.diaries.generate(diary["id"], diary["generation_id"])
    saved = await diary_state(database)
    reviewed = await value.diaries.review(value.context, {"id":saved["id"],
        "expected_revision":saved["revision"],"client_request_id":"review",
        "state":"approved","note":"看过了"})
    assert reviewed.data["content_revision"] == 1
    await value.diaries.revise(value.context, {"id":saved["id"],"expected_revision":reviewed.revision,
        "client_request_id":"edit","content":"修改后的正文"})
    current = await diary_state(database)
    assert current["current_revision"] == 2
    assert current["review_state"] is None and current["review_content_revision"] is None
