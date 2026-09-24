"""Bounded read-only assertions to explicit loopback TLS ports."""

import json
import ssl

from .http_transport import get, get_readiness, post_readonly
from .safety import child, digest, read_bytes, require


def matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and matches(actual[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            matches(left, right) for left, right in zip(actual, expected)
        )
    return type(actual) is type(expected) and actual == expected


def unknown_turns(data):
    """Return the bounded public unknown-turn and reply facts used by A1."""
    body = json.loads(data)
    require(isinstance(body, dict), "drill_unknown_snapshot_invalid")
    history = body.get("history")
    require(isinstance(history, list), "drill_unknown_snapshot_invalid")
    turns = []
    for entry in history:
        require(isinstance(entry, dict), "drill_unknown_snapshot_invalid")
        turn = entry.get("turn")
        require(isinstance(turn, dict), "drill_unknown_snapshot_invalid")
        if turn.get("phase") != "closed_unknown" and turn.get(
            "delivery_state"
        ) != "unknown":
            continue
        replies = entry.get("replies")
        require(isinstance(replies, list), "drill_unknown_snapshot_invalid")
        projected_replies = []
        for reply in replies:
            require(isinstance(reply, dict), "drill_unknown_snapshot_invalid")
            projected_replies.append(
                {"reply_id": reply.get("reply_id"), "state": reply.get("state")}
            )
        turns.append(
            {
                "turn_id": turn.get("turn_id"),
                "turn_sequence": turn.get("turn_sequence"),
                "phase": turn.get("phase"),
                "delivery_state": turn.get("delivery_state"),
                "replies": projected_replies,
            }
        )
    turns.sort(key=lambda item: item["turn_sequence"] or 0)
    return turns


def check(root, assertion, budget, *, capture_unknown_turns=False):
    budget.check()
    required = {
        "id",
        "service",
        "url",
        "ca_file",
        "token_file",
        "expected_status",
        "expected_json",
    }
    optional = {"method", "request_json"}
    require(
        isinstance(assertion, dict)
        and required <= set(assertion)
        and set(assertion) <= required | optional,
        "drill_assertion_invalid",
    )
    method = assertion.get("method", "GET")
    require(
        type(method) is str and method in {"GET", "POST"},
        "drill_assertion_method_invalid",
    )
    if method == "GET":
        require("request_json" not in assertion, "drill_assertion_invalid")
    else:
        require(type(assertion.get("request_json")) is dict, "drill_assertion_invalid")
    token = (
        read_bytes(child(root, assertion["token_file"]), limit=4096).decode().strip()
    )
    require(token and "\r" not in token and "\n" not in token, "drill_token_invalid")
    tls = ssl.create_default_context(cafile=str(child(root, assertion["ca_file"])))
    if method == "GET":
        data = get(assertion["url"], token, tls, budget, assertion["expected_status"])
    else:
        data = post_readonly(
            assertion["url"],
            token,
            assertion["service"],
            assertion["request_json"],
            tls,
            budget,
            assertion["expected_status"],
        )
    budget.check()
    require(
        matches(json.loads(data), assertion["expected_json"]),
        "drill_assertion_body_mismatch",
    )
    result = {
        "id": assertion["id"],
        "service": assertion["service"],
        "status": "passed",
        "response_sha256": digest(data),
    }
    if capture_unknown_turns:
        require(assertion["id"] == "unknown_no_resend", "drill_assertion_invalid")
        result["unknown_turns"] = unknown_turns(data)
    return result


def check_readiness(root, readiness, budget, *, allow_not_ready=False):
    """Read only the A1 Companion worker-readiness endpoint with its dedicated token."""
    budget.check()
    required = {"url", "ca_file", "token_file"}
    require(
        isinstance(readiness, dict) and set(readiness) == required,
        "drill_readiness_assertion_invalid",
    )
    from urllib.parse import urlsplit

    parsed = urlsplit(readiness["url"])
    require(
        parsed.scheme == "https"
        and parsed.hostname == "127.0.0.1"
        and parsed.port is not None
        and parsed.path == "/health/ready"
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password,
        "drill_readiness_endpoint_forbidden",
    )
    token = read_bytes(child(root, readiness["token_file"]), limit=4096)
    token = token.decode().strip()
    require(token and "\r" not in token and "\n" not in token, "drill_token_invalid")
    tls = ssl.create_default_context(cafile=str(child(root, readiness["ca_file"])))
    status, data = get_readiness(readiness["url"], token, tls, budget)
    budget.check()
    actual = json.loads(data)
    require(isinstance(actual, dict), "drill_readiness_body_invalid")
    checks = actual.get("checks")
    require(isinstance(checks, dict), "drill_readiness_body_invalid")
    runtime = checks.get("runtime")
    if status == 503 and allow_not_ready:
        require(
            matches(actual, {"status": "not_ready", "service": "companion"})
            and isinstance(runtime, str),
            "drill_readiness_body_invalid",
        )
        return {"status": "not_ready", "runtime": runtime}
    require(
        status == 200
        and matches(
            actual,
            {
                "status": "ready",
                "service": "companion",
                "checks": {"runtime": "ok"},
            },
        ),
        "drill_worker_runtime_unhealthy",
    )
    return {"status": "ready", "runtime": "ok", "response_sha256": digest(data)}
