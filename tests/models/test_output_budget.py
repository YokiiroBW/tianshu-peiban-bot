"""The compiled native call, attempt estimate and profile share one output limit."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.models.broker import ModelBroker
from tianshu.models.budgets import effective_output_tokens
from tianshu.models.catalog import ModelCatalog, RoutePlan
from tianshu.models.protocols import NativeCompiler
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.config import Secrets
from tianshu.runtime.errors import AppError
from tianshu.runtime.files import FileStore
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def request(**changes):
    return replace(ModelRequest("intent", "original", "life.plan", ServiceClass.ELASTIC,
        TrustedContext("author", "actor", None, None, ("scope",), ("scope",), 1, OriginKind.ACTOR,
                       frozenset({"life.manage"})),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=2)),
        (ModelMessage("user", (ModelPart("text", text="synthetic prompt"),)),), estimated_input_tokens=123), **changes)


def plan(protocol, parameters):
    return RoutePlan("route", 1, "provider", "configured-model", protocol, "https://fixture.invalid/v1", "FIXTURE_KEY", "quota",
                     frozenset({"text", "stream"}), parameters)


@pytest.mark.parametrize("protocol,parameters,native,output,key", [
    ("openai.chat", {"max_tokens": 8192}, None, 8192, "max_tokens"),
    ("openai.chat", {"max_completion_tokens": 16384}, None, 16384, "max_completion_tokens"),
    ("openai.responses", {"max_output_tokens": 32768}, None, 32768, "max_output_tokens"),
    ("anthropic.messages", {"max_tokens": 8192}, None, 8192, "max_tokens"),
    ("gemini.generate", {"generationConfig": {"maxOutputTokens": 32768}}, None, 32768, "maxOutputTokens"),
    ("openai.chat", {}, {"messages": [], "max_completion_tokens": 4096}, 4096, "max_completion_tokens"),
])
def test_native_output_parameter_cannot_bypass_the_effective_budget(tmp_path, protocol, parameters, native, output, key):
    selected, model_request = plan(protocol, parameters), request(native_payload=native)
    call = NativeCompiler(FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"})).compile(selected, model_request, stream=False)
    body = call.body["generationConfig"] if protocol == "gemini.generate" else call.body
    assert effective_output_tokens(selected, model_request) == body[key] == output
    if key == "max_completion_tokens":
        assert "max_tokens" not in body


def test_explicit_request_limit_overrides_the_route_limit_without_losing_other_native_options(tmp_path):
    selected = plan("gemini.generate", {"generationConfig": {"maxOutputTokens": 8192, "thinkingConfig": {"thinkingLevel": "high"}}})
    model_request = request(max_output_tokens=32768, parameters={"generationConfig": {"maxOutputTokens": 32768}})
    call = NativeCompiler(FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"})).compile(selected, model_request, stream=True)
    assert call.body["generationConfig"] == {"maxOutputTokens": 32768, "thinkingConfig": {"thinkingLevel": "high"}}
    selected = plan("openai.chat", {"max_tokens": 4096, "reasoning_effort": "high"})
    call = NativeCompiler(FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"})).compile(selected,
        request(max_output_tokens=16384, parameters={"max_completion_tokens": 16384}), stream=True)
    assert call.body["max_completion_tokens"] == 16384 and "max_tokens" not in call.body
    assert call.body["reasoning_effort"] == "high"
    with pytest.raises(AppError, match="conflicting_output_token_budget"):
        effective_output_tokens(plan("openai.chat", {}), request(parameters={"max_tokens": 2, "max_completion_tokens": 3}))


def compiled_broker(tmp_path, selected, native):
    sent = []

    async def chunks(_size):
        yield json.dumps(native).encode()

    @asynccontextmanager
    async def post(_url, **kwargs):
        sent.append(kwargs["json"])
        yield SimpleNamespace(status=200, content=SimpleNamespace(iter_chunked=chunks))

    row = {"attempt_id": "original", "intent_id": "intent", "status": "queued", "dispatch": "submitted",
           "route_id": "route", "route_revision": 1, "provider_id": "provider", "model": "configured-model", "protocol": selected.protocol,
           "quota_group": "quota", "provider_handle": None, "queued_at": None, "started_at": None,
           "finished_at": None, "first_output_ms": None, "error_code": None, "hold_until": None, "result": None}

    async def finish(_attempt, status, **values):
        row.update(values, status=status)
        row["usage"] = wire(row.get("usage") or {})
        return dict(row)

    broker = ModelBroker.__new__(ModelBroker)
    broker.ready, broker.credential_store = True, None
    broker.prepare_pending, broker.prepare_slots, broker.active = 0, asyncio.Semaphore(1), {}
    broker.recheck, broker.event = AsyncMock(), AsyncMock()
    broker.prepare_media = AsyncMock(side_effect=lambda value: value)
    broker.catalog = SimpleNamespace(resolve=AsyncMock(return_value=selected))
    broker.compiler = NativeCompiler(FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    broker.ledger = SimpleNamespace(create=AsyncMock(return_value=(dict(row), True)), started=AsyncMock(),
        checkpoint=AsyncMock(), finish=AsyncMock(side_effect=finish))
    broker.scheduler = SimpleNamespace(acquire=AsyncMock(), submitted=AsyncMock(), release=AsyncMock(),
        policies={"quota": SimpleNamespace(unknown_hold_seconds=300)})
    broker.session = SimpleNamespace(post=post)
    return broker, sent


@pytest.mark.asyncio
async def test_broker_admits_and_meters_the_exact_compiled_output_limit(tmp_path):
    selected = plan("openai.chat", {"max_tokens": 8192})
    native = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]}
    broker, sent = compiled_broker(tmp_path, selected, native)
    result = await broker.generate(request())
    assert result.receipt.status == "succeeded" and sent[0]["max_tokens"] == 8192
    admitted_request, _route, units = broker.ledger.create.call_args.args
    assert admitted_request.max_output_tokens == 8192 and units == {"tokens": 8315}
    assert broker.scheduler.acquire.call_args.args[2] == units


@pytest.mark.asyncio
async def test_profile_is_read_only_and_frozen_binding_cannot_drift_or_select_another_route(database):
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) VALUES ('provider','Fixture','https://fixture.invalid','FIXTURE_KEY','openai.chat')")
        await connection.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) VALUES ('quota',2,1,1)")
        for route in ("route", "other-route"):
            await connection.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group,verified_capabilities) VALUES (%s,'provider','configured-model','quota',ARRAY['text','stream'])", (route,))
        await connection.execute("INSERT INTO models.bindings(function,service_class,actor_id,route_id) VALUES ('life.plan','elastic','actor','route')")
    broker = ModelBroker.__new__(ModelBroker)
    broker.catalog, broker.recheck = ModelCatalog(database), AsyncMock()
    profile = await broker.generation_profile(request())
    frozen = request(route_id=profile["route_id"], route_revision=profile["route_revision"], protocol=profile["protocol"])
    assert (await broker.catalog.resolve(frozen)).route_id == "route"
    async with database.transaction() as connection:
        assert (await (await connection.execute("SELECT count(*) AS n FROM models.attempts")).fetchone())["n"] == 0
        await connection.execute("UPDATE models.bindings SET route_id='other-route'")
    with pytest.raises(AppError, match="model_route_binding_changed"):
        await broker.catalog.resolve(frozen)
    with pytest.raises(AppError, match="model_route_forbidden"):
        await broker.catalog.resolve(request(route_id="other-route"))
    async with database.transaction() as connection:
        await connection.execute("UPDATE models.bindings SET route_id='route'")
        await connection.execute("UPDATE models.routes SET revision=2 WHERE route_id='route'")
    with pytest.raises(AppError, match="model_route_revision_conflict"):
        await broker.catalog.resolve(frozen)
