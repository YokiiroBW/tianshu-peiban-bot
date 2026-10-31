"""Explicit provider HTTP metadata, shared by native generation and catalogue reads."""

from hashlib import sha256
import json
from urllib.parse import urlsplit

from tianshu.runtime.errors import AppError
from tianshu.types import TrustedContext


def provider_headers(base_url, context, intent_id):
    """Preserve OpenCode's required transport contract without exposing application IDs."""
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
        not context.conversation_id and (not isinstance(intent_id, str) or not intent_id)
    ):
        raise AppError("provider_session_context_required")
    session = json.dumps(
        {
            "principal": context.principal_id,
            "actor": context.actor_id,
            "conversation": context.conversation_id,
            "intent": None if context.conversation_id else intent_id,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    opaque = sha256(("tianshu-opencode-session:" + session).encode("utf-8")).hexdigest()
    return {
        "User-Agent": "tianshu-model-gateway/0.1.0",
        "x-opencode-session": "tianshu-" + opaque,
    }
