import asyncio
import ssl
import socket
from types import SimpleNamespace

import aiohttp
from aiohttp import web

from deploy.single_container.edge import Ingress, Listener
from deploy.single_container.applications import NativeModule
from deploy.single_container.exchange import LocalExchange
from tests.deployment.observability.helpers import certificates


def test_native_http_console_and_tls_api_share_one_private_lifecycle(
    tmp_path, monkeypatch
):
    async def scenario():
        from services.platform import server

        certificates(tmp_path)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            api_port = sock.getsockname()[1]
        exchange = LocalExchange()
        module = NativeModule(
            "platform",
            {},
            {},
            frontend={
                "host": "127.0.0.1",
                "port": api_port,
                "tls_certfile": str(tmp_path / "guard.pem"),
                "tls_keyfile": str(tmp_path / "guard.key"),
            },
            gateway_origin="https://gateway",
        )
        module.ingress = Ingress(
            exchange, "https://gateway", platform_origin="https://platform"
        )
        module.console = SimpleNamespace(
            access=SimpleNamespace(
                current={"mode": "http"},
                config={"host": "127.0.0.1", "port": 0},
                tls=None,
            )
        )
        private = web.Application(middlewares=[module.ingress.middleware])
        public, gateway = web.Application(), web.Application()
        events = []

        async def lifecycle(app):
            events.append("start")
            yield
            events.append("close")

        async def rpc(request):
            assert exchange.owns(request)
            return web.json_response(
                {
                    "authorized": request.headers.get("Authorization")
                    == "Bearer synthetic"
                }
            )

        async def page(request):
            return web.Response(text="native HTTP console")

        private.cleanup_ctx.append(lifecycle)
        private.router.add_route("*", "/internal/{tail:.*}", rpc)
        gateway.router.add_route("*", "/v1/{tail:.*}", rpc)
        public.router.add_get("/", page)
        monkeypatch.setattr(server, "create_app", lambda *args, **kwargs: public)
        monkeypatch.setattr(
            "deploy.single_container.applications.app_platform", lambda app: object()
        )
        exchange.register("platform", private, "aiohttp", "https://platform")
        exchange.register("gateway", gateway, "aiohttp", "https://gateway")
        await exchange.open()
        try:
            await module.start_public(private)
            assert (
                module.api_listener.app is private
                and not module.api_listener.own_lifecycle
            )
            web_port = module.listener.site._server.sockets[0].getsockname()[1]
            trust = ssl.create_default_context(cafile=tmp_path / "ca.pem")
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=trust)
            ) as client:
                async with client.get(f"http://127.0.0.1:{web_port}/") as response:
                    assert (
                        response.status == 200
                        and await response.text() == "native HTTP console"
                    )
                async with client.get(
                    f"http://127.0.0.1:{web_port}/internal/probe"
                ) as response:
                    assert response.status == 403
                for path in ("/internal/probe", "/v1/probe"):
                    async with client.get(
                        f"https://127.0.0.1:{api_port}" + path,
                        headers={"Authorization": "Bearer synthetic"},
                    ) as response:
                        assert (
                            response.status == 200
                            and (await response.json())["authorized"]
                        )
            await module.stop_accepting(private)
            assert not module.listener.site._server.is_serving()
            assert not module.api_listener.site._server.is_serving()
            original_close = module.api_listener.close

            async def failed_after_close():
                await original_close()
                raise RuntimeError("synthetic API close failure")

            module.api_listener.close = failed_after_close
            try:
                try:
                    await module.close_public(private)
                except RuntimeError as error:
                    assert str(error) == "synthetic API close failure"
                else:
                    raise AssertionError("original close failure must remain visible")
            finally:
                module.api_listener.close = original_close
            assert module.listener.runner.server is None
            assert events == ["start"]
        finally:
            await module.close_public(private)
            await module.ingress.close()
            await exchange.close()
        assert events == ["start", "close"]

    asyncio.run(scenario())


