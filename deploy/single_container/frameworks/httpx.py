"""HTTPX transports bound to one registered module, with incremental bodies."""

import asyncio
import queue
import threading

import httpx
from ..loop_bridge import TargetUnavailable


async def _read(response, timeout):
    try:
        async with asyncio.timeout(timeout):
            return await response.receive()
    except TimeoutError as error:
        await response.aclose()
        raise httpx.ReadTimeout("Local response read timed out") from error
    except (OSError, ConnectionError) as error:
        await response.aclose()
        raise httpx.ReadError("Local response stream failed") from error


async def _start(exchange, target, request, body):
    try:
        async with asyncio.timeout(request.extensions.get("timeout", {}).get("read")):
            return await exchange.request(
                target, request.method, str(request.url), request.headers.raw, body
            )
    except TimeoutError as error:
        raise httpx.ReadTimeout(
            "Local response headers timed out", request=request
        ) from error
    except TargetUnavailable as error:
        raise httpx.ConnectError("Local target unavailable", request=request) from error


class _AsyncStream(httpx.AsyncByteStream):
    def __init__(self, response, timeout):
        self.response, self.timeout = response, timeout

    async def __aiter__(self):
        try:
            while (part := await _read(self.response, self.timeout)) is not None:
                yield part
        except BaseException:
            await self.response.aclose()
            raise

    async def aclose(self):
        await self.response.aclose()


class AsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, exchange, target):
        self.exchange, self.target = exchange, target
        self.closed = False

    async def handle_async_request(self, request):
        if self.closed:
            raise RuntimeError("Local HTTPX transport is closed")
        response = await _start(self.exchange, self.target, request, request.stream)
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_AsyncStream(
                response, request.extensions.get("timeout", {}).get("read")
            ),
            extensions={"http_version": b"HTTP/1.1"},
        )

    async def aclose(self):
        self.closed = True


class _ThreadBody:
    """Pull a synchronous upload on its owner thread, never on the event loop.

    The waiting worker services one pull mailbox while the host coroutine runs.
    No extra thread, whole-body buffer, or shared executor is needed, including
    when the calling worker is itself the host's only available executor thread.
    """

    def __init__(self, exchange, stream):
        self.exchange = exchange
        self.stream = iter(stream)
        self.pulls = queue.Queue()

    async def __aiter__(self):
        while True:
            result = asyncio.get_running_loop().create_future()
            self.pulls.put(result)
            present, part = await result
            if not present:
                return
            yield part

    @staticmethod
    def _deliver(future, value=None, error=None):
        if future.done():
            return
        if error is None:
            future.set_result(value)
        else:
            future.set_exception(error)

    def wait(self, operation):
        if threading.get_ident() == self.exchange.loop_thread:
            operation.close()
            raise RuntimeError(
                "Synchronous local streams cannot run on the host loop thread"
            )
        if self.exchange.loop is None or not self.exchange.loop.is_running():
            operation.close()
            raise RuntimeError("Local exchange host loop is not running")
        future = asyncio.run_coroutine_threadsafe(operation, self.exchange.loop)
        future.add_done_callback(lambda _: self.pulls.put(None))
        try:
            while not future.done():
                pull = self.pulls.get()
                if pull is None:
                    continue
                if pull.done():
                    continue
                try:
                    value, error = (True, next(self.stream)), None
                except StopIteration:
                    value, error = (False, b""), None
                except Exception as failure:  # noqa: BLE001 - propagate on the host future
                    value, error = None, failure
                self.exchange.loop.call_soon_threadsafe(
                    self._deliver, pull, value, error
                )
            return future.result()
        except BaseException:
            future.cancel()
            raise


class _SyncStream(httpx.SyncByteStream):
    def __init__(self, response, body, timeout):
        self.response, self.body, self.timeout = response, body, timeout

    def __iter__(self):
        while (part := self.body.wait(_read(self.response, self.timeout))) is not None:
            yield part

    def close(self):
        self.body.wait(self.response.aclose())


class SyncTransport(httpx.BaseTransport):
    def __init__(self, exchange, target):
        self.exchange, self.target = exchange, target
        self.closed = False

    def handle_request(self, request):
        if threading.get_ident() == self.exchange.loop_thread:
            raise RuntimeError(
                "Synchronous local transport cannot run on the host loop thread"
            )
        if self.exchange.loop is None or not self.exchange.loop.is_running():
            raise RuntimeError("Local exchange host loop is not running")
        if self.closed:
            raise RuntimeError("Local HTTPX transport is closed")
        body = _ThreadBody(self.exchange, request.stream)
        response = body.wait(_start(self.exchange, self.target, request, body))
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_SyncStream(
                response, body, request.extensions.get("timeout", {}).get("read")
            ),
            extensions={"http_version": b"HTTP/1.1"},
        )

    def close(self):
        self.closed = True
