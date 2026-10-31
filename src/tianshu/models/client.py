"""The process client. Writes are never retried automatically after an unknown result."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json

import aiohttp

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.runtime.cancellation import finish_cleanup
from .codec import event_from_wire, receipt_from_wire, result_from_wire


class ModelClient:
    def __init__(self, base_url, token, *, max_connections=16):
        self.base_url, self.token = base_url.rstrip("/"), token
        self.max_connections = max_connections
        self.session = None
        self.control_session = None

    async def open(self):
        headers = {"Authorization": "Bearer "+self.token}
        self.session = aiohttp.ClientSession(headers=headers,
            connector=aiohttp.TCPConnector(limit=self.max_connections),
            timeout=aiohttp.ClientTimeout(total=None))
        self.control_session = aiohttp.ClientSession(headers=headers,
            connector=aiohttp.TCPConnector(limit=4), timeout=aiohttp.ClientTimeout(total=5))

    async def close(self):
        for session in (self.session, self.control_session):
            if session:
                await session.close()

    async def _json(self, path, *, body=None, control=False, timeout=None, method="POST"):
        session = self.control_session if control else self.session
        async with session.request(method, self.base_url+path, json=wire(body),
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout or (5 if control else 120))) as response:
            value = await response.json()
            if response.status >= 400:
                error = value.get("error", {})
                raise AppError(error.get("code", "broker_error"), error.get("message", ""),
                               status=response.status, details=error.get("details"))
            return value

    async def generate(self, request):
        try:
            value = await self._json("/internal/v1/models/generate", body=request,
                                     timeout=request.budget.remaining())
            return result_from_wire(value)
        except (asyncio.CancelledError, TimeoutError, aiohttp.ClientError):
            await self._cancel_cleanup(request.attempt_id)
            raise

    async def stream(self, request):
        complete = False
        try:
            async with self.session.post(self.base_url+"/internal/v1/models/stream", json=wire(request),
                timeout=aiohttp.ClientTimeout(total=request.budget.remaining())) as response:
                if response.status >= 400:
                    value = await response.json()
                    raise AppError(value.get("error", {}).get("code", "broker_error"), status=response.status)
                async for line in response.content:
                    if len(line) > 4*1024*1024:
                        raise AppError("broker_event_too_large", status=502)
                    event = event_from_wire(json.loads(line))
                    complete |= event.kind == "receipt"
                    yield event
        finally:
            if not complete:
                await self._cancel_cleanup(request.attempt_id)

    async def _cancel_cleanup(self, attempt_id):
        try:
            await finish_cleanup(self.cancel(attempt_id))
        except (AppError, TimeoutError, aiohttp.ClientError):
            # The durable attempt retains its original identity for subsequent status reads.
            pass

    async def status(self, attempt_id):
        try:
            value = await self._json("/internal/v1/models/attempts/"+attempt_id,
                                     control=True, method="GET")
            return receipt_from_wire(value["receipt"])
        except AppError as exc:
            if exc.status == 404:
                return None
            raise

    async def cancel(self, attempt_id):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/cancel", control=True)
        return receipt_from_wire(value["receipt"])

    async def reload(self):
        return await self._json("/internal/v1/models/reload", control=True)

    async def snapshot(self):
        return await self._json("/health/ready", control=True, method="GET")

    async def embedding_profile(self):
        return await self._json("/internal/v1/models/embedding-profile", control=True, method="GET")

    @asynccontextmanager
    async def resource(self, request):
        await self._json("/internal/v1/resources", body=request, timeout=request.budget.remaining())
        permit = ClientExternalPermit(self, request)
        owner = asyncio.current_task()

        async def watch_cancel():
            while not permit.done:
                await asyncio.sleep(0.5)
                try:
                    value = await self._json("/internal/v1/models/attempts/"+request.attempt_id,
                                             control=True, method="GET")
                    if value["cancel_requested"] or value["receipt"]["status"] in {"unknown", "cancelled"}:
                        owner.cancel()
                        return
                except (AppError, aiohttp.ClientError, TimeoutError):
                    # A lost owner cannot safely authorize more external work.
                    owner.cancel()
                    return

        watcher = asyncio.create_task(watch_cancel())
        try:
            async with request.budget.timeout():
                yield permit
        except BaseException:
            await finish_cleanup(permit.unknown("external_interrupted"))
            raise
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if not permit.done:
                await finish_cleanup(permit.unknown("external_missing_receipt"))


class ClientExternalPermit:
    def __init__(self, client, request):
        self.client, self.request, self.done = client, request, False

    async def _update(self, operation, body):
        value = await self.client._json("/internal/v1/resources/"+self.request.attempt_id+"/"+operation,
                                        body=body, control=True)
        return receipt_from_wire(value["receipt"])

    async def submitted(self, provider_handle=None):
        return await self._update("submitted", {"provider_handle": provider_handle})

    async def finish(self, usage=None, result=None):
        result = await self._update("finish", {"usage": usage, "result": result})
        self.done = True
        return result

    async def unknown(self, error_code="external_effect_unknown"):
        if self.done:
            return await self.client.status(self.request.attempt_id)
        try:
            return await self._update("unknown", {"error_code": error_code})
        except (AppError, aiohttp.ClientError, TimeoutError):
            return await self.client.status(self.request.attempt_id)
        finally:
            self.done = True
