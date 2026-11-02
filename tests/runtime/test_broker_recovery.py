import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from aiohttp import web
import pytest

import tianshu.models.broker as broker_module
from tianshu.models.broker import ModelBroker
from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.types import ExternalRequest, ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.config import Secrets
from tianshu.runtime.errors import AppError
from tianshu.runtime.files import FileStore
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def context():
    return TrustedContext("fixture", None, None, None, (), (), 1, OriginKind.HUMAN)


async def serve(app):
    runner = web.AppRunner(app, handler_cancellation=True)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])


async def seed(database, base_url):
    async with database.transaction() as conn:
        await conn.execute(
            "INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,"
            "background_max,unknown_hold_seconds) VALUES('fixture-account',2,1,1,30)"
        )
        await conn.execute(
            "INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES('fixture','fixture',%s,'FIXTURE_KEY','openai.chat')",
            (base_url,),
        )
        await conn.execute(
            "INSERT INTO models.routes(route_id,provider_id,model,quota_group,"
            "verified_capabilities) VALUES('fixture','fixture','fixture','fixture-account',ARRAY['text'])"
        )
        await conn.execute(
            "INSERT INTO models.bindings(function,service_class,route_id) "
            "VALUES('chat','interactive','fixture')"
        )


@pytest.mark.parametrize("stream", [False, True])
async def test_preparation_and_supplier_queue_do_not_spend_execution_or_client_timeout(
    database, tmp_path, monkeypatch, stream
):
    calls = []

    async def respond(incoming):
        body = await incoming.json()
        calls.append(body)
        await asyncio.sleep(0.04)
        if body.get("stream"):
            value = {"choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}]}
            return web.Response(body=("data: " + json.dumps(value) + "\n\ndata: [DONE]\n\n").encode(),
                content_type="text/event-stream")
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]})

    app = web.Application()
    app.router.add_post("/chat/completions", respond)
    provider_runner, provider_origin = await serve(app)
    await seed(database, provider_origin)
    async with database.transaction() as conn:
        await conn.execute("UPDATE models.routes SET verified_capabilities=ARRAY['text','stream']")
        await conn.execute("INSERT INTO models.bindings(function,service_class,route_id) VALUES('chat','elastic','fixture')")
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    broker_runner, broker_origin = await serve(create_broker_app(broker, "fixture"))
    client = ModelClient(broker_origin, "fixture")
    await client.open()
    blocker = await broker.begin_external(ExternalRequest("blocker", "blocker", context(),
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)), "fixture-account", "image",
        ServiceClass.ELASTIC, "fixture"))
    prepared = asyncio.Event()
    allocated = []
    create = broker.ledger.create
    allocate = broker_module.execution_budget

    async def recorded(request, *args):
        value = await create(request, *args)
        if request.attempt_id == "wait-then-execute":
            prepared.set()
        return value

    def execution_budget(request):
        budget = allocate(request)
        if request.attempt_id == "wait-then-execute":
            allocated.append((budget, budget.remaining(), datetime.now(UTC)))
        return budget

    monkeypatch.setattr(broker.ledger, "create", recorded)
    monkeypatch.setattr(broker_module, "execution_budget", execution_budget)
    for _ in range(8):
        await broker.prepare_slots.acquire()
    original = ModelRequest("wait-then-execute", "wait-then-execute", "chat", ServiceClass.ELASTIC,
        context(), RequestBudget(datetime.now(UTC) + timedelta(seconds=0.05)),
        (ModelMessage("user", (ModelPart("text", text="synthetic"),)),), execution_timeout_seconds=0.2)

    async def execute():
        if stream:
            return [event async for event in client.stream(original)]
        return await client.generate(original)

    task = asyncio.create_task(execute())
    try:
        await asyncio.sleep(0.25)
        assert not task.done() and not calls and not prepared.is_set() and not allocated
        for _ in range(8):
            broker.prepare_slots.release()
        await asyncio.wait_for(prepared.wait(), 5)
        queued = await broker.ledger.get(original.attempt_id)
        assert queued["status"] == "queued" and queued["deadline_at"] is None
        assert queued["execution_timeout_seconds"] == 0.2
        await asyncio.sleep(0.25)
        assert not task.done() and not calls and not allocated
        await blocker.rejected("synthetic_release")
        result = await asyncio.wait_for(task, 5)
        if stream:
            assert result[-1].receipt.status == "succeeded"
            assert any(event.text == "OK" for event in result)
        else:
            assert result.receipt.status == "succeeded" and result.parts[0].text == "OK"
        assert len(calls) == 1
        settled = await broker.ledger.get(original.attempt_id)
        assert len(allocated) == 1
        actual_budget, remaining, admitted_at = allocated[0]
        assert 0.1 < remaining <= original.execution_timeout_seconds + 1e-9
        assert settled["deadline_at"] == actual_budget.deadline_at
        assert admitted_at > original.budget.deadline_at
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if not blocker.done:
            await blocker.rejected("synthetic_cleanup")
        await client.close()
        await broker_runner.cleanup()
        await broker.close()
        await provider_runner.cleanup()


