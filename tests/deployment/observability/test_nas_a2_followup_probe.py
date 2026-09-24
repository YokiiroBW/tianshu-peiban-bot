import json
import tempfile
import unittest
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
        self.assertTrue(followup.RUN_ROOT.name.endswith("r5"))
        self.assertEqual(str(followup.SUBNET), "10.204.54.0/24")
        self.assertEqual(followup.LOKI_IP, "10.204.54.10")
        self.assertEqual(followup.VECTOR_IP, "10.204.54.11")
        self.assertEqual(followup.GUARD_PORT, 19527)
        self.assertEqual(followup.VECTOR_METRICS_PORT, 9598)
        self.assertEqual(followup.TMPFS_BYTES, 512 * 1024**2)
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
            self.assertLess(len(canonical(padded_row)), 4095)

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


if __name__ == "__main__":
    unittest.main()
