"""The real OpenCode transport contract stays scoped, stable and outside native bodies."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import re

import pytest

from tianshu.models.catalog import RoutePlan
from tianshu.models.codec import request_from_wire
from tianshu.models.discovery import native_request
from tianshu.models.protocols import NativeCompiler
from tianshu.models.transport import provider_headers
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.config import Secrets
from tianshu.runtime.files import FileStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def context():
    return TrustedContext(
        "private-principal",
        "private-actor",
        "private-person",
        "private-conversation",
        ("private-scope",),
        ("private-scope",),
        1,
        OriginKind.HUMAN,
    )


@pytest.mark.parametrize(
    "base", ["https://opencode.ai/zen/v1", "https://opencode.ai:443/zen/go/v1/"]
)
@pytest.mark.parametrize("native,stream", [(False, False), (True, False), (True, True)])
def test_generation_and_native_catalogue_share_fixed_scoped_headers(tmp_path, base, native, stream):
    selected = context()
    payload = {"messages": [{"role": "user", "content": "fixture"}], "reasoning_effort": "high"}
    request = ModelRequest(
        "original-intent",
        "original-attempt",
        "chat",
        ServiceClass.INTERACTIVE,
        selected,
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
        (ModelMessage("user", (ModelPart("text", text="fixture"),)),),
        native_payload=payload if native else None,
        provider_session_scope="conversation",
    )
    plan = RoutePlan(
        "route",
        1,
        "provider",
        "native-model",
        "openai.chat",
        base,
        "FIXTURE_KEY",
        "quota",
        frozenset({"text", "stream"}),
        {},
    )
    call = NativeCompiler(FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"})).compile(
        plan,
        request,
        stream=stream,
    )
    url, headers, parameters = native_request(
        {"base_url": base, "protocol": "openai.chat"},
        "synthetic",
        context=selected,
        intent_id="different-catalogue-intent",
    )
    assert url == base.rstrip("/") + "/models" and parameters == {}
    assert call.headers["User-Agent"] == headers["User-Agent"] == "tianshu-model-gateway/0.1.0"
    session = call.headers["x-opencode-session"]
    assert session != headers["x-opencode-session"] and re.fullmatch(
        r"tianshu-[0-9a-f]{64}", session
    )
    assert headers["x-opencode-session"] == provider_headers(base, selected,
        "different-catalogue-intent", session_scope="intent", function="models.providers.models")["x-opencode-session"]
    assert all(
        value not in session
        for value in (selected.principal_id, selected.actor_id, selected.conversation_id)
    )
    assert call.headers["Authorization"] == headers["Authorization"] == "Bearer synthetic"
    continued = NativeCompiler(FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"})).compile(
        plan, replace(request, attempt_id="next-attempt", intent_id="next-turn"), stream=stream
    )
    assert continued.headers["x-opencode-session"] == session
    if native:
        assert call.body["messages"] == payload["messages"]
        assert call.body["reasoning_effort"] == "high"
    assert "x-opencode-session" not in call.body and "User-Agent" not in call.body


def test_session_identity_is_stable_for_continuation_and_distinct_for_other_scenes():
    selected = context()
    base = "https://opencode.ai/zen/go/v1"
    original = provider_headers(base, selected, "first")
    assert provider_headers(base, selected, "next-turn") == original
    for other in (
        replace(selected, principal_id="another-principal"),
        replace(selected, actor_id="another-actor"),
        replace(selected, conversation_id="another-conversation"),
    ):
        assert provider_headers(base, other, "first") != original
    background = replace(selected, conversation_id=None)
    _, catalogue_headers, _ = native_request(
        {"base_url": base, "protocol": "openai.chat"},
        "synthetic",
        context=background,
        intent_id="same-intent",
    )
    assert (
        provider_headers(base, background, "same-intent", session_scope="intent", function="models.providers.models")["x-opencode-session"]
        == catalogue_headers["x-opencode-session"]
    )
    assert provider_headers(base, background, "another-intent") != provider_headers(
        base, background, "same-intent"
    )


def test_new_intents_isolate_actor_scene_function_and_generation_without_changing_authority(tmp_path):
    selected, base = context(), "https://opencode.ai/zen/go/v1"
    original = ModelRequest("diary:day-a", "attempt-a", "reflect", ServiceClass.ELASTIC, selected,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=30)),
        (ModelMessage("user", (ModelPart("text", text="only this day"),)),))
    assert original.provider_session_scope == "intent"
    compiler = NativeCompiler(FileStore(tmp_path), Secrets({"KEY": "synthetic"}))
    plan = RoutePlan("route", 1, "provider", "model", "openai.chat", base, "KEY", "quota", frozenset({"text"}), {})
    session = compiler.compile(plan, original, stream=False).headers["x-opencode-session"]
    # Same frozen logical task remains stable on observation/transport recovery.
    assert compiler.compile(plan, replace(original, attempt_id="transport-attempt"), stream=True).headers["x-opencode-session"] == session
    for other in (
        replace(original, intent_id="diary:day-b"),
        replace(original, function="memory.extract"),
        replace(original, context=replace(selected, actor_id="another-actor")),
        replace(original, context=replace(selected, conversation_id="another-group")),
        replace(original, context=replace(selected, conversation_id="private-chat", conversation_kind="private")),
    ):
        assert compiler.compile(plan, other, stream=False).headers["x-opencode-session"] != session
    assert original.context is selected
    old = wire(original)
    old.pop("provider_session_scope")
    restored = request_from_wire(old)
    assert restored.provider_session_scope == "conversation"
    assert restored.context == original.context
    assert compiler.compile(plan, restored, stream=False).headers["x-opencode-session"] == provider_headers(base, selected, original.intent_id)["x-opencode-session"]


@pytest.mark.parametrize("field", ["previous_response_id", "conversation", "conversation_id", "thread_id", "session_id", "cachedContent"])
@pytest.mark.parametrize("location", ["route", "parameters", "native_payload"])
def test_independent_requests_cannot_inherit_provider_state_from_native_or_route_options(tmp_path, field, location):
    original = ModelRequest("new-work", "new-attempt", "reflect", ServiceClass.ELASTIC, context(),
        RequestBudget(datetime.now(UTC)+timedelta(seconds=30)), ())
    plan = RoutePlan("route", 1, "provider", "model", "openai.responses", "https://provider.example/v1",
        "KEY", "quota", frozenset({"text"}), {field: "previous-context"} if location == "route" else {})
    if location == "parameters":
        original = replace(original, parameters={field: "previous-context"})
    elif location == "native_payload":
        original = replace(original, native_payload={field: "previous-context", "input": []})
    with pytest.raises(AppError, match="independent_session_state_reference"):
        NativeCompiler(FileStore(tmp_path), Secrets({"KEY": "synthetic"})).compile(plan, original, stream=False)


def test_invalid_session_scope_is_rejected_at_the_frozen_request_boundary():
    original = ModelRequest("work", "attempt", "reflect", ServiceClass.ELASTIC, context(),
        RequestBudget(datetime.now(UTC)+timedelta(seconds=30)), ())
    with pytest.raises(AppError, match="invalid_provider_session_scope"):
        request_from_wire({**wire(original), "provider_session_scope": "shared-everything"})


@pytest.mark.parametrize(
    "base",
    [
        "http://opencode.ai/zen/v1",
        "https://opencode.ai:444/zen/v1",
        "https://opencode.ai.example/zen/v1",
        "https://opencode.ai./zen/v1",
        "https://user@opencode.ai/zen/v1",
        "https://opencode.ai/zen/v1?other=1",
        "https://opencode.ai/zen/v1#fragment",
        "https://opencode.ai/zen/v10",
        "https://opencode.ai/zen/go/v1/other",
        "https://another.example/zen/v1",
    ],
)
def test_opencode_headers_never_apply_to_another_target(base):
    assert provider_headers(base, context(), "intent") == {}
