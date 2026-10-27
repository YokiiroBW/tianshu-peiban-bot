"""Real framework applications over explicit transports; no server sockets."""

import asyncio
import hashlib
import threading
from contextlib import asynccontextmanager

import aiohttp
import httpx
import pytest
from aiohttp import web
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from deploy.single_container.exchange import LocalExchange


def application(framework, exchange, operation, *, events=None):
    events = events if events is not None else []
    if framework == "aiohttp":

        @web.middleware
        async def boundary(request, handler):
            assert exchange.owns(request)
            assert request.scheme == "http" and not request.secure
            assert request.transport.get_extra_info("ssl_object") is None
            if request.headers.get("Authorization") != "Bearer retained-token":
                raise web.HTTPUnauthorized()
            return await handler(request)

        app = web.Application(middlewares=[boundary])

        async def endpoint(request):
            return await operation(request)

        app.router.add_route("*", "/{path:.*}", endpoint)

        async def lifecycle(app):
            events.append("startup")
            yield
            events.append("cleanup")

        app.cleanup_ctx.append(lifecycle)
        return app

    async def endpoint(request):
        assert exchange.owns(request)
        assert request.scope["scheme"] == "http" and request.client is None
        if request.headers.get("Authorization") != "Bearer retained-token":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await operation(request)

    @asynccontextmanager
    async def lifecycle(app):
        events.append("startup")
        yield
        events.append("cleanup")

    return Starlette(
        routes=[Route("/{path:path}", endpoint, methods=["GET", "POST"])],
        lifespan=lifecycle,
    )


def answer(request, value, status=200):
    if isinstance(request, web.Request):
        return web.json_response(value, status=status)
    return JSONResponse(value, status_code=status)


@asynccontextmanager
async def client(exchange, kind, target="module", origin="https://module:8443"):
    if kind == "aiohttp":
        async with exchange.session_factory(target)(
            headers={"Authorization": "Bearer retained-token"},
            timeout=aiohttp.ClientTimeout(total=3),
        ) as session:
            yield session
    else:
        async with httpx.AsyncClient(
            transport=exchange.async_transport(target),
            base_url=origin,
            headers={"Authorization": "Bearer retained-token"},
            timeout=3,
        ) as session:
            yield session


@pytest.mark.parametrize("framework", ["aiohttp", "asgi"])
@pytest.mark.parametrize("kind", ["aiohttp", "httpx"])
def test_original_boundary_json_receipts_and_lifecycle(framework, kind):
    async def scenario():
        exchange = LocalExchange(buffer_bytes=1024)
        receipts, events = {}, []

        async def operation(request):
            payload = await request.json()
            if set(payload) != {"key", "value"}:
                return answer(request, {"error": "schema"}, 422)
            key, value = payload["key"], payload["value"]
            if key in receipts and receipts[key] != value:
                return answer(request, {"error": "idempotency_conflict"}, 409)
            receipts[key] = value
            return answer(request, {"receipt": key, "value": value})

        app = application(framework, exchange, operation, events=events)
        exchange.register("module", app, framework, "https://module:8443")
        await exchange.open()
        if framework == "aiohttp":
            assert not exchange._adapters["module"].runner.sites
        async with client(exchange, kind) as session:
            for body, expected in [
                ({"bad": 1}, 422),
                ({"key": "one", "value": 1}, 200),
                ({"key": "one", "value": 1}, 200),
                ({"key": "one", "value": 2}, 409),
            ]:
                if kind == "aiohttp":
                    async with session.post(
                        "https://module:8443/write", json=body
                    ) as result:
                        assert result.status == expected
                        assert await result.json()
                else:
                    result = await session.post("/write", json=body)
                    assert result.status_code == expected
            if kind == "aiohttp":
                async with session.post(
                    "https://module:8443/write",
                    json={},
                    headers={"Authorization": "Bearer wrong"},
                ) as result:
                    assert result.status == 401
            else:
                assert (
                    await session.post(
                        "/write", json={}, headers={"Authorization": "Bearer wrong"}
                    )
                ).status_code == 401
        await exchange.close()
        assert receipts == {"one": 1} and events == ["startup", "cleanup"]
        assert not exchange._calls and not exchange._owned
        assert not exchange.owns({"type": "http", "scheme": "https", "local": True})

    asyncio.run(scenario())