def test_one_public_listener_routes_service_rpc_and_all_model_owners(tmp_path):
    async def scenario():
        certificates(tmp_path)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(tmp_path / "guard.pem", tmp_path / "guard.key")
        trust = ssl.create_default_context(cafile=tmp_path / "ca.pem")
        exchange = LocalExchange()
        ingress = Ingress(
            exchange,
            "https://gateway.internal:8444",
            platform_origin="https://platform.internal:8443",
        )
        private, gateway = web.Application(), web.Application()
        seen = []

        async def private_rpc(request):
            assert exchange.owns(request) and not request.secure
            assert request.headers.get("Authorization") == "Bearer synthetic-service"
            seen.append(("platform", request.path))
            return web.json_response({"owner": "platform"})

        async def gateway_rpc(request):
            assert exchange.owns(request) and not request.secure
            seen.append(("gateway", request.path))
            return web.json_response({"owner": "gateway"})

        async def public_console(request):
            # The native public UI refuses service routes. Ingress must dispatch
            # them to the registered private application before that boundary.
            assert not request.path.startswith("/internal/")
            return web.Response(text="public console")

        private.router.add_route("*", "/{path:.*}", private_rpc)
        gateway.router.add_route("*", "/{path:.*}", gateway_rpc)
        public = web.Application(middlewares=[ingress.middleware])
        public.router.add_route("*", "/{path:.*}", public_console)
        exchange.register(
            "platform", private, "aiohttp", "https://platform.internal:8443"
        )
        exchange.register(
            "gateway", gateway, "aiohttp", "https://gateway.internal:8444"
        )
        await exchange.open()
        listener = Listener(
            public, host="127.0.0.1", port=0, tls=tls, own_lifecycle=True
        )
        await listener.start()
        port = listener.site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=trust)
            ) as client:
                for path, owner in (
                    ("/internal/v1/bot/events", "platform"),
                    ("/internal/v1/provider-self-service/select", "platform"),
                    ("/internal/v1/provider-self-service/runtime", "platform"),
                    ("/internal/v1/provider-self-service/models", "gateway"),
                    ("/internal/v1/provider-self-service/test", "gateway"),
                    ("/internal/v1/memory-retrieval/execute", "gateway"),
                    ("/internal/v1/native-model-requests/fixture", "gateway"),
                ):
                    async with client.post(
                        f"https://127.0.0.1:{port}" + path,
                        json={},
                        headers={"Authorization": "Bearer synthetic-service"},
                    ) as response:
                        assert response.status == 200
                        assert (await response.json())["owner"] == owner
                assert len(seen) == 7
        finally:
            await listener.close()
            await ingress.close()
            await exchange.close()

    asyncio.run(scenario())


def test_public_tls_model_stream_and_single_product_lifecycle(tmp_path):
    async def scenario():
        certificates(tmp_path)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(tmp_path / "guard.pem", tmp_path / "guard.key")
        trust = ssl.create_default_context(cafile=tmp_path / "ca.pem")
        exchange = LocalExchange(buffer_bytes=1024)
        ingress = Ingress(exchange, "https://gateway.internal:8444")
        events, seen = [], []
        first, release = asyncio.Event(), asyncio.Event()

        async def lifecycle(app):
            events.append("start")
            yield
            events.append("close")

        async def home(request):
            return web.Response(text="existing console")

        console = web.Application(middlewares=[ingress.middleware])
        console.router.add_get("/", home)
        console.cleanup_ctx.append(lifecycle)
        gateway = web.Application()

        async def model(request):
            seen.append((request.secure, exchange.owns(request), await request.read()))
            if request.headers.get("Authorization") != "Bearer synthetic-client":
                return web.json_response({"code": "unauthorized"}, status=401)
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"first\n")
            first.set()
            await release.wait()
            await response.write(b"last\n")
            return response

        gateway.router.add_post("/v1/chat/completions", model)
        exchange.register(
            "platform", console, "aiohttp", "https://platform.internal:8443"
        )
        exchange.register(
            "gateway", gateway, "aiohttp", "https://gateway.internal:8444"
        )
        await exchange.open()
        listener = Listener(console, host="127.0.0.1", port=0, tls=tls)
        await listener.start()
        port = listener.site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=trust), trust_env=False
            ) as client:
                async with client.post(
                    f"https://127.0.0.1:{port}/v1/chat/completions",
                    data=b"original bytes",
                    headers={"Authorization": "Bearer synthetic-client"},
                ) as response:
                    assert response.status == 200
                    assert await response.content.readline() == b"first\n"
                    assert first.is_set() and not release.is_set()
                    release.set()
                    assert await response.read() == b"last\n"
                async with client.post(
                    f"https://127.0.0.1:{port}/v1/chat/completions", data=b"denied"
                ) as response:
                    assert response.status == 401
                async with client.get(f"https://127.0.0.1:{port}/") as response:
                    assert await response.text() == "existing console"
            assert seen == [(False, True, b"original bytes"), (False, True, b"denied")]
            assert events == ["start"]
            await listener.stop_accepting()
            assert await ingress.drain(timeout=1)
            await listener.close()
            assert events == ["start"]  # Socket cleanup never closes the product.
        finally:
            release.set()
            await listener.close()
            await ingress.close()
            await exchange.close()
        assert events == ["start", "close"]

    asyncio.run(scenario())


def test_forwarded_https_header_cannot_open_plain_http_model_entry():
    async def scenario():
        exchange = LocalExchange()
        ingress = Ingress(exchange, "https://gateway.internal:8444")
        app = web.Application(middlewares=[ingress.middleware])

        async def console(request):
            return web.Response(text="console")

        app.router.add_route("*", "/{path:.*}", console)
        exchange.register("platform", app, "aiohttp", "http://platform.internal:8443")
        await exchange.open()
        listener = Listener(app, host="127.0.0.1", port=0, tls=None)
        await listener.start()
        port = listener.site._server.sockets[0].getsockname()[1]
        try:
            async with (
                aiohttp.ClientSession(trust_env=False) as client,
                client.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    headers={"X-Forwarded-Proto": "https"},
                ) as response,
            ):
                assert response.status == 403
        finally:
            await listener.close()
            await ingress.close()
            await exchange.close()

    asyncio.run(scenario())
