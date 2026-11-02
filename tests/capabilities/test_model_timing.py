"""Caller timing contracts retain real expiry without charging queue time."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.dto import ReactionIntent
from tianshu.capabilities.execution_context import CURRENT_ACTION
from tianshu.capabilities.external import request as external_request
from tianshu.capabilities.perception.service import Perception
from tianshu.capabilities.plugins.service import Plugins
from tianshu.capabilities.reactions.service import Reactions
from tianshu.capabilities.resources.service import Resources
from tianshu.capabilities.weather.service import WeatherService
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.types import Action, Attempt, MediaRef, OriginKind, Receipt, RequestBudget, ServiceClass, TrustedContext


def context():
    return TrustedContext("owner", "actor", None, None, ("private",), ("private",),
        1, OriginKind.ACTOR, frozenset({"*"}))


def model_result(text="Actual media observation", native=None):
    return ModelResult((ModelPart("text", text=text),), ModelReceipt("attempt", "intent", "succeeded", "submitted",
        "route", 1, "provider", "model", "openai.chat", "quota"),
        native_response=native or {}, completion=ModelCompletion("complete", ("stop",)))


@pytest.mark.asyncio
@pytest.mark.parametrize("business_cap", [False, True])
async def test_direct_perception_and_video_use_execution_seconds_and_only_the_actual_action_expiry(business_cap):
    deadline = datetime.now(UTC)+timedelta(hours=1) if business_cap else None
    broker = SimpleNamespace(generate=AsyncMock(return_value=model_result()))
    owner = Perception(broker, SimpleNamespace(resolve=AsyncMock(return_value=(b"image", "image/png"))),
        SimpleNamespace(check=AsyncMock()), None)
    preparation = RequestBudget(datetime.now(UTC)-timedelta(seconds=1))
    await owner.describe(context(), MediaRef("fixture", "controlled", "image/png"), question="Inspect actual input",
        budget=preparation, business_deadline_at=deadline)
    described = broker.generate.await_args.args[0]
    assert described.execution_timeout_seconds == 180 and described.business_deadline_at == deadline
    assert described.budget is preparation and described.max_queue_wait is None
    action = Action("action", "intent", "video.observe", "actor", context(), {}, OriginKind.ACTOR, deadline_at=deadline)
    video = owner._video_request(action, Attempt("attempt", "action"), (ModelPart("text", text="actual interval"),), ())
    assert video.execution_timeout_seconds == 600 and video.business_deadline_at == deadline and video.max_queue_wait is None


@pytest.mark.asyncio
async def test_reaction_embedding_and_selection_share_only_the_explicit_business_cap():
    deadline = datetime.now(UTC)+timedelta(hours=1)
    broker = SimpleNamespace(generate=AsyncMock(return_value=model_result(native={"data": [{"embedding": [0.1]*1024}]})),
        embedding_profile=AsyncMock(return_value={"dimensions": 1024, "route_id": "route", "route_revision": 1}))
    candidate = {"id": "reaction", "revision": 1, "body": {"media": {"media_id": "artifact:reaction"},
        "description": "A friendly greeting", "ocr": "hello", "purposes": ["greeting"]}}
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=[candidate]))))

    @asynccontextmanager
    async def transaction(**_kwargs):
        yield connection

    configuration = SimpleNamespace(database=SimpleNamespace(transaction=transaction), list=AsyncMock(return_value=[
        {"id": "pack", "scope_ids": ["private"], "actor_ids": ["actor"]}]))
    owner = Reactions(configuration, SimpleNamespace(reference=AsyncMock(return_value=SimpleNamespace(source_refs=()))),
        broker, None, SimpleNamespace(check=AsyncMock()))
    await owner.embed(context(), "hello", business_deadline_at=deadline)
    embedded = broker.generate.await_args.args[0]
    assert embedded.execution_timeout_seconds == 120 and embedded.business_deadline_at == deadline
    owner.embed = AsyncMock(return_value=([0.1], "profile", None))
    broker.generate.return_value = model_result('{"id":null}')
    selected = await owner.search(context(), ReactionIntent("hello", "greeting", "warm", .2, "actor", ("private",)),
        business_deadline_at=deadline)
    assert selected["selected"] is None
    assert owner.embed.await_args.kwargs["business_deadline_at"] == deadline
    request = broker.generate.await_args.args[0]
    assert request.execution_timeout_seconds == 30 and request.business_deadline_at == deadline and request.max_queue_wait is None


@pytest.mark.asyncio
@pytest.mark.parametrize("business_cap", [False, True])
async def test_plugin_llm_port_and_external_factory_keep_queues_separate_from_provider_execution(business_cap):
    deadline = datetime.now(UTC)+timedelta(hours=1) if business_cap else None
    action = Action("action", "intent", "plugin:fixture", "actor", context(), {}, OriginKind.ACTOR, deadline_at=deadline)
    attempt = Attempt("attempt", action.action_id)
    owner = Plugins.__new__(Plugins)
    owner.actions = SimpleNamespace(_load=AsyncMock(return_value=(action, attempt, None)),
        execution_class=AsyncMock(return_value=ServiceClass.ELASTIC))
    owner.authority = SimpleNamespace(check=AsyncMock())
    owner.models = SimpleNamespace(generate=AsyncMock(return_value=model_result()))
    await owner.host_port({"call": action.action_id}, "models.generate", "generate", {
        "request_key": "original", "function": "reflect", "messages": [{"role": "user", "parts": [{"kind": "text", "text": "actual input"}]}]})
    request = owner.models.generate.await_args.args[0]
    assert request.attempt_id == "attempt:original" and request.execution_timeout_seconds == 180
    assert request.business_deadline_at == deadline and request.max_queue_wait is None
    resource = external_request(action, attempt, {"quota_group": "shared", "provider_id": "fixture"}, "images.generate")
    assert resource.execution_timeout_seconds == 600 and resource.business_deadline_at == deadline
    assert resource.max_queue_wait is None


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", ["none", "same_scene", "different_scene"])
async def test_weather_and_asset_reads_remove_synthetic_expiry_but_inherit_real_same_scene_business_cap(parent):
    current = context()
    deadline = datetime.now(UTC)+timedelta(hours=1)
    parent_action = Action("parent", "parent", "images.generate", "actor", current, {}, OriginKind.ACTOR, deadline_at=deadline)
    if parent == "different_scene":
        parent_action = replace(parent_action, context=replace(current, conversation_id="another-conversation"))
    token = CURRENT_ACTION.set(None if parent == "none" else parent_action)
    actions = SimpleNamespace(invoke=AsyncMock(return_value=Receipt("receipt", "action", "attempt", "succeeded", "none", "not_requested")))
    weather = WeatherService.__new__(WeatherService)
    weather.actions = actions
    weather.configuration = SimpleNamespace(get=AsyncMock(return_value={"revision": 1}))
    weather._original_read_pending = AsyncMock()
    resources = Resources.__new__(Resources)
    resources.actions = actions
    try:
        await weather._read(current, "search", {"query": "synthetic city"})
        await resources.read(current, "libraries", {"connection_id": "fixture"})
    finally:
        CURRENT_ACTION.reset(token)
    expected = deadline if parent == "same_scene" else None
    assert len(actions.invoke.await_args_list) == 2
    assert all(call.args[0].deadline_at == expected for call in actions.invoke.await_args_list)
