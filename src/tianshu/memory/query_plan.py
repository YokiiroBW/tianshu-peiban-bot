"""One public query shape and stable scoped cursors for ranked and complete reads."""

import base64
import binascii
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from .domain_utils import optional_time
from .evidence import temporal_selection
from .types import MemoryQueryPlan


EVIDENCE_KINDS = ("claim", "hypothesis", "matter", "episode", "reflection")
PLAN_FIELDS = {"query", "subject_ids", "matter_refs", "time_mode", "valid_at", "known_at",
               "evidence_kinds", "coverage_mode", "budget"}
QUERY_PROPERTIES = {
    "query": {"type": "string", "maxLength": 4000},
    "subject_ids": {"type": "array", "maxItems": 64, "uniqueItems": True,
                    "items": {"type": "string", "maxLength": 256}},
    "matter_refs": {"type": "array", "maxItems": 64, "uniqueItems": True,
                    "items": {"type": "string", "maxLength": 256}},
    "time_mode": {"enum": ["current", "valid_at", "known_at"]},
    "valid_at": {"type": ["string", "null"], "format": "date-time"},
    "known_at": {"type": ["string", "null"], "format": "date-time"},
    "evidence_kinds": {"type": "array", "minItems": 1, "maxItems": 5, "uniqueItems": True,
                       "items": {"enum": list(EVIDENCE_KINDS)}},
    "coverage_mode": {"enum": ["ranked", "list"]},
    "cursor": {"type": ["string", "null"], "maxLength": 4096},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
}


def parse_plan(value):
    if isinstance(value, MemoryQueryPlan):
        value = wire(value)
    if not isinstance(value, dict):
        raise AppError("invalid_memory_query_plan")
    ignored = {"actor_id", "person_id", "conversation_id", "cursor", "limit", "client_request_id"}
    if set(value) - PLAN_FIELDS - ignored:
        raise AppError("invalid_memory_query_fields")
    query = value.get("query", "")
    if not isinstance(query, str) or len(query) > 4000:
        raise AppError("invalid_memory_query")
    collections = {}
    for name in ("subject_ids", "matter_refs", "evidence_kinds"):
        items = value.get(name, EVIDENCE_KINDS if name == "evidence_kinds" else ())
        if not isinstance(items, (tuple, list)) or len(items) > 64 or any(
            not isinstance(item, str) or not 1 <= len(item) <= 256 for item in items):
            raise AppError("invalid_memory_query_" + name)
        collections[name] = tuple(dict.fromkeys(items))
    if not collections["evidence_kinds"] or set(collections["evidence_kinds"]) - set(EVIDENCE_KINDS):
        raise AppError("invalid_memory_evidence_kinds")
    mode, valid_at, known_at = temporal_selection(time_mode=value.get("time_mode", "current"),
        valid_at=value.get("valid_at"), known_at=value.get("known_at"))
    coverage_mode = value.get("coverage_mode", "ranked")
    if coverage_mode not in {"ranked", "list"}:
        raise AppError("invalid_memory_coverage_mode")
    limits = value.get("budget", {})
    if not isinstance(limits, dict) or set(limits) - {"max_candidates", "max_tokens"}:
        raise AppError("invalid_memory_query_budget")
    if any(type(item) is not int or item < 1 for item in limits.values()):
        raise AppError("invalid_memory_query_budget")
    limits = {key: min(item, 128 if key == "max_candidates" else 32000) for key, item in limits.items()}
    return MemoryQueryPlan(query, **collections, time_mode=mode, valid_at=valid_at,
                           known_at=known_at, coverage_mode=coverage_mode, budget=limits)


def plan_fingerprint(context, plan):
    return body_hash({"principal": context.principal_id, "actor": context.actor_id,
        "conversation": context.conversation_id, "scopes": sorted(context.scope_ids),
        "access_epoch": context.access_epoch, "plan": replace(plan, budget={})})


def read_cursor(context, plan, cursor):
    if cursor is None:
        return (None, None), datetime.now(UTC)
    try:
        if not isinstance(cursor, str) or len(cursor) > 4096:
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        snapshot = optional_time(payload["as_of"])
        if (payload["version"] != 1 or payload["fingerprint"] != plan_fingerprint(context, plan)
                or payload["kind"] not in EVIDENCE_KINDS or not isinstance(payload["id"], str)
                or snapshot is None or snapshot > datetime.now(UTC) + timedelta(seconds=1)):
            raise ValueError()
        return (payload["kind"], payload["id"]), snapshot
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as exc:
        raise AppError("memory_query_cursor_stale", status=409) from exc


def write_cursor(context, plan, row, snapshot):
    return base64.urlsafe_b64encode(json.dumps({"version": 1,
        "kind": row["_kind"], "id": row["_id"], "as_of": snapshot.isoformat(),
        "fingerprint": plan_fingerprint(context, plan)}, separators=(",", ":")).encode()).decode()
