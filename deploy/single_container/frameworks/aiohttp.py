"""aiohttp 3.14.1 application bridge and the narrow internal session interface.

Private Request construction is deliberately isolated and version checked here.
The application, route resolver, middleware, prepare hooks and response classes
are aiohttp's real implementations. This adapter supplies application bytes and
flow control where a network protocol would otherwise supply them.
"""

import asyncio
import json as json_module
import logging
import time
import zlib
from http import HTTPStatus

import aiohttp
from aiohttp import web
from aiohttp.abc import AbstractStreamWriter
from aiohttp.helpers import must_be_empty_body
from aiohttp.http import HttpVersion11
from aiohttp.http_parser import RawRequestMessage
from aiohttp.streams import StreamReader
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

LOG = logging.getLogger(__name__)
SUPPORTED_VERSION = "3.14.1"


class _LocalTransport(asyncio.Transport):
    def __init__(self, call):
        super().__init__()
        self.call = call

    def get_extra_info(self, name, default=None):
        # There is no socket, peer address, certificate or TLS context.
        return default

    def is_closing(self):
        return self.call.disconnected.is_set()

    def abort(self):
        self.call.abort()

    def close(self):
        self.abort()


class _Protocol:
    ssl_context = peername = sockname = None
    _reading_paused = False

    def __init__(self, call, limits):
        self.transport = _LocalTransport(call)
        self.max_field_size = limits.get("max_field_size", 8190)
        self.max_headers = limits.get("max_headers", 128)
        self.readable = asyncio.Event()
        self.readable.set()

    @property
    def connected(self):
        return not self.transport.is_closing()

    def pause_reading(self):
        self._reading_paused = True
        self.readable.clear()

    def resume_reading(self, resume_parser=True):
        self._reading_paused = False
        self.readable.set()


class _Writer(AbstractStreamWriter):
    def __init__(self, call):
        self.call = call
        self.length = None
        self.output_size = 0
        self.buffer_size = 0
        self.compressor = None
        self.ended = False
        self.empty_body = False

    def enable_chunking(self):
        # The channel carries payload bytes, without HTTP chunk framing.
        pass

    def enable_compression(self, encoding="deflate", strategy=None):
        self.compressor = zlib.compressobj(
            wbits=16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS,
            strategy=zlib.Z_DEFAULT_STRATEGY if strategy is None else strategy,
        )

    async def write_headers(self, status_line, headers):
        status = int(status_line.split(" ", 2)[1])
        self.empty_body = must_be_empty_body(self.call.request.method, status)
        self.call.start_response(
            status,
            [
                (key.encode("latin-1"), value.encode("latin-1"))
                for key, value in headers.items()
            ],
        )

    async def write(self, chunk):
        if self.ended:
            raise RuntimeError("Local response is already complete")
        if self.empty_body:
            return
        chunk = bytes(chunk)
        if self.length is not None:
            chunk = chunk[: self.length]
            self.length -= len(chunk)
        if self.compressor is not None:
            chunk = self.compressor.compress(chunk)
        self.output_size += len(chunk)
        await self.call.output.send(chunk)

    async def write_eof(self, chunk=b""):
        if self.ended:
            return
        await self.write(chunk)
        if self.compressor is not None:
            tail = self.compressor.flush()
            self.output_size += len(tail)
            await self.call.output.send(tail)
        self.ended = True
        if self.length and not self.empty_body:
            raise ConnectionError("Application ended before its Content-Length")
        await self.call.output.finish()

    async def drain(self):
        await self.call.output.drain()


