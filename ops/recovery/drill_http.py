"""Bounded GET assertions to explicit loopback TLS ports; no redirects or proxies."""

import json
import ssl

from .http_transport import get
from .safety import child, digest, read_bytes, require


def matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and matches(actual[k], v) for k, v in expected.items()
        )
    return type(actual) is type(expected) and actual == expected


def check(root, assertion, budget):
    budget.check()
    token = (
        read_bytes(child(root, assertion["token_file"]), limit=4096).decode().strip()
    )
    require(token and "\r" not in token and "\n" not in token, "drill_token_invalid")
    tls = ssl.create_default_context(cafile=str(child(root, assertion["ca_file"])))
    data = get(assertion["url"], token, tls, budget, assertion["expected_status"])
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
