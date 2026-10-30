import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

from test_guard_tls import TransportFixture


class GuardWithoutLedgerTests(TransportFixture):
    def setUp(self):
        super().setUp()
        logs = self.root / "logs"
        logs.mkdir()
        self.legacy = self.root / "integrity.sqlite"
        self.legacy.write_bytes(b"not a database; synthetic legacy data")
        self.state.settings.update({
            "ledger": str(self.legacy), "log_roots": {"platform": str(logs)},
            "log_budgets": {"platform": 1024**3}, "interval_seconds": 0.02,
        })

    def test_no_ledger_needed_for_ready_query_clear_and_restart(self):
        worker = threading.Thread(target=self.state.run_monitor, daemon=True)
        worker.start()
        self.addCleanup(lambda: (self.state.stop.set(), worker.join(3)))
        deadline = time.monotonic() + 3
        while not self.state.monitor_ready() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.state.monitor_ready())
        self.assertEqual(self.client("metrics").request("/ready")[0], 200)
        self.assertEqual(self.legacy.read_bytes(), b"not a database; synthetic legacy data")
        self.assertNotIn("pending_events", self.state.metrics)
        self.assertEqual(self.client("query").request("/retention/clear", "POST", body=b"")[0], 401)
        status, body, _ = self.client("writer").request("/retention/clear", "POST", body=b"")
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)["status"], "accepted")
        path = '/loki/api/v1/query?query=%7Bstack%3D%22tianshu%22%7D'
        self.assertEqual(self.client("query").request(path)[0], 200)
        forwarded = [p for p, _ in self.backend.observed if p.startswith('/loki/api/v1/query?')][-1]
        query = parse_qs(urlsplit(forwarded).query)["query"][0]
        self.assertIn("__ts_clear_event >", query)
        self.assertIn("__ts_clear_collect >", query)
        from guard import State
        self.assertGreater(State(self.state.settings, self.state.backend).clear_control.cutoff_ms, 0)
