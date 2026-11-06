"""Describe a failure without retaining response bodies or exception messages."""

import asyncio
import json
import re

import aiohttp

from tianshu.runtime.errors import AppError


_PROTOCOL_ERRORS = frozenset({
    "invalid_native_chat_response", "invalid_native_tool_call", "incomplete_native_tool_call",
    "incomplete_native_stream", "upstream_response_incomplete",
})
_DATA_DEPENDENT_ERRORS = (TypeError, ValueError, KeyError, IndexError, AttributeError)
_SAFE_TYPE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")
_SAFE_MODULES = frozenset({"builtins", "aiohttp", "asyncio", "json", "psycopg", "tianshu"})


def _error_type(error):
    kind = type(error)
    name = kind.__name__
    if kind.__module__.split(".", 1)[0] in _SAFE_MODULES and _SAFE_TYPE_NAME.fullmatch(name):
        return name
    return "OtherError"


def failure_diagnostic(error, *, site, http_phase):
    """Classify only facts that the operation and exception jointly establish.

    In particular, an arbitrary exception from interpreting provider data can
    be a bad envelope or a local bug; neither conclusion is assumed.
    """
    if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
        stage = "unknown"
    elif isinstance(error, AppError):
        if site in {"interpret", "protocol"} and error.code in _PROTOCOL_ERRORS:
            stage = "protocol_decode"
        elif site == "domain" and error.code == "embedding_dimension_mismatch":
            stage = "domain_validation"
        else:
            stage = "unknown"
    elif (site == "transport" or http_phase == "close") and isinstance(
        error, (aiohttp.ClientError, OSError)
    ):
        stage = "transport"
    elif isinstance(error, TimeoutError):
        # An execution deadline may expire during any local or remote step.
        stage = "unknown"
    elif site == "parse_json" and isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)):
        stage = "protocol_decode"
    elif site in {"interpret", "parse_json", "domain"} and isinstance(error, _DATA_DEPENDENT_ERRORS):
        stage = "unknown"
    elif site == "http_status":
        stage = "unknown"
    else:
        stage = "local_program"
    return {
        "failure_stage": stage,
        "error_type": _error_type(error),
        # The exception path does not persist a complete original result for
        # the success-only reuse port. A concurrent or historical result is not
        # inferred from the in-memory response or stream prefix.
        "original_output_reusable": None,
    }
