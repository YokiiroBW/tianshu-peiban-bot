"""Explicit QA-only model fixture in gateway's own container/network; no external calls."""

import json
import os
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/health/live":
            self.send_error(404)
            return
        raw = b'{"synthetic_model":"ready"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self.connection.settimeout(5)
        if (
            self.path != "/v1/chat/completions"
            or self.headers.get("Authorization")
            != "Bearer " + os.environ["TS_SYNTHETIC_MODEL"]
        ):
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 1048576:
            self.send_error(413)
            return
        body = json.loads(self.rfile.read(length))
        base = dict(
            id="dep-g-synthetic",
            object="chat.completion",
            created=int(time.time()),
            model="synthetic-recorded-text",
        )
        content = "DEP-G synthetic recorded reply."
        if body.get("stream"):
            base["object"] = "chat.completion.chunk"
            chunk = {
                **base,
                "choices": [
                    dict(
                        index=0,
                        delta=dict(role="assistant", content=content),
                        finish_reason=None,
                    )
                ],
            }
            final = {**base, "choices": [dict(index=0, delta={}, finish_reason="stop")]}
            raw = (
                "data: "
                + json.dumps(chunk)
                + "\n\ndata: "
                + json.dumps(final)
                + "\n\ndata: [DONE]\n\n"
            ).encode()
            mime = "text/event-stream"
        else:
            raw = json.dumps(
                {
                    **base,
                    "choices": [
                        dict(
                            index=0,
                            message=dict(role="assistant", content=content),
                            finish_reason="stop",
                        )
                    ],
                }
            ).encode()
            mime = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 9443), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain("/etc/tianshu/tls/server.pem", "/etc/tianshu/tls/server.key")
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
