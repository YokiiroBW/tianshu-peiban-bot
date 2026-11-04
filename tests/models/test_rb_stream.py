"""A disconnected observer cannot create or cancel a second physical request."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import aiohttp
from aiohttp import web
import pytest
import pytest_asyncio

from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import QuotaPolicy, RoutePlan
from tianshu.models.client import ModelClient
from tianshu.models.codec import request_from_wire
from tianshu.models.http_timing import trace_config
from tianshu.models.ledger import submission_digest
from tianshu.models.scheduler import QuotaScheduler
from tianshu.models.types import ModelMessage, ModelPart, ModelReceipt, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext

@pytest_asyncio.fixture
async def rb_database(database):
    async with database.transaction() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS runtime.installation_generation "
            "(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),id text NOT NULL,"
            "created_at timestamptz NOT NULL DEFAULT now())")
        await conn.execute("INSERT INTO runtime.installation_generation(singleton,id) VALUES(true,%s) "
                           "ON CONFLICT(singleton) DO NOTHING", (str(uuid4()),))
        found = await (await conn.execute("SELECT to_regclass('models.submissions') AS table_name")).fetchone()
        if found["table_name"] is None:
            await conn.execute(files("tianshu.models").joinpath("async_execution.sql").read_text())
        column = await (await conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='models' "
            "AND table_name='attempts' AND column_name='data_generation'")).fetchone()
        if column is None:
            await conn.execute(files("tianshu.models").joinpath("generation_fence.sql").read_text())
    return database


async def test_disconnect_replays_recorded_prefix_and_preserves_one_provider_call(rb_database):
    release = asyncio.Event()
    called = []

    async def upstream(request):
        called.append(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'data: {"choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}\n\n')
        await release.wait()
        await response.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
        await response.write(b'data: [DONE]\n\n')
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/chat/completions", upstream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    broker = ModelBroker(rb_database, None, None)
    broker.ready = True
    broker.scheduler = QuotaScheduler({"account": QuotaPolicy("account", 1, 0, 1)})
    await broker.scheduler.start()
    broker.session = aiohttp.ClientSession(trace_configs=[trace_config()])
    selected = RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "account",
                         frozenset({"text", "stream"}), {})
    broker.catalog.resolve = AsyncMock(return_value=selected)
    broker.compiler.compile = lambda *_args, **_kwargs: SimpleNamespace(
        url=f"http://127.0.0.1:{port}/chat/completions", body={}, headers={})
    context = TrustedContext("fixture", "actor", None, None, (), (), 1, OriginKind.ACTOR)
    request = ModelRequest("intent", "attempt", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)),
        (ModelMessage("user", (ModelPart("text", text="fixture"),)),), execution_timeout_seconds=10)
    try:
        observer = broker.stream(request)
        assert (await asyncio.wait_for(anext(observer), 5)).kind == "native"
        await observer.aclose()
        assert (await broker.cancel(request.attempt_id, reason="consumer_interrupted")).status == "running"
        release.set()

        async def complete():
            while True:
                state = await broker.status(request.attempt_id)
                if state.status == "succeeded":
                    return state
                await asyncio.sleep(0.01)

        state = await asyncio.wait_for(complete(), 5)
        replay = [event async for event in broker.stream(request)]
        assert len(called) == 1
        assert [event.text for event in replay if event.kind == "text"] == ["hello"]
        assert replay[-1].receipt.status == "succeeded"
        assert state.stream_coverage["truncated"] is False
        points = state.http_timing["points_ms"]
        assert points["response_headers"] is not None and points["first_body_byte"] is not None
        assert points["first_valid_output"] >= points["first_body_byte"]
        with pytest.raises(AppError, match="attempt_identity_conflict"):
            await broker.submit(replace(request, system="changed"))
    finally:
        release.set()
        await broker.close()
        await runner.cleanup()


async def test_lost_submit_ack_queries_original_identity_without_resubmitting():
    client = ModelClient("http://broker.invalid", "fixture")
    context = TrustedContext("fixture", None, None, None, (), (), 1, OriginKind.SYSTEM)
    request = ModelRequest("intent", "attempt", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)), (), execution_timeout_seconds=30)
    expected = ModelReceipt("attempt", "intent", "queued", "not_started", "route", 1,
                            "provider", "model", "openai.chat", "account",
                            request_digest=submission_digest(request))
    client._json = AsyncMock(side_effect=aiohttp.ClientConnectionError("lost ACK"))
    client.status = AsyncMock(return_value=expected)
    assert await client.submit(request) == expected
    client._json.assert_awaited_once()
    client.status.assert_awaited_once_with("attempt")
    with pytest.raises(AppError, match="model_completion_ack_unknown"):
        await client.submit(request, completion=JobSpec("memory.index_retry", "wake", {"index_id": "fixture"},
                                                       data_generation=str(uuid4())))


async def test_incomplete_stream_preserves_usage_and_bounded_shape_without_body(rb_database):
    private = "synthetic-private-content"

    async def upstream(_request):
        event = {"id": "provider-handle", "choices": [{"index": 0,
            "delta": {"content": private}, "finish_reason": None}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
        return web.Response(body=("data: " + json.dumps(event) + "\n\n").encode(),
                            content_type="text/event-stream")

    app = web.Application()
    app.router.add_post("/chat/completions", upstream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    broker = ModelBroker(rb_database, None, None)
    broker.ready = True
    broker.scheduler = QuotaScheduler({"account": QuotaPolicy("account", 1, 0, 1)})
    await broker.scheduler.start()
    broker.session = aiohttp.ClientSession(trace_configs=[trace_config()])
    selected = RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "account",
                         frozenset({"text", "stream"}), {})
    broker.catalog.resolve = AsyncMock(return_value=selected)
    broker.compiler.compile = lambda *_args, **_kwargs: SimpleNamespace(
        url=f"http://127.0.0.1:{port}/chat/completions", body={}, headers={})
    context = TrustedContext("fixture", "actor", None, None, (), (), 1, OriginKind.ACTOR)
    original = ModelRequest("intent", "incomplete", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)), (), execution_timeout_seconds=10)
    try:
        observed = [event async for event in broker.stream(original)]
        result = observed[-1].receipt
        assert result.status == result.dispatch == "unknown"
        assert result.provider_handle == "provider-handle"
        assert result.usage.input_tokens == 10 and result.usage.output_tokens == 3
        assert result.diagnostic["category"] == "incomplete_output"
        assert result.diagnostic["part_kinds"] == ["text"] and result.diagnostic["part_count"] == 1
        assert private not in json.dumps(result.diagnostic)
    finally:
        await broker.close()
        await runner.cleanup()


async def test_behavior_refs_cross_rpc_and_are_part_of_frozen_request_identity():
    context = TrustedContext("fixture", "actor", None, None, (), (), 1, OriginKind.ACTOR)
    base = ModelRequest("intent", "behavior", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)), (), execution_timeout_seconds=30)
    old_wire = wire(base)
    for key in ("behavior_refs", "behavior_purpose", "data_generation"):
        old_wire.pop(key)
    assert submission_digest(request_from_wire(old_wire)) == submission_digest(base)

    original = replace(base, behavior_refs=({"rule_id": "rule", "revision": 3},),
                       behavior_purpose="reply", data_generation=str(uuid4()))
    restored = request_from_wire(wire(original))
    assert restored.behavior_refs == original.behavior_refs
    assert restored.behavior_purpose == original.behavior_purpose
    assert restored.data_generation == original.data_generation
    assert submission_digest(restored) == submission_digest(original)
    assert submission_digest(original) != submission_digest(replace(original, behavior_refs=()))
    with pytest.raises(AppError, match="invalid_model_request"):
        request_from_wire(wire(replace(original, behavior_purpose=None)))
    with pytest.raises(AppError, match="behavior_validation_not_bound"):
        await ModelBroker(None, None, None).recheck_behaviors(original)


@pytest.mark.parametrize("invalidated", ["behavior", "generation"])
async def test_provider_dispatch_rechecks_behavior_and_generation_after_admission(rb_database, invalidated):
    checks = 0

    async def validate(_context, _refs, *, purpose):
        nonlocal checks
        checks += 1
        if checks == 2 and invalidated == "generation":
            async with rb_database.transaction() as conn:
                await conn.execute("UPDATE runtime.installation_generation SET id=%s WHERE singleton", (str(uuid4()),))
        return checks != 2 or invalidated != "behavior"

    validator = AsyncMock(side_effect=validate)
    broker = ModelBroker(rb_database, None, None, validate_behaviors=validator)
    broker.ready = True
    broker.scheduler = QuotaScheduler({"account": QuotaPolicy("account", 1, 0, 1)})
    await broker.scheduler.start()
    post = AsyncMock()
    broker.session = SimpleNamespace(post=post, close=AsyncMock())
    selected = RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "account",
                         frozenset({"text"}), {})
    broker.catalog.resolve = AsyncMock(return_value=selected)
    broker.compiler.compile = lambda *_args, **_kwargs: SimpleNamespace(
        url="http://provider.invalid/", body={}, headers={})
    context = TrustedContext("fixture", "actor", None, None, (), (), 1, OriginKind.ACTOR)
    original = ModelRequest("intent", "recheck", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)), (), execution_timeout_seconds=10,
        behavior_refs=({"rule_id": "rule", "revision": 3},), behavior_purpose="reply")
    try:
        await broker.submit(original)

        async def settled():
            while True:
                state = await broker.status(original.attempt_id)
                if state.status == "failed":
                    return state
                await asyncio.sleep(0.01)

        result = await asyncio.wait_for(settled(), 5)
        assert result.dispatch == "not_started"
        assert result.error_code == ("behavior_dependency_invalid" if invalidated == "behavior"
                                     else "data_generation_expired")
        assert validator.await_count == 2
        validator.assert_awaited_with(context, original.behavior_refs, purpose="reply")
        post.assert_not_called()
    finally:
        await broker.close()