async def test_external_client_uses_server_admission_deadline_after_long_queue(database, tmp_path, monkeypatch):
    await seed(database, "http://127.0.0.1:9")
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    runner, origin = await serve(create_broker_app(broker, "fixture"))
    client = ModelClient(origin, "fixture")
    await client.open()
    blocker = await broker.begin_external(ExternalRequest("blocker", "blocker", context(),
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)), "fixture-account", "image",
        ServiceClass.ELASTIC, "fixture"))
    registered, entered = asyncio.Event(), asyncio.Event()
    create = broker.ledger.create

    async def recorded(request, *args):
        value = await create(request, *args)
        if request.attempt_id == "external-queued":
            registered.set()
        return value

    monkeypatch.setattr(broker.ledger, "create", recorded)
    original = ExternalRequest("external-queued", "external-queued", context(),
        RequestBudget(datetime.now(UTC) + timedelta(seconds=0.05)), "fixture-account", "image",
        ServiceClass.ELASTIC, "fixture", execution_timeout_seconds=0.2)

    async def execute():
        async with client.resource(original) as permit:
            entered.set()
            admitted = await broker.ledger.get(original.attempt_id)
            assert admitted["status"] == "queued" and admitted["dispatch"] == "not_started"
            assert permit.request.budget.deadline_at == admitted["deadline_at"]
            assert permit.request.budget.remaining() > 0.1
            await permit.submitted()
            await asyncio.sleep(0.04)
            completed = await permit.finish()
            assert completed.deadline_at == admitted["deadline_at"]

    task = asyncio.create_task(execute())
    try:
        await asyncio.wait_for(registered.wait(), 5)
        await asyncio.sleep(0.25)
        assert not task.done() and not entered.is_set()
        assert (await broker.ledger.get(original.attempt_id))["deadline_at"] is None
        await blocker.rejected("synthetic_release")
        await asyncio.wait_for(task, 5)
        result = await broker.status(original.attempt_id)
        assert result.status == "succeeded" and result.started_at > original.budget.deadline_at
        assert result.deadline_at is not None and result.deadline_at > result.started_at
        assert original.budget.deadline_at < result.started_at
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if not blocker.done:
            await blocker.rejected("synthetic_cleanup")
        await client.close()
        await runner.cleanup()
        await broker.close()


@pytest.mark.parametrize("expiry", ["queue", "business"])
@pytest.mark.parametrize("transport", ["direct", "client"])
async def test_new_expiry_before_admission_is_a_known_not_started_receipt(database, tmp_path, expiry, transport):
    await seed(database, "http://127.0.0.1:9")
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO models.bindings(function,service_class,route_id) VALUES('chat','elastic','fixture')")
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    client = runner = None
    if transport == "client":
        runner, origin = await serve(create_broker_app(broker, "fixture"))
        client = ModelClient(origin, "fixture")
        await client.open()
    blocker = await broker.begin_external(ExternalRequest("blocker", "blocker", context(),
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)), "fixture-account", "image",
        ServiceClass.ELASTIC, "fixture"))
    original = ModelRequest("known-expiry", "known-expiry", "chat", ServiceClass.ELASTIC, context(),
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
        (ModelMessage("user", (ModelPart("text", text="synthetic"),)),), execution_timeout_seconds=10,
        max_queue_wait=0.05 if expiry == "queue" else None,
        business_deadline_at=datetime.now(UTC) + timedelta(seconds=0.5) if expiry == "business" else None)
    code = "queue_wait_expired" if expiry == "queue" else "business_deadline_expired"
    try:
        with pytest.raises(AppError, match=code):
            await (client or broker).generate(original)
        row = await broker.ledger.get(original.attempt_id)
        assert row["status"] == "cancelled" and row["dispatch"] == "not_started"
        assert row["started_at"] is None and row["deadline_at"] is None and row["error_code"] == code
        assert broker.scheduler.snapshot()["fixture-account"]["unknown_holds"] == 0
    finally:
        await blocker.rejected("synthetic_cleanup")
        if client:
            await client.close()
            await runner.cleanup()
        await broker.close()


