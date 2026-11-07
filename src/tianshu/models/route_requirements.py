"""Check one configured route against the same request before admission."""

import json
from types import SimpleNamespace

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire

from .budgets import effective_output_tokens, merged_parameters
from .protocols import requested_capabilities
from .route_adaptation import adapt_request


def compatible_request(request, row, *, source_protocol=None, probing=False, allow_adaptation=False):
    if not row or not row["enabled"] or not row["provider_enabled"]:
        raise AppError("model_route_unavailable", status=503)
    protocol = row["protocol"]
    dedicated = {"embed": "openai.embedding", "rerank": "cohere.rerank"}
    if ((request.function in dedicated and protocol != dedicated[request.function])
            or (request.function not in dedicated and protocol in dedicated.values())):
        raise AppError("model_protocol_function_incompatible", status=422)
    source = source_protocol or request.protocol or protocol
    if source != protocol:
        if not allow_adaptation:
            raise AppError("native_protocol_mismatch", status=422)
        request = adapt_request(request, source, protocol)
    plan = SimpleNamespace(protocol=protocol, parameters=row["parameters"])
    parameters = request.native_payload if request.native_payload is not None else merged_parameters(plan, request)
    generation = parameters.get("generationConfig") or {}
    if not isinstance(generation, dict):
        raise AppError("model_fallback_protocol_incompatible", status=422)
    required = set(requested_capabilities(request))
    missing = required - set(row["verified_capabilities"])
    if missing and not probing:
        raise AppError("model_capability_unverified", status=422, details={"missing": sorted(missing)})
    output = effective_output_tokens(plan, request)
    if request.function not in dedicated:
        evidence = row.get("verification") or {}
        window = evidence.get("context_window_tokens") if isinstance(evidence, dict) else None
        # Missing capacity is unknown, not proof that the request cannot fit.
        # Keep it absent in configuration and let the normal bounded call decide.
        if window is not None:
            if type(window) is not int or window <= 0:
                raise AppError("model_context_window_unverified", status=422)
            estimate = request.estimated_input_tokens
            if estimate is None:
                estimate = len(json.dumps(wire((request.system, request.messages, request.tools)),
                    ensure_ascii=False).encode()) + 2048 * sum(
                        part.media is not None for message in request.messages for part in message.parts)
            if estimate + output > window:
                raise AppError("model_context_window_exceeded", status=422,
                    details={"context_window_tokens": window, "estimated_input_tokens": estimate,
                             "max_output_tokens": output})
    return request


def rejection(route_id, error):
    value = {"route_id": route_id, "reason": error.code}
    if error.details:
        value.update(error.details)
    return value


def no_compatible_route(rejections):
    return AppError("model_no_compatible_route", status=422,
        details={"stage": "route_selection", "rejections": rejections})
