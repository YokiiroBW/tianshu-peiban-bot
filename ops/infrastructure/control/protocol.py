"""Control framing and strict requests; observation protocol stays unchanged."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..protocol import CONTAINER_ID, NODE_ID, REQUEST_ID, Failure, bounded_string, decode_json, integer, utc_now

VERSION = "infrastructure-control.v1"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
STACK_ID = re.compile(r"stack_[a-f0-9]{24}\Z")
PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")
OPERATIONS = ("control.catalog", "stack.list", "stack.inspect", "container.inspect", "stack.plan", "operation.submit", "operation.get", "operation.list", "stack.versions")
ACTIONS = ("container.start", "container.stop", "container.restart", "stack.start", "stack.stop", "stack.restart", "stack.apply")


@dataclass(frozen=True)
class Request:
    request_id: str
    node_id: str | None
    operation: str
    params: dict
    timeout_ms: int

    def value(self):
        return {"version": VERSION, "request_id": self.request_id, "node_id": self.node_id,
                "operation": self.operation, "params": self.params, "timeout_ms": self.timeout_ms}


def matches(pattern, value):
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def parse_request(raw):
    if len(raw) > MAX_REQUEST_BYTES:
        raise Failure("request_too_large", "invalid")
    try:
        value = decode_json(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise Failure("invalid_json", "invalid") from None
    fields = {"version", "request_id", "node_id", "operation", "params", "timeout_ms"}
    if not isinstance(value, dict) or set(value) != fields or value["version"] != VERSION:
        raise Failure("invalid_request", "invalid")
    if not matches(REQUEST_ID, value["request_id"]) or value["operation"] not in OPERATIONS or not integer(value["timeout_ms"], 500, 30000):
        raise Failure("invalid_request", "invalid")
    operation, params, node = value["operation"], value["params"], value["node_id"]
    if (node is not None if operation == "control.catalog" else not matches(NODE_ID, node)):
        raise Failure("invalid_node_id", "invalid")
    if not isinstance(params, dict):
        raise Failure("invalid_params", "invalid")
    params = dict(params)
    valid = False
    if operation == "control.catalog":
        valid = not params
    elif operation in ("stack.list", "operation.list", "stack.versions"):
        valid = set(params) <= {"limit", "cursor", *(('stack_id',) if operation == 'stack.versions' else ())}
        if operation == "stack.versions":
            valid = valid and matches(STACK_ID, params.get("stack_id"))
        params.setdefault("limit", 100)
        params.setdefault("cursor", None)
        valid = valid and integer(params["limit"], 1, 200) and (params["cursor"] is None or bounded_string(params["cursor"], 1024) and bool(params["cursor"]))
    elif operation == "stack.inspect":
        valid = set(params) == {"stack_id"} and matches(STACK_ID, params["stack_id"])
    elif operation == "container.inspect":
        valid = set(params) == {"container_id"} and matches(CONTAINER_ID, params["container_id"])
    elif operation == "operation.get":
        valid = set(params) <= {"operation_id", "reconcile"} and matches(REQUEST_ID, params.get("operation_id"))
        params.setdefault("reconcile", True)
        valid = valid and type(params["reconcile"]) is bool
    elif operation == "operation.submit":
        valid = set(params) == {"operation_id", "action", "target"} and matches(REQUEST_ID, params.get("operation_id"))
        target, action = params.get("target"), params.get("action")
        valid = valid and action in ACTIONS and isinstance(target, dict)
        if valid:
            if action.startswith("container."):
                valid = set(target) == {"container_id"} and matches(CONTAINER_ID, target["container_id"])
            elif action == "stack.apply":
                valid = set(target) == {"plan_id"} and matches(REQUEST_ID, target["plan_id"])
            else:
                valid = set(target) == {"stack_id", "expected_revision"} and matches(STACK_ID, target["stack_id"]) and matches(CONTAINER_ID, target["expected_revision"])
    elif operation == "stack.plan":
        required = {"create": {"action", "project_name", "compose_content"}, "deploy": {"action", "stack_id", "expected_revision", "compose_content"},
                    "update": {"action", "stack_id", "expected_revision"}, "restore": {"action", "stack_id", "expected_revision", "restore_version_id"}}
        action = params.get("action")
        valid = isinstance(action, str) and action in required and set(params) == required[action]
        if valid:
            valid = matches(PROJECT, params.get("project_name")) if action == "create" else matches(STACK_ID, params["stack_id"]) and matches(CONTAINER_ID, params["expected_revision"])
            if action in ("create", "deploy"):
                valid = valid and bounded_string(params["compose_content"], 262144) and bool(params["compose_content"])
            elif action == "restore":
                valid = valid and matches(REQUEST_ID, params["restore_version_id"])
    if not valid:
        raise Failure("invalid_params", "invalid")
    return Request(value["request_id"], node, operation, params, value["timeout_ms"])


def envelope(request=None, *, data=None, status="ok", reasons=(), returned=0, total=None, cursor=None):
    return {"version": VERSION, "request_id": request.request_id if request else None,
            "node_id": request.node_id if request else None, "operation": request.operation if request else None,
            "status": status, "reason_codes": list(dict.fromkeys(reasons)), "observed_at": utc_now(),
            "capabilities": list(OPERATIONS), "completeness": {"state": "complete" if status == "ok" else "partial" if status == "partial" else "unavailable",
            "returned": returned, "total": total, "next_cursor": cursor}, "data": data}


def encode(value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_RESPONSE_BYTES:
        raise Failure("response_too_large")
    return raw + b"\n"


def semantic(node_id, params):
    import hashlib
    return hashlib.sha256(json.dumps([node_id, params], ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def empty_runtime():
    return {"state": "unknown", "health": "unknown", "containers": [], "observed_at": utc_now()}


def receipt(node_id, params):
    now = utc_now()
    return {"operation_id": params["operation_id"], "node_id": node_id, "action": params["action"], "target": params["target"],
            "state": "accepted", "phase": "accepted", "created_at": now, "updated_at": now, "execution_active": None,
            "command": {"state": "not_sent", "exit_code": None, "reason_codes": []},
            "runtime": empty_runtime(), "reason_codes": [], "version_id": None}
