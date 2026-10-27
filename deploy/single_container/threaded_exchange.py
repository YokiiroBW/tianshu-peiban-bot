"""One original application/Store per owner thread, joined by bounded bytes."""

import asyncio
from concurrent.futures import Future
from contextvars import ContextVar
import inspect
import threading
from urllib.parse import urlsplit

from .exchange import LocalExchange, authority, target_name
from .loop_bridge import (
    BodyBridge,
    LoopCall,
    ResponseBridge,
    TargetUnavailable,
    cleanup,
    completed,
)

_lineage = ContextVar("tianshu_threaded_call", default=None)


def plain(value):
    if value is None or type(value) in {bool, int, float, str, bytes}:
        return value
    if type(value) in {list, tuple}:
        return type(value)(plain(item) for item in value)
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: plain(item) for key, item in value.items()}
    raise TypeError("Module invoke must return ordinary values, not an owner resource")


class ModulePorts:
    async_transport = LocalExchange.async_transport
    sync_transport = LocalExchange.sync_transport
    session_factory = LocalExchange.session_factory

    def __init__(self, router, owner):
        self.router, self.owner = router, owner
        self.loop, self.loop_thread = owner.loop, threading.get_ident()

    def owns(self, request):
        return self.owner.local.owns(request)

    async def request(self, target, method, url, headers, body):
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("Module clients belong to their owner loop")
        return await self.router.request(target, method, url, headers, body)


class _Owner:
    def __init__(self, name, factory, framework, origin, buffer_bytes):
        self.name, self.factory, self.framework, self.origin = (
            name,
            factory,
            framework,
            origin,
        )
        self.local = LocalExchange(buffer_bytes=buffer_bytes)
        self.loop = self.thread = self.app = self.ports = None
        self.ready, self.exited = Future(), Future()
        self.available = Future()
        self.close_call = None
        self.started = False

    def _thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self.ready.set_result(None)
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            self.exited.set_result(None)

    async def start(self, router):
        self.thread = threading.Thread(
            target=self._thread, name="tianshu-" + self.name, daemon=True
        )
        self.thread.start()
        await completed(self.ready)

        async def create():
            self.ports = ModulePorts(router, self)
            value = self.factory(self.ports)
            self.app = await value if inspect.isawaitable(value) else value
            self.local.register(self.name, self.app, self.framework, self.origin)
            await self.local.open()
            self.started = True

        try:
            await LoopCall(self.loop, create).wait()
        except BaseException:
            if not self.available.done():
                self.available.set_exception(
                    TargetUnavailable("Target module startup failed")
                )
            raise
        else:
            if not self.available.done():
                self.available.set_result(None)

    def begin_close(self):
        if self.close_call is None:
            self.close_call = LoopCall(self.loop, self.local.close)
        return self.close_call


class _Record:
    def __init__(self, router, owner, method, url, headers, body):
        self.router, self.owner = router, owner
        self.method, self.url, self.headers = method, url, list(headers)
        self.body = body
        self.response = self.released = self.operation = None
        self.head, self.done = Future(), Future()
        self.aborted = False
        self.serving_task = None
        self.failure = None

    async def serve(self):
        self.serving_task = asyncio.current_task()
        token = _lineage.set(self)
        self.released = asyncio.Event()
        try:
            if self.aborted:
                raise ConnectionResetError("Local caller disconnected before dispatch")
            self.response = await self.owner.local.request(
                self.owner.name, self.method, self.url, self.headers, self.body
            )
            self.head.set_result((self.response.status, list(self.response.headers)))
            if self.aborted:
                self.response.call.abort()
                self.released.set()
            await asyncio.shield(self.response.call.task)
            await self.released.wait()
        except BaseException as error:  # noqa: BLE001 - preserve failure before headers
            if not self.head.done():
                self.head.set_exception(error)
        finally:
            try:
                if self.response is not None:
                    await self.response.aclose()
                await self.body.aclose()
            except BaseException as error:  # noqa: BLE001 - retain cleanup failure, still acknowledge completion
                self.failure = error
                with self.router._lock:
                    self.router._errors.append(error)
            finally:
                _lineage.reset(token)

    def abort(self):
        if self.done.done():
            return

        def stop():
            if self.aborted:
                return
            self.aborted = True
            if self.response is not None:
                self.response.call.abort()
                self.released.set()
            elif self.serving_task is not None:
                self.serving_task.cancel()

        try:
            self.owner.loop.call_soon_threadsafe(stop)
        except RuntimeError:
            if not self.done.done():
                raise

    async def receive(self):
        part = await self.response.receive()
        if part is None:
            self.released.set()
        return part