@pytest.mark.parametrize("framework", ["aiohttp", "asgi"])
@pytest.mark.parametrize("kind", ["aiohttp", "httpx"])
def test_stream_is_incremental_bounded_and_cancelled_in_finally(framework, kind):
    async def scenario():
        exchange = LocalExchange(buffer_bytes=1024)
        finished, produced = asyncio.Event(), []

        async def chunks():
            try:
                for number in range(100):
                    produced.append(number)
                    yield bytes([number]) * 1024
            finally:
                finished.set()

        async def operation(request):
            if framework == "asgi":
                return StreamingResponse(chunks(), media_type="text/event-stream")
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            async for part in chunks():
                await response.write(part)
            return response

        exchange.register(
            "module",
            application(framework, exchange, operation),
            framework,
            "https://module:8443",
        )
        await exchange.open()
        async with client(exchange, kind) as session:
            context = (
                session.get("https://module:8443/stream")
                if kind == "aiohttp"
                else session.stream("GET", "/stream")
            )
            async with context as response:
                assert not finished.is_set()
                iterator = (
                    response.content.iter_chunked(8192)
                    if kind == "aiohttp"
                    else response.aiter_raw()
                )
                assert await anext(iterator) == b"\0" * 1024
                await asyncio.sleep(0.02)
                assert len(produced) <= 4
                call = next(iter(exchange._calls))
                assert call.output.peak <= 1024
            await asyncio.wait_for(finished.wait(), 1)
        await exchange.close()
        assert not exchange._calls

    asyncio.run(scenario())


@pytest.mark.parametrize("framework", ["aiohttp", "asgi"])
def test_sync_transport_streams_upload_on_original_worker_and_refuses_loop(framework):
    async def scenario():
        exchange = LocalExchange(buffer_bytes=1024)
        threads = []
        finished = asyncio.Event()

        async def stream():
            try:
                for _ in range(100):
                    yield b"s" * 1024
            finally:
                finished.set()

        async def operation(request):
            if request.url.path == "/sse":
                if framework == "asgi":
                    return StreamingResponse(stream(), media_type="text/event-stream")
                response = web.StreamResponse(
                    headers={"Content-Type": "text/event-stream"}
                )
                await response.prepare(request)
                async for part in stream():
                    await response.write(part)
                return response
            raw = await (request.read() if framework == "aiohttp" else request.body())
            return answer(
                request, {"bytes": len(raw), "digest": hashlib.sha256(raw).hexdigest()}
            )

        exchange.register(
            "module",
            application(framework, exchange, operation),
            framework,
            "https://module:8443",
        )
        await exchange.open()
        transport = exchange.sync_transport("module")
        with (
            httpx.Client(transport=transport) as session,
            pytest.raises(RuntimeError, match="host loop thread"),
        ):
            session.get("https://module:8443/no-self-wait")

        def worker():
            owner = threading.get_ident()

            def body():
                for _ in range(100):
                    threads.append(threading.get_ident())
                    yield b"x" * 2048

            with (
                httpx.Client(
                    transport=exchange.sync_transport("module"),
                    headers={"Authorization": "Bearer retained-token"},
                ) as session,
                session.stream(
                    "POST", "https://module:8443/upload", content=body()
                ) as response,
            ):
                assert response.status_code == 200
                response.read()
                assert response.json() == {
                    "bytes": 204800,
                    "digest": hashlib.sha256(b"x" * 204800).hexdigest(),
                }
                with session.stream("GET", "https://module:8443/sse") as events:
                    assert next(events.iter_raw()) == b"s" * 1024
            return owner

        owner = await asyncio.wait_for(asyncio.to_thread(worker), 3)
        assert set(threads) == {owner} and owner != threading.get_ident()
        await asyncio.wait_for(finished.wait(), 1)
        await exchange.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["aiohttp", "asgi"])
