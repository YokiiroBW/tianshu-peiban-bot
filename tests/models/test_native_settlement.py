"""The Broker settles explicit failure and incomplete output without unknown holds."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import RoutePlan
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("include_output", [False, True], ids=["status-only", "empty-output"])
@pytest.mark.parametrize("provider_state,settled", [("failed", "failed"), ("incomplete", "succeeded")])
async def test_native_terminal_response_is_settled_and_releases_capacity(
    stream, include_output, provider_state, settled
):
    context = TrustedContext("p", "actor", None, None, ("scope",), ("scope",), 1, OriginKind.ACTOR)
    request = ModelRequest("intent", "original", "reflect", ServiceClass.ELASTIC, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)),
        (ModelMessage("user", (ModelPart("text", text="synthetic"),)),))
    native = {"id": "response", "object": "response", "status": provider_state,
              "usage": {"input_tokens": 20, "output_tokens": 4096}}
    if include_output:
        native["output"] = []
    if provider_state == "failed":
        native["error"] = {"code": "server_error"}
    else:
        native["incomplete_details"] = {"reason": "max_output_tokens"}
    payload = (("data: " + json.dumps({"type": "response." + provider_state, "response": native}) + "\n\n").encode()
               if stream else json.dumps(native).encode())

    async def chunks(_size):
        yield payload

    @asynccontextmanager
    async def post(*_args, **_kwargs):
        yield SimpleNamespace(status=200, content=SimpleNamespace(iter_chunked=chunks))

    row = {"attempt_id": "original", "intent_id": "intent", "status": "queued", "dispatch": "submitted",
           "route_id": "r", "route_revision": 1, "provider_id": "p", "model": "m", "protocol": "openai.responses",
           "quota_group": "q", "provider_handle": None, "queued_at": None, "started_at": None,
           "finished_at": None, "first_output_ms": None, "error_code": None, "hold_until": None, "result": None}

    async def finish(_attempt, status, **values):
        row.update(values, status=status)
        row["usage"] = wire(row.get("usage") or {})
        return dict(row)

    broker = ModelBroker(None, None, None)
    broker.ready, broker.credential_store = True, None
    broker.prepare_slots = asyncio.Semaphore(1)
    broker.recheck = AsyncMock()
    broker.prepare_media = AsyncMock(side_effect=lambda value: value)
    plan = RoutePlan("r", 1, "p", "m", "openai.responses", "https://synthetic.invalid", "key", "q", frozenset({"text", "stream"}), {})
    broker.catalog = SimpleNamespace(resolve=AsyncMock(return_value=plan))
    broker.compiler = SimpleNamespace(compile=lambda *_args, **_kwargs: SimpleNamespace(url="https://synthetic.invalid", body={}, headers={}))
    broker.ledger = SimpleNamespace(create=AsyncMock(return_value=(dict(row), True)),
        get=AsyncMock(side_effect=lambda _attempt: dict(row)), record_wire=AsyncMock(),
        started=AsyncMock(), assert_generation=AsyncMock(), save_observation=AsyncMock(), prestart_check=AsyncMock(),
        checkpoint=AsyncMock(), finish=AsyncMock(side_effect=finish))
    broker.scheduler = SimpleNamespace(acquire=AsyncMock(), submitted=AsyncMock(), release=AsyncMock(),
        group_deferred=AsyncMock(return_value=False),
        policies={"q": SimpleNamespace(unknown_hold_seconds=300, max_wait_seconds=None)})
    broker.session, broker.event = SimpleNamespace(post=post), AsyncMock()
    events = [event async for event in broker._events(request, stream=stream)]
    result = next(event.data for event in events if event.kind == "receipt")
    assert result.receipt.status == settled
    assert result.completion.state == "incomplete"
    assert result.receipt.error_code == ("server_error" if provider_state == "failed" else None)
    assert result.completion.provider_status == provider_state
    assert result.native_response == native
    assert result.receipt.usage.input_tokens == 20
    assert result.receipt.usage.output_tokens == 4096
    assert broker.ledger.finish.await_count == 1
    assert broker.scheduler.release.call_args.kwargs["units"] == {"tokens": 4116}
    assert "hold_until" not in broker.scheduler.release.call_args.kwargs
    assert broker.active == {}
