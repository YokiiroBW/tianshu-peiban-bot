"""Bounded read-only assertions to explicit loopback TLS ports."""

import json
import ssl

from .http_transport import get, post_readonly
from .safety import child, digest, read_bytes, require


def matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and matches(actual[k], v) for k, v in expected.items()
        )
    return type(actual) is type(expected) and actual == expected


def check(root, assertion, budget):
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
    return {
        "id": assertion["id"],
        "service": assertion["service"],
        "status": "passed",
        "response_sha256": digest(data),
    }
