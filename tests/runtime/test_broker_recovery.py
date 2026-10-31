import asyncio
from datetime import UTC, datetime, timedelta

from aiohttp import web
import pytest

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
    return runner, "http://127.0.0.1:"+str(site._server.sockets[0].getsockname()[1])


async def seed(database, base_url):
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,"
            "background_max,unknown_hold_seconds) VALUES('fixture-account',2,1,1,30)")
        await conn.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES('fixture','fixture',%s,'FIXTURE_KEY','openai.chat')", (base_url,))
        await conn.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group,"
            "verified_capabilities) VALUES('fixture','fixture','fixture','fixture-account',ARRAY['text'])")
        await conn.execute("INSERT INTO models.bindings(function,service_class,route_id) "
                           "VALUES('chat','interactive','fixture')")


async def test_timeout_and_broker_restart_do_not_replay_or_release_unknown_upstream(database, tmp_path):
    calls = []

    async def slow(_request):
        calls.append(1)
        await asyncio.sleep(1)
        return web.json_response({"choices": [{"message": {"content": "late"}, "finish_reason": "stop"}]})

    app = web.Application()
    app.router.add_post("/chat/completions", slow)
    runner, origin = await serve(app)
    await seed(database, origin)
    broker = ModelBroker(database, FileStore(tmp_path), Secrets({"FIXTURE_KEY": "synthetic"}))
    await broker.open()
    try:
        request = ModelRequest("intent", "original-attempt", "chat", ServiceClass.INTERACTIVE, context(),
            RequestBudget(datetime.now(UTC)+timedelta(seconds=0.2)),
            (ModelMessage("user", (ModelPart("text", text="fixture"),)),))
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


async def test_second_broker_cannot_own_capacity_and_control_cancels_external_owner(database, tmp_path):
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
        request = ExternalRequest("intent", "external-attempt", context(),
            RequestBudget(datetime.now(UTC)+timedelta(seconds=10)), "fixture-account", "tts",
            ServiceClass.INTERACTIVE, "fixture", estimated_units={"utf8_bytes": 18})
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
