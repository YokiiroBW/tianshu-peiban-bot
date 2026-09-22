"""Exercise the pinned public reconciliation CLI against an explicit HTTPS query fixture.

This verifies command/config/TLS wiring, not a real Loki or Vector ingestion pipeline.
"""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
import time
import uuid

from manifest import read_json
from observability_release import reconcile


def exercise(bundle, inputs, output):
    records = []
    for role in ("platform", "companion", "memory", "gateway"):
        event = {
            "schema_version": "1.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": role,
            "instance_id": str(uuid.uuid4()),
            "sequence": 1,
            "event_id": str(uuid.uuid4()),
            "level": "INFO",
            "event": "runtime.started",
            "outcome": "succeeded",
            "correlation_id": None,
            "duration_ms": None,
            "error_code": None,
        }
        raw = json.dumps(event, separators=(",", ":"))
        records.append(raw)
        (bundle / "logs" / role / "synthetic.jsonl").write_bytes(raw.encode() + b"\n")
    generated = output / "generated.jsonl"
    generated.write_bytes(("\n".join(records) + "\n").encode())
    token = (inputs / "secrets/query_token").read_text()
    instant = time.time_ns()
    state = {"omit": False, "calls": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if (
                not self.path.startswith("/loki/api/v1/query_range?")
                or self.headers.get("Authorization") != "Bearer " + token
            ):
                self.send_error(403)
                return
            state["calls"] += 1
            value = {
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {
                            "stream": {"stack": "tianshu"},
                            "values": [
                                [str(instant), row]
                                for row in (records[:-1] if state["omit"] else records)
                            ],
                        }
                    ],
                },
            }
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(inputs / "tls/loki.pem", inputs / "tls/loki.key")
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reports = []
        for missing in (False, True):
            state["omit"] = missing
            path = output / ("missing.json" if missing else "complete.json")
            result = reconcile(
                bundle,
                generated,
                "https://127.0.0.1:" + str(server.server_port),
                inputs / "tls/ca.pem",
                inputs / "secrets/query_token",
                instant - 1,
                instant + 1,
                path,
            )
            assert result["status"] == (
                "log_reconciliation_failed" if missing else "log_reconciled"
            )
            body = read_json(path)
            assert body["missing_in_loki"] == int(missing)
            assert body["application_reclamation_authorized"] is False
            reports.append(body)
        assert state["calls"] == 2
        return {
            "public_cli_tls_wiring": "passed",
            "backend_kind": "synthetic_https_query_fixture",
            "actual_loki": "not_run",
            "reports": reports,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
