"""Inventory cursors bind a page to a complete, sorted identity set."""

from __future__ import annotations

import base64
import hashlib
import json

from .protocol import Failure, decode_json, integer


def page(identities, node_id, operation, params):
    digest = hashlib.sha256("\0".join(identities).encode()).hexdigest()
    offset = 0
    cursor = params.get("cursor")
    if cursor:
        try:
            value = decode_json(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
            if not isinstance(value, dict) or set(value) != {"n", "o", "d", "at"} or value["n"] != node_id or value["o"] != operation:
                raise ValueError("cursor scope")
            if value["d"] != digest:
                raise Failure("cursor_invalidated", "invalid")
            offset = value["at"]
            if not integer(offset, 0, len(identities)):
                raise ValueError("cursor offset")
        except Failure:
            raise
        except (ValueError, UnicodeError, TypeError):
            raise Failure("cursor_invalid", "invalid") from None
    limit = params["limit"]
    selected = identities[offset:offset + limit]
    next_cursor = None
    if offset + len(selected) < len(identities):
        value = {"n": node_id, "o": operation, "d": digest, "at": offset + len(selected)}
        next_cursor = base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
    return selected, next_cursor
