"""Synthetic fault port forwarding real sender requests, dropping only the reply.

No invented receipt or event. The upstream must finish before response loss is injected.
Request bodies and credentials remain in memory and are never exported as evidence.
"""

import http.client
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class SenderProxy:
    def __init__(self, root, upstream, token):
        self.calls = 0
        self.accepted = 0
        self.dropped = 0
        self.drop = False
        owner = self
        target = urlsplit(upstream)
        context = ssl.create_default_context(cafile=str(root / "tls/ca.pem"))

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if (
                    self.path != "/internal/v1/conversation/send"
                    or self.headers.get("Authorization") != "Bearer " + token
                ):
                    self.send_error(403)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1048576:
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                owner.calls += 1
                connection = http.client.HTTPSConnection(
                    target.hostname, target.port, context=context, timeout=10
                )
                try:
                    connection.request(
                        "POST",
                        self.path,
                        body,
                        {
                            "Authorization": "Bearer " + token,
                            "Content-Type": "application/json",
                            "X-Correlation-ID": self.headers.get(
                                "X-Correlation-ID", ""
                            ),
                        },
                    )
                    response = connection.getresponse()
                    raw = response.read(1048577)
                    if len(raw) > 1048576:
                        raise ValueError("sender_response_too_large")
                    confirmed = (
                        response.status == 200
                        and json.loads(raw).get("state") == "sent"
                    )
                    if confirmed:
                        owner.accepted += 1
                    if owner.drop and confirmed:
                        owner.dropped += 1
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self.send_response(response.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                finally:
                    connection.close()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(root / "tls/platform.pem", root / "tls/platform.key")
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.url = "https://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
