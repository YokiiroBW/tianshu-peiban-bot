"""Real loopback TLS adversaries for every HTTP phase; no product/NAS claim."""

import socket
import ssl
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import test_drill_http as certificates

from ops.recovery import http_transport
from ops.recovery.drill_http import check
from ops.recovery.lifecycle import Deadline
from ops.recovery.safety import RecoveryError


class HTTPDeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = certificates.DrillHTTPTests
        cls.fixture.setUpClass()
        cls.root = cls.fixture.root
        cls.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.tls.load_cert_chain(cls.root / "ca.pem", cls.root / "key.pem")

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def setUp(self):
        self.closed = []
        closed = self.closed
        original = http_transport.Connection

        class TrackedConnection(original):
            def close(self):
                sock = self.socket
                super().close()
                if sock is not None:
                    closed.append(sock.fileno())

        self.tracker = patch.object(http_transport, "Connection", TrackedConnection)
        self.tracker.start()

    def tearDown(self):
        self.tracker.stop()
        self.assertTrue(self.closed)
        self.assertTrue(all(fd == -1 for fd in self.closed), self.closed)

    @contextmanager
    def server(self, send, *, slow_tls=False):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        stopping = threading.Event()
        errors = []

        def run():
            connection = None
            try:
                connection, _ = listener.accept()
                connection.settimeout(1)
                if slow_tls:
                    # Real server TLS handshake bytes, trickled continuously. The client
                    # cannot finish do_handshake before its shared budget expires.
                    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
                    engine = self.tls.wrap_bio(incoming, outgoing, server_side=True)
                    incoming.write(connection.recv(65536))
                    try:
                        engine.do_handshake()
                    except ssl.SSLWantReadError:
                        pass
                    payload = outgoing.read()
                    if not payload:
                        raise AssertionError("server handshake produced no bytes")
                    for byte in payload:
                        if stopping.wait(0.01):
                            break
                        connection.sendall(bytes([byte]))
                    return
                connection = self.tls.wrap_socket(connection, server_side=True)
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    part = connection.recv(4096)
                    if not part:
                        return
                    request.extend(part)
                    if len(request) > 16384:
                        raise AssertionError("unexpected large test request")
                send(connection, stopping)
            except OSError:
                # The tested client deliberately closes timeout/cancel/oversize sockets.
                pass
            except BaseException as error:
                errors.append(error)
            finally:
                if connection is not None:
                    connection.close()

        thread = threading.Thread(target=run, name="owned-synthetic-http-adversary")
        thread.start()
        try:
            yield {
                "id": "unknown_no_resend",
                "service": "companion",
                "url": f"https://127.0.0.1:{listener.getsockname()[1]}/receipt",
                "ca_file": "ca.pem",
                "token_file": "token",
                "expected_status": 200,
                "expected_json": {"status": "unknown"},
            }
        finally:
            stopping.set()
            listener.close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "test server must not outlive test")
            self.assertEqual(errors, [])

    @staticmethod
    def trickle(prefix, payload):
        def send(connection, stopping):
            connection.sendall(prefix)
            for byte in payload:
                if stopping.wait(0.01):
                    return
                connection.sendall(bytes([byte]))

        return send

    def bounded(self, sender, *, slow_tls=False, cancelled=False):
        with self.server(sender, slow_tls=slow_tls) as assertion:
            started = time.monotonic()
            budget = Deadline(
                2 if cancelled else 0.15,
                cancel=(lambda: time.monotonic() - started > 0.08)
                if cancelled
                else None,
            )
            with self.assertRaisesRegex(
                RecoveryError, "cancelled" if cancelled else "lifecycle_timeout"
            ):
                check(self.root, assertion, budget)
            self.assertLess(time.monotonic() - started, 0.4)
            # Socket has already closed when check returns, before the server is stopped.
            self.assertEqual(self.closed[-1], -1)

    def test_continuous_slow_status_line_is_bounded(self):
        self.bounded(self.trickle(b"", b"HTTP/1.1 200 OK\r\n"))

    def test_continuous_slow_headers_are_bounded(self):
        self.bounded(
            self.trickle(b"HTTP/1.1 200 OK\r\nX-Slow: ", b"a" * 200 + b"\r\n\r\n")
        )

    def test_continuous_slow_tls_handshake_is_bounded(self):
        self.bounded(None, slow_tls=True)

    def test_one_deadline_is_shared_across_status_headers_and_body(self):
        def send(connection, stopping):
            for part in (
                b"HTTP/1.1 200 OK\r\n",
                b"Content-Length: 20\r\n\r\n",
                b'{"status":"unknown"}',
            ):
                if stopping.wait(0.065):
                    return
                connection.sendall(part)

        self.bounded(send)

    def test_allowlisted_post_uses_the_same_total_deadline(self):
        with self.server(
            self.trickle(b"HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n", b"a" * 200)
        ) as assertion:
            assertion["url"] = assertion["url"].replace(
                "/receipt", "/internal/v1/life-read/actors"
            )
            assertion["method"] = "POST"
            assertion["request_json"] = {"schema_version": 1}
            started = time.monotonic()
            with self.assertRaisesRegex(RecoveryError, "lifecycle_timeout"):
                check(self.root, assertion, Deadline(0.15))
            self.assertLess(time.monotonic() - started, 0.4)

    def test_untrusted_certificate_is_rejected_and_socket_closed(self):
        untrusted = ssl.create_default_context()
        with self.server(
            lambda c, _: c.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
        ) as assertion:
            with patch(
                "ops.recovery.drill_http.ssl.create_default_context",
                return_value=untrusted,
            ):
                with self.assertRaisesRegex(
                    RecoveryError, "^drill_tls_or_transport_failed$"
                ):
                    check(self.root, assertion, Deadline(2))

    def test_body_and_chunk_size_trickle_are_bounded(self):
        for prefix, data in (
            (b"HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n", b"a" * 200),
            (
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
                b"1;" + b"a" * 200,
            ),
        ):
            with self.subTest(prefix=prefix):
                self.bounded(self.trickle(prefix, data))

    def test_cancellation_in_tls_status_headers_and_body_closes_socket(self):
        phases = [
            (None, True),
            (self.trickle(b"", b"HTTP/1.1 200 OK\r\n"), False),
            (self.trickle(b"HTTP/1.1 200 OK\r\nX: ", b"a" * 200), False),
            (
                self.trickle(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n", b"a" * 200
                ),
                False,
            ),
        ]
        for sender, slow_tls in phases:
            with self.subTest(slow_tls=slow_tls):
                self.bounded(sender, slow_tls=slow_tls, cancelled=True)

    def test_header_line_aggregate_and_body_caps(self):
        responses = [
            (b"HTTP/1.1 200 " + b"x" * 1024, "drill_header_limit"),
            (b"HTTP/1.1 200 OK\r\nX: " + b"x" * 8192, "drill_header_limit"),
            (
                b"HTTP/1.1 200 OK\r\n" + (b"X: " + b"x" * 1000 + b"\r\n") * 40,
                "drill_header_limit",
            ),
            (
                b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * (256 * 1024 + 1),
                "drill_response_limit",
            ),
            (
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n40001\r\n",
                "drill_response_limit",
            ),
        ]
        for payload, reason in responses:
            with (
                self.subTest(reason=reason),
                self.server(lambda c, _: c.sendall(payload)) as assertion,
            ):
                with self.assertRaisesRegex(RecoveryError, reason):
                    check(self.root, assertion, Deadline(2))

    def test_chunked_and_connection_close_json_success(self):
        body = b'{"status":"unknown"}'
        responses = [
            b"HTTP/1.1 200 OK\r\n\r\n" + body,
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + f"{len(body):x}\r\n".encode()
            + body
            + b"\r\n0\r\nTrailer: ok\r\n\r\n",
        ]
        for payload in responses:
            with self.server(lambda c, _: c.sendall(payload)) as assertion:
                self.assertEqual(
                    check(self.root, assertion, Deadline(2))["status"], "passed"
                )

    def test_duplicate_lengths_and_truncated_body_rejected(self):
        cases = [
            (
                b"Content-Length: 10\r\nContent-Length: 10\r\n\r\n",
                "drill_http_framing_invalid",
            ),
            (
                b"Content-Length: 10\r\nTransfer-Encoding: chunked\r\n\r\n",
                "drill_http_framing_invalid",
            ),
            (b"Content-Length: 10\r\n\r\nshort", "drill_http_truncated"),
        ]
        for framing, reason in cases:
            with self.server(
                lambda c, _: c.sendall(b"HTTP/1.1 200 OK\r\n" + framing)
            ) as assertion:
                with self.assertRaisesRegex(RecoveryError, reason):
                    check(self.root, assertion, Deadline(2))
