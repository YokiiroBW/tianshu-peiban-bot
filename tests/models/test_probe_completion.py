"""Capability evidence requires a public response and a complete termination."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
from dataclasses import replace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from psycopg.types.json import Jsonb
import pytest

from tianshu.models.management import register_routes
from tianshu.models import probes
from tianshu.models.types import ModelCompletion, ModelEvent, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.api import TRUSTED_CONTEXT, error_middleware
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.config import Settings, Secrets
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, TrustedContext


@pytest.mark.asyncio
@pytest.mark.parametrize("complete,public_text,verified", [(True, True, True), (False, True, False), (True, False, False)])
async def test_stream_probe_reserves_reasoning_headroom_and_only_registers_complete_public_output(database, tmp_path, complete, public_text, verified):
    context = TrustedContext("operator", None, None, None, (), (), 1, OriginKind.HUMAN, frozenset({"models.manage"}))
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) VALUES ('provider','Fixture','https://fixture.invalid','env:FIXTURE_KEY','openai.chat')")
        await connection.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) VALUES ('quota',2,1,1)")
        await connection.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group) VALUES ('route','provider','high-reasoning-fixture','quota')")
    submitted, actual = [], None

    async def stream(request):
        nonlocal actual
        submitted.append(request)
        receipt = ModelReceipt(request.attempt_id, request.intent_id, "succeeded", "submitted", "route", 1,
                               "provider", "high-reasoning-fixture", "openai.chat", "quota")
        actual = ModelResult((ModelPart("text", text="OK"),) if public_text else (), receipt,
                             completion=ModelCompletion("complete" if complete else "incomplete", ("stop" if complete else "length",)))
        if public_text:
            yield ModelEvent("text", text="OK")
        yield ModelEvent("receipt", receipt=receipt)

    async def result(_attempt, *, context):
        return actual

    models = SimpleNamespace(stream=stream, result=result, generation_profile=AsyncMock(return_value={
        "protocol": "openai.chat", "output_parameters": {"max_tokens": 8192}}))
    services = SimpleNamespace(database=database, models=models, commands=CommandStore(database),
        settings=Settings({}, Secrets({}), tmp_path))

    @web.middleware
    async def author(request, handler):
        request[TRUSTED_CONTEXT] = context
        return await handler(request)

    app = web.Application(middlewares=[error_middleware, author])
    register_routes(app, services)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/v1/models/routes/route/probe", json={
            "client_request_id": "same-probe", "expected_revision": 1, "capability": "stream"})
        body = await response.json()
    assert response.status == 200
    assert (body["data"]["verification"] == "provider_test") is verified
    assert submitted[0].max_output_tokens == submitted[0].parameters["max_tokens"] == 8192
    assert submitted[0].budget.remaining() > 170
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT verified_capabilities FROM models.routes WHERE route_id='route'")).fetchone()
    assert ("stream" in row["verified_capabilities"]) is verified


@asynccontextmanager
async def capability_client(database, tmp_path, models):
    context = TrustedContext("operator", None, None, None, (), (), 1, OriginKind.HUMAN, frozenset({"models.manage"}))
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES('provider','Fixture','https://fixture.invalid','env:FIXTURE_KEY','openai.chat')")
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) VALUES('quota',2,1,1)")
        await conn.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group,verification) "
            "VALUES('route','provider','fixture','quota',%s)", (Jsonb({"context_window_tokens": 200000, "note": "original evidence"}),))
    services = SimpleNamespace(database=database, models=models, commands=CommandStore(database),
        settings=Settings({}, Secrets({}), tmp_path))

    @web.middleware
    async def author(request, handler):
        request[TRUSTED_CONTEXT] = context
        return await handler(request)

    app = web.Application(middlewares=[error_middleware, author])
    register_routes(app, services)
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_tool_probe_persists_both_receipts_and_preserves_window_evidence(database, tmp_path):
    saved, requests = {}, []

    async def read(attempt, *, context):
        return saved.get(attempt)

    async def generate(request):
        requests.append(request)
        receipt = ModelReceipt(request.attempt_id, request.intent_id, "succeeded", "submitted", "route", 1,
            "provider", "fixture", "openai.chat", "quota")
        if len(requests) == 1:
            _, expected = probes.prepare(request, "tools")
            parts = (ModelPart("text", text="", native={"thought": True, "reasoning_content": "private"}),
                     ModelPart("tool_call", call_id="call", name="tianshu_probe_echo", arguments={"value": expected}))
        else:
            assert request.messages[-2].native == {"reasoning_content": "private"}
            parts = (ModelPart("text", text=request.messages[-1].parts[0].result["proof"]),)
        result = ModelResult(parts, receipt, completion=ModelCompletion("complete"))
        saved[request.attempt_id] = result
        return result

    models = SimpleNamespace(result=read, generate=generate, generation_profile=AsyncMock(return_value={
        "protocol": "openai.chat", "output_parameters": {"max_tokens": 8192}}))
    body = {"client_request_id": "tool-probe", "expected_revision": 1, "capability": "tools"}
    async with capability_client(database, tmp_path, models) as client:
        response = await client.post("/api/v1/models/routes/route/probe", json=body)
        answer = await response.json()
        assert response.status == 200 and answer["data"]["verification"] == "provider_test"
        assert len(answer["data"]["receipts"]) == 2
        replay = await client.post("/api/v1/models/routes/route/probe", json=body)
        assert replay.status == 200
    assert len(requests) == 2
    async with database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT verified_capabilities,verification,revision FROM models.routes WHERE route_id='route'")).fetchone()
    assert set(row["verified_capabilities"]) == {"text", "tools"} and row["revision"] == 2
    assert row["verification"]["context_window_tokens"] == 200000
    assert row["verification"]["note"] == "original evidence"
    assert row["verification"]["capabilities"]["tools"]["attempt_ids"] == list(saved)


@pytest.mark.parametrize("status", ["unknown", "failed"])
async def test_probe_failure_keeps_route_and_original_diagnostic(database, tmp_path, status):
    original = None

    async def read(attempt, *, context):
        return original

    async def generate(request):
        nonlocal original
        receipt = ModelReceipt(request.attempt_id, request.intent_id, status, "unknown" if status == "unknown" else "submitted",
            "route", 1, "provider", "fixture", "openai.chat", "quota")
        original = ModelResult((), replace(receipt, error_code="upstream_http_429",
            diagnostic={"upstream": {"status": 429, "category": "rate_limit"}}))
        raise AppError("upstream_http_429", status=502)

    models = SimpleNamespace(result=read, generate=generate, generation_profile=AsyncMock(return_value={
        "protocol": "openai.chat", "output_parameters": {"max_tokens": 8192}}))
    async with capability_client(database, tmp_path, models) as client:
        response = await client.post("/api/v1/models/routes/route/probe", json={
            "client_request_id": "failed-probe", "expected_revision": 1, "capability": "text"})
        data = (await response.json())["data"]
    assert response.status == 200 and data["verification"] == "not_verified"
    assert data["receipt"]["status"] == status and data["receipt"]["diagnostic"]["upstream"]["category"] == "rate_limit"
    async with database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT revision,verified_capabilities,verification FROM models.routes WHERE route_id='route'")).fetchone()
    assert row["revision"] == 1 and row["verified_capabilities"] == []
    assert row["verification"]["context_window_tokens"] == 200000
