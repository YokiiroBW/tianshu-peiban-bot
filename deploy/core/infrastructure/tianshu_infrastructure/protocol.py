"""Small JSON process envelope; domain collectors do not handle CLI concerns."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

VERSION = "infrastructure-node.v1"
MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_INVENTORY = 10000
OPERATIONS = (
    "node.catalog", "node.snapshot", "docker.inventory", "docker.stats",
    "docker.logs", "compose.inventory",
)
NODE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
REQUEST_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{64}\Z")


class Failure(Exception):
    def __init__(self, code: str, status: str = "unavailable"):
        super().__init__(code)
        self.code = code
        self.status = status


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def decode_json(raw: bytes | str):
    def invalid_constant(_):
        raise ValueError("non-finite JSON number")

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=invalid_constant)


def integer(value, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def bounded_string(value, maximum=512) -> bool:
    return isinstance(value, str) and len(value) <= maximum and "\x00" not in value


@dataclass(frozen=True)
class Request:
    request_id: str
    node_id: str | None
    operation: str
    params: dict
    timeout_ms: int


def parse_request(raw: bytes) -> Request:
    if len(raw) > MAX_REQUEST_BYTES:
        raise Failure("request_too_large", "invalid")
    try:
        value = decode_json(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise Failure("invalid_json", "invalid") from None
    required = {"version", "request_id", "node_id", "operation", "params", "timeout_ms"}
    if not isinstance(value, dict) or set(value) != required:
        raise Failure("invalid_request", "invalid")
    if value["version"] != VERSION or not isinstance(value["request_id"], str) or not REQUEST_ID.fullmatch(value["request_id"]):
        raise Failure("invalid_request", "invalid")
    operation = value["operation"]
    if operation not in OPERATIONS or not isinstance(operation, str):
        raise Failure("unsupported_operation", "invalid")
    node_id = value["node_id"]
    if operation == "node.catalog":
        if node_id is not None:
            raise Failure("invalid_node_id", "invalid")
    elif not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id):
        raise Failure("invalid_node_id", "invalid")
    if not integer(value["timeout_ms"], 500, 30000) or not isinstance(value["params"], dict):
        raise Failure("invalid_request", "invalid")
    params = dict(value["params"])
    if operation in ("node.catalog", "node.snapshot"):
        valid = not params
    elif operation in ("docker.inventory", "compose.inventory"):
        valid = set(params) <= {"limit", "cursor"}
        valid = valid and integer(params.get("limit", 100), 1, 200)
        cursor = params.get("cursor")
        valid = valid and (cursor is None or bounded_string(cursor, 1024) and bool(cursor))
        params.setdefault("limit", 100)
        params.setdefault("cursor", None)
    elif operation == "docker.stats":
        ids = params.get("container_ids")
        valid = set(params) == {"container_ids"} and isinstance(ids, list) and 1 <= len(ids) <= 200
        valid = valid and all(isinstance(item, str) and CONTAINER_ID.fullmatch(item) for item in ids)
        valid = valid and len(set(ids)) == len(ids)
    else:
        valid = set(params) <= {"container_id", "tail", "since_seconds", "max_bytes"}
        identity = params.get("container_id")
        valid = valid and isinstance(identity, str) and bool(CONTAINER_ID.fullmatch(identity))
        for key, default, minimum, maximum in (
            ("tail", 200, 1, 1000), ("since_seconds", 3600, 1, 86400),
            ("max_bytes", 65536, 1024, 262144),
        ):
            params.setdefault(key, default)
            valid = valid and integer(params[key], minimum, maximum)
    if not valid:
        raise Failure("invalid_params", "invalid")
    return Request(value["request_id"], node_id, operation, params, value["timeout_ms"])


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def envelope(request: Request | None, *, status="ok", reasons=(), data=None,
             capabilities=(), returned=0, total=None, next_cursor=None) -> dict:
    return {
        "version": VERSION,
        "request_id": request.request_id if request else None,
        "node_id": request.node_id if request else None,
        "operation": request.operation if request else None,
        "status": status,
        "reason_codes": list(dict.fromkeys(reasons)),
        "observed_at": utc_now(),
        "capabilities": list(capabilities),
        "completeness": {
            "state": "complete" if status == "ok" else "partial" if status == "partial" else "unavailable",
            "returned": returned, "total": total, "next_cursor": next_cursor,
        },
        "data": data,
    }


def encode_response(response: dict, request: Request | None) -> bytes:
    raw = json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raw = json.dumps(envelope(request, status="unavailable", reasons=["response_too_large"]), separators=(",", ":")).encode("utf-8")
    return raw + b"\n"
