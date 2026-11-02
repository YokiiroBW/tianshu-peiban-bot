"""Explicit provider HTTP metadata, shared by native generation and catalogue reads."""

from hashlib import sha256
import json
from urllib.parse import urlsplit

from tianshu.runtime.errors import AppError
from tianshu.types import TrustedContext


STATE_REFERENCE_FIELDS = ("previous_response_id", "conversation", "conversation_id", "thread_id", "session_id", "cachedContent")


def validate_provider_session(scope, *, parameters=None, native_payload=None):
    if scope not in {"conversation", "intent"}:
        raise AppError("invalid_provider_session_scope")
    if scope == "intent" and any(value.get(key) for value in (parameters or {}, native_payload or {}) for key in STATE_REFERENCE_FIELDS):
        raise AppError("independent_session_state_reference", status=409)


def provider_headers(base_url, context, intent_id, *, session_scope="conversation", function=None):
    """Preserve OpenCode's required transport contract without exposing application IDs."""
    validate_provider_session(session_scope)
    target = urlsplit(base_url)
    try:
        matches = (
            target.scheme == "https"
            and target.hostname == "opencode.ai"
            and target.port in {None, 443}
            and target.username is None
            and target.password is None
            and not target.query
            and not target.fragment
            and target.path.rstrip("/") in {"/zen/v1", "/zen/go/v1"}
        )
    except ValueError:
        matches = False
    if not matches:
        return {}
    if not isinstance(context, TrustedContext) or (
        (session_scope == "intent" or not context.conversation_id) and (not isinstance(intent_id, str) or not intent_id)
    ):
        raise AppError("provider_session_context_required")
    identity = {
        "principal": context.principal_id,
        "actor": context.actor_id,
        "conversation": context.conversation_id,
        "intent": None if session_scope == "conversation" and context.conversation_id else intent_id,
    }
    if session_scope == "intent":
        if not isinstance(function, str) or not function:
            raise AppError("provider_session_function_required")
        identity["function"] = function
    session = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    opaque = sha256(("tianshu-opencode-session:" + session).encode("utf-8")).hexdigest()
    return {
        "User-Agent": "tianshu-model-gateway/0.1.0",
        "x-opencode-session": "tianshu-" + opaque,
    }
