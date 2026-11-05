"""The process client. Writes are never retried automatically after an unknown result."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from urllib.parse import urlencode

import aiohttp

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_from_wire
from tianshu.runtime.serialization import wire
from tianshu.runtime.cancellation import finish_cleanup
from tianshu.types import RequestBudget, ServiceClass
from .codec import event_from_wire, receipt_from_wire, result_from_wire
from .timing import waiting_timeout, ExternalExecutionClock

_DEFAULT_TIMEOUT = object()


def _result_timeout(request):
    """Leave time to observe a deadline already enforced by the broker."""
    timeout = waiting_timeout(request)
    if timeout is not None and request.execution_timeout_seconds is not None:
        # The business deadline still bounds admission and execution on the
        # broker. The transport also needs time to persist and return its
        # original receipt after that deadline has fired.
        return timeout + 5
    return timeout


def _known_wait_expiry(receipt):
    if (receipt is not None and receipt.status == "cancelled" and receipt.dispatch == "not_started"
            and receipt.error_code in {"queue_wait_expired", "business_deadline_expired"}):
        return AppError(receipt.error_code, status=408,
                        details={"status": "cancelled", "dispatch": "not_started"})
    return None


def _recovery_wait(error, status):
    """Decode only an explicitly typed Models transport error."""
    if not isinstance(error, dict):
        raise AppError("broker_protocol_error", status=502)
    if "recovery_wait" not in error:
        return None
    if status != 409:
        raise AppError("broker_protocol_error", status=502)
    try:
        waiting = recovery_wait_from_wire(error["recovery_wait"])
    except (TypeError, ValueError):
        raise AppError("broker_protocol_error", status=502) from None
    if error.get("code") != waiting.reason:
        raise AppError("broker_protocol_error", status=502)
    return waiting


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
                if not isinstance(value, dict):
                    raise AppError("broker_protocol_error", status=502)
                error = value.get("error", {})
                models_path = (path.startswith("/internal/v1/models/") or path == "/internal/v1/resources"
                               or path.startswith("/internal/v1/resources/"))
                if isinstance(error, dict) and "recovery_wait" in error and not models_path:
                    raise AppError("broker_protocol_error", status=502)
                waiting = _recovery_wait(error, response.status)
                if waiting is not None:
                    raise waiting
                raise AppError(error.get("code", "broker_error"), error.get("message", ""),
                               status=response.status, details=error.get("details"))
            return value

    async def generate(self, request):
        timeout = _result_timeout(request)
        value = await self._json("/internal/v1/models/generate", body=request,
                                 timeout=timeout, service_class=request.service_class)
        return result_from_wire(value)

    async def submit(self, request, *, completion=None):
        from .async_jobs import completion_record

        record = completion_record(completion)
        try:
            value = await self._json("/internal/v1/models/submit",
                body={"request": request, "completion": record},
                timeout=waiting_timeout(request), service_class=request.service_class)
            return receipt_from_wire(value["receipt"])
        except (TimeoutError, aiohttp.ClientError):
            from .ledger import digest, submission_digest
            control_error = None
            try:
                original = await self.status(request.attempt_id)
            except (AppError, TimeoutError, aiohttp.ClientError) as error:
                original = None
                control_error = getattr(error, "code", type(error).__name__)
            if original is not None:
                if original.request_digest != submission_digest(request):
                    raise AppError("attempt_identity_conflict", status=409) from None
                if record is not None and original.completion_digest != digest(record):
                    raise AppError("model_completion_ack_unknown", status=503,
                        details={"attempt_id": request.attempt_id, "action": "resubmit_same_completion"}) from None
                return original
            details = {"attempt_id": request.attempt_id, "action": "observe_original_attempt",
                       "original_observation": "unavailable" if control_error else "missing"}
            if control_error:
                details["control_error"] = control_error
            raise AppError("model_submission_ack_unknown", status=503, details=details) from None

    async def stream(self, request):
        timeout = _result_timeout(request)
        complete = False
        try:
            async with self._data_session(request.service_class).post(
                self.base_url+"/internal/v1/models/stream", json=wire(request),
                timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=5)) as response:
                if response.status >= 400:
                    value = await response.json()
                    if not isinstance(value, dict):
                        raise AppError("broker_protocol_error", status=502)
                    error = value.get("error", {})
                    waiting = _recovery_wait(error, response.status)
                    if waiting is not None:
                        raise waiting
                    raise AppError(error.get("code", "broker_error"), status=response.status)
                async for line in response.content:
                    if len(line) > 8*1024*1024:
                        raise AppError("broker_event_too_large", status=502)
                    value = json.loads(line)
                    if value.get("kind") == "error":
                        complete = True
                        failure = value["error"]
                        waiting = _recovery_wait(failure, failure.get("status", 502) if isinstance(failure, dict) else 502)
                        if waiting is not None:
                            raise waiting
                        raise await self._stream_failure(request.attempt_id, failure["code"],
                            status=failure.get("status", 502), fallback=receipt_from_wire(failure.get("receipt")))
                    event = event_from_wire(value)
                    complete |= event.kind == "receipt"
                    if (event.kind == "receipt" and event.receipt is not None
                            and event.receipt.status != "succeeded"):
                        raise await self._stream_failure(request.attempt_id,
                            event.receipt.error_code or "model_attempt_" + event.receipt.status,
                            fallback=event.receipt)
                    yield event
                if not complete:
                    complete = True
                    raise await self._stream_failure(request.attempt_id, "model_stream_incomplete")
        except (TimeoutError, aiohttp.ClientError):
            raise await self._stream_failure(request.attempt_id, "model_stream_interrupted") from None

    async def _stream_failure(self, attempt_id, code, *, status=502, fallback=None):
        try:
            original = await self.status(attempt_id) or fallback
        except (AppError, TimeoutError, aiohttp.ClientError):
            original = fallback
        known = _known_wait_expiry(original)
        if known is not None:
            return known
        return AppError(code, status=status, details={"attempt_id":attempt_id,
            "status":original.status if original else None,
            "dispatch":original.dispatch if original else None,"action":"observe_original_attempt"})

    async def _cancel_cleanup(self, attempt_id):
        try:
            value = await finish_cleanup(self._json("/internal/v1/models/attempts/"+attempt_id+"/cancel",
                body={"external_owner": True}, control=True))
            return receipt_from_wire(value["receipt"])
        except (AppError, TimeoutError, aiohttp.ClientError):
            # The durable attempt retains its original identity for subsequent status reads.
            pass

    async def status(self, attempt_id, *, phase="observe"):
        from .lookup import lookup_phase

        lookup_phase(phase)
        path = "/internal/v1/models/attempts/" + attempt_id
        if phase == "pre_submit":
            path += "?" + urlencode({"phase": phase})
        try:
            value = await self._json(path, control=True, method="GET")
        except AppError as exc:
            if phase == "observe" and exc.status == 404 and exc.code == "attempt_not_found":
                return None
            raise
        original = receipt_from_wire(value["receipt"])
        if phase == "pre_submit":
            if (value.get("attempt_id") != attempt_id or value.get("lookup_phase") != phase
                    or value.get("lookup_state") != ("found" if original else "absent")):
                raise AppError("invalid_model_status_response", status=502)
        elif original is None:
            raise AppError("invalid_model_status_response", status=502)
        if original is not None and original.attempt_id != attempt_id:
            raise AppError("model_attempt_identity_conflict", status=409,
                           details={"attempt_id": attempt_id})
        return original

    async def require_receipt(self, attempt_id):
        from .lookup import missing_receipt

        original = await self.status(attempt_id, phase="observe")
        if original is None:
            raise missing_receipt(attempt_id)
        return original

    async def inspect_admission_retirement(self, attempt_id, *, lineage_id, data_generation, context, original_context):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/admission-retirement",
            body={"lineage_id":lineage_id,"data_generation":data_generation,"context":context,
                "original_context":original_context},control=True)
        if (not isinstance(value,dict) or value.get("attempt_id") != attempt_id
                or value.get("lineage_id") != lineage_id or value.get("data_generation") != str(data_generation)
                or value.get("state") not in {"sealed","unsealed"}
                or value.get("status") not in {None,"queued","running","succeeded","failed","cancelled","unknown"}
                or type(value.get("explicit_cancel")) is not bool):
            raise AppError("invalid_model_admission_retirement_response",status=502)
        return value

    async def seal_admission(self, attempt_id, *, lineage_id, data_generation, context, original_context, reason,
                             expected_status=None):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/seal-admission",
            body={"lineage_id":lineage_id,"data_generation":data_generation,"context":context,
                "original_context":original_context,"reason":reason,"expected_status":expected_status},control=True)
        if (not isinstance(value,dict) or value.get("attempt_id") != attempt_id
                or value.get("lineage_id") != lineage_id or value.get("data_generation") != str(data_generation)
                or value.get("state") not in {"sealed_absent","sealed_queued","sealed_existing","explicit_cancelled","observation_changed"}
                or value.get("reason") not in {"superseded","expired"}
                or value.get("status") not in {None,"queued","running","succeeded","failed","cancelled","unknown"}
                or type(value.get("explicit_cancel")) is not bool):
            raise AppError("invalid_model_admission_retirement_response",status=502)
        return value

    async def reconcile_prestart(self, attempt_id, *, context, expected_version, expected_request_digest,
                                 expected_data_generation, client_request_id, evidence_ref, decision):
        value = await self._json("/internal/v1/models/attempts/" + attempt_id + "/prestart-reconciliation",
            control=True, body={"context": context, "expected_version": expected_version,
                "expected_request_digest": expected_request_digest, "expected_data_generation": expected_data_generation,
                "client_request_id": client_request_id, "evidence_ref": evidence_ref, "decision": decision})
        result = receipt_from_wire(value.get("receipt"))
        if result is None or result.attempt_id != attempt_id:
            raise AppError("invalid_model_reconciliation_response", status=502)
        return result

    async def result(self, attempt_id, *, context):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/result",
                                 body={"context": context}, control=True)
        return result_from_wire(value["result"]) if value["result"] is not None else None

    async def reusable_result(self, attempt_id, *, context, expected_request_digest=None,
                              expected_data_generation=None):
        from .results import validate_reusable_receipt

        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/result",
            body={"context": context, "reuse": {"request_digest": expected_request_digest,
                                                "data_generation": expected_data_generation}}, control=True)
        if value.get("result") is None:
            raise AppError("model_result_unavailable", status=409, details={"attempt_id": attempt_id})
        result = result_from_wire(value["result"])
        validate_reusable_receipt(result.receipt, attempt_id,
            expected_request_digest=expected_request_digest, expected_data_generation=expected_data_generation)
        return result

    async def cancel(self, attempt_id, *, reason="explicit_cancel"):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/cancel",
            body={"reason": reason}, control=True)
        return receipt_from_wire(value["receipt"])

    async def reload(self, *, configuration_event_id=None, expected_revision=None):
        return await self._json("/internal/v1/models/reload", control=True,
            body={"configuration_event_id": configuration_event_id, "expected_revision": expected_revision})

    async def configuration_reload_status(self, event_id):
        return (await self._json("/internal/v1/models/configuration-reloads/"+event_id,
                                 control=True, method="GET"))["reload"]

    async def pending_configuration_reloads(self, *, limit=100, cursor=None):
        query = {"limit": limit}
        if cursor is not None:
            query["cursor"] = json.dumps(cursor, separators=(",", ":"))
        return await self._json("/internal/v1/models/configuration-reloads?" + urlencode(query),
                                control=True, method="GET")

    async def snapshot(self):
        return await self._json("/health/ready", control=True, method="GET")

    async def embedding_profile(self):
        return await self._json("/internal/v1/models/embedding-profile", control=True, method="GET")

    async def generation_profile(self, request):
        return await self._json("/internal/v1/models/generation-profile", body=request,
                                control=True, timeout=min(10, request.budget.remaining()))

    async def observe_external(self, attempt_id, *, context, provider_handle, state):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/external-observation",
            body={"context": context, "provider_handle": provider_handle, "state": state}, control=True)
        return receipt_from_wire(value["receipt"])

    async def observe_usage(self, attempt_id, *, context, provider_handle, usage):
        value = await self._json("/internal/v1/models/attempts/"+attempt_id+"/usage-observation",
            body={"context": context, "provider_handle": provider_handle, "usage": usage}, control=True)
        return receipt_from_wire(value["receipt"])

    @asynccontextmanager
    async def resource(self, request):
        timeout = waiting_timeout(request)
        admitted = None
        try:
            value = await self._json("/internal/v1/resources", body=request,
                             timeout=timeout, service_class=request.service_class)
            if request.execution_timeout_seconds is not None:
                admitted = receipt_from_wire(value["receipt"])
                if admitted.deadline_at is None and request.execution_start != "provider_running":
                    raise AppError("external_execution_deadline_missing", status=502)
                if admitted.deadline_at is not None:
                    request = replace(request, budget=RequestBudget(admitted.deadline_at,
                        cancellation_id=request.budget.cancellation_id, trace_id=request.budget.trace_id))
        except RecoveryWait:
            raise
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
        permit = ClientExternalPermit(self, request, admitted=admitted)
        owner = asyncio.current_task()

        async def watch_cancel():
            while not permit.done:
                await asyncio.sleep(0.5)
                try:
                    value = await self._json("/internal/v1/models/attempts/"+request.attempt_id,
                                             control=True, method="GET")
                    permit.clock.observe(receipt_from_wire(value["receipt"]).deadline_at)
                    if value["cancel_requested"] or value["receipt"]["status"] in {"unknown", "cancelled"}:
                        owner.cancel()
                        return
                except (AppError, aiohttp.ClientError, TimeoutError):
                    # A lost owner cannot safely authorize more external work.
                    owner.cancel()
                    return

        watcher = asyncio.create_task(watch_cancel())
        try:
            async with permit.clock.running():
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
    def __init__(self, client, request, *, admitted=None):
        self.client, self.done = client, False
        self.clock = ExternalExecutionClock(request, admitted.deadline_at if admitted else None)

    @property
    def request(self):
        return self.clock.request

    async def observe_provider(self, state, *, provider_handle):
        result = await self._update("observe", {"provider_handle": provider_handle, "state": state})
        self.clock.observe(result.deadline_at)
        return result

    async def _update(self, operation, body):
        value = await self.client._json("/internal/v1/resources/"+self.request.attempt_id+"/"+operation,
                                        body=body, control=True)
        return receipt_from_wire(value["receipt"])

    async def submitted(self, provider_handle=None):
        self.clock.check()
        result = await self._update("submitted", {"provider_handle": provider_handle})
        self.clock.check()
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
