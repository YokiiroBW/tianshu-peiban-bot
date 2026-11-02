from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from tianshu.capabilities.catalog import CapabilityDefinition
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.life import planner
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage, OriginKind, RequestBudget, TrustedContext


class Identity:
    async def check(self, *_args):
        pass

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)


def context():
    return TrustedContext(
        "author",
        "actor",
        None,
        None,
        ("actor-scope",),
        ("actor-scope",),
        1,
        OriginKind.ACTOR,
        frozenset({"*"}),
    )


def choice(name, schema=None, *, available=True, exposed=True, command=False, administrative=False):
    definition = CapabilityDefinition(
        name,
        name,
        "actual operation",
        schema
        or {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        {"type": "object"},
        model_tool={"name": name.replace(".", "_")} if exposed and not command else None,
        public_command={"name": name} if command else None,
        permissions=frozenset({"config.publish"}) if administrative else frozenset(),
    )
    return {"definition": definition, "can_execute": available}


def data(choices):
    return planner.material(
        context(),
        {"affect": {"regulation": {}}, "outfit": None},
        {"timezone": "UTC", "wake_at": "07:00", "sleep_at": "23:00", "idle_seconds": 5400},
        Page((), None, Coverage(True)),
        Page((), None, Coverage(True)),
        choices,
        datetime.now(UTC).isoformat(),
    )


async def make_request(choices=(), *, persona="authored persona"):
    return await planner.request(
        ContextAssembler(Identity(), None, None),
        context(),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=2)),
        key="fixture-plan",
        persona=persona,
        data=data(choices),
        refs=(),
    )


def result(text):
    return ModelResult(
        (ModelPart("text", text=text),),
        ModelReceipt(
            "attempt",
            "intent",
            "succeeded",
            "submitted",
            "route",
            1,
            "provider",
            "model",
            "native",
            "quota",
        ),
        completion=ModelCompletion("complete", ("stop",)),
    )


def activity(**changes):
    value = {
        "goal_changes":[],"arrangements":[],"cancel_activity_ids":[],"free_time_reason":"Leave genuine free time",
        "activity": {
            "title": "Read the selected page",
            "kind": "reading",
            "goal_id": None,
            "capability_id": "content.read",
            "arguments": {"url": "https://example.test/page"},
            "duration_minutes": 30,
        },
        "reason": "A feasible sourced interest",
    }
    value["activity"].update(changes)
    return json.dumps(value)


@pytest.mark.asyncio
async def test_large_admin_catalog_cannot_remove_the_application_contract():
    choices = [choice("content.read")]
    choices.extend(
        choice(
            f"configuration.{index}.save",
            {
                "type": "object",
                "description": "irrelevant admin material " * 2000,
            },
            administrative=True,
        )
        for index in range(200)
    )
    request = await make_request(choices)
    assert planner.TASK in request.system
    assert "authored persona" in request.system
    frozen = planner.frozen_material(request)
    assert [row["id"] for row in frozen["capabilities"]] == ["content.read"]
    assert "configuration." not in json.dumps(frozen)
    assert request.native_payload is None and request.parameters == {}
    assert (
        planner.decision(result(activity()), request)[0]["activity"]["capability_id"]
        == "content.read"
    )


@pytest.mark.asyncio
async def test_business_plugin_weather_and_public_commands_remain_available_to_planning():
    request = await make_request(
        [
            choice("plugin.trailbook.observe"),
            choice("weather.current"),
            choice("plugin.journal.compose", command=True),
            choice("plugin.disabled.observe", available=False),
            choice("internal.undeclared", exposed=False),
            choice("plugin.trailbook.settings.save", administrative=True),
        ]
    )
    frozen = planner.frozen_material(request)
    assert [row["id"] for row in frozen["capabilities"]] == [
        "plugin.trailbook.observe",
        "weather.current",
        "plugin.journal.compose",
    ]
    decision, _ = planner.decision(
        result(activity(capability_id="plugin.trailbook.observe")), request
    )
    assert decision["activity"]["capability_id"] == "plugin.trailbook.observe"


@pytest.mark.asyncio
async def test_budget_reduction_keeps_contract_and_explains_omitted_capability():
    large = choice(
        "video.observe", {"type": "object", "description": "large actual schema " * 10000}
    )
    request = await make_request([large, choice("content.read")])
    frozen = planner.frozen_material(request)
    assert planner.TASK in request.system
    assert frozen["coverage"]["omitted_capabilities"] == ["video.observe"]
    assert [row["id"] for row in frozen["capabilities"]] == ["content.read"]
    assert request.estimated_input_tokens + planner.RESERVED_OUTPUT_TOKENS <= planner.INPUT_TOKENS
    assert planner.reduced(frozen)


