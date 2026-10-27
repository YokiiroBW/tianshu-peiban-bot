"""One public web/model entry; business applications keep their native lifecycle."""

import asyncio

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL

from .frameworks.aiohttp import SUPPORTED_VERSION

HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def forwarded_headers(headers, *, request=False):
    excluded = HOP_HEADERS | {
        value.strip().lower() for value in headers.get("Connection", "").split(",")
    }
    if request:
        excluded |= {"host", "content-length"}
    return CIMultiDict(
        (key, value) for key, value in headers.items() if key.lower() not in excluded
    )


def model_path(path):
    return path.startswith(
        ("/v1/", "/internal/v1/model-requests/", "/internal/v1/native-model-requests/")
    ) or path in {
        "/internal/v1/model-usage",
        "/internal/v1/model-capabilities",
        "/internal/v1/native-model-usage",
        "/internal/v1/native-model-capabilities",
    }


class Ingress:
    def __init__(self, ports, gateway_origin):
        self.ports, self.gateway_origin = ports, gateway_origin.rstrip("/")
        self.session = ports.session_factory("gateway")(
            timeout=aiohttp.ClientTimeout(total=None), auto_decompress=False
        )
        self.active = set()
        self.stopping = False

    @web.middleware
    async def middleware(self, request, handler):
        if self.ports.owns(request):
            return await handler(request)
        if self.stopping:
            return web.json_response({"code": "dependency_unavailable"}, status=503)
        task = asyncio.current_task()
        self.active.add(task)
        try:
            if model_path(request.path):
                # This is a real public TLS connection. Local requests never
                # manufacture that fact or use a forwarded header as evidence.
                if not request.secure:
                    raise web.HTTPForbidden()
                return await self.model(request)
            return await handler(request)
        finally:
            self.active.discard(task)

    async def model(self, request):
        target = URL(self.gateway_origin + request.raw_path, encoded=True)
        async with self.session.request(
            request.method,
            target,
            headers=forwarded_headers(request.headers, request=True),
            data=request.content.iter_any(),
        ) as upstream:
            response = web.StreamResponse(
                status=upstream.status, headers=forwarded_headers(upstream.headers)
            )
            await response.prepare(request)
            async for part in upstream.content.iter_any():
                await response.write(part)
            await response.write_eof()
            return response

    async def drain(self, *, timeout=30):
        self.stopping = True
        tasks = tuple(self.active)
        if not tasks:
            return True
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            for task in pending:
                task.cancel()
            # Original contract/receipt finalizers finish before dependencies
            # close, even when the caller has already exceeded its deadline.
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if not task.cancelled():
                task.exception()
        return not pending

    async def close(self):
        await self.session.close()


class Listener:
    """A public socket may share an already-started private aiohttp app.

    ServerRunner owns only its socket and external requests. It never enters or
    closes the registered app's startup/cleanup contexts a second time.
    """

    def __init__(self, app, *, host, port, tls, own_lifecycle=False):
        self.app, self.host, self.port = app, host, port
        self.tls, self.own_lifecycle = tls, own_lifecycle
        self.runner = self.site = None

    async def start(self):
        if self.own_lifecycle:
            self.runner = web.AppRunner(
                self.app, access_log=None, handler_cancellation=True
            )
        else:
            if aiohttp.__version__ != SUPPORTED_VERSION or not self.app.frozen:
                raise RuntimeError(
                    "Public listener needs the started, version-pinned app"
                )
            server = web.Server(
                self.app._handle,
                request_factory=self.app._make_request,
                handler_cancellation=True,
                access_log=None,
                **(self.app._handler_args or {}),
            )
            self.runner = web.ServerRunner(server)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port, ssl_context=self.tls)
        try:
            await self.site.start()
        except BaseException:
            await self.runner.cleanup()
            raise

    async def stop_accepting(self):
        if self.site is not None:
            await self.site.stop()

    async def close(self):
        if self.runner is not None:
            await self.runner.cleanup()
