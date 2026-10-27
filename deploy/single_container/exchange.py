"""Explicit module exchange for one business host; no TCP or global HTTP patches.

Original contract bytes, tokens, middleware and application admission still run.
An origin binds a client to a module; it does not assert TLS. Server requests are
plain local HTTP and owners explicitly accept ``owns(request)`` at their boundary.
"""

import asyncio
import re
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

from .frameworks.channels import ByteChannel

_current = ContextVar("tianshu_local_exchange_call", default=None)


def authority(url):
    parsed = urlsplit(str(url))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Local target needs an explicit HTTP origin")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("Local target cannot contain credentials or a fragment")
    return (
        parsed.scheme,
        parsed.hostname.lower(),
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def target_name(name):
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None
    ):
        raise ValueError("Local target must be an explicit module name")
    return name


@dataclass
class Request:
    method: str
    url: str
    headers: list
    body: object


class Response:
    def __init__(self, call, status, headers):
        self.call, self.status, self.headers = call, status, headers
        self.body = call.output
        self.eof = False

    async def receive(self):
        part = await self.body.receive()
        if part is None:
            self.eof = True
        return part

    async def aclose(self):
        if not self.eof and not self.call.task.done():
            self.call.abort()
        await asyncio.gather(self.call.task, return_exceptions=True)
        await self.body.finish(discard=True)


class _Call:
    def __init__(self, exchange, adapter, request):
        self.exchange, self.adapter, self.request = exchange, adapter, request
        self.input = ByteChannel(exchange.buffer_bytes)
        self.output = ByteChannel(exchange.buffer_bytes)
        self.headers = asyncio.get_running_loop().create_future()
        self.disconnected = asyncio.Event()
        self.task = None
        self.server_request = None
        self.completing = False
        self.disconnect_task = None

    def start_response(self, status, headers):
        if self.headers.done():
            raise RuntimeError("Local response headers already sent")
        self.headers.set_result(Response(self, status, headers))

    def abort(self):
        if self.disconnected.is_set():
            return
        self.disconnected.set()

        async def disconnect():
            error = ConnectionResetError("Local caller disconnected")
            await self.input.finish(error, discard=True)
            await self.output.finish(error, discard=True)

        self.disconnect_task = asyncio.create_task(disconnect())
        if self.server_request is not None:
            self.server_request._cancel(
                ConnectionResetError("Local caller disconnected")
            )
        if self.task is not None and not self.task.done() and not self.completing:
            self.task.cancel()

    async def _feed(self):
        try:
            async for part in self.request.body:
                if not isinstance(part, bytes):
                    raise TypeError("Local request streams yield bytes")
                await self.input.send(part)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - forward producer failures to the receiver
            await self.input.finish(error)
        else:
            await self.input.finish()
        finally:
            close = getattr(self.request.body, "aclose", None)
            if close is not None:
                await close()

    async def run(self):
        token = _current.set(self)
        feeder = asyncio.create_task(self._feed())
        try:
            await self.adapter.handle(self)
            if not self.headers.done():
                raise RuntimeError("Application ended without response headers")
        except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 - propagate across stream boundary
            if not self.headers.done():
                self.headers.set_exception(error)
            await self.output.finish(
                ConnectionResetError("Local request cancelled")
                if isinstance(error, asyncio.CancelledError)
                else error
            )
        finally:
            # A caller closing an unread response may race this cleanup. Never
            # inject a second cancellation between its resource release steps.
            self.completing = True
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
            if self.disconnect_task is not None:
                await self.disconnect_task
            await self.input.finish(
                ConnectionResetError("Local request finished"), discard=True
            )
            await self.output.finish()
            self.exchange._unmark(self)
            _current.reset(token)


