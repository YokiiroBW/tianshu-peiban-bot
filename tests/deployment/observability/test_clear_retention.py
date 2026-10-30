import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from clear_retention import ClearControl
from logql_filter import query_path


class Backend:
    def __init__(self, status=204, callback=None):
        self.status = status
        self.callback = callback
        self.calls = []

    def request(self, path, method, body=None):
        self.calls.append((path, method, body))
        if self.callback:
            self.callback()
        return self.status, b"", "application/json"


class ClearRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = threading.Lock()
        self.control = ClearControl(self.root)

    def test_clear_persists_and_constrains_every_log_selector(self):
        self.assertEqual(self.control.filter(), "")
        self.control.clear(now=1234.123)
        control = ClearControl(self.root)
        self.assertEqual(control.cutoff_ms, 1234123)
        self.assertIn("__ts_clear_event > 1234123", control.filter())
        self.assertIn("__ts_clear_collect > 1234123", control.filter())
        original = '/loki/api/v1/query?query=sum(count_over_time(%7Bstack%3D%22tianshu%22%7D%5B1m%5D))'
        rewritten = parse_qs(urlsplit(query_path(original, control.filter())).query)["query"][0]
        self.assertIn(control.filter(), rewritten)
        self.assertTrue(rewritten.endswith('[1m]))'))

    def test_failed_backend_does_not_forget_clear_and_retries(self):
        self.control.clear(now=1234.123)
        with self.assertRaises(RuntimeError):
            self.control.submit(Backend(503), self.lock, now=1300)
        self.assertEqual(ClearControl(self.root).last_delete, 0)
        backend = Backend()
        self.control.submit(backend, self.lock, now=1300)
        self.assertEqual(ClearControl(self.root).last_delete, 1300)
        self.control.submit(backend, self.lock, now=1400)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][1:], ("POST", b""))
        params = parse_qs(urlsplit(backend.calls[0][0]).query)
        self.assertEqual(params["query"], ['{stack="tianshu"}'])
        self.assertTrue(params["end"][0].endswith('00:20:34.122000+00:00'))

    def test_new_clear_during_old_submission_stays_pending(self):
        self.control.clear(now=1234)
        backend = Backend(callback=lambda: self.control.clear(now=1235))
        self.control.submit(backend, self.lock, now=1300)
        self.assertEqual(self.control.cutoff_ms, 1235000)
        self.assertEqual(self.control.last_delete, 0)

    def test_invalid_persisted_state_is_not_silently_reset(self):
        (self.root / "clear-control.json").write_text(json.dumps({"version": 1, "cutoff_ms": "bad"}))
        with self.assertRaises(ValueError):
            ClearControl(self.root)
