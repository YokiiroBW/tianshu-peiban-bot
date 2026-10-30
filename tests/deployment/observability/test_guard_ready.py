"""Live TLS readiness follows local monitoring and the Loki readiness endpoint."""

import threading
import time
from unittest.mock import patch

from alerts import MONITOR_STALE_SECONDS
from configs import ALERTS
from query import QueryError
from test_guard_tls import TransportFixture


class ReadinessTests(TransportFixture):
    def setUp(self):
        super().setUp()
        logs = self.root / "logs"
        logs.mkdir()
        self.state.settings.update({"log_roots": {"platform": str(logs)},
                                    "log_budgets": {"platform": 1024**3},
                                    "interval_seconds": 0.02})

    def until(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("local monitor did not reach expected state")

    def start_monitor(self):
        worker = threading.Thread(target=self.state.run_monitor, daemon=True)
        worker.start()
        def close():
            self.state.stop.set()
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.addCleanup(close)

    def ready_status(self):
        return self.client("metrics").request("/ready")[0]

    def test_ready_requires_running_fresh_monitor_and_loki(self):
        self.assertEqual(self.ready_status(), 503)
        self.start_monitor()
        self.until(self.state.monitor_ready)
        self.assertEqual(self.ready_status(), 200)
        self.assertNotIn("pending_events", self.state.metrics)
        self.assertEqual(self.backend.observed[-1][0], "/ready")

    def test_metrics_values_cannot_fake_an_unstarted_monitor(self):
        self.state.update(monitor_success=1, monitor_last_success_timestamp=time.time())
        self.assertEqual(self.ready_status(), 503)

    def test_real_local_capacity_failure_and_recovery_affect_readiness(self):
        self.start_monitor()
        self.until(self.state.monitor_ready)
        with patch("guard.capacities", side_effect=OSError("private canary")):
            self.until(lambda: self.state.metrics["monitor_success"] == 0)
            self.assertEqual(self.ready_status(), 503)
            self.assertEqual(self.state.metrics["monitor_failure_stage_capacity_current"], 1)
            self.assertNotIn(b"private canary", self.state.render_metrics())
        self.until(self.state.monitor_ready)
        self.assertEqual(self.ready_status(), 200)

    def test_backend_unavailable_never_reports_ready(self):
        self.start_monitor()
        self.until(self.state.monitor_ready)
        with patch.object(self.state.backend, "request", side_effect=QueryError("backend_unavailable")):
            self.assertEqual(self.ready_status(), 503)
        self.assertEqual(self.ready_status(), 200)

    def test_freshness_and_safe_failure_projection(self):
        self.state.monitor_running.set()
        self.state.update(monitor_success=1, monitor_last_success_timestamp=1000)
        self.assertTrue(self.state.monitor_ready(now=1000 + MONITOR_STALE_SECONDS))
        self.assertFalse(self.state.monitor_ready(now=1001 + MONITOR_STALE_SECONDS))
        self.assertFalse(self.state.monitor_ready(now=999))
        self.assertIn(str(MONITOR_STALE_SECONDS), ALERTS["PipelineMonitorStale"][0])
        self.state.monitor_failed("capacity", QueryError("SYNTHETIC_PRIVATE_CANARY"))
        self.assertEqual(self.state.metrics["monitor_failure_reason_unexpected_current"], 1)
        self.assertNotIn(b"SYNTHETIC_PRIVATE", self.state.render_metrics())