def test_close_drains_reentrant_a_b_a_but_refuses_new_roots(first):
    async def scenario():
        exchange = LocalExchange()
        entered, proceed = asyncio.Event(), asyncio.Event()
        events = []

        async def request(target, path):
            async with httpx.AsyncClient(
                transport=exchange.async_transport(target),
                headers={"Authorization": "Bearer retained-token"},
            ) as session:
                response = await session.get("https://" + target + ":8443/" + path)
                return response.json()

        async def a(request_object):
            if request_object.url.path == "/callback":
                return answer(request_object, {"callback": "A"})
            entered.set()
            await proceed.wait()
            value = await request("b", "work")
            events.append("request-finally")
            return answer(request_object, value)

        async def b(request_object):
            return answer(request_object, await request("a", "callback"))

        second = "asgi" if first == "aiohttp" else "aiohttp"
        exchange.register(
            "a", application(first, exchange, a, events=events), first, "https://a:8443"
        )
        exchange.register(
            "b",
            application(second, exchange, b, events=events),
            second,
            "https://b:8443",
        )
        await exchange.open()
        running = asyncio.create_task(request("a", "root"))
        await entered.wait()
        closing = asyncio.create_task(exchange.close(grace=None))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="not accepting"):
            await request("a", "outside")
        proceed.set()
        assert await asyncio.wait_for(running, 2) == {"callback": "A"}
        await asyncio.wait_for(closing, 2)
        assert events.index("request-finally") < events.index("cleanup")
        assert events.count("startup") == events.count("cleanup") == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("framework", ["aiohttp", "asgi"])
def test_shutdown_grace_cancels_before_application_cleanup(framework):
    async def scenario():
        exchange = LocalExchange(buffer_bytes=1024)
        events = []
        active = asyncio.Event()

        async def operation(request):
            active.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("finally")

        exchange.register(
            "module",
            application(framework, exchange, operation, events=events),
            framework,
            "https://module:8443",
        )
        await exchange.open()
        async with client(exchange, "httpx") as session:
            pending = asyncio.create_task(session.get("/held"))
            await active.wait()
            await exchange.close(grace=0.01)
            await asyncio.gather(pending, return_exceptions=True)
        assert events == ["startup", "finally", "cleanup"]
        assert not exchange._calls

    asyncio.run(scenario())


def test_aiohttp_real_http_exceptions_and_transport_abort():
    async def scenario():
        exchange = LocalExchange()
        aborted = asyncio.Event()

        async def operation(request):
            if request.path == "/bad":
                raise web.HTTPConflict(text="retained")
            if request.path == "/timeout":
                raise TimeoutError()
            if request.path == "/bug":
                raise ValueError("controlled test failure")
            response = web.StreamResponse()
            await response.prepare(request)
            try:
                request.transport.abort()
                await asyncio.Event().wait()
            finally:
                aborted.set()

        exchange.register(
            "module",
            application("aiohttp", exchange, operation),
            "aiohttp",
            "https://module:8443",
        )
        await exchange.open()
        async with client(exchange, "httpx") as session:
            assert (await session.get("/bad")).status_code == 409
            assert (await session.get("/timeout")).status_code == 504
            assert (await session.get("/bug")).status_code == 500
            with pytest.raises(httpx.ReadError):
                await session.get("/abort")
        assert aborted.is_set()
        await exchange.close()

    asyncio.run(scenario())


def test_fixed_origin_and_ownership_cannot_be_supplied_in_headers():
    async def scenario():
        exchange = LocalExchange()

        async def operation(request):
            assert not exchange.owns(request.clone())
            return answer(request, {"ok": True})

        exchange.register(
            "module",
            application("aiohttp", exchange, operation),
            "aiohttp",
            "https://module:8443",
        )
        await exchange.open()
        async with client(exchange, "httpx") as session:
            with pytest.raises(ValueError, match="bound target"):
                await session.get(
                    "https://external:8443/wrong", headers={"X-Tianshu-Local": "true"}
                )
            assert (await session.get("/correct")).status_code == 200
        await exchange.close()

    asyncio.run(scenario())
