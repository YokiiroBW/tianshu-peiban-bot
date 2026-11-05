"""A superseded broker must not settle another broker's original attempt."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import RoutePlan
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def original_request():
    context = TrustedContext("fixture", None, None, None, (), (), 1, OriginKind.HUMAN)
    return ModelRequest("intent", "original", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=2)),
        (ModelMessage("user", (ModelPart("text", text="fixture"),)),),
        estimated_input_tokens=5, execution_timeout_seconds=30)


def broker_with_fixture_transport(database, plan, post):
    broker = ModelBroker(database, None, None)
    broker.ready = True
    broker.catalog = SimpleNamespace(resolve=AsyncMock(return_value=plan))
    broker.compiled_requests = []

    def compile_call(_plan, request, **_kwargs):
        broker.compiled_requests.append(request)
        return SimpleNamespace(url="https://fixture.invalid", body={"messages": []}, headers={})

    broker.compiler = SimpleNamespace(compile=compile_call)
    broker.scheduler = SimpleNamespace(acquire=AsyncMock(), submitted=AsyncMock(), release=AsyncMock(),
        group_deferred=AsyncMock(return_value=False),
        policies={"quota": SimpleNamespace(max_wait_seconds=None)})
    broker.session = SimpleNamespace(post=post)
    broker.event = AsyncMock()
    return broker


@pytest.mark.parametrize("winner_state", ["queued", "running"])
async def test_losing_resumed_driver_cannot_finish_or_modify_winning_receipt(database, winner_state):
    request = original_request()
    plan = RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "quota",
                     frozenset({"text"}), {})
    sent, provider_entered, finish_provider = [], asyncio.Event(), asyncio.Event()

    async def chunks(_size):
        await finish_provider.wait()
        yield json.dumps({"id": "original-provider-result", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "original result"},
            "finish_reason": "stop"}]}).encode()

    @asynccontextmanager
    async def post(_url, **_kwargs):
        sent.append(request.attempt_id)
        provider_entered.set()
        yield SimpleNamespace(status=200, content=SimpleNamespace(iter_chunked=chunks))

    old = broker_with_fixture_transport(database, plan, post)
    new = broker_with_fixture_transport(database, plan, post)
    await old.ledger.create(request, plan, {"tokens": 2053}, frozen=request)
    entered_old, entered_new, release_old, release_new = (asyncio.Event() for _ in range(4))
    old_started, new_started = old.ledger.started, new.ledger.started

    async def pause_old(*args, **kwargs):
        entered_old.set()
        await release_old.wait()
        return await old_started(*args, **kwargs)

    async def pause_new(*args, **kwargs):
        entered_new.set()
        await release_new.wait()
        return await new_started(*args, **kwargs)

    old.ledger.started, new.ledger.started = pause_old, pause_new
    tasks = []
    try:
        retry = replace(request, budget=RequestBudget(datetime.now(UTC) + timedelta(minutes=5)))
        await old._start_driver(retry, streaming=False, resume=True)
        await asyncio.wait_for(entered_old.wait(), 5)
        assert old.compiled_requests[0].budget == request.budget
        tasks.append(old.drivers[request.attempt_id][0])
        await new._start_driver(request, streaming=False, resume=True)
        await asyncio.wait_for(entered_new.wait(), 5)
        tasks.append(new.drivers[request.attempt_id][0])
        if winner_state == "running":
            release_new.set()
            await asyncio.wait_for(provider_entered.wait(), 5)
        before = await new.ledger.get(request.attempt_id)
        assert before["broker_instance"] == new.instance_id
        assert before["status"] == winner_state
        release_old.set()
        await asyncio.wait_for(tasks[0], 5)
        after = await new.ledger.get(request.attempt_id)
        for key in ("status", "dispatch", "revision", "diagnostic", "http_timing", "result", "usage"):
            assert after[key] == before[key]
        assert not any(call.args[:2] == ("model.finished", "failed") for call in old.event.await_args_list)
        release_new.set()
        await asyncio.wait_for(provider_entered.wait(), 5)
        finish_provider.set()
        await asyncio.wait_for(tasks[1], 5)
        saved = await new.ledger.get(request.attempt_id)
        assert sent == [request.attempt_id]
        assert saved["status"] == "succeeded" and saved["dispatch"] == "submitted"
        assert saved["result"]["parts"][0]["text"] == "original result"
        assert saved["provider_handle"] == "original-provider-result"
    finally:
        release_old.set()
        release_new.set()
        finish_provider.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_owner_fence_keeps_normal_prestart_failure_and_rejects_stale_owner(database):
    request = original_request()
    plan = RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "quota",
                     frozenset({"text"}), {})
    old = ModelBroker(database, None, None).ledger
    new = ModelBroker(database, None, None).ledger
    await old.create(request, plan, {"tokens": 5}, frozen=request)
    _, claimed, _ = await new.claim_queued_submission(request)
    assert claimed
    with pytest.raises(AppError, match="model_wire_changed_before_execution"):
        await old.record_wire(request.attempt_id, "stale-wire", {})
    assert await old.request_cancel(request.attempt_id, reason="consumer_interrupted",
                                    expected_owner=old.instance_id) is None
    with pytest.raises(AppError, match="attempt_owner_changed"):
        await old.started(request.attempt_id, None)
    assert await old.finish_before_start(request.attempt_id, "failed", error_code="stale") is None
    failed = await new.finish_before_start(request.attempt_id, "failed", error_code="preparation_failed",
                                           diagnostic={"category": "not_started"})
    assert failed["status"] == "failed" and failed["error_code"] == "preparation_failed"
    assert failed["diagnostic"] == {"category": "not_started"}
