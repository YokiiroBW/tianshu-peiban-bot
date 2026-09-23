import unittest
from datetime import datetime, timezone

from linux_scenarios import query_start


class BaselineWindowTests(unittest.TestCase):
    def test_retained_core_events_are_inside_initial_query_window(self):
        earlier = datetime(2026, 9, 24, tzinfo=timezone.utc)
        timestamp = int(earlier.timestamp() * 10**9)
        now = timestamp + 300 * 10**9
        self.assertLess(
            query_start([{"timestamp": earlier.isoformat()}], now), timestamp
        )
        self.assertEqual(query_start([], now), now - 10**9)
