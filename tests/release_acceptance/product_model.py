"""Explicit local HTTPS model fault port; never a real provider."""

import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ModelFixture:
    def __init__(self, root, port, token):
        self.calls = 0
        self.disconnect = False
        self.delay = 0
        self.requests = []
        self.completed = 0
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if (
                    self.path != "/v1/chat/completions"
                    or self.headers.get("Authorization") != "Bearer " + token
                ):
                    self.send_error(403)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1048576:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(length))
                owner.calls += 1
                owner.requests.append(body)
                if owner.delay:
                    owner.release.wait(owner.delay)
                if owner.disconnect:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                base = {
                    "id": "synthetic-completion",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "synthetic-recorded-text",
                }
                if body.get("stream"):
                    chunk = {
                        **base,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "Synthetic recorded reply.",
                                },
                                "finish_reason": None,
                            }
                        ],
                    }
                    final = {
                        **base,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
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
                                {
                                    "index": 0,
                                    "message": {
                                        "role": "assistant",
                                        "content": "Synthetic recorded reply.",
                                    },
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                    ).encode()
                    mime = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (OSError, ssl.SSLError):
                    pass
                finally:
                    owner.completed += 1

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(root / "tls/model.pem", root / "tls/model.key")
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
