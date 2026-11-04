"""Real local HTTP connector isolation; no provider, broker or model execution."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

from aiohttp import web
import pytest

from tianshu.models.client import ModelClient
from tianshu.models.types import ExternalRequest, ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.serialization import wire
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def context():
    return TrustedContext("fixture", None, None, None, (), (), 1, OriginKind.HUMAN)


def request(attempt, service_class):
    return ModelRequest(attempt, attempt, "chat", service_class, context(),
        RequestBudget(datetime.now(UTC) + timedelta(seconds=30)),
        (ModelMessage("user", (ModelPart("text", text="synthetic"),)),))


def receipt(attempt):
    return ModelReceipt(attempt, attempt, "succeeded", "submitted",
        "fixture", 1, "fixture", "fixture", "openai.chat", "fixture")


async def serve(app):
    runner = web.AppRunner(app, handler_cancellation=True)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])


async def test_stream_eof_without_a_terminal_frame_observes_original_and_raises():
    observed=[]
    async def empty(_incoming):
        return web.Response(body=b"",content_type="application/x-ndjson")
    async def original(incoming):
        observed.append(incoming.match_info["attempt"])
        saved=replace(receipt("original"),status="unknown",dispatch="unknown")
        return web.json_response({"receipt":wire(saved),"cancel_requested":False})
    app=web.Application()
    app.router.add_post("/internal/v1/models/stream",empty)
    app.router.add_get("/internal/v1/models/attempts/{attempt}",original)
    runner,origin=await serve(app)
    client=ModelClient(origin,"fixture")
    await client.open()
    async def receive():
        selected=replace(request("original",ServiceClass.ELASTIC),execution_timeout_seconds=0.1)
        return [event async for event in client.stream(selected)]
    try:
        with pytest.raises(AppError,match="model_stream_incomplete") as caught:
            await asyncio.wait_for(receive(),2)
        assert caught.value.details["status"]=="unknown" and observed==["original"]
    finally:
        await client.close()
        await runner.cleanup()


async def test_background_and_due_holds_leave_chat_stream_and_control_available():
    release = asyncio.Event()
    entered = {name: asyncio.Event() for name in ("elastic-hold", "due-hold")}
    seen = []
    cancelled = []

    async def generate(incoming):
        value = await incoming.json()
        seen.append((value["attempt_id"], value["service_class"]))
        if value["attempt_id"] in entered:
            entered[value["attempt_id"]].set()
            await release.wait()
        return web.json_response(wire(ModelResult((), receipt(value["attempt_id"]))))

    async def stream(incoming):
        value = await incoming.json()
        seen.append((value["attempt_id"], value["service_class"]))
        return web.Response(body=(json.dumps({"kind": "receipt", "receipt":
            wire(receipt(value["attempt_id"]))}) + "\n").encode(),
            content_type="application/x-ndjson")

    async def control(incoming):
        if incoming.method == "POST":
            cancelled.append(incoming.match_info["attempt"])
        return web.json_response({"receipt": wire(receipt(incoming.match_info["attempt"]))})

    app = web.Application()
    app.router.add_post("/internal/v1/models/generate", generate)
    app.router.add_post("/internal/v1/models/stream", stream)
    app.router.add_get("/internal/v1/models/attempts/{attempt}", control)
    app.router.add_post("/internal/v1/models/attempts/{attempt}/cancel", control)
    runner, origin = await serve(app)
    client = ModelClient(origin, "fixture", max_connections=1, due_connections=1,
        background_connections=1, control_connections=1)
    await client.open()
    held = []
    try:
        held = [
            asyncio.create_task(client.generate(request("elastic-hold", ServiceClass.ELASTIC))),
            asyncio.create_task(client.generate(request("due-hold", ServiceClass.DUE))),
        ]
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), timeout=5)
        foreground = await asyncio.wait_for(client.generate(request("human", ServiceClass.INTERACTIVE)), timeout=5)

        async def receive():
            return [event async for event in client.stream(request("human-stream", ServiceClass.INTERACTIVE))]

        streamed = await asyncio.wait_for(receive(), timeout=5)
        assert foreground.receipt.attempt_id == "human" and streamed[-1].kind == "receipt"
        assert (await asyncio.wait_for(client.status("original"), timeout=5)).attempt_id == "original"
        assert (await asyncio.wait_for(client.cancel("original"), timeout=5)).attempt_id == "original"
        assert all(not task.done() for task in held)
        assert set(seen) == {("elastic-hold", "elastic"), ("due-hold", "due"),
                             ("human", "interactive"), ("human-stream", "interactive")}
        held[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await held[0]
        assert cancelled == ["original"]  # A disconnected observer does not cancel the model attempt.
        assert (await asyncio.wait_for(client.generate(
            request("after-cancel", ServiceClass.ELASTIC)), timeout=5)).receipt.attempt_id == "after-cancel"
        assert not held[1].done()
        release.set()
        await asyncio.wait_for(held[1], timeout=5)
    finally:
        release.set()
        for task in held:
            if not task.done():
                task.cancel()
        await asyncio.gather(*held, return_exceptions=True)
        await client.close()
        await runner.cleanup()
    assert all(session.closed for session in (
        client.session, client.due_session, client.background_session, client.control_session))


@pytest.mark.parametrize("kind", ["stream", "resource"])
async def test_background_stream_and_external_admission_use_background_capacity(kind):
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def blocked(incoming):
        value = await incoming.json()
        seen.append(value["attempt_id"])
        entered.set()
        await release.wait()
        if kind == "stream":
            return web.Response(body=(json.dumps({"kind": "receipt", "receipt":
                wire(receipt(value["attempt_id"]))}) + "\n").encode(),
                content_type="application/x-ndjson")
        return web.json_response({})

    async def generate(incoming):
        value = await incoming.json()
        seen.append(value["attempt_id"])
        return web.json_response(wire(ModelResult((), receipt(value["attempt_id"]))))

    async def finish(incoming):
        return web.json_response({"receipt": wire(receipt(incoming.match_info["attempt"]))})

    async def status(incoming):
        return web.json_response({"cancel_requested": False,
            "receipt": {**wire(receipt(incoming.match_info["attempt"])), "status": "running"}})

    app = web.Application()
    app.router.add_post("/internal/v1/models/stream", blocked)
    app.router.add_post("/internal/v1/resources", blocked)
    app.router.add_post("/internal/v1/resources/{attempt}/finish", finish)
    app.router.add_post("/internal/v1/models/generate", generate)
    app.router.add_get("/internal/v1/models/attempts/{attempt}", status)
    runner, origin = await serve(app)
    client = ModelClient(origin, "fixture", max_connections=1, due_connections=1,
        background_connections=1, control_connections=1)
    await client.open()
    tasks = []

    async def hold():
        if kind == "stream":
            return [event async for event in client.stream(request("held", ServiceClass.ELASTIC))]
        external = ExternalRequest("held", "held", context(),
            RequestBudget(datetime.now(UTC) + timedelta(seconds=30)),
            "fixture", "tts", ServiceClass.ELASTIC, "fixture")
        async with client.resource(external) as permit:
            await permit.finish()

    try:
        tasks.append(asyncio.create_task(hold()))
        await asyncio.wait_for(entered.wait(), timeout=5)
        tasks.append(asyncio.create_task(client.generate(request("follower", ServiceClass.ELASTIC))))
        assert (await asyncio.wait_for(client.generate(request("human", ServiceClass.INTERACTIVE)), timeout=5)).receipt.attempt_id == "human"
        assert (await asyncio.wait_for(client.generate(request("due", ServiceClass.DUE)), timeout=5)).receipt.attempt_id == "due"
        assert "follower" not in seen and not tasks[0].done()
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        assert seen.count("held") == 1 and seen.count("follower") == 1
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.close()
        await runner.cleanup()