class ThreadedExchange:
    async_transport = LocalExchange.async_transport
    sync_transport = LocalExchange.sync_transport
    session_factory = LocalExchange.session_factory

    def __init__(self, *, buffer_bytes=65536):
        # Reuse the original channel bound validation.
        LocalExchange(buffer_bytes=buffer_bytes)
        self.buffer_bytes = buffer_bytes
        self.loop = self.loop_thread = None
        self._owners, self._invocations = {}, {}
        self._records = set()
        self._errors = []
        self._lock = threading.Lock()
        self._state, self._close_task = "new", None

    def register_factory(self, name, factory, framework, origin):
        target_name(name)
        if self._state != "new" or name in self._owners:
            raise RuntimeError("Register each module factory once, before open")
        parsed = urlsplit(origin)
        authority(origin)
        if (
            parsed.path not in {"", "/"}
            or parsed.query
            or framework not in {"asgi", "aiohttp"}
        ):
            raise ValueError(
                "Register an original framework and an origin without a path"
            )
        self._owners[name] = _Owner(name, factory, framework, origin, self.buffer_bytes)

    def owns(self, request):
        thread = threading.get_ident()
        return any(
            owner.ports is not None
            and owner.ports.loop_thread == thread
            and owner.ports.owns(request)
            for owner in self._owners.values()
        )

    async def open(self):
        if self._state != "new":
            raise RuntimeError("Threaded exchange can be opened once")
        self.loop, self.loop_thread = asyncio.get_running_loop(), threading.get_ident()
        self._state = "opening"
        try:
            for owner in self._owners.values():
                await owner.start(self)
        except BaseException:
            for owner in self._owners.values():
                if not owner.available.done():
                    owner.available.set_exception(
                        TargetUnavailable("Host startup did not complete")
                    )
            await self.close(grace=0)
            raise
        with self._lock:
            self._state = "open"
        return self

    def _admit(self):
        parent = _lineage.get()
        reentrant = parent in self._records or parent in self._invocations
        if self._state not in {"open", "opening"} and not (
            self._state == "closing" and reentrant
        ):
            raise TargetUnavailable(
                "Threaded exchange is not accepting new root requests"
            )

    async def request(self, target, method, url, headers, body):
        with self._lock:
            self._admit()
            if target not in self._owners:
                raise RuntimeError("Local target is not registered")
            owner = self._owners[target]
            if authority(url) != authority(owner.origin):
                raise ValueError(
                    "Request origin differs from its explicitly bound target"
                )
        upload = BodyBridge(asyncio.get_running_loop(), body, self.buffer_bytes)
        record = None
        try:
            await completed(owner.available)
            with self._lock:
                self._admit()
                record = _Record(self, owner, method, url, headers, upload)
                self._records.add(record)
            record.operation = LoopCall(owner.loop, record.serve)

            def retired(_):
                if not record.head.done():
                    record.head.set_exception(
                        ConnectionResetError("Local request ended before dispatch")
                    )
                with self._lock:
                    self._records.discard(record)
                record.done.set_result(None)

            record.operation.ack.add_done_callback(retired)
            status, response_headers = await completed(record.head)
            return ResponseBridge(record, status, response_headers)
        except BaseException:
            if record is None:
                await upload.aclose()
            else:
                record.abort()
                await cleanup(record.done)
            raise

    async def invoke(self, name, operation):
        token = object()
        with self._lock:
            self._admit()
            owner = self._owners.get(name)
            if owner is None:
                raise RuntimeError("Local target is not registered")
        await completed(owner.available)

        async def run():
            context = _lineage.set(token)
            try:
                value = operation(owner.app)
                return plain(await value if inspect.isawaitable(value) else value)
            finally:
                _lineage.reset(context)

        with self._lock:
            self._admit()
            call = LoopCall(owner.loop, run)
            self._invocations[token] = call

        def retired(_):
            with self._lock:
                self._invocations.pop(token, None)

        call.ack.add_done_callback(retired)
        return await call.wait()

    async def close(self, *, grace=30, cleanup_timeout=30):
        """True only after real cleanup and thread joins; false keeps owners alive."""
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("Close the threaded exchange from its host loop")
        with self._lock:
            if _lineage.get() in self._records or _lineage.get() in self._invocations:
                raise RuntimeError(
                    "An active module call cannot close its own exchange"
                )
            if self._state == "closed":
                return True
            self._state = "closing"
            for owner in self._owners.values():
                if not owner.available.done():
                    owner.available.set_exception(
                        TargetUnavailable("Module startup was interrupted")
                    )
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._close(grace, cleanup_timeout))
        return await asyncio.shield(self._close_task)

    async def _settle(self):
        while True:
            with self._lock:
                futures = [record.done for record in self._records]
                futures += [call.ack for call in self._invocations.values()]
            if not futures:
                return
            await asyncio.gather(*(completed(future) for future in futures))

    async def _close(self, grace, cleanup_timeout):
        errors = []
        try:
            async with asyncio.timeout(grace):
                await self._settle()
        except TimeoutError:
            with self._lock:
                records = tuple(self._records)
                invocations = tuple(self._invocations.values())
            for record in records:
                record.abort()
            for call in invocations:
                call.cancel()
        try:
            async with asyncio.timeout(cleanup_timeout):
                await self._settle()
                for owner in reversed(tuple(self._owners.values())):
                    if owner.thread is None:
                        continue
                    close = owner.begin_close()
                    await completed(close.ack)
                    try:
                        close.result.result()
                    except BaseException as error:  # noqa: BLE001 - close every owner before reporting failures
                        errors.append(error)
                    if not owner.exited.done():
                        owner.loop.call_soon_threadsafe(owner.loop.stop)
                    await completed(owner.exited)
                    await asyncio.to_thread(owner.thread.join)
        except TimeoutError:
            return False
        with self._lock:
            self._state = "closed"
            errors += self._errors
        if errors:
            raise BaseExceptionGroup("Module cleanup failed", errors)
        return True
