"""Background input ceilings include complete material and separate output reserves."""
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.life import planner
from tianshu.life.model_budget import generation_request
from tianshu.life.writing import WritingService
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import RequestBudget, SourceRef
from test_planner_contract import Identity, context, data
from test_planner_execution import setup


def assembler(identity=None):
    return ContextAssembler(identity or Identity(), None, None, settings={"tokenizer": "o200k_base"})


def large_material(counter, tokens):
    unit = " budget_sentinel"
    value = unit * (tokens // counter.text(unit) + 1)
    # Check actual production-tokenizer size, rather than treating characters as tokens.
    assert tokens <= counter.text(value) <= tokens + 8
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [16384, 32768])
async def test_planning_accepts_near_200000_input_without_spending_the_native_output_reserve(output):
    assembly = assembler()
    persona = large_material(assembly.counter, 190000)
    profile = {"model": "configured-model", "protocol": "openai.chat", "route_id": "route", "route_revision": 7,
        "verified_capabilities": ["text", "stream"], "max_output_tokens": output, "output_parameters": {}}
    selected = await generation_request(None, context(), key="large-plan", attempt_id="large-plan-attempt",
        function="life.plan", profile=profile)
    request = await planner.request(assembly, context(), selected.budget, key="large-plan", persona=persona,
        data=data([]), refs=(), model_request=selected)
    assert BACKGROUND_INPUT_TOKENS == 200000 and planner.INPUT_BODY_TOKENS == BACKGROUND_INPUT_TOKENS
    assert 190000 < request.estimated_input_tokens <= BACKGROUND_INPUT_TOKENS
    assert persona in request.system and planner.TASK in request.system
    assert request.max_output_tokens == output and request.parameters == selected.parameters
    assert request.execution_timeout_seconds == selected.execution_timeout_seconds
    assert request.budget == selected.budget and request.business_deadline_at == selected.business_deadline_at
    assert assembly.counter.measure(request.system, request.messages, ())[0] == request.estimated_input_tokens


@pytest.mark.asyncio
async def test_planning_still_rejects_a_whole_mandatory_persona_over_the_200000_input_ceiling():
    assembly = assembler()
    persona = large_material(assembly.counter, BACKGROUND_INPUT_TOKENS + 1)
    with pytest.raises(AppError, match="planner_context_budget_exceeded"):
        await planner.request(assembly, context(), RequestBudget(datetime.now(UTC)+timedelta(minutes=5)),
            key="over-limit-plan", persona=persona, data=data([]), refs=())


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens,accepted", [(190000, True), (200001, False)])
async def test_writing_assembles_200000_input_plus_the_original_6000_output_and_preserves_whole_recipes(database, tokens, accepted):
    value = await setup(database, "只保存这次合成章节的完整正文。")
    assembly = assembler(value.worker.life.identity)
    assembly.assemble = AsyncMock(wraps=assembly.assemble)
    text = large_material(assembly.counter, tokens)
    # Three real-shaped prior chapters can contribute more than the old total
    # budget while each remains below the existing chapter character limit.
    width = (len(text) + 2) // 3
    previous = [text[index:index+width] for index in range(0, len(text), width)]
    assert len(previous) == 3 and max(map(len, previous)) < 500000
    life = value.worker.life
    life.sources.record_event = AsyncMock(return_value=(SourceRef("chapter-completion", 1),))
    writing = WritingService(life, value.models, assembly)
    life.jobs.register("life.chapter_generate", writing.generate)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.works(id,actor_id,title,scope_ids) VALUES ('work','actor','Synthetic work',ARRAY['actor-scope'])")
        for index, content in enumerate(previous, start=1):
            identifier = f"previous-{index}"
            await connection.execute("INSERT INTO life.chapters(id,work_id,position,title,current_revision) VALUES (%s,'work',%s,%s,1)",
                (identifier, index, identifier))
            await connection.execute("INSERT INTO life.chapter_revisions(chapter_id,revision,content,work_revision,recipe) VALUES (%s,1,%s,1,%s)",
                (identifier, content, document({"source_refs": wire((SourceRef(identifier, 1),))})))
        await connection.execute("INSERT INTO life.chapters(id,work_id,position,title) VALUES ('next','work',4,'Next chapter')")
    command = await writing.request_generation(value.context, {"id": "next", "instruction": "Continue these exact chapters",
        "expected_revision": 1, "client_request_id": "large-writing"})
    if accepted:
        await writing.generate("next", command.attempt_id)
    else:
        with pytest.raises(AppError, match="context_mandatory_budget_exceeded"):
            await writing.generate("next", command.attempt_id)
    options = assembly.assemble.await_args.kwargs
    assert options["max_input_tokens"] == BACKGROUND_INPUT_TOKENS + 6000
    assert options["reserved_output_tokens"] == 6000
    async with database.transaction(read_only=True) as connection:
        chapter = await (await connection.execute("SELECT * FROM life.chapters WHERE id='next'")).fetchone()
        frozen = await (await connection.execute("SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (command.attempt_id,))).fetchone()
    assert [item["content"] for item in reversed(chapter["generation_recipe"]["previous"])] == previous
    if accepted:
        request = value.models.calls[0]
        assert len(value.models.calls) == 1 and 190000 < request.estimated_input_tokens <= BACKGROUND_INPUT_TOKENS
        assert request.max_output_tokens == 6000 and request.execution_timeout_seconds == 600
        assert request.business_deadline_at is None and request.max_queue_wait is None
        assert assembly.counter.measure(request.system, request.messages, ())[0] == request.estimated_input_tokens
        assert frozen["request"] == wire(request) and chapter["state"] == "draft"
        assert {ref.source_id for ref in request.source_refs} == {f"previous-{index}" for index in range(1, 4)}
        material = json.loads(next(part.text.split("：\n", 1)[1] for message in request.messages for part in message.parts
            if part.text and part.text.startswith("材料 writing_recipe")))
        assert [item["content"] for item in reversed(material["previous"])] == previous
    else:
        assert not value.models.calls and frozen is None
        assert chapter["state"] == "failed" and chapter["reason"] == "context_mandatory_budget_exceeded"
        life.sources.record_event.assert_not_awaited()
