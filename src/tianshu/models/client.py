"""The process client. Writes are never retried automatically after an unknown result."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json

import aiohttp

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.runtime.cancellation import finish_cleanup
from tianshu.types import RequestBudget, ServiceClass
from .codec import event_from_wire, receipt_from_wire, result_from_wire
from .timing import waiting_timeout

_DEFAULT_TIMEOUT = object()


def _known_wait_expiry(receipt):
    if (receipt is not None and receipt.status == "cancelled" and receipt.dispatch == "not_started"
            and receipt.error_code in {"queue_wait_expired", "business_deadline_expired"}):
        return AppError(receipt.error_code, status=408,
                        details={"status": "cancelled", "dispatch": "not_started"})
    return None


class ModelClient:
    def __init__(self, base_url, token, *, max_connections=8,
                 due_connections=4, background_connections=24, control_connections=4):
        self.base_url, self.token = base_url.rstrip("/"), token
        if any(type(count) is not int or count < 1 for count in (
            max_connections, due_connections, background_connections, control_connections,
        )):
            raise ValueError("positive_model_transport_capacity_required")
        self.max_connections = max_connections
        self.due_connections = due_connections
        self.background_connections = background_connections
        self.control_connections = control_connections
        self.session = None
        self.due_session = None
        self.background_session = None
        self.control_session = None

    async def open(self):
        headers = {"Authorization": "Bearer "+self.token}
        self.session = aiohttp.ClientSession(headers=headers,
            connector=aiohttp.TCPConnector(limit=self.max_connections),
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=5), read_bufsize=8*1024*1024)
        self.due_session = aiohttp.ClientSession(headers=headers,
            connector=aiohttp.TCPConnector(limit=self.due_connections),
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=5), read_bufsize=8*1024*1024)
        self.background_session = aiohttp.ClientSession(headers=headers,
            connector=aiohttp.TCPConnector(limit=self.background_connections),
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=5), read_bufsize=8*1024*1024)
        self.control_session = aiohttp.ClientSession(headers=headers,
            connector=aiohttp.TCPConnector(limit=self.control_connections),
            timeout=aiohttp.ClientTimeout(total=5, sock_connect=5))

    async def close(self):
        for session in (self.session, self.due_session, self.background_session, self.control_session):
            if session:
                await session.close()

    def _data_session(self, service_class):
        if service_class == ServiceClass.ELASTIC:
            return self.background_session
        if service_class == ServiceClass.DUE:
            return self.due_session
        if service_class == ServiceClass.INTERACTIVE:
            return self.session
        raise AppError("invalid_model_service_class")

    async def _json(self, path, *, body=None, control=False, timeout=_DEFAULT_TIMEOUT, method="POST",
                    service_class=ServiceClass.INTERACTIVE):
        session = self.control_session if control else self._data_session(service_class)
        async with session.request(method, self.base_url+path, json=wire(body),
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=(
                (5 if control else 120) if timeout is _DEFAULT_TIMEOUT else timeout), sock_connect=5)) as response:
            value = await response.json()
            if response.status >= 400:
                error = value.get("error", {})
                raise AppError(error.get("code", "broker_error"), error.get("message", ""),
                               status=response.status, details=error.get("details"))
            return value

    async def generate(self, request):
        timeout = waiting_timeout(request)
        try:
            value = await self._json("/internal/v1/models/generate", body=request,
                                     timeout=timeout,
                                     service_class=request.service_class)
            return result_from_wire(value)
        except (asyncio.CancelledError, TimeoutError, aiohttp.ClientError) as exc:
            receipt = await self._cancel_cleanup(request.attempt_id)
            known = _known_wait_expiry(receipt)
            if known is not None and not isinstance(exc, asyncio.CancelledError):
                raise known from None
            raise

    async def stream(self, request):
        timeout = waiting_timeout(request)
        complete = False
        cleaned_up = False
        try:
            async with self._data_session(request.service_class).post(
                self.base_url+"/internal/v1/models/stream", json=wire(request),
                timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=5)) as response:
                if response.status >= 400:
                    value = await response.json()
                    raise AppError(value.get("error", {}).get("code", "broker_error"), status=response.status)
                async for line in response.content:
                    if len(line) > 8*1024*1024:
                        raise AppError("broker_event_too_large", status=502)
                    value = json.loads(line)
                    if value.get("kind") == "error":
                        complete = True
                        failure = value["error"]
                        raise await self._stream_failure(request.attempt_id, failure["code"],
                            status=failure.get("status", 502), fallback=receipt_from_wire(failure.get("receipt")))
                    event = event_from_wire(value)
                    complete |= event.kind == "receipt"
                    yield event
                if not complete:
                    complete = True
                    raise await self._stream_failure(request.attempt_id, "model_stream_incomplete")
        except (TimeoutError, aiohttp.ClientError):
            cleaned_up = True
            raise await self._stream_failure(request.attempt_id, "model_stream_interrupted") from None
        finally:
            if not complete and not cleaned_up:
                await self._cancel_cleanup(request.attempt_id)

    async def _stream_failure(self, attempt_id, code, *, status=502, fallback=None):
        try:
            original = await self.status(attempt_id)
        except (AppError, TimeoutError, aiohttp.ClientError):
            original = fallback
        if original is None or original.status in {"queued", "running"}:
            original = await self._cancel_cleanup(attempt_id) or original
        known = _known_wait_expiry(original)
        if known is not None:
            return known
        return AppError(code, status=status, details={"attempt_id":attempt_id,
            "status":original.status if original else None,
            "dispatch":original.dispatch if original else None,"action":"observe_original_attempt"})

    async def _cancel_cleanup(self, attempt_id):
        try:
            return await finish_cleanup(self.cancel(attempt_id))
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

    async def result(self, attempt_id, *, context):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/result",
                                 body={"context": context}, control=True)
        return result_from_wire(value["result"]) if value["result"] is not None else None

    async def cancel(self, attempt_id):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/cancel", control=True)
        return receipt_from_wire(value["receipt"])

    async def reload(self):
        return await self._json("/internal/v1/models/reload", control=True)

    async def snapshot(self):
        return await self._json("/health/ready", control=True, method="GET")

    async def embedding_profile(self):
        return await self._json("/internal/v1/models/embedding-profile", control=True, method="GET")

    async def generation_profile(self, request):
        return await self._json("/internal/v1/models/generation-profile", body=request,
                                control=True, timeout=min(10, request.budget.remaining()))

    @asynccontextmanager
    async def resource(self, request):
        timeout = waiting_timeout(request)
        try:
            value = await self._json("/internal/v1/resources", body=request,
                             timeout=timeout, service_class=request.service_class)
            if request.execution_timeout_seconds is not None:
                admitted = receipt_from_wire(value["receipt"])
                if admitted.deadline_at is None:
                    raise AppError("external_execution_deadline_missing", status=502)
                request = replace(request, budget=RequestBudget(admitted.deadline_at,
                    cancellation_id=request.budget.cancellation_id, trace_id=request.budget.trace_id))
        except AppError as exc:
            # A definite duplicate identifies another owner of this original attempt.
            # It grants no new permit and must never cancel that owner's real work.
            if exc.code != "attempt_already_exists":
                await self._cancel_cleanup(request.attempt_id)
            raise
        except BaseException as exc:
            # Enqueue/admission may have committed even when its response was lost.
            receipt = await self._cancel_cleanup(request.attempt_id)
            known = _known_wait_expiry(receipt)
            if known is not None and not isinstance(exc, asyncio.CancelledError):
                raise known from None
            raise
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
        self.request.budget.check()
        result = await self._update("submitted", {"provider_handle": provider_handle})
        self.request.budget.check()
        return result

    async def finish(self, usage=None, result=None):
        result = await self._update("finish", {"usage": usage, "result": result})
        self.done = True
        return result

    async def rejected(self, error_code, usage=None):
        result = await self._update("rejected", {"error_code": error_code, "usage": usage})
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
