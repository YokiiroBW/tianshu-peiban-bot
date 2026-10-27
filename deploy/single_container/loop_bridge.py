"""Owner-loop calls and pull-driven bytes; no shared asyncio primitives."""

import asyncio
from concurrent.futures import Future
import inspect


class TargetUnavailable(ConnectionError):
    """The registered target has not admitted a handler; execution has not begun."""


async def completed(future):
    wrapped = asyncio.wrap_future(future)
    # A cancelled waiter must not leave a late remote failure unobserved.
    wrapped.add_done_callback(
        lambda value: None if value.cancelled() else value.exception()
    )
    return await asyncio.shield(wrapped)


async def cleanup(future):
    """A second cancellation cannot interrupt the remote finally acknowledgement."""
    while True:
        try:
            return await completed(future)
        except asyncio.CancelledError:
            if future.done():
                return future.result()


class LoopCall:
    def __init__(self, loop, operation):
        self.loop, self.operation = loop, operation
        self.result, self.ack = Future(), Future()
        self.task = None
        self.cancelled = False
        loop.call_soon_threadsafe(self._start)

    def _start(self):
        async def run():
            value = self.operation()
            return await value if inspect.isawaitable(value) else value

        self.task = self.loop.create_task(run())
        self.task.add_done_callback(self._settled)
        if self.cancelled:
            self.task.cancel()

    def _settled(self, task):
        try:
            self.result.set_result(task.result())
        except BaseException as error:  # noqa: BLE001 - forward remote failure, including cancellation
            self.result.set_exception(error)
        finally:
            # Distinct from result cancellation: this means the actual task's
            # finally blocks have ended, even if it never reached its first step.
            self.ack.set_result(None)

    def cancel(self):
        if self.ack.done():
            return

        def stop():
            if not self.cancelled:
                self.cancelled = True
                if self.task is not None and not self.task.done():
                    self.task.cancel()

        try:
            self.loop.call_soon_threadsafe(stop)
        except RuntimeError:
            if not self.ack.done():
                raise

    async def wait(self):
        try:
            return await completed(self.result)
        except asyncio.CancelledError:
            self.cancel()
            await cleanup(self.ack)
            raise


class BodyBridge:
    """Pull and close the original iterator on its loop, at most one piece ahead."""

    def __init__(self, loop, body, limit):
        self.loop, self.body, self.limit = loop, body, limit
        self.iterator = None
        self.pending = memoryview(b"")
        self.max_piece = 0
        self.closing = None

    def __aiter__(self):
        return self

    async def _next(self):
        if self.iterator is None:
            self.iterator = self.body.__aiter__()
        while not self.pending:
            try:
                value = await anext(self.iterator)
            except StopAsyncIteration:
                return None
            if not isinstance(value, bytes):
                raise TypeError("Local request streams yield bytes")
            self.pending = memoryview(value)
        part = bytes(self.pending[: self.limit])
        self.pending = self.pending[len(part) :]
        self.max_piece = max(self.max_piece, len(part))
        return part

    async def __anext__(self):
        part = await LoopCall(self.loop, self._next).wait()
        if part is None:
            raise StopAsyncIteration
        return part

    async def _close(self):
        errors = []
        values = (
            [self.body] if self.iterator is self.body else [self.iterator, self.body]
        )
        try:
            for value in values:
                close = getattr(value, "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except BaseException as error:  # noqa: BLE001 - finish both iterator and stream owners
                        errors.append(error)
        finally:
            self.pending = memoryview(b"")
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Local upload cleanup failed", errors)

    async def aclose(self):
        if self.closing is None:
            self.closing = LoopCall(self.loop, self._close)
        await cleanup(self.closing.ack)
        self.closing.result.result()


class ResponseBridge:
    def __init__(self, record, status, headers):
        self.record, self.status, self.headers = record, status, headers
        self.eof = False
        self.call = record  # Existing aiohttp response.close uses call.abort().

    async def receive(self):
        try:
            part = await LoopCall(self.record.owner.loop, self.record.receive).wait()
            if part is None:
                self.eof = True
            return part
        except BaseException:
            self.record.abort()
            await cleanup(self.record.done)
            raise

    async def aclose(self):
        if not self.record.done.done():
            self.record.abort()
            await cleanup(self.record.done)
        if self.record.failure is not None:
            raise self.record.failure
