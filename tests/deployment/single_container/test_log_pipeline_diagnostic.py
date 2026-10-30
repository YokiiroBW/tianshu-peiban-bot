import unittest

from production_log_history_check import native_comparison


class PipelineDiagnosticTests(unittest.TestCase):
    def test_missing_and_reset_counters_stay_unknown(self):
        first = {"observations": {"vector": {"sink_sent_events": 10, "component_errors": 4}}}
        second = {"observations": {"ready_http": 200, "guard": {"monitor_success": 1},
                    "vector": {"acknowledgements_enabled": True, "buffer_bytes": 42,
                               "sink_sent_events": 13, "component_errors": 1}}}
        result = native_comparison(first, second)
        self.assertEqual(result["vector_counter_deltas"]["sink_sent_events"], 3)
        self.assertIsNone(result["vector_counter_deltas"]["component_errors"])
        self.assertIsNone(result["vector_counter_deltas"]["discarded_events"])
        self.assertEqual(result["historical_integrity"], "not_available_ledger_retired")
