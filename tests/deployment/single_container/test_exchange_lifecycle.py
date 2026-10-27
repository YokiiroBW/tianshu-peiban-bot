import asyncio
import gzip
from contextlib import asynccontextmanager

import aiohttp
import httpx
import pytest
from aiohttp import web
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from deploy.single_container.exchange import LocalExchange


def test_local_session_explicit_raw_compressed_bytes():
    async def scenario():
        exchange = LocalExchange()
        payload = gzip.compress(b"application-owned-compression" * 100)

        async def handler(request):
            return web.Response(body=payload, headers={"Content-Encoding": "gzip"})

        app = web.Application()
        app.router.add_get("/raw", handler)
        exchange.register("module", app, "aiohttp", "https://module:8443")
        await exchange.open()
        try:
            with pytest.raises(ValueError, match="without decompression"):
                exchange.session_factory("module")(auto_decompress=True)
            async with (
                exchange.session_factory("module")(auto_decompress=False) as client,
                client.get("https://module:8443/raw") as response,
            ):
                assert response.headers["Content-Encoding"] == "gzip"
                assert await response.read() == payload
        finally:
            await exchange.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("framework", ["aiohttp", "asgi"])
@pytest.mark.parametrize("headers", [True, False])
def test_httpx_deadline_cancels_real_handler_and_releases_inflight(framework, headers):
    async def scenario():
        exchange = LocalExchange(buffer_bytes=1024)
        finished = asyncio.Event()

        async def stream():
            try:
                yield b"first"
                await asyncio.Event().wait()
            finally:
                finished.set()

        async def handler(request):
            if headers:
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.set()
            if framework == "asgi":
                return StreamingResponse(stream())
            response = web.StreamResponse()
            await response.prepare(request)
            async for part in stream():
                await response.write(part)
            return response

        if framework == "aiohttp":
            app = web.Application()
            app.router.add_get("/held", handler)
        else:
            app = Starlette(routes=[Route("/held", handler)])
        exchange.register("module", app, framework, "https://module:8443")
        await exchange.open()
        async with httpx.AsyncClient(
            transport=exchange.async_transport("module"), timeout=0.03
        ) as session:
            with pytest.raises(httpx.ReadTimeout):
                await session.get("https://module:8443/held")
        await asyncio.wait_for(finished.wait(), 1)
        assert not exchange._calls and not exchange._owned
        await exchange.close()

    asyncio.run(scenario())


def test_asgi_disconnect_is_observable_when_consumer_closes_stream():
    async def scenario():
        exchange = LocalExchange()
        disconnected = asyncio.Event()

        async def app(scope, receive, send):
            if scope["type"] == "lifespan":
                assert (await receive())["type"] == "lifespan.startup"
                await send({"type": "lifespan.startup.complete"})
                assert (await receive())["type"] == "lifespan.shutdown"
                await send({"type": "lifespan.shutdown.complete"})
                return
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {"type": "http.response.body", "body": b"one", "more_body": True}
            )
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                assert (await receive())["type"] == "http.disconnect"
                disconnected.set()
                raise

        exchange.register("module", app, "asgi", "http://module")
        await exchange.open()
        async with (
            httpx.AsyncClient(transport=exchange.async_transport("module")) as session,
            session.stream("GET", "http://module/stream") as response,
        ):
            assert await anext(response.aiter_raw()) == b"one"
        assert disconnected.is_set()
        await exchange.close()

    asyncio.run(scenario())


def test_aiohttp_multipart_temporary_file_closes_on_disconnect():
    async def scenario():
        exchange = LocalExchange()
        files = []

        async def handler(request):
            data = await request.post()
            files.append(data["file"].file)
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"accepted")
            await asyncio.Event().wait()

        app = web.Application()
        app.router.add_post("/upload", handler)
        exchange.register("module", app, "aiohttp", "http://module")
        await exchange.open()
        async with (
            httpx.AsyncClient(transport=exchange.async_transport("module")) as session,
            session.stream(
                "POST",
                "http://module/upload",
                files={"file": ("sample.bin", b"x" * 65536)},
            ) as response,
        ):
            assert await anext(response.aiter_raw()) == b"accepted"
        assert files and files[0].closed
        await exchange.close()

    asyncio.run(scenario())


def test_aiohttp_session_total_deadline_cancels_stream_owner():
    async def scenario():
        exchange = LocalExchange()
        finalized = asyncio.Event()

        async def handler(request):
            response = web.StreamResponse()
            await response.prepare(request)
            try:
                await response.write(b"small")
                await asyncio.Event().wait()
            finally:
                finalized.set()

        app = web.Application()
        app.router.add_get("/held", handler)
        exchange.register("module", app, "aiohttp", "http://module")
        await exchange.open()
        async with exchange.session_factory("module")(
            timeout=aiohttp.ClientTimeout(total=0.03)
        ) as session:
            with pytest.raises(TimeoutError):
                async with session.get("http://module/held") as response:
                    await response.read()
        assert finalized.is_set() and not exchange._calls
        await exchange.close()

    asyncio.run(scenario())


def test_startup_failure_unwinds_previously_opened_application():
    async def scenario():
        exchange = LocalExchange()
        events = []
        aio = web.Application()

        async def first(app):
            events.append("open")
            yield
            events.append("close")

        @asynccontextmanager
        async def failed(app):
            raise RuntimeError("controlled startup failure")
            yield

        aio.cleanup_ctx.append(first)
        exchange.register("first", aio, "aiohttp", "http://first")
        exchange.register("failed", Starlette(lifespan=failed), "asgi", "http://failed")
        with pytest.raises(RuntimeError, match="startup failed"):
            await exchange.open()
        assert events == ["open", "close"] and exchange._state == "closed"

    asyncio.run(scenario())


