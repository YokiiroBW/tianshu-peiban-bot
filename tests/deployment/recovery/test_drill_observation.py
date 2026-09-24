"""Bounded Gateway counter snapshots for the recovery clone observation."""

import json
import tempfile
import unittest
from pathlib import Path

from ops.recovery.drill_observation import (
    gateway_counters,
    summarize_a1_gateway_counts,
)
from ops.recovery.lifecycle import Deadline
from ops.recovery.safety import RecoveryError


class DrillObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.logs = self.root / "logs/gateway"
        self.logs.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, correlation, event, outcome):
        return {
            "schema_version": "1.0.0",
            "service": "gateway",
            "event": event,
            "outcome": outcome,
            "correlation_id": correlation,
        }

    def write_baseline(self):
        rows = []
        for number, outcome in enumerate(
            ("unknown", "unknown", "succeeded", "succeeded"), 1
        ):
            correlation = f"{number:032x}"
            rows.extend(
                (
                    self.record(correlation, "request.accepted", "succeeded"),
                    self.record(correlation, "upstream.call_started", "started"),
                    self.record(correlation, "upstream.call_finished", outcome),
                )
            )
            if number == 4:
                # The real source has one control correlation with a duplicate
                # acceptance event, while retaining a single upstream call.
                rows.append(self.record(correlation, "request.accepted", "succeeded"))
        for number in range(5, 10):
            rows.append(
                self.record(f"{number:032x}", "request.accepted", "succeeded")
            )
        (self.logs / "gateway-one.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def test_reads_unknown_groups_and_successful_controls(self):
        self.write_baseline()
        counts = gateway_counters(self.root, Deadline(3))
        summary = summarize_a1_gateway_counts(counts)
        self.assertEqual(summary["correlation_groups"], 9)
        self.assertEqual(summary["unknown_groups"], 2)
        self.assertEqual(summary["successful_control_groups"], 2)
        self.assertEqual(summary["accepted_only_control_groups"], 5)
        self.assertEqual(summary["event_counts"]["request.accepted"], 10)
        self.assertEqual(summary["event_counts"]["upstream.call_started"], 4)

    def test_extra_call_or_unknown_outcome_is_not_a_valid_baseline(self):
        self.write_baseline()
        with (self.logs / "gateway-one.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    self.record("0" * 31 + "1", "upstream.call_started", "started")
                )
                + "\n"
            )
        counts = gateway_counters(self.root, Deadline(3))
        with self.assertRaisesRegex(
            RecoveryError, "drill_observation_gateway_baseline_invalid"
        ):
            summarize_a1_gateway_counts(counts)

    def test_malformed_log_line_is_rejected(self):
        for body in (b"{not-json}\n", b"[]\n"):
            with self.subTest(body=body):
                (self.logs / "gateway-one.jsonl").write_bytes(body)
                with self.assertRaises(RecoveryError):
                    gateway_counters(self.root, Deadline(3))


if __name__ == "__main__":
    unittest.main()
