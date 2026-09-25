"""Synthetic-only delivery proof with distinct client and server message IDs."""

import hashlib
import http.cookiejar
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit
import uuid

CHECKS = {
    "platform": {
        "config",
        "contract",
        "store",
        "sidecars",
        "web_static",
        "tls",
        "credentials",
        "logging",
        "runtime",
    },
    "companion": {"configuration", "logs", "runtime", "dependencies"},
    "memory": {
        "configuration",
        "contract",
        "database",
        "guard",
        "mode",
        "log",
        "assembled",
        "owner",
        "remote",
    },
    "gateway": {
        "configuration",
        "contracts",
        "runtime",
        "ledger",
        "logging",
        "platform",
        "model",
        "native",
    },
}
OPTIONAL = {
    "platform": set(),
    "companion": {"dependencies"},
    "memory": {"remote"},
    "gateway": {"platform", "model", "native"},
}


def client():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile="/etc/tianshu/tls/ca.pem")
        ),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )


def request(opener, url, data=None, headers=None):
    data = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with opener.open(req, timeout=5) as response:
            status, raw = response.status, response.read(1048577)
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read(1048577)
    assert len(raw) <= 1048576
    return status, json.loads(raw)


class ConsoleHost(urllib.request.BaseHandler):
    handler_order = 100

    def __init__(self, origin):
        self.host = urlsplit(origin).netloc

    def http_request(self, request):
        request.add_unredirected_header("Host", self.host)
        return request

    https_request = http_request


def web_client(origin):
    # Internal TLS/DNS remains platform.internal; HTTP authority must match the
    # configured public console, as it does behind a TLS-terminating proxy.
    opener = client()
    opener.add_handler(ConsoleHost(origin))
    return opener


def ready(role, port):
    opener = client()
    url = "https://" + role + ".internal:" + port + "/health/ready"
    status, _ = request(opener, url)
    assert status in (401, 403)
    status, body = request(
        opener,
        url,
        headers={"Authorization": "Bearer " + os.environ["TIANSHU_DIAGNOSTICS_TOKEN"]},
    )
    assert status == 200 and set(body) == {"status", "service", "checks"}
    assert (
        body["service"] == role
        and body["status"] == "ready"
        and set(body["checks"]) == CHECKS[role]
    )
    assert all(body["checks"][k] == "ok" for k in CHECKS[role] - OPTIONAL[role])
    assert all(
        v in {"ok", "failed", "not_configured", "not_verified", "non_durable"}
        for v in body["checks"].values()
    )


def dialogue():
    probe = json.load(sys.stdin)
    password = probe["password"]
    client_id = probe["client_id"]
    phase = probe["phase"]
    assert str(uuid.UUID(client_id)) == client_id
    assert phase in {
        "after_initial_expiry",
        "after_reauthorization",
    }
    settings = json.load(open("/etc/tianshu/settings.json"))
    base = (
        "http://127.0.0.1:8080"
        if settings.get("web_access")
        else "https://platform.internal:8443"
    )
    opener = web_client(settings["web"]["origin"])
    status, session = request(opener, base + "/api/web/session")
    assert status == 200 and session["authenticated"] is False
    headers = {"Origin": settings["web"]["origin"], "X-CSRF-Token": session["csrf"]}
    status, session = request(
        opener,
        base + "/api/web/login",
        {"username": settings["web"]["username"], "password": password},
        headers,
    )
    assert status == 200 and session["authenticated"] is True
    headers["X-CSRF-Token"] = session["csrf"]
    selection = dict(conversation="web-input", actor="actor:household")
    status, receipt = request(
        opener,
        base + "/api/web/messages",
        {
            **selection,
            "text": "NAS-A3 isolated synthetic " + phase + " " + client_id,
            "client_id": client_id,
        },
        headers,
    )
    assert status == 200 and receipt["state"] == "accepted"
    assert isinstance(receipt.get("message_id"), str) and receipt["message_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status, result = request(
            opener, base + "/api/web/snapshot", {**selection, "before": None}, headers
        )
        assert status == 200
        snapshot = result.get("snapshot") or {}
        for item in snapshot.get("history", []) + snapshot.get("active_turns", []):
            if any(
                m.get("message_id") == receipt["message_id"]
                for m in item.get("messages", [])
            ):
                if item["turn"]["phase"] == "sent":
                    assert item["replies"] and all(
                        r["state"] == "sent"
                        and r["content_state"] == "available"
                        and r["text"] == "DEP-G synthetic recorded reply."
                        for r in item["replies"]
                    )
                    print(json.dumps({
                        "schema_version": "synthetic-dialogue-proof/v1",
                        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "client_id": client_id,
                        "message_id": receipt["message_id"],
                        "turn_id": item["turn"]["turn_id"],
                        "reply_ids": [r["reply_id"] for r in item["replies"]],
                        "receipt_state": receipt["state"],
                        "turn_phase": item["turn"]["phase"],
                        "reply_count": len(item["replies"]),
                        "reply_state": "sent",
                        "content_state": "available",
                        "reply_sha256": hashlib.sha256(
                            item["replies"][0]["text"].encode()
                        ).hexdigest(),
                    }, sort_keys=True))
                    return
                assert item["turn"]["phase"] not in {
                    "failed",
                    "cancelled",
                    "closed_unknown",
                }
        time.sleep(0.2)
    raise AssertionError("deadline")


def model_ready():
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            status, body = request(
                client(), "https://gateway.internal:9443/health/live"
            )
            assert status == 200 and body == {"synthetic_model": "ready"}
            return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise AssertionError("synthetic_model_start_deadline")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "delivery-dialogue":
            dialogue()
        else:
            raise ValueError()
    except Exception:
        print("delivery_probe_failed")
        raise SystemExit(1)
