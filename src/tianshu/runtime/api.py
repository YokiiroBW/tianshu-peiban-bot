"""Public HTTP shapes. Domain routes register actual operations under /api/v1."""

from __future__ import annotations

import asyncio
import logging
import time
import json
from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from aiohttp import web

from tianshu.types import Coverage, Receipt, TrustedContext
from .errors import AppError
from .recovery_policy import RecoveryWait, recovery_wait_to_wire
from .serialization import wire

LOG = logging.getLogger(__name__)
CSRF_HEADER = "X-CSRF-Token"
GENERATION_HEADER = "X-Tianshu-Data-Generation"
API_PREFIX = "/api/v1"
TRUSTED_CONTEXT = "trusted_context"


def require_context(request, permission=None) -> TrustedContext:
    context = request.get(TRUSTED_CONTEXT)
    if not isinstance(context, TrustedContext):
        raise AppError("unauthorized", status=401)
    if permission and permission not in context.permissions and "*" not in context.permissions:
        raise AppError("forbidden", status=403)
    return context


@dataclass(frozen=True, slots=True)
class PrincipalView:
    principal_id: str
    display_name: str
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionView:
    authenticated: bool
    initialized: bool
    principal: PrincipalView | None
    csrf_token: str | None
    setup_token_required: bool
    data_generation: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    client_request_id: str
    data: Any = None
    revision: int | None = None
    action_id: str | None = None
    attempt_id: str | None = None
    receipt: Receipt | None = None


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[Any, ...]
    next_cursor: str | None
    coverage: Coverage


def json_response(value, *, status=200):
    return web.json_response(wire(value), status=status)


async def command_body(request: web.Request) -> Mapping[str, Any]:
    maximum = 2*1024*1024
    chunks, size = [], 0
    async for chunk in request.content.iter_chunked(65536):
        size += len(chunk)
        if size > maximum:
            raise AppError("command_body_too_large", status=413)
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
    except (ValueError, UnicodeError):
        raise AppError("invalid_json") from None
    if not isinstance(body, dict):
        raise AppError("invalid_input")
    client_id = body.get("client_request_id")
    if not isinstance(client_id, str) or not 1 <= len(client_id) <= 128:
        raise AppError("client_request_id_required")
    revision = body.get("expected_revision")
    if revision is not None and (type(revision) is not int or revision < 0):
        raise AppError("invalid_revision")
    return body


@web.middleware
async def error_middleware(request, handler):
    request_id = str(uuid4())
    request["request_id"] = request_id
    event_log = request.app.get("event_log")
    started = time.monotonic()
    resource = request.match_info.route.resource
    route_name = getattr(resource, "canonical", None)
    error_metadata = {}
    confirmed_wait = False

    def capture_error_metadata(exc):
        # Read structured owner facts; never guess a pool from exception text.
        details = exc.details if isinstance(exc, AppError) and isinstance(exc.details, dict) else {}
        database_failure = getattr(exc, "database_failure", None)
        for name in ("dependency_ref", "failure_phase", "pool_name"):
            value = getattr(exc, name, None)
            if value is None and database_failure is not None:
                value = getattr(database_failure, name, None)
            if value is None:
                value = details.get(name)
            if isinstance(value, str):
                error_metadata[name] = value
        if database_failure is not None and error_metadata.get("pool_name") in {"business", "control", "sync"}:
            error_metadata.setdefault("dependency_ref", "postgresql")

    try:
        generation = request.app.get("data_generation")
        supplied_generation = request.headers.get(GENERATION_HEADER) or request.query.get("data_generation")
        if generation and supplied_generation and supplied_generation != generation:
            raise AppError(
                "data_generation_changed", status=409,
                details={"data_generation": generation},
            )
        if event_log and not request.path.startswith("/health/"):
            await event_log.emit("http.started", "started", correlation_id=request_id,
                                 route=route_name, method=request.method)
        response = await handler(request)
    except AppError as exc:
        capture_error_metadata(exc)
        request["error_code"] = exc.code
        error = {"code": exc.code, "message": exc.message,
                 "details": exc.details, "request_id": request_id}
        status = exc.status
        if isinstance(exc, RecoveryWait):
            try:
                error["recovery_wait"] = recovery_wait_to_wire(exc)
                confirmed_wait = error["recovery_wait"]["rollback_confirmed"] is True
            except (TypeError, ValueError):
                # Invalid internal metadata must not become a retryable wait.
                request["error_code"] = "invalid_recovery_wait_envelope"
                error = {"code": request["error_code"], "message": "请求未完成",
                         "details": None, "request_id": request_id}
                status = 500
        response = json_response({"error": error}, status=status)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        capture_error_metadata(exc)
        request["error_code"] = "deadline_exceeded"
        response = json_response({"error": {"code": "deadline_exceeded",
            "message": "请求已超时", "details": None, "request_id": request_id}}, status=504)
    except web.HTTPException as exc:
        capture_error_metadata(exc)
        request["error_code"] = "http_" + str(exc.status)
        response = json_response({"error": {"code": "http_" + str(exc.status),
            "message": exc.reason, "details": None, "request_id": request_id}}, status=exc.status)
    except Exception as exc:
        capture_error_metadata(exc)
        request["error_code"] = type(exc).__name__
        LOG.error("request_failed type=%s request_id=%s", type(exc).__name__, request_id)
        response = json_response({"error": {"code": "internal_error",
            "message": "请求未完成", "details": None, "request_id": request_id}}, status=500)
    response.headers["X-Request-Id"] = request_id
    if request.app.get("data_generation"):
        response.headers[GENERATION_HEADER] = request.app["data_generation"]
    if event_log and not request.path.startswith("/health/"):
        try:
            lookup_phase = request.get("model_lookup_phase")
            lookup_state = request.get("model_lookup_state")
            outcome = "deferred" if confirmed_wait else "succeeded" if response.status < 400 else "failed"
            await event_log.emit("http.finished", outcome,
                correlation_id=request_id, duration_ms=(time.monotonic()-started)*1000,
                route=route_name, method=request.method, status=response.status,
                error_code=request.get("error_code"), attempt_id=request.get("model_attempt_id"),
                lookup_phase=lookup_phase, lookup_state=lookup_state,
                dependency_ref=error_metadata.get("dependency_ref") or (
                    "model-broker" if lookup_phase in ("pre_submit", "observe") else None),
                failure_phase=error_metadata.get("failure_phase") or (
                    "model_status_lookup" if response.status >= 400 and
                    lookup_phase in ("pre_submit", "observe") else None),
                pool_name=error_metadata.get("pool_name"))
        except AppError:
            pass  # Never rewrite an already-committed business outcome because logging failed.
    return response
