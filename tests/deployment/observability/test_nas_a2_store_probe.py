"""Offline route regression for the single aged-source persistence probe."""

import unittest

import nas_a2_store_probe as store


class StoreIntervalTests(unittest.TestCase):
    NOW = 4_000_000_000_000_000_000
    SECOND = 10**9
    SPAN = 59_999 * 2_500_000

    def evidence(self, first_age_seconds=3600, now_offset_seconds=0,
                 end_offset_ns=0):
        now = self.NOW + now_offset_seconds * self.SECOND
        first = self.NOW - first_age_seconds * self.SECOND
        latest = first + self.SPAN
        return store._store_route_evidence(
            now, first - self.SECOND, latest + self.SECOND + end_offset_ns,
            first, latest,
        )

    def test_hour_old_events_stay_on_nonempty_store_route_through_deadline(self):
        for offset in (0, 300, 1200, 1500):
            evidence = self.evidence(now_offset_seconds=offset)
            self.assertTrue(evidence["all_query_buckets_on_store_route"])
            self.assertTrue(evidence["nonempty_store_interval"])
            self.assertGreater(evidence["latest_event_age_seconds"], 1635)
            self.assertLess(evidence["oldest_event_age_seconds"], 3 * 3600)
            self.assertEqual(evidence["timezone"], "UTC")

    def test_recent_r12_age_cannot_be_misreported_as_store_read(self):
        evidence = self.evidence(first_age_seconds=1104)
        self.assertFalse(evidence["all_query_buckets_on_store_route"])
        self.assertFalse(
            evidence["checks"]["all_query_buckets_older_than_store_cutoff"]
        )

    def test_complete_query_bound_must_be_older_than_cutoff(self):
        first = self.NOW - 3600 * self.SECOND
        latest = first + self.SPAN
        cutoff = self.NOW - 1635 * self.SECOND
        evidence = store._store_route_evidence(
            self.NOW, first - self.SECOND, cutoff + 1, first, latest
        )
        self.assertFalse(evidence["all_query_buckets_on_store_route"])

    def test_retention_and_three_hour_policy_are_independent_gates(self):
        old = self.evidence(first_age_seconds=49 * 3600)
        self.assertFalse(old["checks"]["all_events_inside_retention"])
        self.assertFalse(old["all_query_buckets_on_store_route"])
        three_hours = self.evidence(first_age_seconds=3 * 3600 + 1)
        self.assertFalse(
            three_hours["checks"]["all_events_inside_three_hour_ingester_query_policy"]
        )


if __name__ == "__main__":
    unittest.main()
