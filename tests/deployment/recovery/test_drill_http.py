"""Actual loopback TLS/HTTP transport tests, not real-product function evidence."""

import ipaddress
import json
import socket
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

from ops.recovery.drill_http import check, check_readiness, matches
from ops.recovery.http_transport import post_readonly, response_structure
from ops.recovery.lifecycle import Deadline
from ops.recovery.safety import DrillDiagnosticError, RecoveryError


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
        (cls.root / "wrong-token").write_text("isolated-wrong-token")
        (cls.root / "compose.json").write_text(
            json.dumps(
                {"services": {"memory": {"command": [
                    "serve", "--port", "8130", "--allowed-host", "memory.internal:8130"
                ]}}}
            )
        )
        cls.requests = []
        cls.post_requests = []
        cls.host_headers = []
        cls.readiness_status = 200

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                cls.requests.append((self.path, self.headers.get("Authorization")))
                self.respond()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                cls.host_headers.append((self.path, self.headers.get("Host")))
                cls.post_requests.append(
                    (
                        self.path,
                        self.headers.get("Authorization"),
                        self.headers.get("Content-Type"),
                        body,
                    )
                )
                if self.path == "/internal/v1/memory/select":
                    if self.headers.get("Host") != "memory.internal:8130":
                        self.respond_json(400, {"status": "failed", "code": "invalid_host"})
                    elif self.headers.get("Authorization") != "Bearer isolated-test-only-token":
                        self.respond_json(401, {"status": "failed", "code": "unauthorized"})
                    elif json.loads(body).get("query_text") == "forbidden":
                        self.respond_json(403, {"status": "failed", "code": "forbidden"})
                    elif json.loads(body).get("query_text") == "contract_invalid":
                        self.respond_json(400, {"status": "failed", "code": "invalid_input"})
                    else:
                        self.respond_json(200, {"selected_units": [], "omissions": ["no_match"]})
                    return
                self.respond()

            def respond_json(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def respond(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/sentinel")
                    self.end_headers()
                    return
                if self.path == "/health/ready":
                    ready = cls.readiness_status == 200
                    data = json.dumps(
                        {
                            "status": "ready" if ready else "not_ready",
                            "service": "companion",
                            "checks": {
                                "configuration": "ok",
                                "logs": "ok",
                                "runtime": "ok" if ready else "failed",
                                "dependencies": "not_verified",
                            },
                        }
                    ).encode()
                    response_status = cls.readiness_status
                else:
                    data = json.dumps(
                        {"status": "unknown", "detail": "fixture", "history": []}
                    ).encode()
                    response_status = 200
                if self.path == "/large":
                    data = b"x" * (300 * 1024)
                self.send_response(response_status)
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

    def memory_assertion(self, term="green"):
        assertion = self.assertion("/internal/v1/memory/select")
        assertion.update(
            id="forgotten", service="memory", method="POST",
            request_json={"query_text": term},
            expected_json={"selected_units": [], "omissions": ["no_match"]},
        )
        return assertion

    def test_memory_host_comes_from_pinned_compose_listener(self):
        assertion = self.memory_assertion()
        tls = ssl.create_default_context(cafile=str(self.root / "ca.pem"))
        with self.assertRaises(DrillDiagnosticError) as wrong_host:
            post_readonly(
                assertion["url"], "isolated-test-only-token", "memory",
                assertion["request_json"], tls, Deadline(3), 200,
            )
        self.assertEqual(wrong_host.exception.detail()["actual_http_status"], 400)
        self.assertEqual(
            wrong_host.exception.detail()["response_structure"]["product_code"],
            "invalid_host",
        )
        self.assertEqual(check(self.root, assertion, Deadline(3))["status"], "passed")
        self.assertEqual(self.host_headers[-1], (
            "/internal/v1/memory/select", "memory.internal:8130"))

    def test_memory_host_cannot_be_supplied_by_untrusted_compose_input(self):
        path = self.root / "compose.json"
        original = path.read_bytes()
        document = json.loads(original)
        document["services"]["memory"]["command"][-1] = "evil.example.test:8130"
        before = len(self.host_headers)
        try:
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(RecoveryError, "drill_memory_authority_invalid"):
                check(self.root, self.memory_assertion(), Deadline(3))
        finally:
            path.write_bytes(original)
        self.assertEqual(len(self.host_headers), before)

    def test_memory_auth_contract_and_business_outcomes_are_distinct(self):
        cases = (
            ("wrong-token", "green", 401, "unauthorized"),
            ("token", "forbidden", 403, "forbidden"),
            ("token", "contract_invalid", 400, "invalid_input"),
        )
        for token_file, term, status, product_code in cases:
            with self.subTest(status=status, code=product_code):
                assertion = self.memory_assertion(term)
                assertion["token_file"] = token_file
                with self.assertRaises(DrillDiagnosticError) as failed:
                    check(self.root, assertion, Deadline(3))
                detail = failed.exception.detail()
                self.assertEqual(detail["code"], "drill_assertion_status_mismatch")
                self.assertEqual(detail["stage"], "match_status")
                self.assertEqual(detail["actual_http_status"], status)
                self.assertEqual(
                    detail["response_structure"]["product_code"], product_code
                )
        self.assertEqual(
            check(self.root, self.memory_assertion(), Deadline(3))["status"], "passed"
        )
        mismatched = self.memory_assertion()
        mismatched["expected_json"] = {"selected_units": [{"id": "expected"}]}
        with self.assertRaises(DrillDiagnosticError) as body_failure:
            check(self.root, mismatched, Deadline(3))
        self.assertEqual(body_failure.exception.detail()["stage"], "match_body")
        self.assertEqual(body_failure.exception.detail()["actual_http_status"], 200)

    def test_connection_refusal_and_tls_failure_have_separate_codes(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            unused_port = reserved.getsockname()[1]
        refused = self.memory_assertion()
        refused["url"] = f"https://127.0.0.1:{unused_port}/internal/v1/memory/select"
        with self.assertRaises(DrillDiagnosticError) as connection:
            check(self.root, refused, Deadline(3))
        self.assertEqual(connection.exception.detail(), {
            "code": "drill_connection_refused", "stage": "connect"})

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wrong-test-ca")])
        now = datetime.now(timezone.utc)
        wrong_ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(minutes=1))
                    .not_valid_after(now + timedelta(hours=1))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                    .sign(key, hashes.SHA256()))
        (self.root / "wrong-ca.pem").write_bytes(
            wrong_ca.public_bytes(serialization.Encoding.PEM)
        )
        wrong_tls = self.memory_assertion()
        wrong_tls["ca_file"] = "wrong-ca.pem"
        with self.assertRaises(DrillDiagnosticError) as handshake:
            check(self.root, wrong_tls, Deadline(3))
        self.assertEqual(handshake.exception.detail(), {
            "code": "drill_tls_handshake_failed", "stage": "connect"})

    def test_response_structure_never_copies_untrusted_values_or_keys(self):
        shape = response_structure(
            b'{"code":"invalid_host","status":"failed","secret-token":"private"}'
        )
        self.assertEqual(shape, {"kind": "object", "key_count": 3,
                                 "known_keys": ["code", "status"],
                                 "product_code": "invalid_host"})

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

    def test_platform_model_snapshot_is_an_allowlisted_read_boundary(self):
        assertion = self.assertion("/internal/v1/model-config/snapshot")
        assertion.update(
            service="platform",
            method="POST",
            request_json={"schema_version": 1, "request_id": "read:1"},
        )
        result = check(self.root, assertion, Deadline(3))
        self.assertEqual(result["status"], "passed")
        self.assertIn(
            (
                "/internal/v1/model-config/snapshot",
                "Bearer isolated-test-only-token",
                "application/json",
                b'{"request_id":"read:1","schema_version":1}',
            ),
            self.post_requests,
        )
        wrong_service = dict(assertion, service="gateway")
        with self.assertRaisesRegex(RecoveryError, "drill_assertion_endpoint_forbidden"):
            check(self.root, wrong_service, Deadline(3))

    def test_companion_web_snapshot_is_an_allowlisted_read_boundary(self):
        assertion = self.assertion("/internal/v1/conversation/web-snapshot")
        assertion.update(
            method="POST",
            request_json={"schema_version": 1, "query": {"request_id": "read:1"}},
        )
        result = check(self.root, assertion, Deadline(3))
        self.assertEqual(result["status"], "passed")
        self.assertIn(
            (
                "/internal/v1/conversation/web-snapshot",
                "Bearer isolated-test-only-token",
                "application/json",
                b'{"query":{"request_id":"read:1"},"schema_version":1}',
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

    def test_array_expectations_require_exact_length_and_recurse(self):
        expected = [{"turn": {"id": "t1"}}, {"turn": {"id": "t2"}}]
        self.assertTrue(
            matches(
                [
                    {"turn": {"id": "t1", "phase": "closed_unknown"}},
                    {"turn": {"id": "t2", "phase": "closed_unknown"}},
                ],
                expected,
            )
        )
        self.assertFalse(
            matches(
                [
                    {"turn": {"id": "t1"}},
                    {"turn": {"id": "t2"}},
                    {"turn": {"id": "t3"}},
                ],
                expected,
            )
        )
        self.assertFalse(matches([{"id": "t1"}], expected))

    def test_dedicated_readiness_probe_accepts_only_ready_or_retryable_startup(self):
        config = {
            "url": f"https://127.0.0.1:{self.server.server_port}/health/ready",
            "ca_file": "ca.pem",
            "token_file": "token",
        }
        self.assertEqual(
            check_readiness(self.root, config, Deadline(3))["runtime"], "ok"
        )
        self.__class__.readiness_status = 503
        self.assertEqual(
            check_readiness(
                self.root, config, Deadline(3), allow_not_ready=True
            )["status"],
            "not_ready",
        )
        with self.assertRaisesRegex(RecoveryError, "drill_worker_runtime_unhealthy"):
            check_readiness(self.root, config, Deadline(3))
        self.__class__.readiness_status = 200
