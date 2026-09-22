"""Synthetic internal-only Grafana recorder, launched inside the owned guard."""

import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import time

ROOT = Path("/var/lib/guard")
ALLOWED = {
    "PipelineTargetDown",
    "InvalidOrChangedSource",
    "CollectorDiscardOrError",
    "StorageQueryFailed",
    "PendingDelivery",
}


def safe_alerts(document):
    return [
        {"name": alert["labels"]["alertname"], "status": alert["status"]}
        for alert in document.get("alerts", [])
        if alert.get("labels", {}).get("alertname") in ALLOWED
        and alert.get("status") in {"firing", "resolved"}
    ]


def main():
    token = (ROOT / "depi-receiver-token").read_text().strip()
    output = ROOT / "depi-alerts.jsonl"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.connection.settimeout(5)
            if self.path != "/record" or not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            ):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 262144 or (
                    output.exists() and output.stat().st_size > 1024**2
                ):
                    self.send_error(507)
                    return
                raw = self.rfile.read(size)
                if len(raw) != size:
                    self.send_error(400)
                    return
                alerts = safe_alerts(json.loads(raw))
                with output.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps({"received_at": time.time(), "alerts": alerts})
                        + "\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except (ValueError, OSError, TypeError):
                self.close_connection = True

    HTTPServer(("0.0.0.0", 18081), Handler).serve_forever()


if __name__ == "__main__":
    main()
