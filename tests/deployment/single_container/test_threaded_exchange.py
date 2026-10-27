"""Real framework boundaries on independent owner loops, without sockets."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import sqlite3
import threading

import aiohttp
import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from deploy.single_container.loop_bridge import LoopCall
from deploy.single_container.threaded_exchange import ThreadedExchange
from test_exchange import answer, application, client


async def signalled(event):
    assert await asyncio.to_thread(event.wait, 2)


@pytest.mark.parametrize("framework", ["aiohttp", "asgi"])
@pytest.mark.parametrize("kind", ["aiohttp", "httpx"])
def test_original_json_auth_and_lifecycle_run_on_module_owner(framework, kind):
    async def run():
        exchange = ThreadedExchange(buffer_bytes=1024)
        threads, events = [], []

        def factory(ports):
            threads.append(threading.get_ident())

            async def operation(request):
                assert threading.get_ident() == threads[0]
                return answer(request, await request.json())

            return application(framework, ports, operation, events=events)

        exchange.register_factory("module", factory, framework, "https://module:8443")
        await exchange.open()
        try:
            assert threads[0] != threading.get_ident()
            async with client(exchange, kind) as session:
                if kind == "aiohttp":
                    async with session.post(
                        "https://module:8443/echo", json={"receipt": "original"}
                    ) as response:
                        assert await response.json() == {"receipt": "original"}
                    async with session.post(
                        "https://module:8443/echo",
                        json={},
                        headers={"Authorization": "Bearer wrong"},
                    ) as response:
                        assert response.status == 401
                else:
                    assert (
                        await session.post("/echo", json={"receipt": "original"})
                    ).json() == {"receipt": "original"}
                    assert (
                        await session.post(
                            "/echo", json={}, headers={"Authorization": "Bearer wrong"}
                        )
                    ).status_code == 401
        finally:
            assert await exchange.close()
        assert events == ["startup", "cleanup"]
        assert not exchange._owners["module"].thread.is_alive()

    asyncio.run(run())


def test_factory_sqlite_invoke_and_blocking_work_keep_owner_and_other_loop_free():
    async def run():
        exchange = ThreadedExchange()
        entered, release = threading.Event(), threading.Event()
        closed = []

        def factory(ports):
            database = sqlite3.connect(":memory:")
            database.execute("CREATE TABLE facts(value INTEGER)")
            database.execute("INSERT INTO facts VALUES(7)")
            owner = threading.get_ident()

            async def route(request):
                if request.url.path == "/blocking":
                    entered.set()
                    release.wait(2)  # A synchronous database-like wait on THIS loop.
                assert ports.owns(request) and request.scope["scheme"] == "http"
                return JSONResponse(
                    {"value": database.execute("SELECT value FROM facts").fetchone()[0]}
                )

            @asynccontextmanager
            async def lifespan(app):
                assert threading.get_ident() == owner
                yield
                assert threading.get_ident() == owner
                database.close()
                closed.append(owner)

            app = Starlette(routes=[Route("/{path:path}", route)], lifespan=lifespan)
            app.state.database, app.state.owner, app.state.values = (
                database,
                owner,
                {"list": [7]},
            )
            return app

        exchange.register_factory("a", factory, "asgi", "https://a")
        exchange.register_factory("b", factory, "asgi", "https://b")
        await exchange.open()
        try:
            a = await exchange.invoke(
                "a",
                lambda app: (
                    threading.get_ident(),
                    app.state.database.execute("SELECT value FROM facts").fetchone()[0],
                ),
            )
            b = await exchange.invoke("b", lambda app: app.state.owner)
            assert a[1] == 7 and len({a[0], b, threading.get_ident()}) == 3
            copied = await exchange.invoke("a", lambda app: app.state.values)
            copied["list"].append(8)
            assert await exchange.invoke("a", lambda app: app.state.values) == {
                "list": [7]
            }
            with pytest.raises(TypeError, match="ordinary values"):
                await exchange.invoke("a", lambda app: app.state.database)
            settled = LoopCall(exchange._owners["a"].loop, lambda: "completed")
            assert await settled.wait() == "completed"
            async with httpx.AsyncClient(
                transport=exchange.async_transport("a")
            ) as caller:
                blocked = asyncio.create_task(caller.get("https://a/blocking"))
                await signalled(entered)
                async with httpx.AsyncClient(
                    transport=exchange.async_transport("b")
                ) as other:
                    async with asyncio.timeout(0.5):
                        assert (await other.get("https://b/ready")).json() == {
                            "value": 7
                        }
                release.set()
                assert (await blocked).status_code == 200
        finally:
            release.set()
            assert await exchange.close()
        assert len(closed) == 2
        settled.cancel()  # A late waiter must not touch a loop that already joined.

    asyncio.run(run())


@pytest.mark.parametrize("synchronous", [False, True])
def test_a_to_b_to_a_callbacks_survive_router_close(synchronous):
    async def run():
        exchange = ThreadedExchange()
        entered, release = threading.Event(), threading.Event()

        def first(ports):
            async def operation(request):
                if request.url.path == "/callback":
                    return answer(request, {"callback": threading.get_ident()})
                async with httpx.AsyncClient(
                    transport=ports.async_transport("b")
                ) as session:
                    result = await session.post(
                        "https://b/outer",
                        json={},
                        headers={"Authorization": "Bearer retained-token"},
                    )
                    return answer(request, result.json())

            return application("asgi", ports, operation)

        def second(ports):
            async def operation(request):
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.001)
                if synchronous:

                    def callback():
                        with httpx.Client(
                            transport=ports.sync_transport("a")
                        ) as session:
                            return session.post(
                                "https://a/callback",
                                json={},
                                headers={"Authorization": "Bearer retained-token"},
                            ).json()

                    value = await asyncio.to_thread(callback)
                else:
                    async with ports.session_factory("a")() as session:
                        async with session.post(
                            "https://a/callback",
                            json={},
                            headers={"Authorization": "Bearer retained-token"},
                        ) as response:
                            value = await response.json()
                return answer(request, value)

            return application("aiohttp", ports, operation)

        exchange.register_factory("a", first, "asgi", "https://a")
        exchange.register_factory("b", second, "aiohttp", "https://b")
        await exchange.open()
        async with httpx.AsyncClient(
            transport=exchange.async_transport("a"),
            headers={"Authorization": "Bearer retained-token"},
        ) as session:
            request = asyncio.create_task(session.post("https://a/start", json={}))
            await signalled(entered)
            closing = asyncio.create_task(exchange.close(grace=1))
            await asyncio.sleep(0)
            with pytest.raises(httpx.ConnectError):
                await session.get("https://a/new")
            release.set()
            result = await request
            assert result.json()["callback"] == exchange._owners["a"].ports.loop_thread
            assert await closing

    asyncio.run(run())


def test_module_sync_transport_rejects_loop_thread_but_accepts_its_worker():
    async def run():
        exchange = ThreadedExchange()

        def factory(ports):
            app = application("asgi", ports, lambda request: None)
            app.state.ports = ports
            return app

        exchange.register_factory("a", factory, "asgi", "https://a")
        await exchange.open()
        try:

            def misuse(app):
                with httpx.Client(
                    transport=app.state.ports.sync_transport("a")
                ) as caller:
                    with pytest.raises(RuntimeError, match="loop thread"):
                        caller.get("https://a/test")
                return True

            assert await exchange.invoke("a", misuse)
        finally:
            assert await exchange.close()

    asyncio.run(run())


def test_streams_keep_iterator_affinity_backpressure_and_finally():
    async def run():
        exchange = ThreadedExchange(buffer_bytes=1024)
        stopped = threading.Event()
        original = b"original-body" * 2048
        produced = []

        def factory(ports):
            async def endpoint(request):
                value = hashlib.sha256()
                async for part in request.stream():
                    value.update(part)
                assert value.digest() == hashlib.sha256(original).digest()

                async def stream():
                    try:
                        for index in range(10000):
                            produced.append(index)
                            yield b"x" * 1024
                    finally:
                        stopped.set()

                return StreamingResponse(stream())

            return Starlette(routes=[Route("/stream", endpoint, methods=["POST"])])

        exchange.register_factory("module", factory, "asgi", "https://module")
        await exchange.open()
        source_loop = asyncio.get_running_loop()
        source_closed = asyncio.Event()

        async def body():
            try:
                assert asyncio.get_running_loop() is source_loop
                yield original
            finally:
                assert asyncio.get_running_loop() is source_loop
                source_closed.set()

        try:
            async with httpx.AsyncClient(
                transport=exchange.async_transport("module")
            ) as session:
                async with session.stream(
                    "POST", "https://module/stream", content=body()
                ) as response:
                    iterator = response.aiter_bytes()
                    assert await anext(iterator) == b"x" * 1024
                    await asyncio.sleep(0.02)
                    assert len(produced) < 10
                    with exchange._lock:
                        record = next(iter(exchange._records))
                    peaks = await LoopCall(
                        record.owner.loop,
                        lambda: (
                            record.response.call.input.peak,
                            record.response.call.output.peak,
                        ),
                    ).wait()
                    assert max(peaks) <= 1024 and record.body.max_piece <= 1024
            assert stopped.is_set() and source_closed.is_set()
        finally:
            assert await exchange.close()

    asyncio.run(run())


def test_cancel_waits_for_distinct_cleanup_ack_and_close_does_not_cancel_finally_twice():
    async def run():
        exchange = ThreadedExchange()
        entered, finishing, release, closed = (threading.Event() for _ in range(4))
        double_cancel = []

        def factory(ports):
            async def endpoint(request):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    finishing.set()
                    try:
                        while not release.is_set():
                            await asyncio.sleep(0.001)
                    except asyncio.CancelledError:
                        double_cancel.append(True)
                        raise

            @asynccontextmanager
            async def lifespan(app):
                yield
                closed.set()

            return Starlette(routes=[Route("/held", endpoint)], lifespan=lifespan)

        exchange.register_factory("module", factory, "asgi", "https://module")
        await exchange.open()
        async with httpx.AsyncClient(
            transport=exchange.async_transport("module")
        ) as session:
            request = asyncio.create_task(session.get("https://module/held"))
            try:
                await signalled(entered)
                request.cancel()
                await signalled(finishing)
                assert not request.done()
                assert not await exchange.close(grace=0, cleanup_timeout=0.02)
                assert not closed.is_set() and not request.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await request
                assert await exchange.close(grace=0)
                assert closed.is_set() and not double_cancel
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)
                await exchange.close(grace=0)

    asyncio.run(run())


def test_blocked_synchronous_invoke_is_reported_incomplete_and_can_finish_later():
    async def run():
        exchange = ThreadedExchange()
        entered, release, closed = (threading.Event() for _ in range(3))

        def factory(ports):
            @asynccontextmanager
            async def lifespan(app):
                yield
                closed.set()

            return Starlette(lifespan=lifespan)

        exchange.register_factory("module", factory, "asgi", "https://module")
        await exchange.open()

        def blocking(app):
            entered.set()
            return release.wait(2)

        invocation = asyncio.create_task(exchange.invoke("module", blocking))
        try:
            await signalled(entered)
            assert not await exchange.close(grace=0, cleanup_timeout=0.02)
            assert not closed.is_set() and exchange._owners["module"].thread.is_alive()
        finally:
            release.set()
            await asyncio.gather(invocation, return_exceptions=True)
            assert await exchange.close(grace=0)
        assert closed.is_set() and not exchange._owners["module"].thread.is_alive()

    asyncio.run(run())


def test_startup_failure_unwinds_already_started_owner_threads():
    async def run():
        exchange = ThreadedExchange()
        events = []

        def factory(ports):
            @asynccontextmanager
            async def lifespan(app):
                events.append("open")
                try:
                    if len(events) == 2:
                        raise RuntimeError("synthetic startup failure")
                    yield
                finally:
                    events.append("close")

            return Starlette(lifespan=lifespan)

        exchange.register_factory("a", factory, "asgi", "https://a")
        exchange.register_factory("b", factory, "asgi", "https://b")
        with pytest.raises(RuntimeError, match="startup"):
            await exchange.open()
        assert events == ["open", "open", "close", "close"]
        assert all(not owner.thread.is_alive() for owner in exchange._owners.values())

    asyncio.run(run())


def test_cancelled_close_waiter_does_not_close_active_application_early():
    async def run():
        exchange = ThreadedExchange()
        entered, release, closed = (threading.Event() for _ in range(3))

        def factory(ports):
            async def endpoint(request):
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.001)
                return JSONResponse({"completed": True})

            @asynccontextmanager
            async def lifespan(app):
                yield
                closed.set()

            return Starlette(routes=[Route("/held", endpoint)], lifespan=lifespan)

        exchange.register_factory("module", factory, "asgi", "https://module")
        await exchange.open()
        async with httpx.AsyncClient(
            transport=exchange.async_transport("module")
        ) as caller:
            request = asyncio.create_task(caller.get("https://module/held"))
            await signalled(entered)
            waiter = asyncio.create_task(exchange.close())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not closed.is_set()
            release.set()
            assert (await request).json() == {"completed": True}
            assert await exchange.close() and closed.is_set()

    asyncio.run(run())


def test_source_body_close_runs_on_source_loop_and_failure_still_joins_every_owner():
    async def run():
        exchange = ThreadedExchange()
        source_thread, source_closed, app_closed = threading.get_ident(), [], []

        class Body:
            def __init__(self):
                self.sent = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.sent:
                    raise StopAsyncIteration
                self.sent = True
                return b"original"

            async def aclose(self):
                assert threading.get_ident() == source_thread
                source_closed.append(True)
                raise ValueError("synthetic upload cleanup failure")

        def factory(ports):
            async def endpoint(request):
                return JSONResponse({"length": len(await request.body())})

            @asynccontextmanager
            async def lifespan(app):
                yield
                app_closed.append(True)

            return Starlette(
                routes=[Route("/body", endpoint, methods=["POST"])], lifespan=lifespan
            )

        exchange.register_factory("module", factory, "asgi", "https://module")
        await exchange.open()
        response = await exchange.request(
            "module", "POST", "https://module/body", [], Body()
        )
        while await response.receive() is not None:
            pass
        with pytest.raises(ValueError, match="upload cleanup"):
            await response.aclose()
        with pytest.raises(ExceptionGroup, match="Module cleanup failed"):
            await exchange.close()
        assert source_closed == app_closed == [True]
        assert not exchange._owners["module"].thread.is_alive()

    asyncio.run(run())


def test_application_cleanup_failure_does_not_skip_other_owner_or_thread_join():
    async def run():
        exchange = ThreadedExchange()
        stopped = []

        def factory(name):
            def create(ports):
                @asynccontextmanager
                async def lifespan(app):
                    yield
                    stopped.append(name)
                    if name == "b":
                        raise ValueError("synthetic app cleanup failure")

                return Starlette(lifespan=lifespan)

            return create

        for name in ("a", "b"):
            exchange.register_factory(name, factory(name), "asgi", "https://" + name)
        await exchange.open()
        with pytest.raises(ExceptionGroup, match="Module cleanup failed"):
            await exchange.close()
        assert stopped == ["b", "a"]
        assert all(not owner.thread.is_alive() for owner in exchange._owners.values())

    asyncio.run(run())


def test_cancel_before_dispatch_still_closes_source_body_and_acknowledges_task():
    async def run():
        exchange = ThreadedExchange()
        entered, release, body_closed = (threading.Event() for _ in range(3))
        source = threading.get_ident()
        called = []

        class Body:
            def __aiter__(self):
                return self

            async def __anext__(self):
                return b"unused"

            async def aclose(self):
                assert threading.get_ident() == source
                body_closed.set()

        def factory(ports):
            async def endpoint(request):
                called.append(True)
                return JSONResponse({})

            return Starlette(routes=[Route("/cancelled", endpoint)])

        exchange.register_factory("module", factory, "asgi", "https://module")
        await exchange.open()

        def blocked(app):
            entered.set()
            return release.wait(2)

        invocation = asyncio.create_task(exchange.invoke("module", blocked))
        await signalled(entered)
        request = asyncio.create_task(
            exchange.request("module", "GET", "https://module/cancelled", [], Body())
        )
        await asyncio.sleep(0)
        request.cancel()
        await asyncio.sleep(0)
        release.set()
        await invocation
        with pytest.raises(asyncio.CancelledError):
            await request
        assert body_closed.is_set() and not called
        assert await exchange.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["httpx", "aiohttp"])
def test_registered_startup_wait_respects_deadline_without_cancelling_shared_ready(
    kind,
):
    async def run():
        exchange = ThreadedExchange()
        starting, release, pumped = (threading.Event() for _ in range(3))
        responses = []

        def platform(ports):
            async def operation(request):
                return answer(request, {"platform": "ready"})

            app = application("asgi", ports, operation)
            original_lifespan = app.router.lifespan_context

            async def pump():
                async with client(
                    ports, kind, "companion", "https://companion:8443"
                ) as caller:
                    if kind == "httpx":
                        result = (
                            await caller.get("https://companion:8443/ready")
                        ).json()
                    else:
                        async with caller.get(
                            "https://companion:8443/ready"
                        ) as response:
                            result = await response.json()
                responses.append(result)
                pumped.set()

            @asynccontextmanager
            async def lifespan(app):
                async with original_lifespan(app):
                    task = asyncio.create_task(pump())
                    yield
                    await task

            app.router.lifespan_context = lifespan
            return app

        def companion(ports):
            async def operation(request):
                return answer(request, {"companion": "ready"})

            app = application("asgi", ports, operation)
            original_lifespan = app.router.lifespan_context

            @asynccontextmanager
            async def lifespan(app):
                starting.set()
                while not release.is_set():
                    await asyncio.sleep(0.001)
                async with original_lifespan(app):
                    yield

            app.router.lifespan_context = lifespan
            return app

        def gateway(ports):
            @asynccontextmanager
            async def lifespan(app):
                async with client(
                    ports, kind, "platform", "https://platform:8443"
                ) as caller:
                    if kind == "httpx":
                        assert (
                            await caller.get("https://platform:8443/ready")
                        ).json() == {"platform": "ready"}
                    else:
                        async with caller.get(
                            "https://platform:8443/ready"
                        ) as response:
                            assert await response.json() == {"platform": "ready"}
                yield

            return Starlette(lifespan=lifespan)

        exchange.register_factory("platform", platform, "asgi", "https://platform:8443")
        exchange.register_factory(
            "companion", companion, "asgi", "https://companion:8443"
        )
        exchange.register_factory("gateway", gateway, "asgi", "https://gateway:8443")
        opening = asyncio.create_task(exchange.open())
        try:
            await signalled(starting)
            if kind == "httpx":
                async with httpx.AsyncClient(
                    transport=exchange.async_transport("companion"), timeout=0.01
                ) as caller:
                    with pytest.raises(httpx.ReadTimeout):
                        await caller.get("https://companion:8443/ready")
            else:
                async with exchange.session_factory("companion")(
                    timeout=aiohttp.ClientTimeout(total=None, sock_read=0.01)
                ) as caller:
                    with pytest.raises(aiohttp.ServerTimeoutError):
                        await caller.get("https://companion:8443/ready")
            assert not exchange._owners["companion"].available.done()
            assert not exchange._owners["companion"].available.cancelled()
            async with httpx.AsyncClient(
                transport=exchange.async_transport("missing")
            ) as caller:
                with pytest.raises(RuntimeError, match="not registered"):
                    await caller.get("https://missing")
            async with httpx.AsyncClient(
                transport=exchange.async_transport("companion")
            ) as caller:
                with pytest.raises(ValueError, match="origin"):
                    await caller.get("https://wrong-origin/ready")
            release.set()
            await opening
            await signalled(pumped)
            assert responses == [{"companion": "ready"}]
        finally:
            release.set()
            await asyncio.gather(opening, return_exceptions=True)
            assert await exchange.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["httpx", "aiohttp"])
def test_failed_target_startup_reaches_existing_client_error_handler(kind):
    async def run():
        exchange = ThreadedExchange()
        caught = []

        def platform(ports):
            async def pump():
                try:
                    async with client(
                        ports, kind, "companion", "https://companion:8443"
                    ) as caller:
                        await caller.get("https://companion:8443/ready")
                except (httpx.ConnectError, aiohttp.ClientConnectionError):
                    caught.append(True)

            @asynccontextmanager
            async def lifespan(app):
                task = asyncio.create_task(pump())
                yield
                await task

            return Starlette(lifespan=lifespan)

        def companion(ports):
            @asynccontextmanager
            async def lifespan(app):
                await asyncio.sleep(0.01)
                raise ValueError("synthetic cold startup failure")
                yield  # pragma: no cover - a lifespan generator that fails before startup

            return Starlette(lifespan=lifespan)

        exchange.register_factory("platform", platform, "asgi", "https://platform:8443")
        exchange.register_factory(
            "companion", companion, "asgi", "https://companion:8443"
        )
        with pytest.raises(RuntimeError, match="startup"):
            await exchange.open()
        assert caught == [True]
        assert all(not owner.thread.is_alive() for owner in exchange._owners.values())

    asyncio.run(run())


def test_real_sqlite_write_lock_blocks_only_its_module_loop(tmp_path):
    async def run():
        exchange = ThreadedExchange()
        entered = threading.Event()
        database_path = tmp_path / "locked.sqlite"

        def factory(name):
            def create(ports):
                database = sqlite3.connect(
                    database_path if name == "a" else ":memory:", timeout=2
                )
                database.execute("CREATE TABLE IF NOT EXISTS facts(value INTEGER)")
                database.commit()

                async def endpoint(request):
                    if name == "a":
                        entered.set()
                        database.execute("INSERT INTO facts VALUES(1)")
                        database.commit()
                    return JSONResponse(
                        {
                            "rows": database.execute(
                                "SELECT COUNT(*) FROM facts"
                            ).fetchone()[0]
                        }
                    )

                @asynccontextmanager
                async def lifespan(app):
                    yield
                    database.close()

                return Starlette(routes=[Route("/query", endpoint)], lifespan=lifespan)

            return create

        for name in ("a", "b"):
            exchange.register_factory(name, factory(name), "asgi", "https://" + name)
        await exchange.open()
        locker = sqlite3.connect(database_path)
        locker.execute("BEGIN IMMEDIATE")
        try:
            async with httpx.AsyncClient(
                transport=exchange.async_transport("a")
            ) as first:
                blocked = asyncio.create_task(first.get("https://a/query"))
                await signalled(entered)
                async with httpx.AsyncClient(
                    transport=exchange.async_transport("b")
                ) as other:
                    async with asyncio.timeout(0.5):
                        assert (await other.get("https://b/query")).json() == {
                            "rows": 0
                        }
                assert not blocked.done()
                locker.commit()
                assert (await blocked).json() == {"rows": 1}
        finally:
            locker.close()
            assert await exchange.close()

    asyncio.run(run())
