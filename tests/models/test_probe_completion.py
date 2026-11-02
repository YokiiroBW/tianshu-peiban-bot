"""Capability evidence requires a public response and a complete termination."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from tianshu.models.management import register_routes
from tianshu.models.types import ModelCompletion, ModelEvent, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.api import TRUSTED_CONTEXT, error_middleware
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.config import Settings, Secrets
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
