"""Actual matched delivery time, including the review's wait-only false-positive case."""

import copy
import unittest
import uuid
from unittest.mock import patch

from acceptance.source_lease import completed_after_expiry
from acceptance.transport import Failed

EXPIRY = "2026-09-22T00:01:40Z"


def delivery(seconds, correlation="a" * 32):
    return {
        "service": "companion",
        "event": "turn.delivery.finished",
        "outcome": "succeeded",
        "correlation_id": correlation,
        "event_id": str(uuid.uuid4()),
        "timestamp": f"2026-09-22T00:01:{seconds:02}Z",
    }


class SourceLeaseTests(unittest.TestCase):
    def test_matched_success_uses_actual_first_last_times_and_count(self):
        rows = [delivery(45), delivery(39, "b" * 32), delivery(41, "c" * 32)]
        facts = completed_after_expiry(
            rows, EXPIRY, [r["correlation_id"] for r in rows]
        )
        self.assertEqual(facts["successful_delivery_count"], 3)
        self.assertEqual(facts["deliveries_after_initial_expiry"], 2)
        self.assertEqual(facts["first_delivery_at"], rows[1]["timestamp"])
        self.assertEqual(
            facts["first_delivery_after_initial_expiry"], rows[2]["timestamp"]
        )
        self.assertEqual(
            facts["last_delivery_after_initial_expiry"], rows[0]["timestamp"]
        )

    def test_waiting_until_inspection_101_cannot_promote_delivery_99(self):
        with patch("time.time", return_value=10**12):
            with self.assertRaisesRegex(
                Failed, "initial_source_lease_not_crossed_by_delivery"
            ):
                completed_after_expiry([delivery(39)], EXPIRY, ["a" * 32])

    def test_equal_expiry_is_not_strictly_after(self):
        with self.assertRaisesRegex(
            Failed, "initial_source_lease_not_crossed_by_delivery"
        ):
            completed_after_expiry([delivery(40)], EXPIRY, ["a" * 32])

    def test_missing_records_or_accepted_correlations_cannot_pass(self):
        for events, correlations in (([], ["a" * 32]), ([delivery(41)], [])):
            with self.assertRaisesRegex(Failed, "delivery_evidence_missing"):
                completed_after_expiry(events, EXPIRY, correlations)

    def test_invalid_or_unzoned_time_fails_even_with_another_valid_late_event(self):
        for timestamp in (
            None,
            "",
            101,
            "2026-09-22",
            "2026-09-22T00:01:41",
            "2026-02-30T00:01:41Z",
            "2026-09-22T00:01:41+25:00",
            "2026-09-22T00:01:39-00:60",
            "0001-01-01T00:00:00+23:00",
        ):
            row = dict(delivery(41), timestamp=timestamp)
            with (
                self.subTest(timestamp=timestamp),
                self.assertRaisesRegex(Failed, "delivery_timestamp_invalid"),
            ):
                completed_after_expiry(
                    [row, delivery(42, "b" * 32)], EXPIRY, ["a" * 32, "b" * 32]
                )
        with self.assertRaisesRegex(Failed, "delivery_timestamp_invalid"):
            completed_after_expiry([delivery(41)], "invalid", ["a" * 32])

    def test_wrong_service_event_or_outcome_cannot_supply_late_evidence(self):
        for key, value in (
            ("service", "gateway"),
            ("event", "turn.generation.finished"),
            ("outcome", "failed"),
            ("outcome", "unknown"),
        ):
            with (
                self.subTest(key=key, value=value),
                self.assertRaisesRegex(Failed, "successful_delivery_required"),
            ):
                completed_after_expiry(
                    [dict(delivery(41), **{key: value})], EXPIRY, ["a" * 32]
                )

    def test_duplicate_or_unmatched_correlation_and_identity_fail(self):
        first = delivery(41)
        for rows, expected, code in (
            (
                [first, copy.deepcopy(first)],
                ["a" * 32],
                "delivery_correlation_duplicate",
            ),
            ([first], ["b" * 32], "delivery_correlation_unmatched"),
            ([first], ["a" * 32, "b" * 32], "delivery_correlation_missing"),
            ([first], ["a" * 32, "a" * 32], "accepted_correlation_duplicate"),
            (
                [first, dict(first, correlation_id="b" * 32)],
                ["a" * 32, "b" * 32],
                "delivery_identity_invalid_or_duplicate",
            ),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(Failed, code):
                completed_after_expiry(rows, EXPIRY, expected)

    def test_timezones_compare_instants_not_local_clock_strings(self):
        row = dict(delivery(41), timestamp="2026-09-22T01:01:41+01:00")
        self.assertEqual(
            completed_after_expiry([row], EXPIRY, ["a" * 32])[
                "deliveries_after_initial_expiry"
            ],
            1,
        )
