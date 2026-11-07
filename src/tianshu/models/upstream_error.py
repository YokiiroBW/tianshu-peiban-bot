"""Bounded provider error identifiers; never persist arbitrary error messages."""

import json
import re


def describe(status, raw, secrets=()):
    diagnostic = {
        "status": status,
        "category": {
            400: "invalid_request",
            401: "authentication",
            402: "payment",
            403: "forbidden",
            404: "model_or_endpoint",
            413: "request_size",
            422: "invalid_request",
            429: "rate_or_quota",
        }.get(status, "provider_error"),
    }
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, UnicodeError):
        return diagnostic
    if not isinstance(value, dict):
        return diagnostic
    error = value.get("error", value)
    if not isinstance(error, dict):
        return diagnostic
    for key in ("code", "type", "param"):
        identifier = error.get(key)
        if type(identifier) is int:
            identifier = str(identifier)
        if (
            isinstance(identifier, str)
            and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,64}", identifier)
            and not identifier.lower().startswith(("sk-", "bearer"))
            and not any(secret and secret in identifier for secret in secrets)
        ):
            diagnostic[key] = identifier
    kinds = {diagnostic.get("code"), diagnostic.get("type")}
    if kinds & {
        "insufficient_quota",
        "insufficient_balance",
        "balance_not_enough",
        "quota_exhausted",
    }:
        diagnostic["category"] = "quota_or_balance"
    elif kinds & {"rate_limit_exceeded", "rate_limit_error", "too_many_requests"}:
        diagnostic["category"] = "rate_limit"
    elif kinds & {"context_length_exceeded", "max_tokens_exceeded"}:
        diagnostic["category"] = "context_length"
    return diagnostic
