"""Consumer interruption never impersonates an explicit human cancellation."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import QuotaPolicy, RoutePlan
from tianshu.models.client import ModelClient
from tianshu.models.codec import receipt_from_wire
from tianshu.models.ledger import AttemptLedger, receipt
from tianshu.models.scheduler import QuotaScheduler
from tianshu.models.types import ModelReceipt, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def request(identity="work"):
    return ModelRequest(identity, identity, "reflect", ServiceClass.ELASTIC,
        TrustedContext("principal", "actor", None, None, (), (), 1, OriginKind.ACTOR),
        RequestBudget(datetime.now(UTC)+timedelta(minutes=1)), (), execution_timeout_seconds=300)


def plan():
    return RoutePlan("route", 1, "provider", "model", "openai.chat", "https://synthetic.invalid",
        "key", "quota", frozenset({"text", "stream"}), {})


def broker_for(ledger):
    broker = ModelBroker.__new__(ModelBroker)
    broker.ledger, broker.active, broker.external, broker.ready = ledger, {}, {}, True
    broker.scheduler = SimpleNamespace(cancel=AsyncMock(), acquire=AsyncMock(), submitted=AsyncMock(),
        release=AsyncMock(), policies={"quota": SimpleNamespace(unknown_hold_seconds=300)})
    broker.recheck, broker.event = AsyncMock(), AsyncMock()
    return broker


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,code", [("explicit_cancel", "cancelled_before_start"), ("consumer_interrupted", "consumer_interrupted")])
async def test_cancel_before_create_persists_reason_and_late_explicit_cancel_has_priority(database, reason, code):
    ledger = AttemptLedger(database, "instance")
    original = request()
    assert await ledger.request_cancel(original.attempt_id, reason=reason) is None
    row, created = await ledger.create(original, plan(), {})
    assert created and row["status"] == "cancelled" and row["dispatch"] == "not_started"
    assert receipt(row).cancellation_reason == reason and row["error_code"] == code
    await ledger.request_cancel(original.attempt_id)  # Human explicit cancel upgrades any cleanup.
    await ledger.request_cancel(original.attempt_id, reason="consumer_interrupted")
    current = await ledger.get(original.attempt_id)
    assert current["cancellation_reason"] == "explicit_cancel"
    assert current["status"] == row["status"] and current["error_code"] == code
    duplicate, created = await ledger.create(original, plan(), {})
    assert not created and receipt(duplicate).cancellation_reason == "explicit_cancel"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "succeeded"])
async def test_status_and_result_project_latest_explicit_reason_without_rewriting_original_receipt(database, state):
    ledger, original = AttemptLedger(database, "instance"), request()
    await ledger.create(original, plan(), {})
    await ledger.started(original.attempt_id, 300, deadline_at=datetime.now(UTC)+timedelta(minutes=1))
    await ledger.request_cancel(original.attempt_id, reason="consumer_interrupted")
    body = {"parts": [{"kind": "text", "text": "original result"}]} if state == "succeeded" else None
    old = await ledger.finish(original.attempt_id, state, result=body,
        error_code="consumer_interrupted" if state == "unknown" else None)
    broker = broker_for(ledger)
    before = await broker.status(original.attempt_id)
    assert before.cancellation_reason == "consumer_interrupted"
    explicit = await broker.cancel(original.attempt_id)
    assert explicit.cancellation_reason == "explicit_cancel"
    # A delayed cleanup cannot downgrade the already persisted human choice.
    await broker.cancel(original.attempt_id, reason="consumer_interrupted")
    current = await broker.status(original.attempt_id)
    result = await broker.result(original.attempt_id, context=original.context)
    assert current.cancellation_reason == result.receipt.cancellation_reason == "explicit_cancel"
    assert current.status == result.receipt.status == state
    assert current.error_code == result.receipt.error_code == old["error_code"]
    assert (await ledger.get(original.attempt_id))["result"] == body
    broker.scheduler.acquire.assert_not_awaited()  # Observation/cancel never admits another attempt.
    if state == "succeeded":
        assert result.parts[0].text == "original result"


@pytest.mark.asyncio
@pytest.mark.parametrize("cause,code,reason", [
    ("consumer", None, None),
    ("explicit", "cancelled_unknown", "explicit_cancel"),
    ("owner", "broker_stopping", None),
])
async def test_submitted_model_cancellation_keeps_unknown_and_its_actual_cause(database, cause, code, reason):
    ledger, original = AttemptLedger(database, "instance"), request()
    broker = ModelBroker(database, None, None)
    broker.ledger, broker.ready = ledger, True
    broker.scheduler = QuotaScheduler({"quota": QuotaPolicy("quota", 2, 1, 1)})
    await broker.scheduler.start()
    broker.recheck, broker.event = AsyncMock(), AsyncMock()
    broker.prepare_media = AsyncMock(side_effect=lambda value: value)
    broker.catalog = SimpleNamespace(resolve=AsyncMock(return_value=plan()))
    broker.compiler = SimpleNamespace(compile=lambda *_args, **_kwargs: SimpleNamespace(url="synthetic", body={}, headers={}))
    submitted, release = asyncio.Event(), asyncio.Event()

    async def chunks(_size):
        yield json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                                      "finish_reason": "stop"}]}).encode()

    @asynccontextmanager
    async def post(*_args, **_kwargs):
        submitted.set()
        await release.wait()  # A synthetic in-flight provider call; no network.
        yield SimpleNamespace(status=200, content=SimpleNamespace(iter_chunked=chunks))

    broker.session = SimpleNamespace(post=post, close=AsyncMock())
    task = asyncio.create_task(broker.generate(original))
    try:
        await asyncio.wait_for(submitted.wait(), 5)
        if cause == "explicit":
            await broker.cancel(original.attempt_id)
        elif cause == "owner":
            await broker.close()
        else:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if cause == "consumer":
            row = await ledger.get(original.attempt_id)
            assert row["status"] == "running" and row["cancellation_reason"] is None
            release.set()
            await asyncio.wait_for(asyncio.gather(*(item[0] for item in broker.drivers.values())), 5)
            row = await ledger.get(original.attempt_id)
            assert row["status"] == "succeeded" and row["cancellation_reason"] is None
        else:
            row = await ledger.get(original.attempt_id)
            assert row["status"] == row["dispatch"] == "unknown"
            assert row["error_code"] == code and row["cancellation_reason"] == reason
            assert row["result"] is None
        assert broker.scheduler.snapshot()["quota"]["unknown_holds"] == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if broker.ready:
            await broker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["generate", "stream"])
async def test_client_disconnect_does_not_cancel_original_but_explicit_cancel_does(operation):
    client, calls = ModelClient("http://synthetic", "synthetic"), []
    original = request()
    value = ModelReceipt(original.attempt_id, original.intent_id, "unknown", "unknown",
        "route", 1, "provider", "model", "openai.chat", "quota", error_code="consumer_interrupted")

    async def json(path, *, body=None, **_kwargs):
        if path.endswith("/generate"):
            raise aiohttp.ServerDisconnectedError("synthetic response lost")
        calls.append((path, body))
        return {"receipt": wire(replace(value, cancellation_reason=body["reason"]))}

    @asynccontextmanager
    async def post(*_args, **_kwargs):
        raise aiohttp.ServerDisconnectedError("synthetic stream lost")
        yield

    client._json = json
    client.status = AsyncMock(return_value=replace(value, status="running"))
    client._data_session = lambda _service: SimpleNamespace(post=post)
    if operation == "generate":
        with pytest.raises(aiohttp.ServerDisconnectedError):
            await client.generate(original)
    else:
        with pytest.raises(AppError, match="model_stream_interrupted"):
            async for _event in client.stream(original):
                pass
    assert calls == []
    result = await client.cancel(original.attempt_id)
    assert calls[-1][1] == {"reason": "explicit_cancel"} and result.cancellation_reason == "explicit_cancel"
    legacy = wire(value)
    legacy.pop("cancellation_reason")
    assert receipt_from_wire(legacy).cancellation_reason is None


async def test_stream_failure_retains_observed_receipt_when_status_disappears():
    client = ModelClient("http://synthetic", "synthetic")
    original = request()
    fallback = ModelReceipt(original.attempt_id, original.intent_id, "unknown", "unknown",
        "route", 1, "provider", "model", "openai.chat", "quota", error_code="timeout_unknown")
    client.status = AsyncMock(return_value=None)
    failure = await client._stream_failure(original.attempt_id, fallback.error_code, fallback=fallback)
    assert failure.code == "timeout_unknown"
    assert failure.details["attempt_id"] == original.attempt_id
    assert failure.details["status"] == failure.details["dispatch"] == "unknown"