async def test_bad_provider_stream_ends_with_a_diagnostic_and_observes_the_original_receipt(database,tmp_path,monkeypatch):
    calls=[]
    async def invalid(_request):
        calls.append(1)
        # Deliberately missing required choice.index; the parser must reject it.
        value={"choices":[{"delta":{"content":"malformed"},"finish_reason":"stop"}]}
        return web.Response(body=("data: "+json.dumps(value)+"\n\n").encode(),content_type="text/event-stream")
    provider=web.Application()
    provider.router.add_post("/chat/completions",invalid)
    provider_runner,origin=await serve(provider)
    await seed(database,origin)
    async with database.transaction() as conn:
        await conn.execute("UPDATE models.routes SET verified_capabilities=ARRAY['text','stream']")
    broker=ModelBroker(database,FileStore(tmp_path),Secrets({"FIXTURE_KEY":"synthetic"}))
    await broker.open()
    runner,origin=await serve(create_broker_app(broker,"fixture"))
    client=ModelClient(origin,"fixture")
    await client.open()
    observed=[]
    status=client.status
    async def original_status(attempt_id):
        observed.append(attempt_id)
        return await status(attempt_id)
    monkeypatch.setattr(client,"status",original_status)
    original=ModelRequest("bad-stream","bad-stream","chat",ServiceClass.INTERACTIVE,context(),
        RequestBudget(datetime.now(UTC)+timedelta(seconds=1)),(),execution_timeout_seconds=1)
    async def receive():
        return [event async for event in client.stream(original)]
    try:
        with pytest.raises(AppError,match="invalid_native_chat_response") as caught:
            await asyncio.wait_for(receive(),3)
        assert caught.value.details["status"]==caught.value.details["dispatch"]=="unknown"
        assert caught.value.details["attempt_id"]==original.attempt_id
        assert observed==[original.attempt_id] and calls==[1]
        assert (await broker.status(original.attempt_id)).status=="unknown"
    finally:
        await client.close()
        await runner.cleanup()
        await broker.close()
        await provider_runner.cleanup()


@pytest.mark.parametrize("execution_timeout", [None, 0.2], ids=["legacy-absolute", "admitted-execution"])
async def test_timeout_and_broker_restart_do_not_replay_or_release_unknown_upstream(
    database, tmp_path, execution_timeout
):
    calls = []

    async def slow(_request):
        calls.append(1)
        await asyncio.sleep(1)
        return web.json_response(
            {"choices": [{"index": 0, "message": {"role": "assistant", "content": "late"}, "finish_reason": "stop"}]}
        )

    app = web.Application()
    app.router.add_post("/chat/completions", slow)
    runner, origin = await serve(app)
    await seed(database, origin)
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    try:
        request = ModelRequest(
            "intent",
            "original-attempt",
            "chat",
            ServiceClass.INTERACTIVE,
            context(),
            RequestBudget(datetime.now(UTC) + timedelta(seconds=0.2)),
            (ModelMessage("user", (ModelPart("text", text="fixture"),)),),
            execution_timeout_seconds=execution_timeout,
        )
        with pytest.raises(TimeoutError):
            await broker.generate(request)
        assert (await broker.status("original-attempt")).status == "unknown"
        assert len(calls) == 1
    finally:
        await broker.close()
    restarted = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await restarted.open()
    try:
        assert (await restarted.status("original-attempt")).dispatch == "unknown"
        assert restarted.scheduler.snapshot()["fixture-account"]["unknown_holds"] == 1
        assert len(calls) == 1
    finally:
        await restarted.close()
        await runner.cleanup()


async def test_second_broker_cannot_own_capacity_and_control_cancels_external_owner(
    database, tmp_path
):
    await seed(database, "http://127.0.0.1:9")
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    second = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    with pytest.raises(AppError, match="broker_owner_already_active"):
        await second.open()
    runner, origin = await serve(create_broker_app(broker, "fixture-control-token"))
    client = ModelClient(origin, "fixture-control-token")
    await client.open()
    entered = asyncio.Event()

    async def external_owner():
        request = ExternalRequest(
            "intent",
            "external-attempt",
            context(),
            RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
            "fixture-account",
            "tts",
            ServiceClass.INTERACTIVE,
            "fixture",
            estimated_units={"utf8_bytes": 18},
        )
        async with client.resource(request) as permit:
            await permit.submitted()
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(external_owner())
    try:
        await entered.wait()
        await client.cancel("external-attempt")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert (await client.status("external-attempt")).status == "unknown"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.close()
        await runner.cleanup()
        await broker.close()


