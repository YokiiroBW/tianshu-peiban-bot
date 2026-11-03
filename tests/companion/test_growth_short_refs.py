"""NR-08 Growth aliases, strict conflicts and one durable output correction."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from tianshu.companion.growth import GrowthService
from tianshu.companion import growth_materials
from tianshu.companion.growth_materials import material
from tianshu.companion.growth_output_repair import corrected_request
from tianshu.companion.reflection_output import (
    INPUT_TOKEN_BUDGET, MATERIAL_PREFIX, OUTPUT_PROTOCOL, OUTPUT_REPAIR_RESERVE,
    episode_references, frozen_episodes, frozen_material, output_schema, prepare_reflections, validate_batch_request,
)
from tianshu.conversation.codec import signature
from tianshu.models.types import ModelCompletion, ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext
from test_growth_output import Memory, Models, note, prepare, retry_state, state
from test_growth_retry import due, tail


def original_request(*, legacy=False):
    episode = replace(Memory().episode, episode_id="episode_12345678-1234-1234-1234-abcdef129fd3")
    context = TrustedContext("author", "actor", "person", "conversation", ("private",), ("private",), 1, OriginKind.ACTOR)
    payload = material((episode,))
    if legacy:
        payload.pop("model_protocol")
        payload.pop("episode_refs")
        payload["episodes"][0]["episode_id"] = episode.episode_id
        payload["output_schema"] = output_schema((episode.episode_id,))
    return ModelRequest("original", "original-attempt", "reflect", ServiceClass.ELASTIC, context,
        RequestBudget(datetime.now(UTC)+timedelta(minutes=3)),
        (ModelMessage("user", (ModelPart("text", text=MATERIAL_PREFIX+json.dumps(payload)),)),),
        source_refs=episode.source_refs, execution_timeout_seconds=180), episode


def output(request, notes):
    return ModelResult((ModelPart("text", text=json.dumps({"notes": notes})),),
        ModelReceipt(request.attempt_id, request.intent_id, "succeeded", "submitted", "route", 1,
                     "provider", "model", "openai.chat", "quota"), completion=ModelCompletion("complete", ("stop",)))


def affect():
    return {"appraisal": "对原经历的感受", "valence": 0.2, "arousal": 0.2,
        "stress": 0, "social_need": 0.1, "confidence": 0.6, "goal_relevance": 0,
        "attribution": "situation", "control": 0.5}


def test_short_references_round_trip_exact_material_and_old_uuid_protocol_remains_strict():
    for legacy in (False, True):
        request, episode = original_request(legacy=legacy)
        frozen = frozen_episodes(request)
        assert set(frozen) == {episode.episode_id}
        assert frozen[episode.episode_id].source_refs == episode.source_refs
        assert frozen[episode.episode_id].revision == episode.revision
        refs = episode_references(request, frozen)
        assert refs == ({episode.episode_id: episode.episode_id} if legacy else {"e1": episode.episode_id})
        value = {**note(), "episode_ids": list(refs)}
        prepared = prepare_reflections(output(request, [value]), request.context, frozen, request.source_refs, request=request)
        assert prepared[0].support_ids == (episode.episode_id,)
        value["episode_ids"] = [episode.episode_id[:-1] + "0"]
        with pytest.raises(AppError, match="reflection_support_invalid") as caught:
            prepare_reflections(output(request, [value]), request.context, frozen, request.source_refs, request=request)
        assert caught.value.details["path"] == ["notes", 0, "episode_ids", 0]
        assert caught.value.details["allowed_refs"] == list(refs)


@pytest.mark.parametrize("field", ["content", "title", "conditions", "counterexamples", "affect"])
def test_only_identical_full_semantics_are_deduplicated(field):
    request, _ = original_request()
    first = {**note(), "episode_ids": ["e1"], "affect": affect()}
    second = deepcopy(first)
    prepared = prepare_reflections(output(request, [first, second]), request.context,
        frozen_episodes(request), request.source_refs, request=request)
    assert len(prepared) == 1 and prepared[0].affect is not None
    second[field] = {"content": "不同理解", "title": "不同标题",
        "conditions": {"when": ["不同条件"], "unless": []}, "counterexamples": ["实际反例"],
        "affect": None}[field]
    with pytest.raises(AppError, match="reflection_duplicate_proposal") as caught:
        prepare_reflections(output(request, [first, second]), request.context,
            frozen_episodes(request), request.source_refs, request=request)
    assert caught.value.details["conflict_fields"] == [field]
    assert caught.value.details["conflict_with"] == ["notes", 0]


def test_correction_never_trims_evidence_to_fit_its_budget():
    request, _ = original_request()
    result = output(request, [{**note(), "episode_ids": ["wrong"]}])
    class Counter:
        def measure(self, *_args):
            return INPUT_TOKEN_BUDGET+1, None
    saved = deepcopy(request)
    with pytest.raises(AppError, match="reflection_material_budget_exceeded"):
        corrected_request(request, result, {"error_code": "reflection_support_invalid"}, Counter())
    assert request == saved and request.execution_timeout_seconds == 180


def test_short_mapping_cannot_be_borrowed_from_another_batch():
    request, episode = original_request()
    batch = {"reflection_output_protocol": OUTPUT_PROTOCOL, "episode_refs": {"e1": "another-episode"},
        "inputs": [(episode.episode_id, episode.revision)]}
    with pytest.raises(AppError, match="reflection_material_contract_invalid"):
        validate_batch_request(batch, request, frozen_episodes(request))


@pytest.mark.asyncio
async def test_reference_error_reserves_one_correction_and_restart_reuses_exact_alias_mapping(database):
    models = Models({"notes": [{**note(), "episode_ids": ["e9"]}]}, {"notes": [note()]})
    growth, memory, jobs, context, notifications = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    rejected = await growth.organize("actor", scope, 1)
    original = models.calls[0]
    assert original.estimated_input_tokens+OUTPUT_REPAIR_RESERVE <= INPUT_TOKEN_BUDGET
    original_result = models.results[original.attempt_id]
    saved = await growth.generations.request(original.attempt_id, context)
    row = await retry_state(database)
    assert rejected["state"] == "rejected" and rejected["recovery"] == "retry_waiting"
    assert row["batch"]["output_repair_count"] == 1 and row["batch"]["episode_refs"] == {"e1": "episode"}
    assert row["batch"]["output_repair"]["diagnostic"]["allowed_refs"] == ["e1"]
    assert memory.proposals == notifications == [] and (await state(database))["cursor"] is None
    restarted = GrowthService(growth.companion, jobs, models, growth.assembler,
        on_projection_changed=growth.on_projection_changed)
    await due(database)
    await restarted.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 2 and len(memory.proposals) == len(notifications) == 1
    repaired = models.calls[1]
    assert repaired.attempt_id != original.attempt_id and repaired.execution_timeout_seconds == 180
    assert repaired.source_refs == original.source_refs
    assert frozen_material(repaired) == frozen_material(original)
    assert "output_repair" in repaired.messages[-1].parts[0].text
    assert await restarted.generations.request(original.attempt_id, context) == saved
    assert models.results[original.attempt_id] is original_result
    assert (await retry_state(database))["state"] == "completed"


@pytest.mark.asyncio
async def test_conflicting_second_output_cannot_reset_the_persisted_correction_allowance(database):
    conflict = {"notes": [note(), {**note(), "content": "另一条不同理解"}]}
    models = Models(conflict, conflict)
    growth, memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    row = await retry_state(database)
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    stopped = await retry_state(database)
    assert len(models.calls) == 2 and memory.proposals == []
    assert stopped["state"] == "blocked" and stopped["batch"]["output_repair_count"] == 1
    assert not (await growth.view(context))["review"]["retry_batches"][0]["can_request_review"]
    with pytest.raises(AppError, match="reflection_retry_not_available"):
        await growth.request_review(context, {"retry_key": row["retry_key"], "client_request_id": "exhausted-output",
            "expected_revision": (await state(database))["revision"]})
    await growth.recover()
    await growth.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 2


@pytest.mark.asyncio
async def test_existing_blocked_uuid_request_reviews_as_a_new_short_reference_candidate(database, monkeypatch):
    models = Models({"notes": [{**note(), "episode_ids": ["episod0"]}]}, {"notes": [note()]})
    growth, memory, jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    defer = growth.retries.defer
    async def old_blocked(state_value, batch, code, result=None, *, diagnostic=None):
        return await defer(state_value, batch, code, result)
    with monkeypatch.context() as old:
        old.setattr(growth_materials, "short_material", lambda value: value)
        old.setattr(growth.retries, "defer", old_blocked)
        await growth.organize("actor", scope, 1)
    original = models.calls[0]
    original_request_value = await growth.generations.request(original.attempt_id, context)
    assert "model_protocol" not in frozen_material(original)
    row = await retry_state(database)
    assert row["state"] == "blocked" and "output_repair_count" not in row["batch"]
    # The native review command only enqueues, including when Models is absent.
    controller = GrowthService(growth.companion, jobs, None, growth.assembler,
        on_projection_changed=growth.on_projection_changed)
    await controller.request_review(context, {"retry_key": row["retry_key"], "client_request_id": "old-v2-review",
        "expected_revision": (await state(database))["revision"]})
    assert len(models.calls) == 1
    await growth.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 2 and len(memory.proposals) == 1
    assert frozen_material(models.calls[1])["model_protocol"] == OUTPUT_PROTOCOL
    assert frozen_material(models.calls[1])["episode_refs"] == {"e1": "episode"}
    assert await growth.generations.request(original.attempt_id, context) == original_request_value


@pytest.mark.asyncio
async def test_correction_transport_retry_keeps_one_allowance_and_late_result_is_historical_only(database):
    class Outcomes(Models):
        async def generate(self, request):
            self.status = "failed" if len(self.calls) == 1 else "succeeded"
            result = await super().generate(request)
            if self.status == "failed":
                result = replace(result, receipt=replace(result.receipt, error_code="upstream_http_503"))
                self.results[request.attempt_id] = result
            return result
    models = Outcomes({"notes": [{**note(), "episode_ids": ["bad"]}]}, {}, {"notes": []},
        {"notes": [{**note(), "affect": affect()}, note("interest")]})
    growth, memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    row = await retry_state(database)
    await due(database)
    failed = await growth.retries.run("actor", scope, row["retry_key"])
    assert failed["state"] == "retry_waiting"
    waiting = await retry_state(database)
    assert waiting["batch"]["output_repair_count"] == 1 and waiting["attempt_number"] == 2
    await tail(growth, memory, context)
    await growth.organize("actor", scope, (await state(database))["revision"])
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    finished = await retry_state(database)
    assert len(models.calls) == 4 and finished["outcome"] == "historical_only"
    assert finished["batch"]["output_repair_count"] == 1
    assert [item.kind for item in memory.proposals] == ["reflection"]
    async with database.transaction(read_only=True) as connection:
        for table in ("companion.affect_events", "companion.style_rules"):
            assert (await (await connection.execute("SELECT count(*) AS n FROM "+table)).fetchone())["n"] == 0


@pytest.mark.asyncio
async def test_reserved_correction_rechecks_source_before_freezing_a_new_attempt(database, monkeypatch):
    models = Models({"notes": [{**note(), "episode_ids": ["bad"]}]})
    growth, _memory, _jobs, context, _ = await prepare(database, models)
    scope = signature(sorted(context.scope_ids))
    await growth.organize("actor", scope, 1)
    row = await retry_state(database)
    async def withdrawn(*_args):
        return False
    monkeypatch.setattr(growth.companion.sources, "validate_persistence", withdrawn)
    await due(database)
    await growth.retries.run("actor", scope, row["retry_key"])
    assert len(models.calls) == 1 and (await retry_state(database))["state"] == "blocked"
    async with database.transaction(read_only=True) as connection:
        assert (await (await connection.execute("SELECT count(*) AS n FROM conversation.generation_requests")).fetchone())["n"] == 1
