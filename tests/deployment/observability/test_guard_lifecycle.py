"""Real TLS shutdown and bounded ownership tests; Linux PID1 is a separate run."""

import json
from contextlib import closing, redirect_stdout
import io
import os
from pathlib import Path
import signal
import sqlite3
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from helpers import PACKAGE, certificates, emit, event
from guard import Server, State
from guard_lifecycle import SignalStop, run
from query import LokiClient, QueryError


class Backend:
    def __init__(self):
        self.cancelled = threading.Event()
        self.entered = threading.Event()
        self.users = 0
        self.closed = False
        self.closed_with_users = False

    def request(self, *args, **kwargs):
        self.users += 1
        self.entered.set()
        try:
            self.cancelled.wait(5)
            raise QueryError("backend_unavailable")
        finally:
            self.users -= 1

    def range(self, *args, **kwargs):
        return self.request()

    def cancel(self):
        self.cancelled.set()

    def close(self):
        self.closed_with_users = self.users > 0
        self.closed = True


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        certificates(self.root)
        tokens = {}
        for role, char in (("writer", "w"), ("query", "q"), ("metrics", "m")):
            path = self.root / role
            path.write_text(char * 40)
            tokens[role] = str(path)
        self.backend = Backend()
        self.state = State(
            {
                "tokens": tokens,
                "storage_roots": {"guard": str(self.root)},
                "reserve_bytes": 0,
            },
            self.backend,
        )
        self.monitor_done = threading.Event()

        def monitor():
            self.state.stop.wait()
            self.monitor_done.set()

        self.state.run_monitor = monitor
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.root / "guard.pem", self.root / "guard.key")
        self.server = Server(("127.0.0.1", 0), self.state, context)
        self.addCleanup(self.server.server_close)
        self.stop = threading.Event()
        self.results = []

    def start(self, budget=1):
        worker = threading.Thread(
            target=lambda: self.results.append(
                run(self.server, self.state, self.backend, self.stop.is_set, budget)
            )
        )
        worker.start()
        self.addCleanup(lambda: (self.stop.set(), worker.join(3)))
        return worker

    def test_slow_tls_cancel_joins_requests_before_backend_close(self):
        worker = self.start()
        raw = socket.create_connection(self.server.server_address, timeout=1)
        self.addCleanup(raw.close)
        deadline = time.monotonic() + 1
        while not self.server.active and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(self.server.active)
        started = time.monotonic()
        self.stop.set()
        worker.join(1.5)
        self.assertEqual(self.results, [0])
        self.assertLess(time.monotonic() - started, 1.1)
        self.assertTrue(self.monitor_done.is_set())
        self.assertTrue(self.backend.closed)
        self.assertFalse(self.backend.closed_with_users)
        self.assertFalse(self.server.active)
        self.assertEqual(self.server.slots._value, 8)

    def test_slow_backend_cancellation_finishes_http_before_close(self):
        worker = self.start()
        client = LokiClient(
            f"https://127.0.0.1:{self.server.server_port}",
            self.root / "ca.pem",
            "q" * 40,
        )
        self.addCleanup(client.close)
        received = []

        def query():
            try:
                received.append(client.request("/loki/api/v1/labels")[0])
            except QueryError:
                received.append("cancelled")

        caller = threading.Thread(target=query)
        caller.start()
        self.addCleanup(lambda: caller.join(3))
        self.assertTrue(self.backend.entered.wait(1))
        self.stop.set()
        worker.join(1.5)
        caller.join(1)
        self.assertEqual(self.results, [0])
        self.assertTrue(received)
        self.assertNotIn(200, received)
        self.assertFalse(self.backend.closed_with_users)
        self.assertTrue(self.backend.closed)

    def test_stuck_monitor_returns_nonzero_without_closing_owned_backend(self):
        release = threading.Event()
        entered = threading.Event()

        def monitor():
            entered.set()
            release.wait(3)

        self.state.run_monitor = monitor
        self.addCleanup(release.set)
        worker = self.start(budget=0.1)
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.stop.set()
        worker.join(0.8)
        self.assertEqual(self.results, [2])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(self.backend.closed)
        release.set()

    def test_stuck_http_returns_nonzero_without_backend_close(self):
        self.backend.cancel = lambda: None
        worker = self.start(budget=0.1)
        client = LokiClient(
            f"https://127.0.0.1:{self.server.server_port}",
            self.root / "ca.pem",
            "q" * 40,
        )
        self.addCleanup(client.close)

        def query():
            try:
                client.request("/loki/api/v1/labels")
            except QueryError:
                pass

        caller = threading.Thread(target=query)
        caller.start()
        self.assertTrue(self.backend.entered.wait(1))
        self.stop.set()
        worker.join(0.8)
        self.assertEqual(self.results, [2])
        self.assertFalse(self.backend.closed)
        self.backend.cancelled.set()
        caller.join(1)
        self.assertTrue(self.server.join_requests(1))

    @unittest.skipUnless(
        os.environ.get("DEP_B_CONTRACT"), "authoritative diagnostics contract required"
    )
    def test_real_monitor_closes_durable_ledger_before_backend(self):
        roots = {}
        for service in ("platform", "companion", "memory", "gateway"):
            directory = self.root / (service + "-logs")
            directory.mkdir()
            emit(
                directory / "synthetic.jsonl", [event(i, service) for i in range(1, 11)]
            )
            roots[service] = str(directory)
        self.state.settings.update(
            {
                "snapshot": str(PACKAGE / "vocabulary.json"),
                "contract": os.environ["DEP_B_CONTRACT"],
                "ledger": str(self.root / "ledger.sqlite"),
                "log_roots": roots,
                "log_budgets": {p: 1024**3 for p in roots},
                "interval_seconds": 1,
            }
        )
        self.state.run_monitor = State.run_monitor.__get__(self.state, State)
        worker = self.start(budget=1)
        self.assertTrue(self.backend.entered.wait(1))
        self.stop.set()
        worker.join(1.5)
        self.assertEqual(self.results, [0])
        self.assertTrue(self.backend.closed)
        self.assertFalse(self.backend.closed_with_users)
        with closing(sqlite3.connect(self.root / "ledger.sqlite")) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM events").fetchone()[0], 40
            )
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
        with closing(sqlite3.connect(self.root / "alerts.sqlite")) as connection:
            self.assertGreater(
                connection.execute("SELECT count(*) FROM history").fetchone()[0], 0
            )

    def test_monitor_startup_failure_cannot_report_normal_stop(self):
        self.state.run_monitor = lambda: None
        worker = self.start()
        worker.join(1)
        self.assertEqual(self.results, [2])
        self.assertTrue(self.backend.closed)

    def test_second_thread_start_failure_joins_first(self):
        original = threading.Thread.start

        def start(thread):
            if thread.name == "obs-listener":
                raise RuntimeError("synthetic start failure")
            return original(thread)

        with patch.object(threading.Thread, "start", start):
            result = run(self.server, self.state, self.backend, lambda: False, 1)
        self.assertEqual(result, 2)
        self.assertTrue(self.monitor_done.is_set())
        self.assertTrue(self.backend.closed)

    def test_main_listener_construction_failure_closes_backend_and_redacts(self):
        import guard

        settings = {
            **self.state.settings,
            "loki_url": "https://127.0.0.1:1",
            "ca": str(self.root / "ca.pem"),
            "client_cert": str(self.root / "client.pem"),
            "client_key": str(self.root / "client.key"),
            "server_cert": str(self.root / "guard.pem"),
            "server_key": str(self.root / "guard.key"),
        }
        path = self.root / "settings.json"
        path.write_text(json.dumps(settings))
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["guard.py", "--settings", str(path)]),
            patch.object(guard, "LokiClient", return_value=self.backend),
            patch.object(guard, "Server", side_effect=OSError("PRIVATE_SENTINEL")),
            redirect_stdout(output),
        ):
            result = guard.main()
        self.assertEqual(result, 2)
        self.assertTrue(self.backend.closed)
        self.assertNotIn("PRIVATE_SENTINEL", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "startup_failed")

    def test_repeated_signal_only_sets_flag_and_restores_handlers(self):
        previous = signal.getsignal(signal.SIGINT)
        with SignalStop() as stop:
            callback = signal.getsignal(signal.SIGINT)
            for _ in range(10):
                callback(signal.SIGINT, None)
            self.assertTrue(stop.requested)
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_real_process_sigint_gracefully_stops_lifecycle(self):
        script = r"""
import json, signal, sys, threading, time
sys.path.insert(0,sys.argv[1])
from guard_lifecycle import SignalStop, run
class State:
    stop=threading.Event()
    def run_monitor(self): self.stop.wait()
class Server:
    timeout=0.01
    def handle_request(self): time.sleep(0.01)
    def cancel_requests(self): pass
    def join_requests(self, timeout): return True
    def server_close(self): pass
class Backend:
    def cancel(self): pass
    def close(self): pass
with SignalStop() as stop:
    print('ready',flush=True)
    threading.Timer(.2,lambda: signal.raise_signal(signal.SIGINT)).start()
    result=run(Server(),State(),Backend(),lambda:stop.requested,1)
print(json.dumps({'result':result}))
sys.exit(result)
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(PACKAGE)], capture_output=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1]), {"result": 0})

    @unittest.skipUnless(
        sys.platform == "linux", "external SIGTERM requires POSIX process"
    )
    def test_external_sigterm_process(self):
        script = "import signal,sys,time;sys.path.insert(0,sys.argv[1]);from guard_lifecycle import SignalStop\nwith SignalStop() as s:\n print('ready',flush=True)\n while not s.requested: time.sleep(.01)\nprint('stopped',flush=True)"
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", script, str(PACKAGE)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(process.stdout.readline().strip(), b"ready")
        os.kill(process.pid, signal.SIGTERM)
        output, errors = process.communicate(timeout=2)
        self.assertEqual(process.returncode, 0, errors)
        self.assertEqual(output.strip(), b"stopped")


if __name__ == "__main__":
    unittest.main()
