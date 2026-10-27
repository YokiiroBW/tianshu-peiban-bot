import asyncio
import ssl

import aiohttp
from aiohttp import web

from deploy.single_container.edge import Ingress, Listener
from deploy.single_container.exchange import LocalExchange
from tests.deployment.observability.helpers import certificates


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
