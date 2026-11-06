"""The same input, attempt, action and expression survive local resource waits."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import aiohttp
from aiohttp import web

from tianshu.capabilities.actions import Actions as CapabilityActions, encoded
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.contracts import canonical
from tianshu.conversation.codec import document
from tianshu.conversation.expression import ExpressionService
from tianshu.conversation.generation import GenerationRequests, attempt_for
from tianshu.conversation.rendering import ExpressionRenderer
from tianshu.conversation.service import ConversationService
from tianshu.conversation.types import ExpressionPlan, InputMessage, InputPart
from tianshu.models.types import ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import QuotaPolicy, RoutePlan
from tianshu.models.http_timing import trace_config
from tianshu.models.scheduler import QuotaScheduler
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.types import Action, ArtifactRef, Attempt, MediaRef, OriginKind, Receipt, RequestBudget, ServiceClass, TrustedContext


def wait():
    return RecoveryWait("source_lock_set_changed", datetime.now(UTC) + timedelta(seconds=5),
                        dependency="sources:lock_set")


async def setup(database):
    await migrate(database, (
        Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("0041_conversation_submission_confirmation", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text()),
    ))
    return TrustedContext("principal", "actor", "person", "chat", ("scope",), ("scope",), 1,
                          OriginKind.HUMAN, conversation_kind="private", delivery_allowed=True)


class Authority:
    async def check(self, *_args):
        pass

    async def check_on(self, *_args):
        pass

    async def validate_persistence(self, *_args):
        return True


class Jobs:
    def __init__(self):
        self.items = []

    async def enqueue(self, _connection, spec):
        self.items.append(spec)

    async def assert_generation_on(self, *_args):
        pass


class ActionJobs(Jobs):
    current_generation_on = staticmethod(JobQueue.current_generation_on)
    assert_generation_on = staticmethod(JobQueue.assert_generation_on)

    async def observe_on(self, _connection, **_kwargs):
        return None

    async def enqueue(self, connection, spec, *, queueing_lock=None):
        await super().enqueue(connection, spec)


async def action_heads(database, jobs, authority):
    await migrate(database, (Migration("capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text()),))
    actions = CapabilityActions(database, jobs, Catalog(), authority)
    actions.sources = authority
    return actions


async def record_action(database, jobs, action, actual):
    """Publish a synthetic receipt on the real Action row used by the fence."""
    attempt = Attempt(actual.attempt_id, action.action_id)
    async with database.transaction() as connection:
        generation = await jobs.current_generation_on(connection)
        await connection.execute(
            "INSERT INTO capabilities.actions AS current_action(id,intent,semantics,principal_id,kind,created_at,document,"
            "action,attempt,receipt,result_revision,result_observed_at) "
            "VALUES(%s,%s,%s,%s,%s,0,%s,%s::jsonb,%s::jsonb,%s::jsonb,1,now()) "
            "ON CONFLICT(id) DO UPDATE SET receipt=excluded.receipt,"
            "result_revision=current_action.result_revision+1,result_observed_at=now() "
            "WHERE current_action.receipt IS DISTINCT FROM excluded.receipt",
            (action.action_id, action.intent_key, "fixture", action.context.principal_id,
             action.capability_id, canonical({"data_generation": generation}), encoded(action),
             encoded(attempt), encoded(actual)))


@pytest.mark.asyncio
async def test_input_and_claimed_turn_resume_after_source_wait_without_new_identity(database):
    context = await setup(database)

    class Sources(Authority):
        calls = 0

        async def prepare_event_write(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise wait()

        async def record_event(self, *_args, **_kwargs):
            return ()

    sources, jobs = Sources(), Jobs()
    actor = SimpleNamespace(actor=AsyncMock(return_value={"enabled": True}))
    identity = Authority()
    catalog = SimpleNamespace(discover=AsyncMock(return_value=[]))
    models = SimpleNamespace(generate=AsyncMock(), submit=AsyncMock())
    service = ConversationService(database, None, jobs, identity, sources, None, actor,
                                  None, None, None, models, catalog, None, None, None)
    service.participation.prepare = AsyncMock(return_value=None)
    service.participation.offer = AsyncMock()
    service._observe_turn = AsyncMock()
    service._next = AsyncMock()
    now = datetime.now(UTC)
    message = InputMessage("fixture", "same-input", 1, now, now, (InputPart("text", "hello"),),
                           conversation_kind="private", deployment_generation="fixture-generation")
    with pytest.raises(RecoveryWait):
        await service.ingest(context, message)
    first = await service.ingest(context, message)
    replay = await service.ingest(context, message)
    assert first["input_id"] == replay["input_id"]
    async with database.transaction() as connection:
        count = await (await connection.execute("SELECT count(*) AS n FROM conversation.turns")).fetchone()
        assert count["n"] == 1
        await connection.execute("UPDATE conversation.turns SET state='queued' WHERE turn_id=%s", (first["input_id"],))
    await service.run(first["input_id"])
    prepare = next(item for item in jobs.items if item.task == "conversation.prepare")
    attempt_id = prepare.payload["attempt_id"]
    service._model_turn = AsyncMock(side_effect=[wait(), None])
    with pytest.raises(RecoveryWait):
        await service.prepare_turn(first["input_id"], attempt_id)
    async with database.transaction(read_only=True) as connection:
        turn = await (await connection.execute("SELECT state,model_attempt_id FROM conversation.turns WHERE turn_id=%s",
                                               (first["input_id"],))).fetchone()
    assert turn == {"state": "running", "model_attempt_id": attempt_id}
    await service.prepare_turn(first["input_id"], attempt_id)
    assert [call.args[2] for call in service._model_turn.await_args_list] == [attempt_id, attempt_id]
    models.generate.assert_not_awaited()
    models.submit.assert_not_awaited()


def request(context, key="same"):
    return ModelRequest(key, attempt_for(key), "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=2)),
        (ModelMessage("user", (ModelPart("text", text="hello"),)),), execution_timeout_seconds=30)


@pytest.mark.asyncio
async def test_confirmed_original_receipt_is_observed_and_never_readmitted(database):
    context = await setup(database)
    frozen = request(context)
    receipt = ModelReceipt(frozen.attempt_id, frozen.intent_id, "unknown", "unknown",
                           "route", 1, "provider", "model", "responses", "quota")
    models = SimpleNamespace(status=AsyncMock(return_value=receipt),
        require_receipt=AsyncMock(return_value=receipt), result=AsyncMock(return_value=ModelResult((), receipt)),
        generate=AsyncMock(side_effect=TimeoutError("ACK lost")))
    generation = GenerationRequests(database, Authority(), models)
    await generation.prepare(frozen.intent_id, frozen)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.generation_requests SET state='claimed',submission_confirmed=true WHERE attempt_id=%s",
                                 (frozen.attempt_id,))
    assert (await generation.run(frozen.intent_id, frozen)).receipt == receipt
    models.generate.assert_not_awaited()
    models.status.assert_not_awaited()
    models.require_receipt.assert_awaited_once_with(frozen.attempt_id)
    models.require_receipt.side_effect = AppError("attempt_not_found", status=404)
    with pytest.raises(AppError, match="attempt_not_found"):
        await generation.run(frozen.intent_id, frozen)
    models.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_before_rpc_and_lost_ack_keep_same_frozen_request(database):
    context = await setup(database)
    frozen = request(context, "before-rpc")
    original_receipt = ModelReceipt(frozen.attempt_id, frozen.intent_id, "unknown", "unknown",
                                    "route", 1, "provider", "model", "responses", "quota")
    admitted = False

    async def status(_attempt_id, *, phase):
        assert phase == "pre_submit"
        return original_receipt if admitted else None

    async def generate(actual):
        nonlocal admitted
        assert actual == frozen
        admitted = True
        raise TimeoutError("acknowledgement lost after registration")

    models = SimpleNamespace(status=AsyncMock(side_effect=status), generate=AsyncMock(side_effect=generate),
        result=AsyncMock(return_value=ModelResult((), original_receipt)), require_receipt=AsyncMock(return_value=original_receipt))
    generation = GenerationRequests(database, Authority(), models)
    await generation.prepare(frozen.intent_id, frozen)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.generation_requests SET state='claimed' WHERE attempt_id=%s", (frozen.attempt_id,))
    with pytest.raises(TimeoutError):
        await generation.run(frozen.intent_id, frozen)
    assert (await generation.run(frozen.intent_id, frozen)).receipt == original_receipt
    models.generate.assert_awaited_once_with(frozen)
    async with database.transaction(read_only=True) as connection:
        saved = await (await connection.execute("SELECT submission_confirmed FROM conversation.generation_requests WHERE attempt_id=%s", (frozen.attempt_id,))).fetchone()
    assert saved["submission_confirmed"] is True


@pytest.mark.asyncio
async def test_generation_remote_rollback_unknown_never_creates_a_new_attempt(database):
    context = await setup(database)
    frozen = request(context, "uncertain-generation")
    original = ModelReceipt(frozen.attempt_id, frozen.intent_id, "unknown", "unknown",
                            "route", 1, "provider", "model", "responses", "quota")
    error = wait()
    error.remote_rollback_confirmed = False
    models = SimpleNamespace(status=AsyncMock(side_effect=error), require_receipt=AsyncMock(return_value=original),
                             generate=AsyncMock())
    generation = GenerationRequests(database, Authority(), models)
    with pytest.raises(RecoveryWait):
        await generation.run(frozen.intent_id, frozen)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT attempt_id,recovery_unconfirmed FROM conversation.generation_requests WHERE material_key=%s",
                                              (frozen.intent_id,))).fetchone()
    assert row == {"attempt_id": frozen.attempt_id, "recovery_unconfirmed": True}
    with pytest.raises(AppError, match="generation_recovery_unconfirmed"):
        await generation.run(frozen.intent_id, replace(frozen, attempt_id="different-attempt"))
    models.require_receipt.assert_awaited_once_with(frozen.attempt_id)
    models.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_and_late_same_attempt_reach_provider_once(database):
    await setup(database)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def upstream(request):
        calls.append(await request.json())
        entered.set()
        await release.wait()
        return web.json_response({"id": "synthetic", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "answer"}, "finish_reason": "stop"}]})

    app = web.Application()
    app.router.add_post("/chat/completions", upstream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    broker = ModelBroker(database, None, None)
    broker.ready = True
    broker.scheduler = QuotaScheduler({"account": QuotaPolicy("account", 2, 0, 2)})
    await broker.scheduler.start()
    broker.session = aiohttp.ClientSession(trace_configs=[trace_config()])
    route = RoutePlan("route", 1, "provider", "model", "openai.chat", "", "", "account",
                      frozenset({"text"}), {})
    broker.catalog.resolve = AsyncMock(return_value=route)
    broker.compiler.compile = lambda *_args, **_kwargs: SimpleNamespace(
        url=f"http://127.0.0.1:{port}/chat/completions", body={}, headers={})
    context = TrustedContext("fixture", "actor", None, None, (), (), 1, OriginKind.ACTOR)
    frozen = request(context, "concurrent")
    generation = GenerationRequests(database, Authority(), broker)
    try:
        await generation.prepare(frozen.intent_id, frozen)
        async with database.transaction() as connection:
            await connection.execute("UPDATE conversation.generation_requests SET state='claimed' WHERE attempt_id=%s", (frozen.attempt_id,))
        # Simulate a restarted owner and the delayed original RPC racing with it.
        resumed = asyncio.create_task(generation.run(frozen.intent_id, frozen))
        late = asyncio.create_task(broker.generate(frozen))
        await asyncio.wait_for(entered.wait(), timeout=5)
        release.set()
        first, second = await asyncio.gather(resumed, late)
        assert first.receipt.attempt_id == second.receipt.attempt_id == frozen.attempt_id
        assert first.receipt.status == second.receipt.status == "succeeded"
        assert len(calls) == 1
        assert (await generation.run(frozen.intent_id, frozen)).receipt.attempt_id == frozen.attempt_id
        assert len(calls) == 1
    finally:
        release.set()
        await broker.close()
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_confirmed", [True, False])
async def test_successful_model_result_retries_local_commit_without_regeneration(database, rollback_confirmed):
    context = await setup(database)
    original_receipt = ModelReceipt("attempt", "intent", "succeeded", "submitted",
                                    "route", 1, "provider", "model", "responses", "quota")
    result = ModelResult((ModelPart("text", text="answer"),), original_receipt)
    models = SimpleNamespace(result=AsyncMock(return_value=result), reusable_result=AsyncMock(return_value=result),
                             require_receipt=AsyncMock(return_value=original_receipt), submit=AsyncMock(), generate=AsyncMock())
    jobs, sources = Jobs(), Authority()
    service = ConversationService(database, None, jobs, sources, sources, None, None, None, None,
                                  None, models, SimpleNamespace(discover=AsyncMock(return_value=[])), None, None, None)
    service._next = AsyncMock()
    service.generation.request = AsyncMock(return_value=replace(request(context), attempt_id="attempt"))
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,context,"
            "input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,model_attempt_id,"
            "occurred_at,received_at,deadline_at) VALUES('turn','actor','chat','test','event',1,%s,'[]','[]','[]','[]',"
            "'waiting_external','tianshu',true,'attempt',now(),now(),now()+interval '5 minutes')", (document(context),))
        await connection.execute("INSERT INTO conversation.context_selections(attempt_id,turn_id,round,request_digest,selection,"
            "token_usage,source_refs,round_snapshot) VALUES('attempt','turn',0,'digest','[]','{}','[]',%s)",
            (document({"ordered_parts": [], "continuation": [], "artifacts": []}),))
    calls = []

    async def consume(_context, row, attempt_id, _pack, _tools, actual):
        calls.append((row["turn_id"], attempt_id, actual))
        if len(calls) == 1:
            error = wait()
            if not rollback_confirmed:
                error.remote_rollback_confirmed = False
            raise error
        async with database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET state='completed' WHERE turn_id='turn'")

    service._consume_model_result = consume
    with pytest.raises(RecoveryWait):
        await service.model_complete("turn", "attempt")
    async with database.transaction(read_only=True) as connection:
        turn = await (await connection.execute("SELECT state,model_attempt_id FROM conversation.turns WHERE turn_id='turn'")).fetchone()
        selected = await (await connection.execute("SELECT consumed_at FROM conversation.context_selections WHERE attempt_id='attempt'")).fetchone()
    assert selected["consumed_at"] is None
    if not rollback_confirmed:
        assert turn == {"state": "running", "model_attempt_id": "attempt"}
        async with database.transaction() as connection:
            await connection.execute("UPDATE conversation.turns SET lease_until=now()-interval '1 second' WHERE turn_id='turn'")
        await service.model_consume_expire("turn", "attempt")
        async with database.transaction(read_only=True) as connection:
            pending = await (await connection.execute("SELECT state,error_code FROM conversation.turns WHERE turn_id='turn'")).fetchone()
            selected = await (await connection.execute("SELECT consumption_state FROM conversation.context_selections WHERE attempt_id='attempt'")).fetchone()
        assert pending == {"state": "running", "error_code": None}
        assert selected["consumption_state"] == "unconfirmed"
        models.require_receipt.assert_awaited_once_with("attempt")
        assert len(calls) == 1
        models.submit.assert_not_awaited()
        models.generate.assert_not_awaited()
        return
    assert turn == {"state": "waiting_external", "model_attempt_id": "attempt"}
    await service.model_complete("turn", "attempt")
    await service.model_complete("turn", "attempt")
    assert len(calls) == 2 and all(item[1:] == ("attempt", result) for item in calls)
    assert models.reusable_result.await_count == 2
    models.submit.assert_not_awaited()
    models.generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status,state", [
    ("source_unavailable", 403, "failed"),
    ("model_attempt_cancelled", 409, "cancelled"),
])
async def test_reusing_a_result_respects_revocation_and_explicit_cancel(database, code, status, state):
    context = await setup(database)
    original = ModelReceipt("attempt", "intent", "succeeded", "submitted",
                            "route", 1, "provider", "model", "responses", "quota")
    models = SimpleNamespace(result=AsyncMock(return_value=ModelResult((ModelPart("text", text="answer"),), original)),
        reusable_result=AsyncMock(side_effect=AppError(code, status=status)), submit=AsyncMock())
    jobs, authority = Jobs(), Authority()
    service = ConversationService(database, None, jobs, authority, authority, None, None, None, None,
                                  None, models, None, None, None, None)
    service._next = AsyncMock()
    service._consume_model_result = AsyncMock()
    service.generation.request = AsyncMock(return_value=replace(request(context), attempt_id="attempt"))
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,context,"
            "input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,model_attempt_id,"
            "occurred_at,received_at,deadline_at) VALUES('turn','actor','chat','test','event',1,%s,'[]','[]','[]','[]',"
            "'waiting_external','tianshu',true,'attempt',now(),now(),now()+interval '5 minutes')", (document(context),))
        await connection.execute("INSERT INTO conversation.context_selections(attempt_id,turn_id,round,request_digest,selection,"
            "token_usage,source_refs,round_snapshot) VALUES('attempt','turn',0,'digest','[]','{}','[]','{}')")
    await service.model_complete("turn", "attempt")
    async with database.transaction(read_only=True) as connection:
        turn = await (await connection.execute("SELECT state,error_code,model_attempt_id FROM conversation.turns WHERE turn_id='turn'")).fetchone()
    assert turn == {"state": state, "error_code": code, "model_attempt_id": "attempt"}
    service._consume_model_result.assert_not_awaited()
    models.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_unconfirmed_wait_blocks_automatic_admission_and_consumption(database):
    context = await setup(database)
    error = wait()
    error.remote_rollback_confirmed = False
    original = ModelReceipt("attempt", "intent", "unknown", "unknown", "route", 1,
                            "provider", "model", "responses", "quota")
    models = SimpleNamespace(result=AsyncMock(side_effect=error), require_receipt=AsyncMock(return_value=original),
                             submit=AsyncMock())
    authority, jobs = Authority(), Jobs()
    service = ConversationService(database, None, jobs, authority, authority, None, None, None, None,
                                  None, models, None, None, None, None)
    service._consume_model_result = AsyncMock()
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,context,"
            "input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,model_attempt_id,"
            "occurred_at,received_at,deadline_at) VALUES('turn','actor','chat','test','event',1,%s,'[]','[]','[]','[]',"
            "'waiting_external','tianshu',true,'attempt',now(),now(),now()+interval '5 minutes')", (document(context),))
        await connection.execute("INSERT INTO conversation.context_selections(attempt_id,turn_id,round,request_digest,selection,"
            "token_usage,source_refs,round_snapshot) VALUES('attempt','turn',0,'digest','[]','{}','[]','{}')")
    with pytest.raises(RecoveryWait):
        await service.model_complete("turn", "attempt")
    async with database.transaction(read_only=True) as connection:
        selected = await (await connection.execute("SELECT consumption_state,consumed_at FROM conversation.context_selections WHERE attempt_id='attempt'")).fetchone()
    assert selected == {"consumption_state": "unconfirmed", "consumed_at": None}
    await service.model_complete("turn", "attempt")
    await service.model_submit("turn", "attempt")
    assert models.result.await_count == 1
    assert models.require_receipt.await_count == 2
    service._consume_model_result.assert_not_awaited()
    models.submit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_before_send", [True, False])
async def test_expression_resource_wait_never_duplicates_channel_send(database, wait_before_send):
    context = await setup(database)

    class FlakyAuthority(Authority):
        checks = 0

        async def check(self, *_args):
            self.checks += 1
            if wait_before_send and self.checks == 4:
                raise wait()

    delivered = Receipt("receipt", "original-action", "original-attempt", "succeeded", "confirmed", "delivered")
    channel_action = Action("original-action", "expression:expression", "channels.deliver", "actor",
                            context, {}, OriginKind.ACTOR, expression_id="expression")

    class Channels:
        calls = 0

        async def deliver(self, _context, _plan):
            self.calls += 1
            if not wait_before_send:
                raise wait()
            await record_action(database, jobs, channel_action, delivered)
            return delivered

        async def reconcile(self, _context, _expression_id):
            await record_action(database, jobs, channel_action, delivered)
            return delivered

    class Voice:
        async def expression_profile(self, _context):
            return {"profile_id": "fixed", "revision": 1, "output_mode": "text"}

    authority, jobs, channels = FlakyAuthority(), ActionJobs(), Channels()
    actions = await action_heads(database, jobs, authority)
    contacts = SimpleNamespace(record_delivery=AsyncMock())
    delivery = ExpressionService(database, jobs, authority, channels, contacts, sources=authority, actions=actions)
    renderer = ExpressionRenderer(database, jobs, authority, authority, actions, Voice(), delivery)
    plan = ExpressionPlan("expression", "actor", "chat", ({"kind": "text", "text": "answer"},), (), OriginKind.ACTOR)
    with pytest.raises(RecoveryWait):
        await renderer.send(context, plan)
    if wait_before_send:
        assert channels.calls == 0
        assert (await renderer.release(plan.expression_id)).state == "delivered"
    else:
        assert channels.calls == 1
        assert (await renderer.release(plan.expression_id)).state == "delivering"
        async with database.transaction() as connection:
            await connection.execute("UPDATE conversation.expressions SET lease_until=now()-interval '1 second' WHERE expression_id=%s", (plan.expression_id,))
        await renderer.expire_delivery(plan.expression_id)
        assert (await renderer.get(context, plan.expression_id))["state"] == "delivered"
    assert channels.calls == 1
    observed = (await actions.get("original-action", context))["receipt"]
    assert (observed["receipt_id"], observed["attempt_id"]) == ("receipt", "original-attempt")


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_confirmed", [True, False])
async def test_render_result_commit_resumes_delivery_from_original_action_receipt(database, rollback_confirmed):
    context = await setup(database)

    class Voice:
        async def expression_profile(self, _context):
            return {"profile_id": "fixed", "revision": 1, "output_mode": "audio"}

    delivered = Receipt("delivery", "original-delivery", "original-attempt", "succeeded", "confirmed", "delivered")
    authority, jobs = Authority(), ActionJobs()
    actions = await action_heads(database, jobs, authority)
    actions.invoke = AsyncMock()
    channel_action = Action("original-delivery", "expression:spoken-expression", "channels.deliver", "actor",
                            context, {}, OriginKind.ACTOR, expression_id="spoken-expression")

    async def send_channel(_context, _plan):
        await record_action(database, jobs, channel_action, delivered)
        return delivered

    delivery = SimpleNamespace(send=AsyncMock(side_effect=send_channel))
    renderer = ExpressionRenderer(database, jobs, authority, authority, actions, Voice(), delivery)
    plan = ExpressionPlan("spoken-expression", "actor", "chat", ({"kind": "text", "text": "answer"},), (), OriginKind.ACTOR)
    progress = await renderer.send(context, plan)
    action_id = progress.render_action_ids[0]
    result = Receipt("render-receipt", action_id, "render-attempt", "succeeded", "confirmed", "not_requested",
                     artifacts=(ArtifactRef("speech", MediaRef("artifact:speech", "controlled", "audio/wav")),))
    tts_action = Action(action_id, "expression-render:spoken-expression:0", "speech.synthesize", "actor", context,
                        {"profile_id": "fixed", "profile_revision": 1, "text": "answer"},
                        OriginKind.ACTOR, expression_id=plan.expression_id)
    await record_action(database, jobs, tts_action, result)
    original_deliver = renderer._deliver
    calls = 0

    async def fail_once(ctx, rendered, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = wait()
            if not rollback_confirmed:
                error.remote_rollback_confirmed = False
            raise error
        return await original_deliver(ctx, rendered, **kwargs)

    renderer._deliver = fail_once
    with pytest.raises(RecoveryWait):
        await renderer.on_action_result(context, result, 1)
    assert (await renderer.get(context, plan.expression_id))["state"] == "delivering"
    assert await renderer.on_action_result(context, result, 1)
    actions.invoke.assert_not_awaited()
    if not rollback_confirmed:
        assert (await renderer.get(context, plan.expression_id))["state"] == "delivering"
        delivery.send.assert_not_awaited()
        return
    assert (await renderer.get(context, plan.expression_id))["state"] == "delivered"
    assert await renderer.on_action_result(context, result, 1)
    actions.invoke.assert_not_awaited()
    delivery.send.assert_awaited_once()
