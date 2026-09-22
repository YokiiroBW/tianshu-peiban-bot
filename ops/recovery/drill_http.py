"""Bounded GET assertions to explicit loopback TLS ports; no redirects or proxies."""

import json
import ssl
import time
import urllib.error
import urllib.request

from .safety import child, digest, read_bytes, require


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


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
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
        urllib.request.HTTPSHandler(context=tls),
    )
    request = urllib.request.Request(
        assertion["url"],
        method="GET",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        response = opener.open(
            request, timeout=min(2, max(0.01, budget.ends - time.monotonic()))
        )
    except urllib.error.HTTPError as error:
        response = error
    with response:
        require(
            response.status == assertion["expected_status"],
            "drill_assertion_status_mismatch",
        )
        data = bytearray()
        while True:
            budget.check()
            part = response.read1(4096)
            if not part:
                break
            data.extend(part)
            require(len(data) <= 256 * 1024, "drill_response_limit")
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