class Application:
    def __init__(self, app):
        if aiohttp.__version__ != SUPPORTED_VERSION:
            raise RuntimeError(
                "Local aiohttp bridge requires aiohttp==" + SUPPORTED_VERSION
            )
        if not isinstance(app, web.Application):
            raise TypeError("Register a real aiohttp Application")
        self.app = app
        self.started = False
        self.runner = None

    async def open(self):
        self.runner = web.AppRunner(
            self.app, handler_cancellation=True, access_log=None
        )
        await self.runner.setup()
        self.started = True

    async def close(self):
        if self.runner is not None:
            await self.runner.cleanup()
        self.started = False

    async def handle(self, call):
        spec = call.request
        url = URL(spec.url)
        relative = url.relative()
        headers = CIMultiDict(
            (key.decode("latin-1"), value.decode("latin-1"))
            for key, value in spec.headers
        )
        message = RawRequestMessage(
            spec.method,
            relative.raw_path_qs,
            HttpVersion11,
            CIMultiDictProxy(headers),
            tuple(spec.headers),
            False,
            None,
            False,
            False,
            relative,
        )
        protocol = _Protocol(call, self.app._handler_args or {})
        payload = StreamReader(protocol, max(512, call.exchange.buffer_bytes // 2))
        writer = _Writer(call)
        request = web.Request(
            message,
            payload,
            protocol,
            writer,
            asyncio.current_task(),
            asyncio.get_running_loop(),
            client_max_size=self.app._client_max_size,
            scheme="http",
        )
        call.server_request = request
        call.exchange._mark(call, request)

        async def feed():
            try:
                while (part := await call.input.receive()) is not None:
                    payload.feed_data(part)
                    await protocol.readable.wait()
                payload.feed_eof()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - propagate input errors through StreamReader
                payload.set_exception(error)

        feeder = asyncio.create_task(feed())
        try:
            try:
                response = await self.app._handle(request)
                if not isinstance(response, web.StreamResponse):
                    raise TypeError("aiohttp handler did not return a StreamResponse")
            except web.HTTPException as error:
                response = error
            except TimeoutError:
                if call.headers.done():
                    raise
                response = web.Response(status=504)
            except Exception:
                if call.headers.done():
                    raise
                LOG.exception("Local aiohttp handler failed")
                response = web.Response(status=500)
            await response.prepare(request)
            await response.write_eof()
        finally:
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
            request._finish()


class _Content:
    def __init__(self, response):
        self.response = response
        self.pending = bytearray()

    async def read(self, n=-1):
        if n == 0:
            return b""
        while n < 0 or not self.pending:
            part = await self.response._receive()
            if part is None:
                break
            self.pending.extend(part)
            if n >= 0:
                break
        if n < 0:
            result = bytes(self.pending)
            self.pending.clear()
            return result
        result = bytes(self.pending[:n])
        del self.pending[:n]
        return result

    async def readany(self):
        if self.pending:
            result = bytes(self.pending)
            self.pending.clear()
            return result
        return await self.response._receive() or b""

    def at_eof(self):
        return self.response.local.eof and not self.pending

    async def iter_chunked(self, size):
        if size <= 0:
            raise ValueError("Chunk size must be positive")
        while part := await self.read(size):
            yield part

    async def iter_any(self):
        while part := await self.readany():
            yield part

    async def iter_chunks(self):
        async for part in self.iter_any():
            yield part, True


class ClientResponse:
    def __init__(self, local, session, method, url, headers, deadline, read_timeout):
        self.local, self.session = local, session
        self.status = local.status
        self.headers = CIMultiDictProxy(
            CIMultiDict(
                (key.decode("latin-1"), value.decode("latin-1"))
                for key, value in local.headers
            )
        )
        self.raw_headers = tuple(local.headers)
        self.url, self.method = URL(url), method
        self.reason = (
            HTTPStatus(self.status).phrase
            if self.status in HTTPStatus._value2member_map_
            else ""
        )
        self.request_info = aiohttp.RequestInfo(
            self.url, method, CIMultiDictProxy(headers), self.url
        )
        self.history = ()
        self.content = _Content(self)
        self.deadline, self.read_timeout = deadline, read_timeout
        self._body = None
        self.closed = False

    async def _receive(self):
        remaining = (
            None if self.deadline is None else max(0, self.deadline - time.monotonic())
        )
        timeout = self.read_timeout
        if remaining is not None:
            timeout = remaining if timeout is None else min(timeout, remaining)
        try:
            async with asyncio.timeout(timeout):
                return await self.local.receive()
        except BaseException:
            await self.local.aclose()
            raise

    async def read(self):
        if self._body is None:
            self._body = await self.content.read()
        return self._body

    async def text(self, encoding=None, errors="strict"):
        return (await self.read()).decode(encoding or self.charset or "utf-8", errors)

    async def json(
        self, *, encoding=None, loads=json_module.loads, content_type="application/json"
    ):
        if content_type and content_type not in self.headers.get("Content-Type", ""):
            raise aiohttp.ContentTypeError(
                self.request_info, (), message="Unexpected local content type"
            )
        return loads(await self.text(encoding))

    @property
    def charset(self):
        value = self.headers.get("Content-Type", "")
        for item in value.split(";")[1:]:
            if item.strip().lower().startswith("charset="):
                return item.split("=", 1)[1].strip(' "')
        return None

    @property
    def content_type(self):
        return (
            self.headers.get("Content-Type", "application/octet-stream")
            .split(";", 1)[0]
            .strip()
            .lower()
        )

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                self.request_info,
                (),
                status=self.status,
                message=self.reason,
                headers=self.headers,
            )

    def close(self):
        self.local.call.abort()

    def release(self):
        if not self.local.eof:
            self.close()

    async def wait_for_close(self):
        await self.aclose()

    async def aclose(self):
        if not self.closed:
            self.closed = True
            try:
                await self.local.aclose()
            finally:
                self.session.responses.discard(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()


class _RequestContext:
    def __init__(self, operation):
        self.operation, self.response = operation, None

    def __await__(self):
        return self.operation.__await__()

    async def __aenter__(self):
        self.response = await self.operation
        return self.response

    async def __aexit__(self, *args):
        await self.response.aclose()


class Session:
    """Target-bound internal ClientSession surface, with no network fallback."""

    def __init__(
        self,
        exchange,
        target,
        *,
        timeout=None,
        headers=None,
        trust_env=False,
        raise_for_status=False,
        **unsupported,
    ):
        if unsupported or trust_env:
            raise TypeError(
                "Unsupported local session options: " + ",".join(unsupported)
            )
        self.exchange, self.target = exchange, target
        self.timeout = timeout or aiohttp.ClientTimeout(total=300)
        self.headers = CIMultiDict(headers or {})
        self.raise_errors = raise_for_status
        self.responses = set()
        self.closed = False

    def request(self, method, url, **options):
        return _RequestContext(self._request(method, url, **options))

    def get(self, url, **options):
        return self.request("GET", url, **options)

    def post(self, url, **options):
        return self.request("POST", url, **options)

    def put(self, url, **options):
        return self.request("PUT", url, **options)

    def delete(self, url, **options):
        return self.request("DELETE", url, **options)

    async def _request(
        self,
        method,
        url,
        *,
        headers=None,
        data=None,
        json=None,
        params=None,
        allow_redirects=False,
        timeout=None,
        ssl=True,
        **unsupported,
    ):
        # Existing callers may supply their TLS policy. No socket or SSL object
        # is created here; the receiving boundary sees plain HTTP plus owns().
        if unsupported:
            raise TypeError(
                "Unsupported local request options: " + ",".join(unsupported)
            )
        if self.closed:
            raise RuntimeError("Local session is closed")
        if allow_redirects:
            raise ValueError("Internal local clients do not follow redirects")
        if json is not None and data is not None:
            raise ValueError("Use data or json, not both")
        url = URL(url)
        if params is not None:
            url = url.extend_query(params)
        merged = self.headers.copy()
        merged.update(headers or {})
        merged.setdefault("Host", url.raw_authority)
        if json is not None:
            data = json_module.dumps(json).encode()
            merged.setdefault("Content-Type", "application/json")
        if isinstance(data, str):
            data = data.encode()
            merged.setdefault("Content-Type", "text/plain; charset=utf-8")
        if data is None:
            data = b""
        if isinstance(data, (bytes, bytearray, memoryview)):
            data = bytes(data)
            merged.setdefault("Content-Length", str(len(data)))

            async def body():
                yield data
        elif hasattr(data, "__aiter__"):
            body = data.__aiter__
        else:
            raise TypeError("Local request data must be bytes or an async byte stream")
        wire_headers = []
        for key, value in merged.items():
            if any(char in key + value for char in "\r\n"):
                raise ValueError("Invalid local header")
            wire_headers.append((key.encode("latin-1"), value.encode("latin-1")))
        policy = self.timeout if timeout is None else timeout
        if not isinstance(policy, aiohttp.ClientTimeout):
            raise TypeError("Local session timeout uses aiohttp.ClientTimeout")
        deadline = None if policy.total is None else time.monotonic() + policy.total
        async with asyncio.timeout(policy.total):
            local = await self.exchange.request(
                self.target, method, str(url), wire_headers, body()
            )
        response = ClientResponse(
            local, self, method, url, merged, deadline, policy.sock_read
        )
        self.responses.add(response)
        if self.raise_errors and response.status >= 400:
            await response.aclose()
            response.raise_for_status()
        return response

    async def close(self):
        self.closed = True
        await asyncio.gather(*(response.aclose() for response in tuple(self.responses)))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