def test_cancelled_close_waiter_does_not_close_resources_before_finally():
    async def scenario():
        exchange = LocalExchange()
        active, proceed, finalized = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []

        async def handler(request):
            active.set()
            try:
                await proceed.wait()
                return web.Response(text="complete")
            finally:
                finalized.set()

        async def lifecycle(app):
            yield
            assert finalized.is_set()
            events.append("cleanup")

        app = web.Application()
        app.router.add_get("/held", handler)
        app.cleanup_ctx.append(lifecycle)
        exchange.register("module", app, "aiohttp", "http://module")
        await exchange.open()
        async with httpx.AsyncClient(
            transport=exchange.async_transport("module")
        ) as session:
            request = asyncio.create_task(session.get("http://module/held"))
            await active.wait()
            closing = asyncio.create_task(exchange.close(grace=None))
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)
            assert not events
            proceed.set()
            assert (await request).text == "complete"
            await exchange.close()
        assert events == ["cleanup"]

    asyncio.run(scenario())


def test_disconnect_and_shutdown_do_not_cancel_business_finally_twice():
    async def scenario():
        exchange = LocalExchange()
        cleaning, finish = asyncio.Event(), asyncio.Event()
        events = []

        async def handler(request):
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"one")
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish.wait()
                events.append("receipt-finally")

        async def lifecycle(app):
            yield
            assert events == ["receipt-finally"]
            events.append("cleanup")

        app = web.Application()
        app.router.add_get("/held", handler)
        app.cleanup_ctx.append(lifecycle)
        exchange.register("module", app, "aiohttp", "http://module")
        await exchange.open()
        async with httpx.AsyncClient(
            transport=exchange.async_transport("module")
        ) as session:
            context = session.stream("GET", "http://module/held")
            response = await context.__aenter__()
            assert await anext(response.aiter_raw()) == b"one"
            disconnected = asyncio.create_task(context.__aexit__(None, None, None))
            await asyncio.wait_for(cleaning.wait(), 1)
            closing = asyncio.create_task(exchange.close(grace=0))
            await asyncio.sleep(0)
            assert not events
            finish.set()
            await asyncio.wait_for(asyncio.gather(disconnected, closing), 1)
        assert events == ["receipt-finally", "cleanup"]

    asyncio.run(scenario())


def test_sync_request_can_reenter_async_application_on_one_worker():
    async def scenario():
        exchange = LocalExchange()

        async def inner(request):
            return web.Response(text="inner")

        async def outer(request):
            def worker():
                with httpx.Client(
                    transport=exchange.sync_transport("inner")
                ) as session:
                    return session.get("http://inner/lookup").text

            return web.Response(text=await asyncio.to_thread(worker))

        for name, handler in [("inner", inner), ("outer", outer)]:
            app = web.Application()
            app.router.add_get("/lookup", handler)
            exchange.register(name, app, "aiohttp", "http://" + name)
        await exchange.open()
        async with (
            exchange.session_factory("outer")() as session,
            session.get("http://outer/lookup", ssl=False) as response,
        ):
            assert response.content_type == "text/plain"
            assert await response.text() == "inner"
        await exchange.close()

    asyncio.run(scenario())


def test_factories_bind_before_registration_and_missing_targets_never_fall_back():
    async def scenario():
        exchange = LocalExchange()
        factory = exchange.session_factory("module")
        async_transport = exchange.async_transport("module")
        sync_transport = exchange.sync_transport("module")
        missing = exchange.async_transport("missing")
        missing_sync = exchange.sync_transport("missing")

        async def handler(request):
            return web.json_response({"module": "registered-later"})

        app = web.Application()
        app.router.add_get("/value", handler)
        exchange.register("module", app, "aiohttp", "http://module")
        await exchange.open()
        async with factory() as session, session.get("http://module/value") as response:
            assert (await response.json())["module"] == "registered-later"
        async with httpx.AsyncClient(transport=async_transport) as session:
            assert (await session.get("http://module/value")).status_code == 200
        async with httpx.AsyncClient(transport=missing) as session:
            with pytest.raises(RuntimeError, match="not registered"):
                await session.get("http://missing/value")

        def worker():
            with httpx.Client(transport=sync_transport) as session:
                assert session.get("http://module/value").status_code == 200
            with (
                httpx.Client(transport=missing_sync) as session,
                pytest.raises(RuntimeError, match="not registered"),
            ):
                session.get("http://missing/value")

        await asyncio.to_thread(worker)
        with pytest.raises(ValueError, match="module name"):
            exchange.session_factory("https://unbound")
        await exchange.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("framework", ["aiohttp", "asgi"])
def test_head_sends_headers_without_waiting_for_content_length_body(framework):
    async def scenario():
        exchange = LocalExchange()

        async def handler(request):
            if framework == "aiohttp":
                return web.Response(text="body kept for GET")
            from starlette.responses import PlainTextResponse

            return PlainTextResponse("body kept for GET")

        if framework == "aiohttp":
            app = web.Application()
            app.router.add_get("/value", handler)
        else:
            app = Starlette(routes=[Route("/value", handler)])
        exchange.register("module", app, framework, "http://module")
        await exchange.open()
        async with httpx.AsyncClient(
            transport=exchange.async_transport("module")
        ) as session:
            response = await session.head("http://module/value")
            assert response.status_code == 200 and response.content == b""
            assert response.headers["content-length"] == "17"
        await exchange.close()

    asyncio.run(scenario())