class LocalExchange:
    def __init__(self, *, buffer_bytes=65536):
        if type(buffer_bytes) is not int or buffer_bytes < 1024:
            raise ValueError("Local stream buffer must be at least 1024 bytes")
        self.buffer_bytes = buffer_bytes
        self.loop = self.loop_thread = None
        self._adapters = {}
        self._origins = {}
        self._calls = set()
        self._owned = {}
        self._state = "new"
        self._close_task = None

    def register(self, name, app, framework, origin):
        target_name(name)
        if self._state != "new" or name in self._adapters:
            raise RuntimeError("Register each local module once, before open")
        parsed = urlsplit(origin)
        bound = authority(origin)
        if parsed.path not in {"", "/"} or parsed.query:
            raise ValueError("Register an origin, without a path or query")
        if framework == "aiohttp":
            from .frameworks.aiohttp import Application
        elif framework == "asgi":
            from .frameworks.asgi import Application
        else:
            raise ValueError("Local framework must be aiohttp or asgi")
        self._adapters[name] = Application(app)
        self._origins[name] = bound

    def owns(self, request):
        raw = getattr(request, "scope", request)
        entry = self._owned.get(id(raw))
        return entry is not None and entry[0] is raw

    def _mark(self, call, request):
        self._owned[id(request)] = (request, call)

    def _unmark(self, call):
        for key, (_, owner) in tuple(self._owned.items()):
            if owner is call:
                del self._owned[key]

    async def open(self):
        if self._state != "new":
            raise RuntimeError("Local exchange can be opened once")
        self.loop = asyncio.get_running_loop()
        self.loop_thread = threading.get_ident()
        self._state = "opening"
        try:
            for adapter in self._adapters.values():
                await adapter.open()
        except BaseException:
            await self.close(grace=0)
            raise
        self._state = "open"
        return self

    async def request(self, target, method, url, headers, body):
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("Async local requests belong to the host loop")
        parent = _current.get()
        reentrant = parent in self._calls
        if self._state not in {"open", "opening"} and not (
            self._state == "closing" and reentrant
        ):
            raise RuntimeError("Local exchange is not accepting new requests")
        if target not in self._adapters:
            raise RuntimeError("Local target is not registered")
        adapter = self._adapters[target]
        if not adapter.started:
            raise RuntimeError("Target module has not completed startup")
        if authority(url) != self._origins[target]:
            raise ValueError("Request origin differs from its explicitly bound target")
        call = _Call(
            self, adapter, Request(method.upper(), str(url), list(headers), body)
        )
        self._calls.add(call)
        call.task = asyncio.create_task(call.run())

        def finished(task):
            self._calls.discard(call)
            if not call.headers.done():
                # Includes cancellation before the new task ever runs.
                call.headers.set_exception(
                    ConnectionResetError("Local request ended before headers")
                )

        call.task.add_done_callback(finished)
        try:
            return await asyncio.shield(call.headers)
        except BaseException:
            call.abort()
            await asyncio.gather(call.task, return_exceptions=True)
            # Consume a late header failure after cancellation, too.
            if call.headers.done() and not call.headers.cancelled():
                call.headers.exception()
            raise

    async def close(self, *, grace=30):
        if _current.get() in self._calls:
            raise RuntimeError("A module cannot close its own active exchange")
        if self._state == "closed":
            return
        if self._close_task is None:
            self._state = "closing"
            self._close_task = asyncio.create_task(self._close(grace))
        await asyncio.shield(self._close_task)

    async def _close(self, grace):
        self._state = "closing"

        async def drain():
            # Callback children can be admitted while an original call drains.
            while self._calls:
                calls = tuple(self._calls)
                await asyncio.gather(
                    *(call.task for call in calls), return_exceptions=True
                )
                # gather(done_tasks) may return without yielding to their done
                # callbacks. Retire them here rather than busy-looping on them.
                self._calls.difference_update(
                    call for call in calls if call.task.done()
                )

        draining = asyncio.create_task(drain())
        try:
            if grace is None:
                await draining
            else:
                async with asyncio.timeout(grace):
                    await asyncio.shield(draining)
        except TimeoutError:
            while self._calls:
                calls = tuple(self._calls)
                for call in calls:
                    call.abort()
                await asyncio.gather(
                    *(call.task for call in calls), return_exceptions=True
                )
                self._calls.difference_update(
                    call for call in calls if call.task.done()
                )
            await draining
        finally:
            # No application resource is closed before request finally blocks.
            if not self._calls:
                errors = []
                for adapter in reversed(tuple(self._adapters.values())):
                    try:
                        await adapter.close()
                    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 - finish all owners, then raise
                        errors.append(error)
                self._state = "closed"
                if errors:
                    raise BaseExceptionGroup("Local application cleanup failed", errors)

    def session_factory(self, target):
        from .frameworks.aiohttp import Session

        target_name(target)
        return lambda **options: Session(self, target, **options)

    def async_transport(self, target):
        from .frameworks.httpx import AsyncTransport

        target_name(target)
        return AsyncTransport(self, target)

    def sync_transport(self, target):
        from .frameworks.httpx import SyncTransport

        target_name(target)
        return SyncTransport(self, target)
