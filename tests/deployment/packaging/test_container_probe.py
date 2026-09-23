"""Real TLS/cookie transport regression; does not claim browser rendering."""

import json
import ssl
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "deploy/tianshu"))
import container_probe as probe
from synthetic_init import tls


class ProbeTransportTests(unittest.TestCase):
    def test_public_host_internal_tls_and_secure_cookie_round_trip(self):
        origin = "https://console.synthetic.test:19443"
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append((self.headers.get("Host"), self.headers.get("Cookie")))
                allowed = self.headers.get("Host") == "console.synthetic.test:19443"
                self.send_response(200 if allowed else 403)
                self.send_header("Content-Type", "application/json")
                self.send_header(
                    "Set-Cookie", "session=fixture; Path=/; Secure; HttpOnly"
                )
                self.end_headers()
                self.wfile.write(json.dumps({"allowed": allowed}).encode())

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tls"
            tls(root)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(
                root / "platform/server.pem", root / "platform/server.key"
            )
            context = ssl.create_default_context(cafile=str(root / "ca.pem"))
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.socket = server_context.wrap_socket(server.socket, server_side=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f"https://127.0.0.1:{server.server_port}/api/web/session"
                with patch.object(
                    probe.ssl, "create_default_context", return_value=context
                ):
                    self.assertEqual(probe.request(probe.client(), url)[0], 403)
                    opener = probe.web_client(origin)
                    self.assertEqual(probe.request(opener, url)[0], 200)
                    self.assertEqual(probe.request(opener, url)[0], 200)
                self.assertEqual(observed[-2], ("console.synthetic.test:19443", None))
                self.assertEqual(
                    observed[-1], ("console.synthetic.test:19443", "session=fixture")
                )
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
