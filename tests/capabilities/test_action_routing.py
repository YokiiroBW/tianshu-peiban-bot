"""Business-lane and service-class regressions; isolated PG, no external work."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from tianshu.capabilities.actions import Actions, encoded
from tianshu.capabilities.api import invoke
from tianshu.capabilities.catalog import Availability, CapabilityDefinition, Catalog
from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.execution_context import CURRENT_EXECUTION
from tianshu.capabilities.external import request as external_request
from tianshu.capabilities.perception.service import Perception
from tianshu.capabilities.reactions.service import Reactions
from tianshu.conversation.codec import document
from tianshu.conversation.collector import ConversationCollector
from tianshu.conversation.rendering import ExpressionRenderer
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import CollectorPolicy, ExpressionPlan
from tianshu.models.types import ModelPart
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Action, MediaRef, OriginKind, Receipt, ServiceClass, TrustedContext


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def admitted_actions(database):
    await migrate(database, (Migration(
        "capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text(),
    ),))
    context = TrustedContext(
        "synthetic-user", "synthetic-actor", "synthetic-person", "synthetic-chat",
        ("synthetic-private",), ("synthetic-private",), 1, OriginKind.HUMAN,
        frozenset({"*"}), conversation_kind="private", delivery_allowed=True,
    )
    jobs, catalog, executed = JobQueue(database.config.dsn), Catalog(), []

    async def execute(action, attempt):
        physical = external_request(action, attempt, {"quota_group": "synthetic"}, "fixture")
        executed.append((action.action_id, attempt.attempt_id, physical.service_class))
        return Receipt(str(uuid4()), action.action_id, attempt.attempt_id,
                       "succeeded", "none", "not_requested")

    for identifier in ("images.generate", "reactions.index", "speech.synthesize", "memory.search"):
        handler = SimpleNamespace(
            definition=CapabilityDefinition(
                identifier, identifier, "Synthetic routing fixture", {"type": "object"},
                {"type": "object"}, long_running=identifier != "memory.search",
            ),
            availability=AsyncMock(return_value=Availability(True, True)), execute=execute,
        )
        if identifier != "reactions.index":
            handler.resume = execute
        catalog.register(handler)
    authority = SimpleNamespace(check=AsyncMock())
    owner = Actions(database, jobs, catalog, authority)
    owner.sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    owner.register_tasks()
    try:
        yield SimpleNamespace(database=database, jobs=jobs, actions=owner, context=context,
                              catalog=catalog, executed=executed, authority=authority)
    finally:
        await owner.close()


def action_for(state, identifier, *, origin=OriginKind.HUMAN):
    key = str(uuid4())
    return Action(key, key, identifier, state.context.actor_id, state.context, {}, origin)


async def job_rows(state):
    async with state.database.transaction(read_only=True) as connection:
        return await (await connection.execute(
            "SELECT id,queue_name,status,attempts,args,priority FROM procrastinate_jobs ORDER BY id"
        )).fetchall()


@pytest.mark.parametrize("identifier,lane", [
    ("images.generate", ExecutionLane.IMAGES),
    ("reactions.index", ExecutionLane.REACTIONS),
])
async def test_http_long_work_keeps_user_class_and_native_business_lane(admitted_actions, identifier, lane):
    state = admitted_actions
    domain, operation = identifier.split(".", 1)
    result = await invoke(SimpleNamespace(actions=state.actions), domain, operation, {
        "client_request_id": "manual-long-work", "service_class": "elastic",
        "execution_lane": "memory_consolidation",
    }, state.context)
    saved = await state.actions.get(result.action_id, state.context)
    assert result.receipt.execution == "pending" and not state.executed
    assert saved["execution"]["service_class"] == "interactive"
    assert saved["execution"]["execution_lane"] == lane.value
    assert saved["execution"]["wait_reason"] == "native_task_queue"
    assert saved["execution"]["attempts"] == 0
    assert "execution_lane" not in saved["action"]["arguments"]
    assert "service_class" not in saved["action"]["arguments"]
    await state.actions.run(result.action_id)
    assert state.executed == [(result.action_id, result.attempt_id, ServiceClass.INTERACTIVE)]


@pytest.mark.parametrize("parent_class,origin,expected", [
    (None, OriginKind.TIMER, ServiceClass.ELASTIC),
    (ServiceClass.DUE, OriginKind.TOOL_RESULT, ServiceClass.DUE),
])
async def test_automatic_and_nested_actions_keep_admitted_class(admitted_actions, parent_class, origin, expected):
    state = admitted_actions
    action = action_for(state, "images.generate", origin=origin)
    token = CURRENT_EXECUTION.set({"service_class": parent_class}) if parent_class else None
    try:
        pending = await state.actions.invoke(action, background=True)
    finally:
        if token is not None:
            CURRENT_EXECUTION.reset(token)
    saved = await state.actions.get(action.action_id, state.context)
    assert saved["execution"]["execution_lane"] == "images"
    assert saved["execution"]["service_class"] == expected.value
    await state.actions.run(action.action_id)
    assert state.executed == [(action.action_id, pending.attempt_id, expected)]


@pytest.mark.parametrize("identifier", ["images.generate", "reactions.index"])
async def test_pending_resume_reroutes_original_job_without_another_attempt(admitted_actions, identifier):
    state = admitted_actions
    action = action_for(state, identifier)
    pending = await state.actions.invoke(action, background=True, service_class=ServiceClass.ELASTIC)
    original = (await job_rows(state))[0]
    async with state.database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET queue_name='elastic' WHERE id=%s",
                                 (original["id"],))
    before = await state.actions.get(action.action_id, state.context)
    assert before["execution"]["execution_lane"] is None
    assert before["execution"]["wait_reason"] == "execution_lane_unresolved"
    keywords = {"client_request_id": "reroute-original", "expected_attempt_id": pending.attempt_id,
                "expected_job_id": original["id"]}
    assert await state.actions.resume(action.action_id, state.context, **keywords) == pending
    assert await state.actions.resume(action.action_id, state.context, **keywords) == pending
    after = await state.actions.get(action.action_id, state.context)
    assert {k: v for k, v in before.items() if k != "execution"} == {
        k: v for k, v in after.items() if k != "execution"
    }
    actual = (await job_rows(state))[0]
    assert actual["queue_name"] == after["execution"]["execution_lane"]
    assert after["execution"]["service_class"] == "interactive"
    assert {k: v for k, v in original.items() if k != "queue_name"} == {
        k: v for k, v in actual.items() if k != "queue_name"
    }
    assert len(await job_rows(state)) == 1
    await state.actions.run(action.action_id)
    assert state.executed == [(action.action_id, pending.attempt_id, ServiceClass.INTERACTIVE)]
    replay = await state.actions.resume(action.action_id, state.context, **keywords)
    assert replay.execution == "succeeded" and replay.attempt_id == pending.attempt_id
    assert len(state.executed) == 1 and len(await job_rows(state)) == 1


@pytest.mark.parametrize("unsafe", ["doing", "retried", "submitted", "unknown", "revoked", "wrong_job"])
async def test_pending_reroute_rejects_started_unknown_or_mismatched_identity(admitted_actions, unsafe):
    state = admitted_actions
    action = action_for(state, "images.generate")
    pending = await state.actions.invoke(action, background=True, service_class=ServiceClass.ELASTIC)
    original = (await job_rows(state))[0]
    async with state.database.transaction() as connection:
        if unsafe == "doing":
            await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (original["id"],))
        elif unsafe == "retried":
            await connection.execute("UPDATE procrastinate_jobs SET attempts=1 WHERE id=%s", (original["id"],))
        elif unsafe == "submitted":
            await connection.execute(
                "UPDATE capabilities.actions SET attempt=attempt||'{\"dispatch\":\"submitted\",\"provider_handle\":\"original-handle\"}'::jsonb WHERE id=%s",
                (action.action_id,),
            )
        elif unsafe == "unknown":
            await connection.execute("UPDATE capabilities.actions SET receipt=%s::jsonb WHERE id=%s",
                                     (encoded(replace(pending, execution="unknown", effect="unknown")), action.action_id))
        elif unsafe == "revoked":
            state.actions.sources.validate_persistence.return_value = False
    with pytest.raises(Fault) as failure:
        await state.actions.resume(
            action.action_id, state.context, client_request_id="do-not-resubmit",
            expected_attempt_id=pending.attempt_id,
            expected_job_id=original["id"] + (1 if unsafe == "wrong_job" else 0),
        )
    assert failure.value.code in {"action_already_started", "source_revoked", "job_changed"}
    observed = await state.actions.get(action.action_id, state.context)
    assert observed["attempt"]["attempt_id"] == pending.attempt_id
    assert observed["execution"]["service_class"] == "elastic"
    assert len(await job_rows(state)) == 1 and not state.executed


async def test_explicit_resume_persists_the_new_class_for_nested_provider_work(admitted_actions):
    state = admitted_actions
    action = action_for(state, "images.generate", origin=OriginKind.TIMER)
    pending = await state.actions.invoke(action, background=True)
    _, original_attempt, _ = await state.actions._load(action.action_id)
    failed = await state.actions.save(replace(pending, execution="failed"), original_attempt)
    async with state.database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='failed'")
    resumed = await state.actions.resume(action.action_id, state.context,
                                         client_request_id="human-resume")
    assert resumed.attempt_id != pending.attempt_id
    assert await state.actions.resume(action.action_id, state.context,
                                      client_request_id="human-resume") == resumed
    saved = await state.actions.get(action.action_id, state.context)
    assert saved["execution"]["service_class"] == "interactive"
    assert saved["execution"]["execution_lane"] == "images"
    assert saved["execution"]["job_id"] == (await job_rows(state))[1]["id"]
    await state.actions.run(action.action_id, mode="resume")
    assert state.executed == [(action.action_id, resumed.attempt_id, ServiceClass.INTERACTIVE)]
    async with state.database.transaction(read_only=True) as connection:
        prior = await (await connection.execute(
            "SELECT receipt FROM capabilities.attempts WHERE id=%s", (pending.attempt_id,)
        )).fetchone()
    assert prior["receipt"]["receipt_id"] == failed.receipt_id
    assert prior["receipt"]["execution"] == "failed"


@pytest.mark.parametrize("autonomous", [False, True])
async def test_conversation_jobs_and_long_tools_follow_persisted_turn_kind(admitted_actions, autonomous):
    state = admitted_actions
    await migrate(state.database, (Migration(
        "0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text(),
    ),))
    service = object.__new__(ConversationService)
    service.database, service.jobs, service.actions = state.database, state.jobs, state.actions
    service.sources = state.actions.sources
    service.collector = ConversationCollector(state.database, state.jobs, CollectorPolicy())
    service._input_refs, service._tool_receipt = AsyncMock(return_value=()), AsyncMock()

    async def unused_run(**_payload):
        raise AssertionError("this fixture only admits the original turn")

    state.jobs.register("conversation.run", unused_run, lane_resolver=service.execution_lane_on)
    now, deadline = datetime.now(UTC), datetime.now(UTC) + timedelta(minutes=3)
    row = {"turn_id": "original-turn", "round": 0, "autonomous": autonomous,
           "received_at": now, "deadline_at": deadline}
    async with state.database.transaction() as connection:
        await connection.execute(
            "INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
            "context,input_parts,source_refs,original_parts,original_source_refs,autonomous,state,reply_owner,"
            "reply_required,occurred_at,received_at,deadline_at) VALUES(%s,%s,%s,'fixture','event',1,%s,"
            "'[]','[]','[]','[]',%s,'running','tianshu',true,%s,%s,%s)",
            (row["turn_id"], state.context.actor_id, state.context.conversation_id,
             document(state.context), autonomous, now, now, deadline),
        )
        await service.collector._run(connection, row["turn_id"], state.context.actor_id,
                                     service_class=ServiceClass.ELASTIC if autonomous else ServiceClass.INTERACTIVE)
        # A payload cannot turn an autonomous persisted turn into foreground work.
        lane = await service.execution_lane_on(connection, {"turn_id": row["turn_id"], "autonomous": not autonomous})
    assert lane == (ExecutionLane.PROACTIVE if autonomous else ExecutionLane.CONVERSATION)
    assert (await job_rows(state))[0]["queue_name"] == lane.value
    definition = state.catalog.handler("images.generate").definition
    await service._invoke(state.context, row, "original-call", definition, {}, ())
    result = service._tool_receipt.await_args.args[1]
    saved = await state.actions.get(result.action_id, state.context)
    assert result.execution == "pending" and not state.executed
    assert saved["execution"]["execution_lane"] == "images"
    assert saved["execution"]["service_class"] == ("elastic" if autonomous else "interactive")
    async with state.database.transaction(read_only=True) as connection:
        link = await (await connection.execute("SELECT background FROM conversation.tool_links")).fetchone()
    assert link["background"] is True


@pytest.mark.parametrize("service_class", [ServiceClass.INTERACTIVE, ServiceClass.DUE, ServiceClass.ELASTIC])
async def test_expression_rendering_preserves_service_class_through_speech(admitted_actions, service_class):
    state = admitted_actions
    await migrate(state.database, (Migration(
        "0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text(),
    ),))
    speech = SimpleNamespace(expression_profile=AsyncMock(return_value={
        "profile_id": "synthetic-voice", "revision": 1, "output_mode": "audio",
    }))
    delivery = SimpleNamespace(send=AsyncMock())
    renderer = ExpressionRenderer(state.database, state.jobs, state.authority,
                                  state.actions.sources, state.actions, speech, delivery)
    state.jobs.register("conversation.expression_render", renderer.render)
    state.jobs.register("conversation.render_expire", renderer.expire)
    plan = ExpressionPlan("original-expression", state.context.actor_id, state.context.conversation_id,
                          ({"kind": "text", "text": "Synthetic speech"},), (), OriginKind.ACTOR)
    waiting = await renderer.send(state.context, plan, service_class=service_class)
    assert waiting.state == "waiting_render"
    async with state.database.transaction(read_only=True) as connection:
        stored = await (await connection.execute("SELECT service_class,steps FROM conversation.expression_plans")).fetchone()
    assert stored["service_class"] == service_class.value
    await renderer.render(plan.expression_id, 0)
    saved = await state.actions.get(stored["steps"][0]["action_id"], state.context)
    assert saved["execution"]["service_class"] == service_class.value
    assert saved["execution"]["execution_lane"] == "speech"
    assert saved["receipt"]["execution"] == "pending" and not state.executed
    delivery.send.assert_not_awaited()


@pytest.mark.parametrize("service_class", [ServiceClass.DUE, ServiceClass.ELASTIC])
async def test_reaction_and_perception_model_calls_inherit_parent_class(service_class):
    class AdmissionCaptured(Exception):
        pass

    context = TrustedContext("user", "actor", None, None, (), (), 1, OriginKind.HUMAN)
    broker = SimpleNamespace(generate=AsyncMock(side_effect=AdmissionCaptured))
    reactions = object.__new__(Reactions)
    reactions.broker = broker
    perception = object.__new__(Perception)
    perception.broker, perception.authority = broker, SimpleNamespace(check=AsyncMock())
    media = MediaRef("synthetic", "controlled", "image/png")
    perception.direct_parts = AsyncMock(return_value=((ModelPart("image", media=media),), {}))
    token = CURRENT_EXECUTION.set({"service_class": service_class})
    try:
        with pytest.raises(AdmissionCaptured):
            await reactions.embed(context, "synthetic reaction")
        assert broker.generate.await_args.args[0].service_class == service_class
        with pytest.raises(AdmissionCaptured):
            await perception.describe(context, media, question="Describe the synthetic image")
        assert broker.generate.await_args.args[0].service_class == service_class
    finally:
        CURRENT_EXECUTION.reset(token)