@pytest.mark.parametrize(
    "operation,status,unknown_holds",
    [("unknown", "unknown", 1), ("rejected", "failed", 0), ("finish", "succeeded", 0)],
)
async def test_external_submit_and_terminal_transition_are_mutually_exclusive(
    database, tmp_path, monkeypatch, operation, status, unknown_holds
):
    await seed(database, "http://127.0.0.1:9")
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    request = ExternalRequest(
        "race",
        "race-attempt",
        context(),
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
        "fixture-account",
        "tts",
        ServiceClass.INTERACTIVE,
        "fixture",
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original = broker.ledger.started

    async def paused_start(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(broker.ledger, "started", paused_start)
    try:
        permit = await broker.begin_external(request)
        submitting = asyncio.create_task(permit.submitted())
        await entered.wait()
        terminal = asyncio.create_task(
            permit.finish()
            if operation == "finish"
            else getattr(permit, operation)("terminal_during_submit")
        )
        await asyncio.sleep(0)
        assert not terminal.done()
        release.set()
        await submitting
        result = await terminal
        assert result.status == status
        persisted = await broker.ledger.get(request.attempt_id)
        assert persisted["started_at"] is not None
        assert persisted["dispatch"] == ("unknown" if status == "unknown" else "submitted")
        assert broker.scheduler.snapshot()["fixture-account"]["unknown_holds"] == unknown_holds
        with pytest.raises(AppError, match="attempt_already_finished"):
            await permit.submitted()
    finally:
        await broker.close()


async def test_upstream_502_retains_unknown_effect(database, tmp_path):
    app = web.Application()

    async def failed(_request):
        return web.Response(status=502)

    app.router.add_post("/chat/completions", failed)
    runner, origin = await serve(app)
    await seed(database, origin)
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    try:
        request = ModelRequest(
            "bad-gateway",
            "bad-gateway",
            "chat",
            ServiceClass.INTERACTIVE,
            context(),
            RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
            (ModelMessage("user", (ModelPart("text", text="fixture"),)),),
        )
        with pytest.raises(AppError, match="upstream_http_502"):
            await broker.generate(request)
        assert (await broker.status(request.attempt_id)).status == "unknown"
        assert broker.scheduler.snapshot()["fixture-account"]["unknown_holds"] == 1
    finally:
        await broker.close()
        await runner.cleanup()


async def test_absent_attempt_first_registration_is_unique_even_with_a_late_original_rpc(
    database, tmp_path
):
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def provider(_request):
        calls.append(1)
        entered.set()
        await release.wait()
        return web.json_response(
            {"choices": [{"index": 0, "message": {"role": "assistant", "content": "one actual result"}, "finish_reason": "stop"}]}
        )

    app = web.Application()
    app.router.add_post("/chat/completions", provider)
    runner, origin = await serve(app)
    await seed(database, origin)
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    request = ModelRequest(
        "frozen-intent",
        "same-original-attempt",
        "chat",
        ServiceClass.INTERACTIVE,
        context(),
        RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
        (ModelMessage("user", (ModelPart("text", text="frozen original request"),)),),
    )
    try:
        assert await broker.result(request.attempt_id, context=request.context) is None
        original = asyncio.create_task(broker.generate(request))
        await entered.wait()
        observed = await broker.generate(request)
        assert observed.receipt.status == "running" and observed.parts == ()
        release.set()
        actual = await original
        recovered = await broker.result(request.attempt_id, context=request.context)
        assert actual.receipt.attempt_id == recovered.receipt.attempt_id == request.attempt_id
        assert recovered.parts[0].text == "one actual result" and len(calls) == 1
    finally:
        release.set()
        await broker.close()
        await runner.cleanup()


async def test_native_continuation_cannot_cross_conversation_with_empty_sources(database, tmp_path):
    calls = []

    async def respond(_request):
        calls.append(1)
        return web.json_response(
            {
                "id": "native-state",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "fixture"}],
                    }
                ],
            }
        )

    app = web.Application()
    app.router.add_post("/responses", respond)
    runner, origin = await serve(app)
    await seed(database, origin)
    async with database.transaction() as conn:
        await conn.execute("UPDATE models.providers SET protocol='openai.responses'")
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    try:
        original = ModelRequest(
            "first",
            "first",
            "chat",
            ServiceClass.INTERACTIVE,
            replace(
                context(),
                actor_id="actor-a",
                conversation_id="private-a",
                conversation_kind="private",
            ),
            RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
            (ModelMessage("user", (ModelPart("text", text="private material"),)),),
            persona_revision=1,
        )
        assert (await broker.generate(original)).receipt.status == "succeeded"
        later = replace(
            original,
            intent_id="second",
            attempt_id="second",
            context=replace(original.context, conversation_id="private-b"),
            native_payload={"previous_response_id": "native-state", "input": "continue"},
        )
        with pytest.raises(AppError, match="state_reference_route_incompatible"):
            await broker.generate(later)
        assert len(calls) == 1
    finally:
        await broker.close()
        await runner.cleanup()
