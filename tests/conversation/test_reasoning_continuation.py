"""The actual tool-consumption path preserves reasoning without publishing it."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tianshu.conversation.service import ConversationService
from tianshu.models.catalog import RoutePlan
from tianshu.models.protocols import NativeCompiler, ResponseCollector
from tianshu.models.types import ModelCompletion, ModelReceipt, ModelRequest, ModelResult, ToolSpec
from tianshu.runtime.config import Secrets
from tianshu.runtime.files import FileStore
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


async def test_tool_result_consumption_retains_all_assistant_reasoning(monkeypatch, tmp_path):
    import tianshu.conversation.service as module

    monkeypatch.setattr(module, "lock_material_row", AsyncMock(return_value={"state": "running"}))
    connection = SimpleNamespace(execute=AsyncMock())

    @asynccontextmanager
    async def transaction():
        yield connection

    service = SimpleNamespace(
        database=SimpleNamespace(transaction=transaction),
        identity=SimpleNamespace(check=AsyncMock()),
        sources=SimpleNamespace(validate_persistence=AsyncMock(return_value=True)),
        _invoke=AsyncMock(),
        _await_tools=AsyncMock(),
    )
    context = TrustedContext("p", "a", "person", "c", (), (), 1, OriginKind.HUMAN)
    collector = ResponseCollector("openai.chat")
    for delta, finish in [
        ({"role": "assistant", "reasoning_content": "new private "}, None),
        ({"reasoning_content": "reasoning"}, None),
        (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ]
            },
            "tool_calls",
        ),
    ]:
        collector.feed({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
    receipt = ModelReceipt(
        "attempt",
        "intent",
        "succeeded",
        "submitted",
        "route",
        1,
        "provider",
        "model",
        "openai.chat",
        "quota",
    )
    result = ModelResult(
        (*collector.parts, *collector.tool_parts()),
        receipt,
        collector.native_response,
        ModelCompletion("complete"),
    )
    old = {
        "role": "assistant",
        "parts": [{"kind": "text", "text": "older public answer"}],
        "native": {"reasoning_content": "older private reasoning"},
    }
    row = {"turn_id": "turn", "round": 1, "ordered_parts": [], "continuation": [old]}
    pack = SimpleNamespace(source_refs=(), missing=(), deployment_generation=None, behavior_refs=())
    await ConversationService._consume_model_result(
        service, context, row, "attempt", pack, {"echo": object()}, result
    )
    statements = connection.execute.await_args_list
    saved = next(call.args[1][0].obj for call in statements if "SET continuation=" in call.args[0])
    public = next(
        call.args[1][0].obj for call in statements if "SET ordered_parts=" in call.args[0]
    )
    assert "private" not in json.dumps(public)
    assert saved[0] == old and saved[1]["native"] == {"reasoning_content": "new private reasoning"}
    assert all(not item.get("native", {}).get("thought") for item in saved[1]["parts"])
    messages = tuple(
        ConversationService._message(item["role"], item["parts"], item.get("native"))
        for item in saved
    )
    request = ModelRequest(
        "intent",
        "next-attempt",
        "chat",
        ServiceClass.INTERACTIVE,
        context,
        RequestBudget(datetime.now(UTC) + timedelta(seconds=30)),
        messages,
        tools=(ToolSpec("echo", "fixture", {"type": "object"}),),
    )
    route = RoutePlan(
        "route",
        1,
        "provider",
        "model",
        "openai.chat",
        "https://fixture.invalid",
        "KEY",
        "quota",
        frozenset(),
        {},
    )
    body = (
        NativeCompiler(FileStore(tmp_path), Secrets({"KEY": "fixture"}))
        .compile(route, request, stream=False)
        .body
    )
    assert [item["reasoning_content"] for item in body["messages"]] == [
        "older private reasoning",
        "new private reasoning",
    ]
    assert body["messages"][1]["content"] is None
    service._invoke.assert_awaited_once()