@pytest.mark.asyncio
async def test_persona_cannot_fit_returns_explicit_budget_failure():
    with pytest.raises(AppError, match="planner_context_budget_exceeded"):
        await make_request(persona="authored character " * 30000)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Perhaps reading would be pleasant today.",
        "{}",
        '```json\n{"activity":null,"reason":"rest"}\n```',
        '{"activity":null,"activity":null,"reason":"rest"}',
        '{"activity":null,"reason":"   "}',
    ],
)
async def test_invalid_successful_provider_text_is_not_a_no_activity_decision(text):
    request = await make_request()
    with pytest.raises(AppError, match="planner_output_invalid"):
        planner.decision(result(text), request)


@pytest.mark.asyncio
async def test_null_is_legitimate_and_action_arguments_are_strict():
    request = await make_request([choice("content.read")])
    assert (
        planner.decision(result(json.dumps({"activity":None,"reason":"No feasible activity now","goal_changes":[],"arrangements":[],"cancel_activity_ids":[],"free_time_reason":"Rest"})), request)[
            0
        ]["activity"]
        is None
    )
    with pytest.raises(AppError, match="planner_arguments_invalid"):
        planner.decision(result(activity(arguments={})), request)
    with pytest.raises(AppError, match="planner_capability_unavailable"):
        planner.decision(result(activity(capability_id="configuration.delete")), request)
    with pytest.raises(AppError, match="planner_output_invalid"):
        planner.decision(
            result(activity(capability_id=None, kind="reading", arguments={})), request
        )
    with pytest.raises(AppError, match="planner_original_contract_missing"):
        planner.decision(
            result(activity()), replace(request, system="pre-upgrade application rules")
        )


def test_style_seconds_and_hidden_events_do_not_change_decision_material():
    from copy import deepcopy
    from tianshu.life.planning_engine import decision_fingerprint
    original=data([choice('content.read')])
    changed=deepcopy(original)
    changed['local_time']='2026-11-02T23:58:59+00:00'
    changed['observed_at']='2026-11-02T23:58:59+00:00'
    changed['growth_watermark']='style-only-new-revision'
    changed['affect_regulation']={'intensity':.635,'expression_direction':'another style'}
    actor={'persona_id':'persona','persona_revision':3}
    expected=decision_fingerprint(context(),actor,original,'2026-11-02')
    assert decision_fingerprint(context(),actor,changed,'2026-11-02',
        {'private':{'kind':'goal','scope_ids':['another-private'],'watermark':'hidden-change'}})==expected
    changed['affect_regulation']={'mode':'a passive mood boundary','activity_bias':'ordinary natural decay'}
    assert decision_fingerprint(context(),actor,changed,'2026-11-02')==expected
    changed['interests']=[{'id':'interest','title':'A new actual interest'}]
    assert decision_fingerprint(context(),actor,changed,'2026-11-02')!=expected


def test_autonomous_catalog_excludes_user_fact_writes_but_keeps_reading():
    write=choice('memory.claim.correct')
    write['definition']=replace(write['definition'],effect='write')
    assert [item.id for item in planner.eligible_capabilities([write,choice('content.read')])]==['content.read']


def test_discovery_options_and_arguments_obey_actual_source_configuration():
    selected = choice("content.discover", {"type": "object", "properties": {
        "query": {"type": "string"}, "mode": {"type": "string"}, "limit": {"type": "integer"},
        "sources": {"type": "array", "items": {"type": "string"}}, "provider_id": {"type": ["string", "null"]}},
        "additionalProperties": False})
    selected["availability"] = {"constraints": {"sources": {
        "web": {"can_execute": False}, "forum": {"can_execute": False}, "bilibili": {"can_execute": True}},
        "source_options": [{"sources": ["bilibili"], "provider_id": None}]}}
    material = data([selected])
    material["interests"] = [{"id": "interest", "title": "Coffee"}]
    from tianshu.life.opportunities import choices
    offers = choices(material)
    assert offers and offers[0]["activity"]["arguments"]["sources"] == ["bilibili"]
    capability = material["capabilities"][0]
    assert planner.allowed_arguments(capability, offers[0]["activity"]["arguments"])
    assert not planner.allowed_arguments(capability, {"query": "Coffee", "sources": ["web"]})
    assert not planner.allowed_arguments(capability, {"query": "Coffee"})
    assert not planner.allowed_arguments(capability, {"sources": ["bilibili"], "provider_id": "invented-provider"})
