"""Actual loopback TLS/HTTP transport tests, not real-product function evidence."""

import ipaddress
import json
import ssl
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from ops.recovery.drill_http import check, matches
from ops.recovery.lifecycle import Deadline
from ops.recovery.safety import RecoveryError


class DrillHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-recovery-test")]
        )
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .sign(key, hashes.SHA256())
        )
        (cls.root / "ca.pem").write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
        )
        (cls.root / "key.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (cls.root / "token").write_text("isolated-test-only-token")
        cls.requests = []
        cls.post_requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                cls.requests.append((self.path, self.headers.get("Authorization")))
                self.respond()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                cls.post_requests.append(
                    (
                        self.path,
                        self.headers.get("Authorization"),
                        self.headers.get("Content-Type"),
                        body,
                    )
                )
                self.respond()

            def respond(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/sentinel")
                    self.end_headers()
                    return
                data = json.dumps({"status": "unknown", "detail": "fixture"}).encode()
                if self.path == "/large":
                    data = b"x" * (300 * 1024)
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    if self.path == "/slow":
                        for byte in data:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.05)
                    else:
                        self.wfile.write(data)
                except OSError:
                    pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.root / "ca.pem", cls.root / "key.pem")
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def assertion(self, path):
        return {
            "id": "unknown_no_resend",
            "service": "companion",
            "url": f"https://127.0.0.1:{self.server.server_port}{path}",
            "ca_file": "ca.pem",
            "token_file": "token",
            "expected_status": 200,
            "expected_json": {"status": "unknown"},
        }

    def test_actual_tls_authenticated_get_redacts_response(self):
        result = check(self.root, self.assertion("/good"), Deadline(3))
        self.assertEqual(result["status"], "passed")
        self.assertNotIn("fixture", json.dumps(result))
        self.assertIn(("/good", "Bearer isolated-test-only-token"), self.requests)

    def test_allowlisted_readonly_post_sends_bounded_json_and_redacts_response(self):
        assertion = self.assertion("/internal/v1/life-read/actors")
        assertion.update(method="POST", request_json={"schema_version": 1, "limit": 2})
        result = check(self.root, assertion, Deadline(3))
        self.assertEqual(result["status"], "passed")
        self.assertNotIn("fixture", json.dumps(result))
        self.assertIn(
            (
                "/internal/v1/life-read/actors",
                "Bearer isolated-test-only-token",
                "application/json",
                b'{"limit":2,"schema_version":1}',
            ),
            self.post_requests,
        )

    def test_mutating_or_wrong_service_post_is_rejected_before_network(self):
        initial = len(self.post_requests)
        cases = (
            ("/internal/v1/conversation/ingest", "companion"),
            ("/internal/v1/life-read/actors", "gateway"),
        )
        for path, service in cases:
            with self.subTest(path=path, service=service):
                assertion = self.assertion(path)
                assertion.update(service=service, method="POST", request_json={})
                with self.assertRaisesRegex(
                    RecoveryError, "drill_assertion_endpoint_forbidden"
                ):
                    check(self.root, assertion, Deadline(3))
        self.assertEqual(len(self.post_requests), initial)

    def test_post_query_and_oversized_body_are_rejected_before_network(self):
        initial = len(self.post_requests)
        query = self.assertion("/internal/v1/life-read/actors?limit=2")
        query.update(method="POST", request_json={})
        with self.assertRaisesRegex(
            RecoveryError, "drill_assertion_endpoint_forbidden"
        ):
            check(self.root, query, Deadline(3))

        oversized = self.assertion("/internal/v1/life-read/actors")
        oversized.update(method="POST", request_json={"padding": "x" * (16 * 1024)})
        with self.assertRaisesRegex(RecoveryError, "drill_request_limit"):
            check(self.root, oversized, Deadline(3))
        self.assertEqual(len(self.post_requests), initial)

    def test_redirect_is_never_followed(self):
        with self.assertRaisesRegex(RecoveryError, "drill_assertion_status_mismatch"):
            check(self.root, self.assertion("/redirect"), Deadline(3))
        self.assertFalse(any(path == "/sentinel" for path, _ in self.requests))

    def test_continuous_trickle_obeys_total_deadline(self):
        started = time.monotonic()
        with self.assertRaisesRegex(RecoveryError, "lifecycle_timeout"):
            check(self.root, self.assertion("/slow"), Deadline(0.2))
        self.assertLess(time.monotonic() - started, 1)

    def test_response_limit_and_typed_expectations(self):
        with self.assertRaisesRegex(RecoveryError, "drill_response_limit"):
            check(self.root, self.assertion("/large"), Deadline(3))
        self.assertFalse(matches({"value": True}, {"value": 1}))
