"""Local stdlib-tool checks only; these do not claim Linux producer execution."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import linux_producer_capacity as probe


class LocalToolChecks(unittest.TestCase):
    def test_cleanup_preserves_active_bytes_and_only_removes_synthetic_sealed_logs(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            active = directory / "companion.jsonl.2"
            active.write_bytes(b'{"sequence":3}\n')
            (directory / "companion.jsonl").write_bytes(b'{"sequence":1}\n')
            (directory / "companion.jsonl.1").write_bytes(b'{"sequence":2}\n')
            other = directory / "kept.txt"
            other.write_bytes(b"not a log")
            before, removed = probe.sealed_cleanup(directory, active)
            self.assertEqual((before, removed), (b'{"sequence":3}\n', 2))
            self.assertEqual(active.read_bytes(), before)
            self.assertTrue(other.exists())

    def test_real_record_verifier_refuses_duplicate_sequence_and_partial_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = directory / "instance.000000.jsonl"
            first.write_bytes(b'{"sequence":1}\n{"sequence":2}\n')
            self.assertEqual(len(probe.lines(directory)), 2)
            second = directory / "instance.000001.jsonl"
            second.write_bytes(b'{"sequence":2}\n')
            with self.assertRaisesRegex(probe.Refused, "duplicate_or_invalid_sequence"):
                probe.lines(directory)
            second.write_bytes(b'{"sequence":3}')
            with self.assertRaisesRegex(probe.Refused, "partial_log_tail"):
                probe.lines(directory)

    def test_source_overlay_is_rejected_before_any_product_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "product.py"
            path.write_text("# source fixture\n")
            with self.assertRaisesRegex(probe.Refused, "source_overlay_refused"):
                probe.installed(SimpleNamespace(__file__=str(path)))

    def test_nonlinux_never_imports_products_or_creates_the_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-exist"
            printed = StringIO()
            with patch.object(probe.sys, "platform", "win32"), redirect_stdout(printed):
                self.assertEqual(probe.main(["--output", str(output)]), 1)
            result = json.loads(printed.getvalue())
            self.assertEqual(result["code"], "linux_required")
            self.assertEqual(result["status"], "failed")
            self.assertFalse(output.exists())

    def test_unexpected_failure_never_prints_exception_details(self):
        printed = StringIO()
        with (
            patch.object(probe, "run", side_effect=OSError("private-canary")),
            redirect_stdout(printed),
        ):
            self.assertEqual(probe.main(["--output", "/tmp/unused-ts369"]), 1)
        result = json.loads(printed.getvalue())
        self.assertEqual(result["code"], "producer_probe_unexpected")
        self.assertNotIn("private-canary", printed.getvalue())
        self.assertIn("not_exercised", result["limits"]["unknown_real_fsync_fault"])


if __name__ == "__main__":
    unittest.main()
