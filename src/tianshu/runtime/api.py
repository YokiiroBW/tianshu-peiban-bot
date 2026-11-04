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
        request["error_code"] = exc.code
        response = json_response({"error": {"code": exc.code, "message": exc.message,
            "details": exc.details, "request_id": request_id}}, status=exc.status)
    except (TimeoutError, asyncio.TimeoutError):
        request["error_code"] = "deadline_exceeded"
        response = json_response({"error": {"code": "deadline_exceeded",
            "message": "请求已超时", "details": None, "request_id": request_id}}, status=504)
    except web.HTTPException as exc:
        response = json_response({"error": {"code": "http_" + str(exc.status),
            "message": exc.reason, "details": None, "request_id": request_id}}, status=exc.status)
    except Exception as exc:
        request["error_code"] = type(exc).__name__
        LOG.error("request_failed type=%s request_id=%s", type(exc).__name__, request_id)
        response = json_response({"error": {"code": "internal_error",
            "message": "请求未完成", "details": None, "request_id": request_id}}, status=500)
    response.headers["X-Request-Id"] = request_id
    if request.app.get("data_generation"):
        response.headers[GENERATION_HEADER] = request.app["data_generation"]
    if event_log and not request.path.startswith("/health/"):
        try:
            await event_log.emit("http.finished", "succeeded" if response.status < 400 else "failed",
                correlation_id=request_id, duration_ms=(time.monotonic()-started)*1000,
                route=route_name, method=request.method, status=response.status,
                error_code=request.get("error_code"))
        except AppError:
            pass  # Never rewrite an already-committed business outcome because logging failed.
    return response
