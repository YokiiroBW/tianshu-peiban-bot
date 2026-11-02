"""A diary observes its own day without reproducing full technical originals."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.life.diaries import DiaryService
from tianshu.life.diary_materials import MATERIAL_PREFIX, SELECTION_KEY, assemble_diary_material, frozen_recipe
from tianshu.life.model_budget import DIARY_INPUT_TOKENS, INPUT_TOKENS
from tianshu.models.types import ModelMessage, ModelPart
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, RequestBudget, SourceRef
from test_diary_automation import Authority, collect_current, diary_state, fixture, start_day


def material(request):
    return json.loads(next(part.text[len(MATERIAL_PREFIX):] for message in request.messages
        for part in message.parts if part.text and part.text.startswith(MATERIAL_PREFIX)))


@pytest.mark.asyncio
async def test_daily_semantics_preserve_discovery_read_failure_imagined_and_unknown_image_boundaries(database):
    value = await fixture(database, unknown=True)
    technical = "TECHNICAL_PAYLOAD_NOT_DIARY " * 5000
    cases = (
        ("discovery", "寻找容器故障切换资料", {"capability_id": "content.discover", "execution": "succeeded",
            "effect": "none", "read_basis": "discovery_metadata", "experience_eligible": False,
            "candidates": [{"snippet": technical}], "next_actions": [{"arguments": technical}]}),
        ("read", "阅读一篇容器切换文章", {"capability_id": "content.read", "execution": "succeeded",
            "effect": "none", "read_basis": "actual_source_body", "experience_eligible": True,
            "content": {"title": "容器切换的基本过程", "text": technical}}),
        ("failed-read", "操作未完成：阅读另一篇文章", {"capability_id": "content.read", "execution": "cancelled",
            "effect": "none", "error_code": "capability_timeout", "source_refs": []}),
        ("image", "生成了一张图片", {"capability_id": "images.generate", "execution": "succeeded",
            "effect": "created", "plan": {"prompt": technical}, "provider_payload": technical}),
        ("uncertain", "结果未确认：生成图片", {"capability_id": "images.generate", "execution": "unknown",
            "effect": "unknown", "plan": {"prompt": technical}}),
        ("imagined", "在想象的小屋里整理书架", {"imagined": True, "representation": "fictional_world",
            "software_action": False, "feelings": "主观上觉得放松。"}),
    )
    value.memory.episodes = tuple(replace(value.episode, episode_id=key, summary=summary,
        source_refs=(SourceRef("original-"+key, 1),), actual_result=actual,
        origin_kind=OriginKind.ACTOR if key == "imagined" else OriginKind.TOOL_RESULT,
        coverage=Coverage(False, missing=("candidate_body_unread",)) if key == "failed-read" else Coverage(True))
        for key, summary, actual in cases)
    await start_day(value)
    diary = await collect_current(database, value)
    original = deepcopy(diary["generation_recipe"])
    with pytest.raises(TimeoutError):
        await value.diaries.generate(diary["id"], diary["generation_id"])
    request = value.models.calls[0]
    view = material(request)
    by_id = {episode["episode_id"]: episode for episode in view["episodes"]}
    assert "TECHNICAL_PAYLOAD_NOT_DIARY" not in json.dumps(wire(request.messages))
    assert by_id["discovery"]["actual_result"]["discovery"]["body_read"] is False
    assert by_id["discovery"]["actual_result"]["experience_eligible"] is False
    assert by_id["read"]["actual_result"]["body_read"] is True
    assert by_id["read"]["actual_result"]["title"] == "容器切换的基本过程"
    assert "source_body_not_reproduced" in by_id["read"]["diary_coverage"]["missing"]
    assert by_id["failed-read"]["actual_result"]["error_code"] == "capability_timeout"
    assert by_id["failed-read"]["coverage"]["missing"] == ["candidate_body_unread"]
    assert by_id["imagined"]["actual_result"]["representation"] == "fictional_world"
    assert by_id["imagined"]["actual_result"]["software_action"] is False
    assert by_id["imagined"]["actual_result"]["feelings"] == "主观上觉得放松。"
    assert by_id["uncertain"]["actual_result"]["execution"] == "unknown"
    assert "image_contents_not_described" in by_id["image"]["diary_coverage"]["missing"]
    retained = await diary_state(database)
    assert retained["generation_recipe"]["episodes"] == original["episodes"]
    assert retained["generation_recipe"][SELECTION_KEY]["material"] == view
    assert wire(request.source_refs) == wire(tuple(ref for episode in value.memory.episodes for ref in episode.source_refs))
    # A late success consumes the same frozen observations once; no extra summary
    # model, changed recipe, partial assistant text or second model invocation.
    value.models.actual = replace(value.models.actual, receipt=replace(value.models.actual.receipt, status="succeeded"))
    await value.diaries.generate(diary["id"], diary["generation_id"])
    assert len(value.models.calls) == 1
    async with database.transaction(read_only=True) as connection:
        saved = await (await connection.execute("SELECT recipe FROM life.diary_revisions")).fetchone()
    assert saved["recipe"]["episodes"] == original["episodes"]
    assert saved["recipe"]["source_refs"] == wire(request.source_refs)
    assert saved["recipe"][SELECTION_KEY]["material"] == view
    corrupted = deepcopy(retained["generation_recipe"])
    corrupted["episodes"][0]["revision"] += 1
    with pytest.raises(AppError, match="diary_material_contract_invalid"):
        frozen_recipe(request, corrupted)


@pytest.mark.asyncio
async def test_diary_uses_exact_independent_200000_input_budget_and_omits_whole_units(database):
    value = await fixture(database)
    assembler = ContextAssembler(Authority(), None, None, settings={"tokenizer": "o200k_base"})
    persona = "固定人格必须完整保留。" * 2000
    oversized = "整段经历描述必须整段选择。" * 30000
    recipe = {"day": "2026-11-02", "timezone": "Asia/Shanghai", "scan_hash": "synthetic-scan",
        "episodes": [wire(replace(value.episode, summary=oversized)),
            wire(replace(value.episode, episode_id="small", summary="今天确实整理了书架。"))],
        "coverage": {"complete": True, "missing": []}}
    instruction = "Only describe the supplied day."
    pack, view = await assemble_diary_material(assembler, value.context, recipe, instruction,
        RequestBudget(datetime.now(UTC)+timedelta(minutes=5)), persona_text=persona,
        input_tokens=DIARY_INPUT_TOKENS, output_tokens=16384)
    assert DIARY_INPUT_TOKENS == 200000 and INPUT_TOKENS == 16000
    assert pack.token_usage.estimated_tokens <= 200000
    assert persona in pack.system
    assert "summary" not in view["episodes"][0]
    assert view["episodes"][0]["diary_coverage"]["omitted_units"] == ["summary"]
    assert view["episodes"][1]["summary"] == "今天确实整理了书架。"
    assert recipe["episodes"][0]["summary"] == oversized  # No mutation/truncation of the original.
    # Fixed persona alone over the diary ceiling remains a clear failure.
    with pytest.raises(AppError, match="context_mandatory_budget_exceeded"):
        await assemble_diary_material(assembler, value.context, recipe, instruction,
            RequestBudget(datetime.now(UTC)+timedelta(minutes=5)), persona_text=oversized,
            input_tokens=DIARY_INPUT_TOKENS, output_tokens=16384)


@pytest.mark.asyncio
async def test_each_day_has_no_chat_previous_day_memory_or_failed_partial_context(database):
    value = await fixture(database, unknown=True)
    value.context = replace(value.context, conversation_id="same-chat", conversation_kind="group")
    actor = await value.life.companion.actor(value.context)
    value.life.companion.actor = AsyncMock(return_value={**actor, "persona_id": "persona", "persona_revision": 7})
    value.life.companion.persona = AsyncMock(return_value=SimpleNamespace(prompt_text="当天人格全文。"))
    shared = ContextAssembler(value.identity, SimpleNamespace(projection=AsyncMock()),
        SimpleNamespace(search=AsyncMock()), settings={"tokenizer": "o200k_base"})
    value.diaries = DiaryService(value.life, value.models, shared)
    assert value.diaries.assembler.retrieval is None
    value.models.output = "PREVIOUS_FAILED_PARTIAL"
    await start_day(value)
    first = await collect_current(database, value)
    with pytest.raises(TimeoutError):
        await value.diaries.generate(first["id"], first["generation_id"])
    first_request = value.models.calls[0]
    value.memory.episodes = (replace(value.episode, episode_id="next-day", summary="NEXT_DAY_FACT",
        occurred_at=value.episode.occurred_at+timedelta(days=1)),)
    await value.diaries.request(value.context, {"client_request_id": "next-day-independent",
        "day": "2026-11-03", "expected_revision": None})
    async with database.transaction(read_only=True) as connection:
        second = await (await connection.execute("SELECT * FROM life.diaries WHERE day='2026-11-03'")).fetchone()
    await value.diaries.collect(second["id"], second["generation_id"], None)
    value.models.lose_response, value.models.actual = False, None
    await value.diaries.generate(second["id"], second["generation_id"])
    second_request = value.models.calls[1]
    text = json.dumps(wire(second_request.messages), ensure_ascii=False)
    assert first_request.intent_id != second_request.intent_id
    assert second_request.context == value.context  # Authority is not replaced to fake isolation.
    assert second_request.provider_session_scope == first_request.provider_session_scope == "intent"
    assert len(second_request.messages) == 2 and all(message.role == "user" for message in second_request.messages)
    assert "NEXT_DAY_FACT" in text and "PREVIOUS_FAILED_PARTIAL" not in text
    assert material(second_request)["day"] == "2026-11-03"
    assert [episode["episode_id"] for episode in material(second_request)["episodes"]] == ["next-day"]
    assert value.episode.summary not in text
    assert "当天人格全文。" in second_request.system
    shared.retrieval.search.assert_not_awaited()
    shared.memory.projection.assert_not_awaited()
    # Existing full-recipe frozen intents are still decoded without reassembly.
    legacy = replace(first_request, provider_session_scope="conversation", messages=(ModelMessage("user", (
        ModelPart("text", text=MATERIAL_PREFIX+json.dumps(first["generation_recipe"], ensure_ascii=False)),)),))
    assert frozen_recipe(legacy) == first["generation_recipe"]
