"""Actual TLS readiness with a real local ledger and an explicit Loki substitute."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import threading
import time
from unittest.mock import patch

from test_guard_tls import TransportFixture
from alerts import MONITOR_STALE_SECONDS
from configs import ALERTS
from monitor import Ledger
from query import QueryError
from test_ledger_storage import LEGACY_SCHEMA


class ReadinessTests(TransportFixture):
    def setUp(self):
        super().setUp()
        repository = Path(__file__).resolve().parents[3]
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.state.settings.update({
            "snapshot": str(repository / "deploy/observability/vocabulary.json"),
            "contract": os.environ.get("DEP_B_CONTRACT", str(repository / "contracts/diagnostics/v1")),
            "ledger": str(self.root / "ledger.sqlite"),
            "log_roots": {"platform": str(self.logs)},
            "log_budgets": {"platform": 1024**3},
            "interval_seconds": 0.02,
        })

    def until(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("isolated integrity monitor did not reach expected state")

    def start_monitor(self):
        worker = threading.Thread(target=self.state.run_monitor, daemon=True)
        worker.start()

        def close():
            self.state.stop.set()
            worker.join(3)
            self.assertFalse(worker.is_alive())

        self.addCleanup(close)
        return worker

    def ready_status(self):
        return self.client("metrics").request("/ready")[0]

    def test_ready_requires_completed_real_monitor_cycle_and_loki(self):
        self.assertEqual(self.ready_status(), 503)
        self.start_monitor()
        self.until(self.state.monitor_ready)
        self.assertEqual(self.ready_status(), 200)
        self.assertEqual(self.backend.observed[-1][0], "/ready")
        self.assertEqual(self.state.metrics["landed_events"], 0)

    def test_metrics_values_cannot_fake_an_unstarted_monitor(self):
        self.state.update(monitor_success=1, monitor_last_success_timestamp=time.time())
        self.assertEqual(self.ready_status(), 503)

    def test_legacy_migration_failure_is_not_ready_or_destructive(self):
        path = self.root / "ledger.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.executescript(LEGACY_SCHEMA)
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (
                "00000000-0000-4000-8000-000000000001", "platform",
                "00000000-0000-4000-8000-000000000002", 1, "a" * 62, 1, 0, 0,
            ))
            db.commit()
        worker = self.start_monitor()
        self.until(lambda: not worker.is_alive())
        self.assertEqual(self.state.metrics["monitor_success"], 0)
        self.assertEqual(self.ready_status(), 503)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT typeof(hash),hash FROM events").fetchone(), ("text", "a" * 62))

    def test_query_failure_recovers_without_fake_loki_readiness(self):
        self.start_monitor()
        self.until(self.state.monitor_ready)
        self.assertEqual(self.ready_status(), 200)
        with patch.object(self.state.backend, "range", side_effect=QueryError("synthetic unavailable")):
            self.until(lambda: self.state.metrics["monitor_success"] == 0)
            self.assertEqual(self.ready_status(), 503)
        self.until(self.state.monitor_ready)
        self.assertEqual(self.ready_status(), 200)

    def test_metrics_failure_does_not_refresh_success_timestamp(self):
        self.start_monitor()
        self.until(self.state.monitor_ready)
        with patch.object(Ledger, "metrics", side_effect=sqlite3.OperationalError("synthetic IO")):
            self.until(lambda: self.state.metrics["monitor_success"] == 0)
            stamp = self.state.metrics["monitor_last_success_timestamp"]
            time.sleep(0.06)
            self.assertEqual(self.state.metrics["monitor_last_success_timestamp"], stamp)
            self.assertEqual(self.ready_status(), 503)
        self.until(self.state.monitor_ready)
        self.assertEqual(self.ready_status(), 200)

    def test_freshness_reuses_existing_alert_window_and_rejects_future(self):
        self.start_monitor()
        self.until(self.state.monitor_ready)
        stamp = self.state.metrics["monitor_last_success_timestamp"]
        self.assertEqual(ALERTS["IntegrityMonitorStale"][0], f"time()-tianshu_obs_monitor_last_success_timestamp > bool {MONITOR_STALE_SECONDS}")
        self.assertFalse(self.state.monitor_ready(now=stamp + MONITOR_STALE_SECONDS + 1))
        self.assertFalse(self.state.monitor_ready(now=stamp - 1))

    def test_monitor_exit_revokes_readiness_even_when_loki_stays_available(self):
        worker = self.start_monitor()
        self.until(self.state.monitor_ready)
        self.assertEqual(self.ready_status(), 200)
        self.state.stop.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.state.metrics["monitor_success"], 0)
        self.assertFalse(self.state.monitor_running.is_set())
        self.assertEqual(self.ready_status(), 503)

    def test_local_monitor_does_not_replace_actual_loki_ready(self):
        self.start_monitor()
        self.until(self.state.monitor_ready)
        original = self.state.backend.request

        def request(path, *args, **kwargs):
            if path == "/ready":
                return 503, b"", "application/json"
            return original(path, *args, **kwargs)

        with patch.object(self.state.backend, "request", request):
            self.assertEqual(self.ready_status(), 503)
        self.assertEqual(self.ready_status(), 200)
