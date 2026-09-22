"""A tiny loopback synthetic app for restart/functional recovery checks, not a product."""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from ops.recovery.engine import Recovery
from ops.recovery.safety import lease


def serve(root, scope_id, deployment):
    recovery = Recovery(root, scope_id)
    source, _, _ = recovery.deployment(deployment)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/recall":
                with closing(sqlite3.connect(source / "data/memory/main.db")) as db:
                    rows = db.execute(
                        "SELECT value FROM facts WHERE id='visible' AND value!='forgotten'"
                    ).fetchall()
                self.answer(200, {"results": [row[0] for row in rows]})
            elif self.path == "/model":
                with closing(sqlite3.connect(source / "data/platform/main.db")) as db:
                    value = db.execute(
                        "SELECT value FROM facts WHERE id='model'"
                    ).fetchone()[0]
                self.answer(403 if value == "revoked" else 200, {"status": value})
            elif self.path == "/receipt":
                with closing(
                    sqlite3.connect(source / "data/platform/main.db.web-inputs.sqlite")
                ) as db:
                    value = db.execute(
                        "SELECT status FROM receipts WHERE id='input-1'"
                    ).fetchone()[0]
                self.answer(200, {"status": value})
            else:
                self.answer(404, {"status": "not_found"})

        def do_POST(self):
            if self.path != "/retry-unknown":
                self.answer(404, {"status": "not_found"})
                return
            with closing(sqlite3.connect(source / "data/gateway/main.db")) as db:
                status = db.execute(
                    "SELECT status FROM receipts WHERE id='unknown-job'"
                ).fetchone()[0]
            self.answer(
                409 if status == "unknown" else 200, {"status": status, "resent": False}
            )

    with lease(recovery.root / ".recovery-owner"):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        print(json.dumps({"port": server.server_port, "synthetic": True}), flush=True)
        try:
            server.serve_forever(poll_interval=0.05)
        finally:
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--deployment", required=True)
    args = parser.parse_args()
    serve(args.root, args.scope_id, args.deployment)
