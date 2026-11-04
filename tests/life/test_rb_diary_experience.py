"""RB-15: one scene/day, independent submissions and saved-body recovery."""

from dataclasses import replace
from datetime import timedelta
from importlib.resources import files

import pytest

from tianshu.identity.service import IdentityService
from tianshu.life.diary_automation import diary_experience
from tianshu.life.diary_materials import episode_observation, episode_versions
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind
from test_diary_automation import collect_current, diary_state, fixture


class Broker:
    """A receipt ledger: submit returns before a result exists."""

    def __init__(self, profile):
        self.generation_profile = profile
        self.requests = {}
        self.receipts = {}
        self.results = {}
        self.completions = {}

    async def submit(self, request, *, completion=None):
        if request.attempt_id in self.requests:
            assert self.requests[request.attempt_id] == request
            return self.receipts[request.attempt_id]
        self.requests[request.attempt_id] = request
        self.completions[request.attempt_id] = completion
        receipt = ModelReceipt(request.attempt_id, request.intent_id, "queued", "not_started",
            "route", 1, "provider", "model", "openai.chat", "quota")
        self.receipts[request.attempt_id] = receipt
        return receipt

    async def status(self, attempt_id):
        return self.receipts.get(attempt_id)

    async def result(self, attempt_id, *, context):
        return self.results.get(attempt_id)

    async def cancel(self, attempt_id, *, reason="explicit_cancel"):
        if attempt_id in self.receipts:
            self.receipts[attempt_id] = replace(self.receipts[attempt_id], status="cancelled",
                cancellation_reason=reason)
        return self.receipts.get(attempt_id)

    def complete(self, attempt_id, content="今天做了实际记录。"):
        receipt = replace(self.receipts[attempt_id], status="succeeded", dispatch="submitted")
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
    if not column:
        await migrate(database, (Migration("rb_experience_test",
            files("tianshu.life").joinpath("rb_experience.sql").read_text()),))
    broker = Broker(value.models.generation_profile)
    value.diaries.models = broker
    value.diaries.generations.models = broker
    value.models = broker
    return value


async def begin(value, database, day="2026-11-02"):
    key = value.identity.scene_id(value.context)
    await value.diaries.day_closed(value.context, day)
    await value.diaries.review_day(value.context.actor_id, key, day, 1)
    return await collect_current(database, value)


@pytest.mark.asyncio
async def test_scope_change_preserves_one_day_and_reconciles_original_body(database):
    value = await rb_fixture(database)
    diary = await begin(value, database)
    await value.diaries.generate(diary["id"], diary["generation_id"])
    assert len(value.models.requests) == 1
    assert value.models.completions[diary["generation_id"]].task == "life.diary_generate"
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
    outcome = await value.diaries.review_day("actor", value.identity.scene_id(wider), "2026-11-02", 2)
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
    second_context = replace(first_context, person_id="member-b")
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
    await value.diaries.review_day("actor", value.identity.scene_id(other), "2026-11-02", 1)
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
    first = await value.diaries.consume_episode_change(payload, value.episode.episode_id, 1, "e:1")
    await value.diaries.consume_episode_change(payload, value.episode.episode_id, 1, "e:1")
    async with database.transaction(read_only=True) as connection:
        day = await (await connection.execute("SELECT * FROM life.diary_days")).fetchone()
    assert first["state"] == "observed" and day["material_revisions"] == {value.episode.episode_id: 1}
    assert day["revision"] == 2
    stale = await value.diaries.consume_episode_change(payload, value.episode.episode_id, 2, "e:2")
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
