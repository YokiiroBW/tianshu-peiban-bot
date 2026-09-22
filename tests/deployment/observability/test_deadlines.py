"""Real loopback adversarial TLS; 10s production deadline, 1.5s test tolerance."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import patch

from helpers import PACKAGE, ROOT
import test_guard_tls as fixtures
from query import LokiClient, QueryError
from transport import Deadline
import transport

OBSERVATIONS = []


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TransportTests("test_noncanonical_route_refused")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.stop = threading.Event()
        self.addCleanup(self.stop.set)

    def observed(self, case, seconds, **facts):
        OBSERVATIONS.append(
            {"case": case, "elapsed_seconds": round(seconds, 3), **facts}
        )

    def elapsed_bound(self, started, case, **facts):
        elapsed = time.monotonic() - started
        self.observed(
            case,
            elapsed,
            configured_deadline_seconds=10,
            scheduling_tolerance_seconds=1.5,
            **facts,
        )
        self.assertGreaterEqual(elapsed, 9.5)
        self.assertLess(elapsed, 11.5)

    def available(self):
        deadline = time.monotonic() + 0.5
        while self.fixture.guard.slots._value != 8 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.fixture.guard.slots._value, 8)
        self.assertEqual(self.fixture.client("query").request("/ready")[0], 200)

    def connect(self, tls=True):
        raw = socket.create_connection(
            ("127.0.0.1", self.fixture.guard.server_port), timeout=2
        )
        if tls:
            context = ssl.create_default_context(
                cafile=str(self.fixture.root / "ca.pem")
            )
            raw = context.wrap_socket(raw, server_hostname="127.0.0.1")
        raw.settimeout(0.05)
        self.addCleanup(raw.close)
        return raw

    def drip(self, prefix, fragments):
        started = time.monotonic()
        connection = self.connect()
        connection.sendall(prefix)
        received = b""
        while time.monotonic() - started < 14:
            elapsed = time.monotonic() - started
            while fragments and elapsed >= fragments[0][0]:
                _, payload = fragments.pop(0)
                connection.sendall(payload)
            try:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                received += chunk
            except socket.timeout:
                continue
            except (OSError, ssl.SSLError):
                break
        self.assertNotIn(b"204", received)
        self.assertNotIn(b"200", received)
        return started

    def test_slow_unauthenticated_headers(self):
        started = self.drip(b"G", [(6, b"E"), (12, b"T")])
        self.elapsed_bound(
            started, "slow_unauthenticated_headers", sent_seconds=[0, 6, 12]
        )
        self.available()

    def test_slow_authenticated_body(self):
        prefix = (
            "POST /loki/api/v1/push HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer "
            + self.fixture.tokens["writer"]
            + "\r\nContent-Length: 3\r\n\r\nx"
        ).encode()
        started = self.drip(prefix, [(6, b"x"), (12, b"x")])
        self.elapsed_bound(
            started,
            "slow_authenticated_body",
            sent_seconds=[0, 6, 12],
            backend_pushes=len(self.fixture.backend.observed),
        )
        self.assertEqual(self.fixture.backend.observed, [])
        self.available()

    def test_guard_stalled_tls_handshake(self):
        started = time.monotonic()
        connection = self.connect(tls=False)
        connection.settimeout(14)
        self.assertEqual(connection.recv(1), b"")
        self.elapsed_bound(started, "guard_stalled_tls_handshake")
        self.available()

    def slow_response(self, handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "3")
        handler.end_headers()
        for index in range(3):
            if index and self.stop.wait(6):
                return
            try:
                handler.wfile.write(b"x")
                handler.wfile.flush()
            except OSError:
                return

    def test_query_slow_response(self):
        test = self
        with patch.object(
            fixtures.BackendHandler,
            "do_GET",
            lambda handler: test.slow_response(handler),
        ):
            started = time.monotonic()
            with self.assertRaisesRegex(QueryError, "query_deadline_exceeded"):
                self.fixture.state.backend.request("/slow")
            self.elapsed_bound(started, "query_slow_response", sent_seconds=[0, 6, 12])
        self.assertEqual(len(self.fixture.state.backend.active), 0)
        self.available()

    def test_query_slow_response_headers(self):
        stop = self.stop

        def headers(handler):
            handler.connection.sendall(b"HTTP/1.0 200 OK\r\nX-Probe: ")
            if not stop.wait(6):
                try:
                    handler.connection.sendall(b"x")
                    stop.wait(6)
                except OSError:
                    pass

        with patch.object(fixtures.BackendHandler, "do_GET", headers):
            started = time.monotonic()
            with self.assertRaisesRegex(QueryError, "query_deadline_exceeded"):
                self.fixture.state.backend.request("/slow-headers")
            self.elapsed_bound(
                started,
                "query_slow_response_headers",
                completed_headers=False,
                sent_seconds=[0, 6],
            )
        self.assertEqual(len(self.fixture.state.backend.active), 0)
        self.available()

    def test_query_stalled_tls_handshake(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        stop = self.stop

        def accept_without_tls():
            connection, _ = listener.accept()
            with connection:
                stop.wait(14)

        thread = threading.Thread(target=accept_without_tls, daemon=True)
        thread.start()
        client = LokiClient(
            f"https://127.0.0.1:{listener.getsockname()[1]}",
            self.fixture.root / "ca.pem",
        )
        started = time.monotonic()
        with self.assertRaisesRegex(QueryError, "query_deadline_exceeded"):
            client.request("/ready")
        self.elapsed_bound(started, "query_stalled_tls_handshake")
        self.assertEqual(len(client.active), 0)
        stop.set()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())

    def test_range_pages_share_deadline(self):
        calls = []
        stop = self.stop

        def page(handler):
            calls.append(handler.path)
            if stop.wait(6):
                return
            raw = json.dumps(
                {
                    "status": "success",
                    "data": {
                        "resultType": "streams",
                        "result": [{"values": [["1", "a"], ["2", "b"]]}],
                    },
                }
            ).encode()
            try:
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(raw)))
                handler.end_headers()
                handler.wfile.write(raw)
            except OSError:
                pass

        with patch.object(fixtures.BackendHandler, "do_GET", page):
            started = time.monotonic()
            with self.assertRaisesRegex(QueryError, "query_deadline_exceeded"):
                self.fixture.state.backend.range("x", 1, 4, limit=2)
            self.elapsed_bound(
                started,
                "range_pages_share_deadline",
                requests=len(calls),
                response_delay_each=6,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.fixture.state.backend.active), 0)
        self.available()

    def test_guard_body_and_backend_share_deadline(self):
        stop = self.stop

        def delayed(handler):
            handler.rfile.read(int(handler.headers["Content-Length"]))
            if not stop.wait(6):
                try:
                    handler.send_response(204)
                    handler.end_headers()
                except OSError:
                    pass

        prefix = (
            "POST /loki/api/v1/push HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer "
            + self.fixture.tokens["writer"]
            + "\r\nContent-Length: 2\r\n\r\nx"
        ).encode()
        with patch.object(fixtures.BackendHandler, "do_POST", delayed):
            started = self.drip(prefix, [(6, b"x")])
            self.elapsed_bound(
                started,
                "guard_body_and_backend_share_deadline",
                body_complete_seconds=6,
                backend_delay_seconds=6,
            )
        self.available()

    def test_guard_response_write_has_deadline(self):
        def large(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(8 * 1024**2))
            handler.end_headers()
            try:
                handler.wfile.write(b"x" * (8 * 1024**2))
            except OSError:
                pass

        with patch.object(fixtures.BackendHandler, "do_GET", large):
            started = time.monotonic()
            connection = self.connect()
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            connection.sendall(
                (
                    "GET /loki/api/v1/query HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer "
                    + self.fixture.tokens["query"]
                    + "\r\n\r\n"
                ).encode()
            )
            deadline = time.monotonic() + 14
            while self.fixture.guard.slots._value != 8 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.elapsed_bound(
                started,
                "guard_slow_reader_response_write",
                response_bytes=8 * 1024**2,
                received_by_client=0,
            )
        connection.close()
        self.available()

    def test_query_close_cancels_active_read(self):
        entered = threading.Event()
        stop = self.stop

        def stalled(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", "3")
            handler.end_headers()
            entered.set()
            stop.wait(3)

        with (
            patch.object(fixtures.BackendHandler, "do_GET", stalled),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(self.fixture.state.backend.request, "/stall")
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            self.fixture.state.backend.close()
            with self.assertRaises(QueryError):
                future.result(timeout=0.5)
            elapsed = time.monotonic() - started
            self.observed(
                "query_close_cancels_active_read", elapsed, tolerance_seconds=0.5
            )
            self.assertLess(elapsed, 0.5)
            self.assertEqual(len(self.fixture.state.backend.active), 0)

    def test_guard_close_releases_handshake_slot(self):
        connection = self.connect(tls=False)
        deadline = time.monotonic() + 1
        while self.fixture.guard.slots._value == 8 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.fixture.guard.shutdown()
        started = time.monotonic()
        self.fixture.guard.server_close()
        connection.settimeout(0.5)
        self.assertEqual(connection.recv(1), b"")
        deadline = time.monotonic() + 0.5
        while self.fixture.guard.slots._value != 8 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.fixture.guard.slots._value, 8)
        self.observed(
            "guard_close_releases_handshake_slot",
            time.monotonic() - started,
            tolerance_seconds=0.5,
        )

    def test_dns_worker_is_bounded_and_caller_expires(self):
        stop = self.stop

        def stuck(*args, **kwargs):
            stop.wait(3)
            raise OSError("synthetic_dns_failure")

        with patch.object(transport.socket, "getaddrinfo", stuck):
            client = LokiClient(
                "https://deadline.example.invalid", self.fixture.root / "ca.pem"
            )
            started = time.monotonic()
            with self.assertRaisesRegex(QueryError, "query_deadline_exceeded"):
                client.request("/ready", deadline=Deadline(0.2))
            elapsed = time.monotonic() - started
            count = sum(t.name == "obs-dns-resolver" for t in threading.enumerate())
            self.assertLess(elapsed, 0.7)
            self.assertEqual(count, 1)
            self.observed(
                "bounded_dns_worker",
                elapsed,
                parent_budget_seconds=0.2,
                tolerance_seconds=0.5,
                resolver_workers=count,
                os_dns_force_cancelled=False,
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--case",
        action="append",
        help="Only newly affected cases; never present as the whole suite",
    )
    args = parser.parse_args()
    sources = [*PACKAGE.glob("*.py"), Path(__file__)]
    hashes = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sources
    }
    suite = (
        unittest.TestSuite(DeadlineTests(name) for name in args.case)
        if args.case
        else unittest.defaultTestLoader.loadTestsFromTestCase(DeadlineTests)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    unchanged = all(
        hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
        for path, digest in hashes.items()
    )
    report = {
        "kind": "dep-b-total-deadline-regressions",
        "status": "passed" if result.wasSuccessful() and unchanged else "failed",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skips": len(result.skipped),
        "implementation_sha256": hashes,
        "source_unchanged_during_run": unchanged,
        "network": "real_loopback_TLS",
        "backend": "explicit_synthetic_substitute",
        "observations": OBSERVATIONS,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
