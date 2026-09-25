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
    @staticmethod
    def sample(monotonic_ns, source_events, buffer_bytes, *, sink_running=False,
               discarded=0, error_series=None):
        return {
            "observed_at": {"utc": "2026-09-25T00:00:00Z", "monotonic_ns": monotonic_ns},
            "metrics": {
                "source_sent_events": source_events,
                "buffer_bytes": buffer_bytes,
                "discarded_events": discarded,
                "component_error_series": error_series or [],
            },
            "source_sha256": "unchanged",
            "source_file_bytes": 20000000,
            "sink_running": sink_running,
            "vector_running": True,
        }

    def test_run_scope_and_owned_names_are_unique_and_bounded(self):
        self.assertEqual(followup.RUN_ROOT.parent, followup.BASE_SCOPE / "runs")
        self.assertTrue(followup.PROJECT.startswith("tianshu-accept-a2-"))
        self.assertTrue(followup.VECTOR_NAME.startswith("tianshu-accept-a2-"))
        self.assertTrue(followup.LOKI_NAME.startswith("tianshu-accept-a2-"))
        self.assertTrue(followup.RUN_ROOT.name.endswith("r12"))
        self.assertTrue(followup.VECTOR_SERVICE.startswith("nas-a2-vector-r12-"))
        self.assertTrue(followup.RECOVERY_SERVICE.startswith("nas-a2-recovery-r12-"))
        self.assertEqual(str(followup.SUBNET), "10.205.21.0/24")
        self.assertEqual(followup.LOKI_IP, "10.205.21.10")
        self.assertEqual(followup.VECTOR_IP, "10.205.21.11")
        self.assertEqual(followup.GUARD_PORT, 19525)
        self.assertEqual(followup.VECTOR_METRICS_PORT, 9598)
        self.assertEqual(followup.VECTOR_BUFFER_FULL_TIMEOUT_SECONDS, 300)
        self.assertEqual(followup.VECTOR_REPLAY_DRAIN_TIMEOUT_SECONDS, 600)
        self.assertEqual(followup.STORE_ONLY_QUERY_TIMEOUT_SECONDS, 300)
        self.assertEqual(followup.TOTAL_RUN_TIMEOUT_SECONDS, 1500)
        self.assertEqual(followup.VECTOR_REQUEST_RATE_LIMIT_PER_SECOND, 4)
        self.assertEqual(followup.VECTOR_PADDING_BYTES, 2500)
        self.assertEqual(followup.VECTOR_RECORDS, 60000)
        self.assertEqual(followup.TMPFS_BYTES, 512 * 1024**2)
        self.assertEqual(followup.VECTOR_BUFFER_BYTES, 268435488)
        self.assertEqual(followup.VECTOR_INTERNAL_BUFFER_BYTES, 134217760)

    def test_compact_source_and_enriched_output_fit_the_new_tmpfs_budget(self):
        source_row = probe._record(7)
        output_row = {
            **source_row,
            "a2_buffer_fixture": "x" * followup.VECTOR_PADDING_BYTES,
        }
        source_bytes = (len(canonical(source_row)) + 1) * followup.VECTOR_RECORDS
        output_bytes = (len(canonical(output_row)) + 1) * followup.VECTOR_RECORDS
        self.assertGreater(output_bytes, followup.VECTOR_INTERNAL_BUFFER_BYTES)
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
            self.assertLess(len(canonical(padded_row)), 4096)

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

    def test_backpressure_uses_internal_limit_and_three_spaced_stall_samples(self):
        samples = [
            self.sample(index * 5_100_000_000, 49000, 134125880)
            for index in range(3)
        ]
        self.assertLess(134125880, followup.VECTOR_BUFFER_BYTES * 0.98)
        evidence = followup._backpressure_evidence(samples, 60000, 2750)
        self.assertTrue(evidence["passed"])
        self.assertEqual(evidence["internal_buffer_limit_bytes"], 134217760)
        self.assertEqual(evidence["observed_headroom_bytes"], [91880] * 3)

    def test_empirical_metric_byte_estimate_needs_observed_growth(self):
        samples = [
            self.sample(0, 0, 0),
            self.sample(5_100_000_000, 100, 275000),
            self.sample(10_200_000_000, 200, 275000),
        ]
        self.assertEqual(followup._empirical_encoded_event_bytes(samples), 2750)
        stalled = samples[1:]
        self.assertIsNone(followup._empirical_encoded_event_bytes(stalled))
        self.assertFalse(
            followup._backpressure_evidence(stalled, 60000, None)["passed"]
        )

    def test_backpressure_rejects_short_spacing_eof_online_sink_and_large_gap(self):
        valid = [self.sample(index * 5_100_000_000, 49000, 134125880) for index in range(3)]
        short_spacing = [dict(item, observed_at={"monotonic_ns": index * 4_900_000_000}) for index, item in enumerate(valid)]
        self.assertFalse(followup._backpressure_evidence(short_spacing, 60000, 2750)["passed"])
        at_eof = [self.sample(index * 5_100_000_000, 60000, 134125880) for index in range(3)]
        self.assertFalse(followup._backpressure_evidence(at_eof, 60000, 2750)["passed"])
        online = [self.sample(index * 5_100_000_000, 49000, 134125880, sink_running=True) for index in range(3)]
        self.assertFalse(followup._backpressure_evidence(online, 60000, 2750)["passed"])
        far_from_limit = [self.sample(index * 5_100_000_000, 49000, 131316136) for index in range(3)]
        self.assertFalse(followup._backpressure_evidence(far_from_limit, 60000, 2750)["passed"])

    def test_expected_offline_retry_is_separate_from_parse_drop_and_http_errors(self):
        expected_error = [{"labels": {"component_id": "loki", "error_type": "request_failed"}, "value": 5}]
        samples = [self.sample(index * 5_100_000_000, 49000, 134125880,
                               error_series=expected_error) for index in range(3)]
        self.assertTrue(followup._backpressure_evidence(samples, 60000, 2750)["passed"])
        parse_error = [{"labels": {"component_id": "a2_json", "error_type": "parse_failed"}, "value": 1}]
        samples[-1] = self.sample(10_200_000_000, 49000, 134125880, error_series=parse_error)
        self.assertFalse(followup._backpressure_evidence(samples, 60000, 2750)["passed"])
        samples[-1] = self.sample(10_200_000_000, 49000, 134125880, discarded=1)
        self.assertFalse(followup._backpressure_evidence(samples, 60000, 2750)["passed"])
        logs = ('WARN sink{component_id=loki}: error=error trying to connect: dns error: '
                'failed to lookup address information\n')
        self.assertEqual(followup._classify_vector_error_logs(logs, sink_offline=True)["unexpected_error_count"], 0)
        self.assertEqual(
            followup._classify_vector_error_logs(
                logs, sink_offline=False, allow_recovery_transport_retry=True
            )["expected_transport_retry_count"], 1
        )
        self.assertEqual(
            followup._classify_vector_error_logs(logs, sink_offline=False)["unexpected_error_count"], 1
        )
        http_error = 'WARN sink{component_id=loki}: error=Server responded with an error: 500 Internal Server Error\n'
        self.assertEqual(
            followup._classify_vector_error_logs(
                http_error, sink_offline=False, allow_recovery_transport_retry=True
            )["unexpected_error_count"], 1
        )

    def test_full_query_deadline_is_not_reported_as_exact(self):
        class Client:
            def range(self, *_args, **_kwargs):
                raise AssertionError("expired query must not make a request")

        result = followup._query_all_events(
            Client(), '{stack="tianshu"}', 0, 10**9,
            Counter({("event-a", "digest-a"): 1}), deadline_monotonic=0,
        )
        self.assertFalse(result["query_complete"])
        self.assertFalse(result["identity_hash_multiset_match"])
        self.assertEqual(result["query_errors"][0]["error"], "query_deadline_exceeded")

    def test_timestamped_raw_metric_sample_keeps_file_and_counter_units(self):
        payload = (
            b'vector_buffer_size_bytes{buffer_id="loki",component_id="loki"} 134125880\n'
            b'vector_buffer_max_size_bytes{buffer_id="loki",component_id="loki"} 268435488\n'
            b'vector_component_sent_events_total{component_id="a2_file"} 49000\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_bytes(b'{}\n')
            data = root / "vector" / "buffer" / "v2" / "loki"
            data.mkdir(parents=True)
            (data / "buffer-data-1.dat").write_bytes(b"abc")
            paths = {"vector_data": root / "vector"}

            def container(container_id):
                return {"State": {"Running": container_id == "vector-id"}}

            with (
                patch.object(followup, "_vector_prometheus_body", return_value=payload),
                patch.object(probe, "_container_by_id", side_effect=container),
            ):
                sample = followup._timestamped_vector_sample(
                    paths, source, "vector-id", "loki-id"
                )
        self.assertTrue(sample["observed_at"]["utc"].endswith("Z"))
        self.assertIsInstance(sample["observed_at"]["monotonic_ns"], int)
        self.assertEqual(sample["metrics"]["buffer_bytes"], 134125880)
        self.assertEqual(sample["metrics"]["source_sent_events"], 49000)
        self.assertFalse(sample["sink_running"])
        self.assertEqual(sample["buffer_data_files"][0]["bytes"], 3)
        self.assertEqual(len(sample["raw_prometheus_selected_lines"]), 3)

    def test_loki_inventory_records_each_storage_layer_hash_and_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = {
                "chunks": "chunks/tianshu/chunk-a",
                "object_index": "chunks/index/index-a",
                "active_index": "index/active-a",
                "index_cache": "index-cache/cache-a",
            }
            for relative in names.values():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
            inventory = followup._loki_file_inventory({"loki_data": root}, {})
            for category, relative in names.items():
                records = inventory["categories"][category]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["path"], relative)
                self.assertEqual(records[0]["sha256"], probe.sha(relative.encode()))
                self.assertGreater(records[0]["mtime_ns"], 0)

    def test_scope_archive_verifies_source_before_unmount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scratch = root / "tmpfs"
            source = scratch / "vector-source" / "events.jsonl"
            source.parent.mkdir(parents=True)
            source.write_bytes(b'{"event_id":"synthetic"}\n')
            evidence = root / "evidence"
            evidence.mkdir()
            paths = {
                "scratch": scratch,
                "vector_source": source.parent,
                "evidence": evidence,
            }
            report = {}
            with (
                patch.object(followup, "RUN_ROOT", root),
                patch.object(followup.subprocess, "run") as umount,
            ):
                umount.return_value.returncode = 0
                followup._archive_and_unmount_new_scope(paths, report)
            self.assertTrue(report["archive"]["gzip_verified"])
            self.assertTrue(report["archive"]["source_matches_live"])
            self.assertEqual(report["archive"]["source_sha256"], probe.sha(source.read_bytes()))
            self.assertTrue(report["archive"]["tmpfs_unmounted_after_verification"])
            self.assertEqual(umount.call_args.args[0], ["umount", str(scratch)])

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
