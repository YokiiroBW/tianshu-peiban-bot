"""The business host is ready only while its real module snapshot is fresh."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from deploy.single_container.health import (
    check_module_state,
    check_state,
    probe,
    validate_probe,
)
from deploy.single_container.runtime import write_state


class HostHealthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "host.json"
        self.state = {
            "version": 1,
            "pid": os.getpid(),
            "updated_at": time.time(),
            "status": "ready",
            "modules": {
                name: {"state": "ready", "health": {"ok": True, "code": "ready"}}
                for name in ("platform", "companion", "memory", "knowledge", "gateway")
            },
        }

    def write(self, state=None):
        write_state(self.path, self.state if state is None else state)

    def test_probe_config_has_bounded_age_and_requires_absolute_file_path(self):
        raw = {"type": "module_state", "path": str(self.path)}
        check = validate_probe(raw)
        self.assertEqual(check["max_age_seconds"], 10)
        self.assertEqual(validate_probe({**raw, "max_age_seconds": 0.25})["max_age_seconds"], 0.25)
        self.assertEqual(raw, {"type": "module_state", "path": str(self.path)})
        for changes in (
            {"path": "relative.json"},
            {"path": None},
            {"max_age_seconds": 0},
            {"max_age_seconds": True},
            {"max_age_seconds": float("nan")},
            {"max_age_seconds": float("inf")},
            {"max_age_seconds": 3601},
            {"url": "http://localhost/ready"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_probe({**raw, **changes})

    def test_ready_snapshot_uses_no_network_and_does_not_modify_file(self):
        self.write()
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        check = validate_probe({"type": "module_state", "path": str(self.path)})
        with (
            patch("deploy.single_container.health.socket.create_connection", side_effect=AssertionError("network")),
            patch("deploy.single_container.health.urllib.request.build_opener", side_effect=AssertionError("network")),
        ):
            self.assertEqual(probe(check, {}), {"ok": True, "code": "module_ready"})
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)

    def test_stale_future_nonfinite_or_invalid_version_cannot_claim_ready(self):
        now = self.state["updated_at"]
        self.write()
        self.assertTrue(check_module_state(self.path, now=now + 10))
        self.assertFalse(check_module_state(self.path, now=now + 10.01))
        self.assertFalse(check_module_state(self.path, now=now - 0.01))
        for field, value in (
            ("version", True), ("version", 2), ("pid", True), ("pid", 0),
            ("updated_at", True), ("updated_at", "now"),
            ("updated_at", float("nan")), ("updated_at", float("inf")),
            ("status", "starting"), ("status", "degraded"), ("status", "stopped"),
        ):
            with self.subTest(field=field, value=value):
                # Deliberately bypass the host writer, which rejects NaN/Infinity.
                self.path.write_text(json.dumps({**self.state, field: value}), encoding="utf-8")
                self.assertFalse(check_module_state(self.path, now=now))

    def test_busy_failed_or_missing_module_health_overrides_top_level_ready(self):
        for module in (
            {"state": "degraded", "health": {"ok": False, "code": "busy"}},
            {"state": "starting", "health": {"ok": True, "code": "ready"}},
            {"state": "ready", "health": {"ok": False, "code": "failed"}},
            {"state": "ready", "health": {"ok": 1, "code": "ready"}},
            {"state": "ready"}, {"state": "ready", "health": []}, None,
        ):
            with self.subTest(module=module):
                state = copy.deepcopy(self.state)
                state["modules"]["companion"] = module
                self.write(state)
                self.assertFalse(check_module_state(self.path))
        for modules in ({}, [], None):
            with self.subTest(modules=modules):
                self.write({**self.state, "modules": modules})
                self.assertFalse(check_module_state(self.path))

    def test_missing_or_incomplete_atomic_snapshot_recovers_on_real_ready_write(self):
        self.assertFalse(check_module_state(self.path))
        for content in ("{", "null", "[]", '{"status":"ready"}'):
            self.path.write_text(content, encoding="utf-8")
            self.assertFalse(check_module_state(self.path))
        self.write()
        self.assertTrue(check_module_state(self.path))
        state = copy.deepcopy(self.state)
        state["modules"]["memory"]["health"] = {"ok": False, "code": "unavailable"}
        self.write(state)
        self.assertFalse(check_module_state(self.path))
        self.write()
        self.assertTrue(check_module_state(self.path))

    def test_terminated_host_is_not_ready_even_with_fresh_all_ready_snapshot(self):
        child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"])
        try:
            self.write({**self.state, "pid": child.pid})
            self.assertTrue(check_module_state(self.path))
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(check_module_state(self.path))

    def test_host_and_legacy_container_snapshots_keep_distinct_contracts(self):
        legacy = {**self.state, "processes": {"business-host": {"state": "ready", "health": {"ok": True}}}}
        del legacy["modules"]
        self.write(legacy)
        self.assertTrue(check_state(self.path))
        self.assertFalse(check_module_state(self.path))
        self.write()
        self.assertTrue(check_module_state(self.path))
        self.assertFalse(check_state(self.path))

    def test_probe_failure_returns_only_fixed_safe_code(self):
        state = copy.deepcopy(self.state)
        state["modules"]["gateway"]["health"] = {"ok": False, "code": "secret-token-and-private-path"}
        self.write(state)
        check = validate_probe({"type": "module_state", "path": str(self.path)})
        self.assertEqual(probe(check, {}), {"ok": False, "code": "module_unavailable"})
        self.path.write_bytes(b"\xff secret-token-and-private-path")
        self.assertEqual(probe(check, {}), {"ok": False, "code": "module_unavailable"})


if __name__ == "__main__":
    unittest.main()
