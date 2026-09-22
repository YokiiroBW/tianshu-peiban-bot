"""Boundary tests for fault evidence, independent of the four-product execution."""

import http.client
import json
import os
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from acceptance.transport import Failed
from product_faults import FaultControl
from product_inputs import tls
from product_sender import SenderProxy
from product_stack import Stack


class SenderBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tls(self.root / "tls")
        self.state = "sent"
        self.received = 0
        owner = self

        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.received += 1
                raw = json.dumps({"state": owner.state}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            self.root / "tls/platform.pem", self.root / "tls/platform.key"
        )
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.proxy = SenderProxy(
            self.root, "https://127.0.0.1:" + str(self.server.server_port), "test-only"
        )

    def tearDown(self):
        self.proxy.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, token="test-only"):
        connection = http.client.HTTPSConnection(
            "127.0.0.1",
            self.proxy.server.server_port,
            context=ssl.create_default_context(cafile=str(self.root / "tls/ca.pem")),
            timeout=3,
        )
        try:
            connection.request(
                "POST",
                "/internal/v1/conversation/send",
                b"{}",
                {"Authorization": "Bearer " + token},
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_response_loss_requires_actual_sent_receipt(self):
        self.proxy.drop = True
        with self.assertRaises(http.client.RemoteDisconnected):
            self.request()
        self.assertEqual(
            (1, 1, 1), (self.received, self.proxy.accepted, self.proxy.dropped)
        )
        self.proxy.drop = False
        self.assertEqual(200, self.request()[0])
        self.assertEqual(1, self.proxy.dropped)

    def test_failed_200_receipt_is_forwarded_not_rewritten_as_unknown(self):
        self.proxy.drop = True
        self.state = "failed"
        status, body = self.request()
        self.assertEqual((200, {"state": "failed"}), (status, json.loads(body)))
        self.assertEqual((0, 0), (self.proxy.accepted, self.proxy.dropped))

    def test_wrong_credential_never_reaches_sender(self):
        self.assertEqual(403, self.request("wrong")[0])
        self.assertEqual((0, 0), (self.received, self.proxy.calls))


class LogBoundaryTests(unittest.TestCase):
    def test_rotated_segment_is_observed_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs/companion"
            logs.mkdir(parents=True)
            (logs / "companion.jsonl").write_text('{"event":"old"}\n')
            (logs / "companion.jsonl.1").write_text('{"event":"new"}\n{"incomplete":')
            stack = object.__new__(Stack)
            stack.root = root
            self.assertEqual(
                {"old", "new"}, {row["event"] for row in stack.logs("companion")}
            )

    @unittest.skipUnless(os.name == "nt", "Windows mandatory byte-lock fault")
    def test_lock_covers_rotated_append_and_unlock_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs/companion"
            logs.mkdir(parents=True)
            path = logs / "companion.jsonl.3"
            path.write_bytes(b"original\n")
            control = object.__new__(FaultControl)
            control.stack = SimpleNamespace(root=root)
            control.locks = []
            control.lock_logs("companion")
            try:
                with self.assertRaises(OSError):
                    with path.open("ab", buffering=0) as stream:
                        stream.write(b"must-not-write")
            finally:
                control.unlock_logs()
            self.assertEqual(b"original\n", path.read_bytes())

    def test_no_file_is_a_refusal_not_a_successful_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = object.__new__(FaultControl)
            control.stack = SimpleNamespace(root=Path(tmp))
            control.locks = []
            if os.name == "nt":
                with self.assertRaisesRegex(Failed, "no_real_log_file_to_lock"):
                    control.lock_logs("memory")
                self.assertEqual([], control.locks)


if __name__ == "__main__":
    unittest.main()
