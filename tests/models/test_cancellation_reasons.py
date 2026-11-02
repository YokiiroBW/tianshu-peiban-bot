"""Consumer interruption never impersonates an explicit human cancellation."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import RoutePlan
from tianshu.models.client import ModelClient
from tianshu.models.codec import receipt_from_wire
from tianshu.models.ledger import AttemptLedger, receipt
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
    ("consumer", "consumer_interrupted", "consumer_interrupted"),
    ("explicit", "cancelled_unknown", "explicit_cancel"),
    ("owner", "broker_stopping", None),
])
async def test_submitted_model_cancellation_keeps_unknown_and_its_actual_cause(database, cause, code, reason):
    ledger, original = AttemptLedger(database, "instance"), request()
    broker = broker_for(ledger)
    broker.credential_store, broker.prepare_pending, broker.prepare_slots = None, 0, asyncio.Semaphore(1)
    broker.prepare_media = AsyncMock(side_effect=lambda value: value)
    broker.catalog = SimpleNamespace(resolve=AsyncMock(return_value=plan()))
    broker.compiler = SimpleNamespace(compile=lambda *_args, **_kwargs: SimpleNamespace(url="synthetic", body={}, headers={}))
    submitted = asyncio.Event()

    @asynccontextmanager
    async def post(*_args, **_kwargs):
        submitted.set()
        await asyncio.Future()  # A synthetic in-flight provider call; no network.
        yield

    broker.session = SimpleNamespace(post=post)
    task = asyncio.create_task(broker.generate(original))
    await asyncio.wait_for(submitted.wait(), 5)
    if cause == "explicit":
        await broker.cancel(original.attempt_id)
    else:
        if cause == "owner":
            broker.ready = False
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    row = await ledger.get(original.attempt_id)
    assert row["status"] == row["dispatch"] == "unknown"
    assert row["error_code"] == code and row["cancellation_reason"] == reason
    assert row["result"] is None and broker.active == {}
    assert broker.scheduler.release.await_args.kwargs["hold_until"] == row["hold_until"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["generate", "stream"])
async def test_client_network_cleanup_passes_consumer_reason_and_explicit_cancel_keeps_default(operation):
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
    assert calls[0][1] == {"reason": "consumer_interrupted"}
    result = await client.cancel(original.attempt_id)
    assert calls[-1][1] == {"reason": "explicit_cancel"} and result.cancellation_reason == "explicit_cancel"
    legacy = wire(value)
    legacy.pop("cancellation_reason")
    assert receipt_from_wire(legacy).cancellation_reason is None
