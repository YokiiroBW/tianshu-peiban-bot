import json
import tempfile
import unittest
from collections import Counter
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import nas_a2_followup_probe as followup
import nas_a2_probe as probe
from policy import canonical


class NasA2FollowupBudgetTests(unittest.TestCase):
    def test_run_scope_and_owned_names_are_unique_and_bounded(self):
        self.assertEqual(followup.RUN_ROOT.parent, followup.BASE_SCOPE / "runs")
        self.assertTrue(followup.PROJECT.startswith("tianshu-accept-a2-"))
        self.assertTrue(followup.VECTOR_NAME.startswith("tianshu-accept-a2-"))
        self.assertTrue(followup.LOKI_NAME.startswith("tianshu-accept-a2-"))
        self.assertTrue(followup.RUN_ROOT.name.endswith("r11"))
        self.assertTrue(followup.VECTOR_SERVICE.startswith("nas-a2-vector-r11-"))
        self.assertTrue(followup.RECOVERY_SERVICE.startswith("nas-a2-recovery-r11-"))
        self.assertEqual(str(followup.SUBNET), "10.205.20.0/24")
        self.assertEqual(followup.LOKI_IP, "10.205.20.10")
        self.assertEqual(followup.VECTOR_IP, "10.205.20.11")
        self.assertEqual(followup.GUARD_PORT, 19524)
        self.assertEqual(followup.VECTOR_METRICS_PORT, 9598)
        self.assertEqual(followup.VECTOR_BUFFER_FULL_TIMEOUT_SECONDS, 600)
        self.assertEqual(followup.VECTOR_REPLAY_DRAIN_TIMEOUT_SECONDS, 1800)
        self.assertEqual(followup.VECTOR_REQUEST_RATE_LIMIT_PER_SECOND, 1)
        self.assertEqual(followup.VECTOR_PADDING_BYTES, 2500)
        self.assertEqual(followup.TMPFS_BYTES, 1024**3)
        self.assertEqual(followup.VECTOR_BUFFER_BYTES, 268435488)

    def test_compact_source_and_enriched_output_fit_the_new_tmpfs_budget(self):
        source_row = probe._record(7)
        output_row = {
            **source_row,
            "a2_buffer_fixture": "x" * followup.VECTOR_PADDING_BYTES,
        }
        source_bytes = (len(canonical(source_row)) + 1) * followup.VECTOR_RECORDS
        output_bytes = (len(canonical(output_row)) + 1) * followup.VECTOR_RECORDS
        self.assertGreater(output_bytes, followup.VECTOR_BUFFER_BYTES)
        self.assertLess(
            source_bytes + followup.VECTOR_BUFFER_BYTES, followup.TMPFS_BYTES
        )

    def test_resource_gate_counts_live_resident_memory_and_mounted_tmpfs_only(self):
        historical_tmpfs = {
            "r2": {"mounted": True, "used_bytes": 229376},
            "r3": {"mounted": True, "used_bytes": 0},
            "r4": {"mounted": False, "used_bytes": 0},
            "r5": {"mounted": False, "used_bytes": 0},
        }

        result = followup._concurrent_a2_resource_budget(
            2 * 1024**3, 0, historical_tmpfs
        )

        self.assertEqual(result["historical_mounted_tmpfs_used_bytes"], 229376)
        self.assertEqual(
            result["total_concurrent_resources_bytes"], 2 * 1024**3 + 229376
        )
        self.assertTrue(result["within_budget"])

    def test_followup_vector_config_pins_full_buffer_and_unique_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vector.json"
            config = json.loads(followup._vector_config(path, Path(directory)))
            self.assertEqual(
                config["sinks"]["loki"]["buffer"]["max_size"],
                followup.VECTOR_BUFFER_BYTES,
            )
            self.assertEqual(
                config["sinks"]["loki"]["labels"]["service"],
                followup.VECTOR_SERVICE,
            )
            self.assertEqual(config["sinks"]["loki"]["request"]["concurrency"], "none")
            self.assertEqual(
                config["sinks"]["loki"]["request"]["rate_limit_num"],
                followup.VECTOR_REQUEST_RATE_LIMIT_PER_SECOND,
            )
            self.assertEqual(config["sinks"]["loki"]["request"]["timeout_secs"], 60)
            self.assertEqual(config["log_schema"]["timestamp_key"], "timestamp")
            self.assertFalse(config["sinks"]["loki"]["remove_timestamp"])
            self.assertIn(
                'parse_timestamp!(string!(.timestamp), format: "%+")',
                config["transforms"]["a2_json"]["source"],
            )
            self.assertIn(
                "a2_buffer_fixture",
                config["transforms"]["a2_json"]["source"],
            )
            padded_row = {
                **probe._record(8),
                "a2_buffer_fixture": "x" * followup.VECTOR_PADDING_BYTES,
            }
            self.assertLess(len(canonical(padded_row)), 6144)

    def test_vector_event_match_accepts_equivalent_json_field_order(self):
        row = probe._record(8)
        row["event_id"] = "12345678-1234-4234-8234-123456789abc"
        encoded = json.dumps(dict(reversed(list(row.items()))), separators=(",", ":"))

        class Client:
            def request(self, path):
                payload = {
                    "status": "success",
                    "data": {
                        "result": [
                            {"values": [["123456789", encoded]]},
                        ]
                    },
                }
                return 200, json.dumps(payload).encode(), {}

        evidence = followup._query_evidence(
            Client(), '{stack="tianshu",service="synthetic"}', 0, 1, row
        )
        self.assertEqual(evidence["exact_line_match_count"], 0)
        self.assertEqual(evidence["semantic_payload_match_count"], 1)

    def test_vector_metrics_are_scraped_from_the_isolated_container_ip(self):
        payload = (
            b'vector_buffer_size_bytes{buffer_id="loki",component_id="loki"} 99\n'
            b'vector_buffer_max_size_bytes{buffer_id="loki",component_id="loki"} 100\n'
            b'vector_component_sent_events_total{component_id="a2_file"} 7\n'
        )
        with patch.object(
            followup.urllib.request, "urlopen", return_value=BytesIO(payload)
        ) as open_url:
            snapshot = followup._vector_metrics_snapshot()
        self.assertEqual(snapshot["buffer_bytes"], 99)
        self.assertEqual(snapshot["buffer_max_bytes"], 100)
        self.assertEqual(snapshot["source_sent_events"], 7)
        self.assertEqual(
            open_url.call_args.args[0].full_url,
            f"http://{followup.VECTOR_IP}:{followup.VECTOR_METRICS_PORT}/metrics",
        )

    def test_missing_buffer_size_metric_is_retryable_not_reported_as_zero(self):
        payload = (
            b'vector_buffer_max_size_bytes{buffer_id="loki",component_id="loki"} 100\n'
            b'vector_component_sent_events_total{component_id="a2_file"} 7\n'
        )
        with patch.object(
            followup.urllib.request, "urlopen", return_value=BytesIO(payload)
        ):
            with self.assertRaisesRegex(
                probe.ProbeError, "vector_required_metric_missing"
            ):
                followup._vector_metrics_snapshot()

    def test_transient_vector_metrics_failure_is_retryable(self):
        expected = {"buffer_bytes": 0, "source_sent_events": 0}
        with patch.object(
            followup,
            "_vector_metrics_snapshot",
            side_effect=[probe.ProbeError("vector_metrics_unavailable"), expected],
        ):
            self.assertIsNone(followup._try_vector_metrics_snapshot())
            self.assertEqual(followup._try_vector_metrics_snapshot(), expected)

    def test_backpressure_requires_full_buffer_and_stalled_incomplete_source(self):
        self.assertTrue(
            followup._backpressure_verified(
                [40, 40, 40], [99, 99, 99], 100, 100
            )
        )
        self.assertFalse(
            followup._backpressure_verified(
                [40, 41, 42], [99, 99, 99], 100, 100
            )
        )
        self.assertFalse(
            followup._backpressure_verified(
                [40, 40, 40], [99, 98, 97], 100, 100
            )
        )
        self.assertFalse(
            followup._backpressure_verified(
                [100, 100, 100], [99, 99, 99], 100, 100
            )
        )

    def test_vector_metrics_are_not_host_published_and_use_the_fixed_container_ip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "config": root,
                "tls": root,
                "vector_source": root,
                "vector_data": root,
            }
            with (
                patch.object(probe, "PROJECT", followup.PROJECT),
                patch.object(probe, "VECTOR_NAME", followup.VECTOR_NAME),
            ):
                args = followup._vector_args(paths, "0")
        self.assertIn("--ip", args)
        self.assertEqual(args[args.index("--ip") + 1], followup.VECTOR_IP)
        self.assertNotIn("--publish", args)

    def test_reconciliation_separates_missing_rows_from_duplicate_occurrences(self):
        expected = Counter(
            (f"event-{index}", f"digest-{index}") for index in range(1, 201)
        )
        actual = expected.copy()
        for index in range(117, 201):
            del actual[(f"event-{index}", f"digest-{index}")]
        for index in range(1, 85):
            actual[(f"event-{index}", f"digest-{index}")] += 1

        result = followup._reconcile_event_identities(actual, expected)

        self.assertEqual(result["expected_unique_event_count"], 200)
        self.assertEqual(result["actual_unique_event_identity_hash_count"], 116)
        self.assertEqual(result["matching_identity_hash_count"], 116)
        self.assertEqual(result["missing_identity_hash_count"], 84)
        self.assertEqual(result["missing_event_id_count"], 84)
        self.assertEqual(result["unexpected_unique_identity_hash_count"], 0)
        self.assertEqual(result["unexpected_event_id_count"], 0)
        self.assertEqual(result["duplicate_identity_occurrence_count"], 84)
        self.assertEqual(result["duplicate_event_id_count"], 84)
        self.assertEqual(result["payload_mismatch_event_id_count"], 0)
        self.assertEqual(len(result["missing_event_ids_sample"]), 84)
        self.assertEqual(len(result["duplicate_event_ids_sample"]), 84)
        self.assertEqual(result["unexpected_identity_occurrence_count"], 84)

    def test_reconciliation_identifies_unexpected_ids_and_payload_mismatches(self):
        expected = Counter(
            {
                ("event-a", "digest-a"): 1,
                ("event-b", "digest-b"): 1,
            }
        )
        actual = Counter(
            {
                ("event-a", "digest-a"): 2,
                ("event-b", "digest-wrong"): 1,
                ("event-new", "digest-new"): 1,
            }
        )

        result = followup._reconcile_event_identities(actual, expected)

        self.assertEqual(result["missing_event_id_count"], 0)
        self.assertEqual(result["missing_identity_hash_count"], 1)
        self.assertEqual(result["unexpected_unique_identity_hash_count"], 2)
        self.assertEqual(result["unexpected_event_id_count"], 1)
        self.assertEqual(result["duplicate_identity_occurrence_count"], 1)
        self.assertEqual(result["duplicate_event_id_count"], 1)
        self.assertEqual(result["payload_mismatch_event_id_count"], 1)
        self.assertEqual(result["unexpected_identity_occurrence_count"], 3)


if __name__ == "__main__":
    unittest.main()
