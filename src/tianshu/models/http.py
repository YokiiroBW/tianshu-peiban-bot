"""Bounded process transport and same-protocol external model endpoints."""

import base64
import asyncio
from datetime import UTC, datetime, timedelta
import hmac
import json
from uuid import uuid4
from hashlib import sha256

from aiohttp import web

from tianshu.runtime.api import error_middleware, json_response
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_to_wire
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext
from .codec import context_from_wire, external_from_wire, request_from_wire
from .ledger import receipt
from .types import ModelRequest, ModelUsage


def create_broker_app(broker, token, external_keys=()):
    @web.middleware
    async def authenticate(request, handler):
        if request.path.startswith("/internal/"):
            supplied = request.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, "Bearer " + token):
                raise AppError("unauthorized", status=401)
        return await handler(request)

    app = web.Application(
        middlewares=[error_middleware, authenticate], client_max_size=32 * 1024 * 1024
    )

    async def generate(request):
        model_request = request_from_wire(await request.json())
        return json_response(await broker.generate(model_request))

    async def submit(request):
        from .async_jobs import completion_spec

        body = await request.json()
        try:
            model_request = request_from_wire(body["request"])
            completion = completion_spec(body["completion"]) if body.get("completion") is not None else None
        except (KeyError, TypeError, ValueError):
            raise AppError("invalid_model_submission") from None
        return json_response({"receipt": await broker.submit(model_request, completion=completion)})

    async def stream_error(response, model_request, error, *, native=False):
        try:
            original = await broker.status(model_request.attempt_id)
        except Exception:
            original = None
        payload = {"code":error.code if isinstance(error, AppError) else "model_stream_interrupted",
            "status":error.status if isinstance(error, AppError) else 502,
            "attempt_id":model_request.attempt_id,"receipt":wire(original)}
        if not native and isinstance(error, RecoveryWait):
            try:
                payload["recovery_wait"] = recovery_wait_to_wire(error)
            except (TypeError, ValueError):
                payload.update(code="broker_protocol_error", status=502)
        frame = {"error":payload} if native else {"kind":"error","error":payload}
        encoded = json.dumps(frame, ensure_ascii=False).encode()
        await response.write(b"event: error\ndata: " + encoded + b"\n\n" if native else encoded + b"\n")
        await response.write_eof()
        return response

    async def stream(request):
        model_request = request_from_wire(await request.json())
        events = broker.stream(model_request)
        response = web.StreamResponse(
            headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store"}
        )
        await response.prepare(request)
        try:
            async for event in events:
                await response.write(json.dumps(wire(event), ensure_ascii=False).encode() + b"\n")
            await response.write_eof()
        except (asyncio.CancelledError, ConnectionError):
            response.force_close()
            raise
        except Exception as error:
            return await stream_error(response, model_request, error)
        finally:
            await events.aclose()
        return response

    async def state(request):
        from .lookup import lookup_phase, missing_receipt

        attempt_id = request.match_info["attempt_id"]
        phase = lookup_phase(request.query.get("phase", "observe"))
        # Shared HTTP event middleware can attribute this exact lookup without
        # classifying unrelated 404s by route or error text alone.
        request["model_attempt_id"] = attempt_id
        request["model_lookup_phase"] = phase
        request["model_lookup_state"] = "unavailable"
        row = await broker.ledger.get(attempt_id)
        if not row:
            request["model_lookup_state"] = "absent"
            if phase == "pre_submit":
                return json_response({"attempt_id": attempt_id, "lookup_phase": phase,
                    "lookup_state": "absent", "receipt": None, "cancel_requested": None})
            raise missing_receipt(attempt_id)
        request["model_lookup_state"] = "found"
        return json_response({"attempt_id": attempt_id, "lookup_phase": phase, "lookup_state": "found",
                              "receipt": receipt(row), "cancel_requested": row["cancel_requested"]})

    async def cancel(request):
        body = await request.json() if request.can_read_body else {}
        if not isinstance(body, dict):
            raise AppError("invalid_input")
        if body.get("external_owner") is True:
            result = await broker.cancel_external_owner(request.match_info["attempt_id"])
        else:
            result = await broker.cancel(request.match_info["attempt_id"], reason=body.get("reason", "explicit_cancel"))
        if result is None:
            raise AppError("attempt_not_found", status=404)
        return json_response({"receipt": result})

    async def saved_result(request):
        body = await request.json()
        try:
            context = context_from_wire(body["context"])
        except (KeyError, TypeError, ValueError):
            raise AppError("invalid_model_result_context") from None
        attempt_id = request.match_info["attempt_id"]
        if "reuse" in body:
            options = body["reuse"]
            if not isinstance(options, dict) or set(options) - {"request_digest", "data_generation"}:
                raise AppError("invalid_model_result_reuse")
            request["model_attempt_id"] = attempt_id
            request["model_lookup_phase"] = "observe"
            request["model_lookup_state"] = "unavailable"
            try:
                result = await broker.reusable_result(attempt_id, context=context,
                    expected_request_digest=options.get("request_digest"),
                    expected_data_generation=options.get("data_generation"))
            except AppError as exc:
                if exc.code == "attempt_not_found":
                    request["model_lookup_state"] = "absent"
                raise
            request["model_lookup_state"] = "found"
        else:
            result = await broker.result(attempt_id, context=context)
        return json_response({"result": result})

    async def external_observation(request):
        body = await request.json()
        try:
            context = context_from_wire(body["context"])
        except (KeyError, TypeError, ValueError):
            raise AppError("invalid_model_result_context") from None
        return json_response({"receipt": await broker.observe_external(request.match_info["attempt_id"],
            context=context, provider_handle=body.get("provider_handle"), state=body.get("state"))})

    async def usage_observation(request):
        body = await request.json()
        try:
            context = context_from_wire(body["context"])
            usage = ModelUsage(**body["usage"])
        except (KeyError, TypeError, ValueError):
            raise AppError("invalid_model_usage_observation") from None
        return json_response({"receipt": await broker.observe_usage(request.match_info["attempt_id"],
            context=context, provider_handle=body.get("provider_handle"), usage=usage)})

    async def reload(request):
        body = await request.json() if request.can_read_body else {}
        body = body or {}
        if not isinstance(body, dict):
            raise AppError("invalid_model_configuration_reload")
        return json_response(await broker.reload(configuration_event_id=body.get("configuration_event_id"),
                                                 expected_revision=body.get("expected_revision")))

    async def reload_status(request):
        return json_response({"reload": await broker.configuration_reload_status(request.match_info["event_id"])})

    async def reload_pending(request):
        try:
            limit = int(request.query.get("limit", "100"))
        except ValueError:
            raise AppError("invalid_reload_limit") from None
        try:
            raw_cursor = request.query.get("cursor")
            cursor = json.loads(raw_cursor) if raw_cursor is not None else None
        except (TypeError, ValueError):
            raise AppError("invalid_reload_cursor") from None
        return json_response(await broker.pending_configuration_reloads(limit=limit, cursor=cursor))

    async def embedding_profile(request):
        return json_response(await broker.embedding_profile())

    async def generation_profile(request):
        return json_response(await broker.generation_profile(request_from_wire(await request.json())))

    async def external_begin(request):
        permit = await broker.begin_external(external_from_wire(await request.json()))
        return json_response({"receipt": await broker.status(permit.request.attempt_id)})

    async def external_update(request):
        attempt_id, operation = request.match_info["attempt_id"], request.match_info["operation"]
        permit = broker.external.get(attempt_id)
        if permit is None:
            raise AppError("external_permit_not_active", status=409)
        body = await request.json()
        if operation == "submitted":
            await permit.submitted(body.get("provider_handle"))
            result = await broker.status(attempt_id)
        elif operation == "finish":
            result = await permit.finish(body.get("usage"), body.get("result"))
        elif operation == "unknown":
            result = await permit.unknown(body.get("error_code", "external_effect_unknown"))
        elif operation == "rejected":
            result = await permit.rejected(
                body.get("error_code", "external_rejected"), body.get("usage")
            )
        elif operation == "observe":
            result = await permit.observe_provider(body.get("state"), provider_handle=body.get("provider_handle"))
        else:
            raise AppError("unknown_resource_operation", status=404)
        return json_response({"receipt": result})

    async def health(request):
        if request.path.endswith("live"):
            return json_response({"status": "live"})
        ready = broker.ready and broker.session is not None and not broker.session.closed
        return json_response(
            {
                "status": "ready" if ready else "unavailable",
                "quota_groups": broker.scheduler.snapshot() if broker.scheduler else {},
            },
            status=200 if ready else 503,
        )

    def external_grant(request):
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if request.path.startswith("/v1beta/"):
            supplied = request.headers.get("x-goog-api-key", request.query.get("key", supplied))
        elif request.path == "/v1/messages":
            supplied = request.headers.get("x-api-key", supplied)
        grant = next(
            (
                entry
                for entry in external_keys
                if hmac.compare_digest(supplied, broker.compiler.secrets.resolve(entry["key_ref"]))
            ),
            None,
        )
        if grant is None:
            raise AppError("unauthorized", status=401)
        return grant

    async def model_list(request):
        grant = external_grant(request)
        return json_response(
            {
                "object": "list",
                "data": [
                    {"id": alias, "object": "model", "created": None, "owned_by": "tianshu"}
                    for alias in sorted(grant.get("routes", {}))
                ],
            }
        )

    async def native(request):
        grant = external_grant(request)
        protocol = (
            "openai.chat"
            if request.path == "/v1/chat/completions"
            else "openai.responses"
            if request.path == "/v1/responses"
            else "anthropic.messages"
            if request.path == "/v1/messages"
            else "openai.embedding"
            if request.path == "/v1/embeddings"
            else "gemini.generate"
        )
        body = await request.json()
        if not isinstance(body, dict):
            raise AppError("invalid_input")
        alias = request.match_info.get("model") or body.get("model")
        route_id = grant.get("routes", {}).get(alias)
        if route_id is None:
            raise AppError("model_not_allowed", status=403)
        streaming = bool(body.get("stream")) or ":streamGenerateContent" in request.path
        execution_seconds = min(grant.get("timeout_seconds", 120), 900)
        deadline = datetime.now(UTC) + timedelta(seconds=execution_seconds)
        supplied_id = request.headers.get("X-Request-Id")
        identity = ("native:" + sha256((grant["id"] + "\0" + supplied_id).encode()).hexdigest()
                    if supplied_id else str(uuid4()))
        context = TrustedContext(
            "model-client:" + grant["id"],
            None,
            None,
            None,
            (),
            (),
            1,
            OriginKind.SYSTEM,
            frozenset({"models.route"}),
        )
        model_request = ModelRequest(
            identity,
            identity,
            "embed" if protocol == "openai.embedding" else "chat",
            ServiceClass.INTERACTIVE,
            context,
            RequestBudget(deadline),
            (),
            execution_timeout_seconds=execution_seconds,
            max_output_tokens=0
            if protocol == "openai.embedding"
            else body.get(
                "max_tokens",
                body.get(
                    "max_output_tokens",
                    body.get("generationConfig", {}).get("maxOutputTokens", 2048),
                ),
            ),
            native_payload=body,
            provider_session_scope="intent" if protocol == "openai.embedding" else "conversation",
            protocol=protocol,
            route_id=route_id,
            required_capabilities=frozenset(
                {"embed" if protocol == "openai.embedding" else "text"}
            ),
        )
        if not streaming:
            result = await broker.generate(model_request)
            if result.receipt.status != "succeeded":
                raise AppError(
                    "model_attempt_" + result.receipt.status,
                    status=502,
                    details={"attempt_id": result.receipt.attempt_id},
                )
            return json_response(result.native_response)
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"}
        )
        events = broker.stream(model_request)
        await response.prepare(request)
        try:
            async for event in events:
                if event.kind == "native":
                    await response.write(base64.b64decode(event.data["chunk_b64"]))
                elif event.kind == "receipt" and event.receipt.status != "succeeded":
                    raise AppError(event.receipt.error_code or "model_attempt_" + event.receipt.status, status=502)
            await response.write_eof()
        except (asyncio.CancelledError, ConnectionError):
            response.force_close()
            raise
        except Exception as error:
            return await stream_error(response, model_request, error, native=True)
        finally:
            await events.aclose()
        return response

    app.router.add_post("/internal/v1/models/generate", generate)
    app.router.add_post("/internal/v1/models/submit", submit)
    app.router.add_post("/internal/v1/models/stream", stream)
    app.router.add_post("/internal/v1/models/reload", reload)
    app.router.add_get("/internal/v1/models/configuration-reloads", reload_pending)
    app.router.add_get("/internal/v1/models/configuration-reloads/{event_id}", reload_status)
    app.router.add_get("/internal/v1/models/embedding-profile", embedding_profile)
    app.router.add_post("/internal/v1/models/generation-profile", generation_profile)
    app.router.add_get("/internal/v1/models/attempts/{attempt_id}", state)
    app.router.add_post("/internal/v1/models/attempts/{attempt_id}/result", saved_result)
    app.router.add_post("/internal/v1/models/attempts/{attempt_id}/external-observation", external_observation)
    app.router.add_post("/internal/v1/models/attempts/{attempt_id}/usage-observation", usage_observation)
    app.router.add_post("/internal/v1/models/attempts/{attempt_id}/cancel", cancel)
    app.router.add_post("/internal/v1/resources", external_begin)
    app.router.add_post("/internal/v1/resources/{attempt_id}/{operation}", external_update)
    app.router.add_get("/health/live", health)
    app.router.add_get("/health/ready", health)
    app.router.add_get("/v1/models", model_list)
    for path in ("/v1/chat/completions", "/v1/responses", "/v1/messages", "/v1/embeddings"):
        app.router.add_post(path, native)
    app.router.add_post("/v1beta/models/{model}:generateContent", native)
    app.router.add_post("/v1beta/models/{model}:streamGenerateContent", native)
    return app
