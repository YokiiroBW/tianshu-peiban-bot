"""Local fixture/verification checks; these do not claim Linux engine execution."""

import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from linux_observability import EngineLifecycle, Suite, exact_lines, main, read_five

# isort: split

from configs import ALERTS
from helpers import SNAPSHOT, emit, event
from linux_observability_fixture import SOURCES, prepare_fixture
from policy import Policy, canonical

CONTRACT = os.environ.get("DEP_B_CONTRACT")


class ByteChecks(unittest.TestCase):
    def test_query_requires_every_original_canonical_byte_but_allows_exact_replay(self):
        first, second = event(1), event(2)
        lines = [(1, canonical(first).decode()), (2, canonical(second).decode())]
        digest = exact_lines([first, second], lines)
        self.assertEqual(exact_lines([first, second], lines + [lines[0]]), digest)
        with self.assertRaisesRegex(ValueError, "retrieved_event_loss"):
            exact_lines([first, second], lines[:1])
        with self.assertRaisesRegex(ValueError, "retrieved_original_bytes_changed"):
            exact_lines([first], [(1, json.dumps(first))])

    def test_dedicated_knowledge_read_keeps_original_service(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = {name: Path(directory) / name for name in SOURCES}
            for path in roots.values():
                path.mkdir()
            emit(roots["knowledge"] / "valid.jsonl", [event(1, "memory-knowledge")])
            records, invalid, partial = read_five(roots, Policy(SNAPSHOT))
            self.assertEqual((len(records), invalid, partial), (1, 0, 0))
            emit(roots["knowledge"] / "wrong-owner.jsonl", [event(1, "memory")])
            with self.assertRaisesRegex(ValueError, "knowledge_service_changed"):
                read_five(roots, Policy(SNAPSHOT))


class ProgressChecks(unittest.TestCase):
    def test_deadline_retains_phase_attempts_observations_and_safe_error_code(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = object.__new__(Suite)
            suite.root = Path(directory)
            suite.phase("prometheus_targets")
            clock = [0]

            def targets():
                suite.last_observation["jobs"] = {
                    "guard": "0",
                    "vector": "1",
                    "loki": "0",
                }
                raise ValueError("prometheus_query_failed")

            with (
                patch(
                    "linux_observability.time.monotonic", side_effect=lambda: clock[0]
                ),
                patch(
                    "linux_observability.time.sleep",
                    side_effect=lambda seconds: clock.__setitem__(
                        0, clock[0] + seconds
                    ),
                ),
                self.assertRaisesRegex(ValueError, "scenario_deadline_exceeded"),
            ):
                suite.wait(targets, seconds=4)
            progress = json.loads((suite.root / "progress.json").read_bytes())
            self.assertEqual(progress["phase"], "prometheus_targets")
            self.assertEqual(progress["state"], "deadline_exceeded")
            self.assertEqual(progress["attempts"], 2)
            self.assertEqual(progress["last_error_code"], "prometheus_query_failed")
            self.assertEqual(progress["last_observation"]["jobs"]["guard"], "0")

    def test_success_after_retry_and_non_code_exception_does_not_expose_text(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = object.__new__(Suite)
            suite.root = Path(directory)
            suite.phase("native_engine_startup")
            clock = [0]
            responses = iter([RuntimeError("Bearer SYNTHETIC_PRIVATE_VALUE"), "ready"])

            def ready():
                value = next(responses)
                if isinstance(value, Exception):
                    raise value
                return value

            with (
                patch(
                    "linux_observability.time.monotonic", side_effect=lambda: clock[0]
                ),
                patch(
                    "linux_observability.time.sleep",
                    side_effect=lambda seconds: clock.__setitem__(
                        0, clock[0] + seconds
                    ),
                ),
            ):
                self.assertEqual(suite.wait(ready, seconds=4), "ready")
            progress = (suite.root / "progress.json").read_text()
            self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", progress)
            self.assertEqual(json.loads(progress)["state"], "passed")


class CollectorLifecycleChecks(unittest.TestCase):
    def test_rotation_restarts_already_stopped_collector_without_false_crash(self):
        item = Mock(state="stopped", forced=False)
        item.group_running.return_value = False
        lifecycle = EngineLifecycle(
            SimpleNamespace(processes={"obs-vector": item}), None
        )
        with patch.object(lifecycle, "start") as start:
            lifecycle.recreate_vector()
        start.assert_called_once_with("obs-vector")
        item.signal.assert_not_called()
        self.assertEqual(lifecycle.crashes, [])

    def test_live_collector_requires_observed_sigkill_before_restart(self):
        item = Mock()
        item.child.pid = 123
        item.child.wait.return_value = -9
        item.group_running.side_effect = [True, False]
        lifecycle = EngineLifecycle(
            SimpleNamespace(processes={"obs-vector": item}), None
        )
        with (
            patch.object(signal, "SIGKILL", 9, create=True),
            patch.object(lifecycle, "start") as start,
        ):
            lifecycle.recreate_vector()
        item.signal.assert_called_once_with(9)
        start.assert_called_once_with("obs-vector")
        self.assertEqual(
            lifecycle.crashes, [{"pid": 123, "exit_code": -9, "intentional": True}]
        )

    def test_unexpected_dead_or_forced_collector_is_not_misreported_as_rotation(self):
        for state, forced in (("failed", False), ("stopped", True)):
            item = Mock(state=state, forced=forced)
            item.group_running.return_value = False
            lifecycle = EngineLifecycle(
                SimpleNamespace(processes={"obs-vector": item}), None
            )
            with (
                self.subTest(state=state, forced=forced),
                self.assertRaisesRegex(ValueError, "collector_not_stopped"),
            ):
                lifecycle.recreate_vector()


@unittest.skipUnless(
    CONTRACT, "DEP_B_CONTRACT required for original authoritative bytes"
)
class FixtureChecks(unittest.TestCase):
    def test_native_five_sources_tls_retention_rules_and_loopback_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "new"
            fixture = prepare_fixture(root, Path(CONTRACT))
            self.assertEqual(set(fixture["logs"]), set(SOURCES))
            native = root / "native"
            vector = json.loads((native / "vector.json").read_bytes())
            self.assertEqual(
                set(vector["sources"]) - {"internal_metrics"}, set(SOURCES)
            )
            self.assertIn(
                '["memory-knowledge"]', vector["transforms"]["knowledge_safe"]["source"]
            )
            self.assertEqual(vector["sinks"]["loki"]["buffer"]["max_size"], 4 * 1024**3)
            self.assertEqual(
                vector["sinks"]["loki"]["endpoint"], "https://127.0.0.1:8445"
            )
            self.assertEqual(vector["sinks"]["loki"]["tls"]["verify_hostname"], True)
            loki = json.loads((native / "loki.json").read_bytes())
            self.assertEqual(loki["limits_config"]["retention_period"], "720h")
            self.assertEqual(loki["ingester"]["max_chunk_age"], "2h")
            self.assertEqual(
                loki["server"]["http_tls_config"]["client_auth_type"],
                "RequireAndVerifyClientCert",
            )
            guard = json.loads((native / "guard.json").read_bytes())
            self.assertEqual(set(guard["log_roots"]), set(SOURCES))
            self.assertEqual(guard["interval_seconds"], 30)
            self.assertEqual(guard["reserve_bytes"], 1024**3)
            prometheus = json.loads((native / "prometheus.json").read_bytes())
            for job in prometheus["scrape_configs"]:
                if "authorization" in job:
                    self.assertEqual(
                        Path(job["authorization"]["credentials_file"]),
                        native / "secrets/metrics_token",
                    )
                    self.assertTrue(
                        Path(job["authorization"]["credentials_file"]).is_file()
                    )
            rules = json.loads(
                (native / "provisioning/alerting/tianshu.yaml").read_bytes()
            )
            self.assertEqual(
                {row["title"] for row in rules["groups"][0]["rules"]}, set(ALERTS)
            )
            for spec in fixture["processes"]:
                self.assertEqual((spec["uid"], spec["gid"]), (10001, 10001))
                self.assertIn("https://127.0.0.1:", spec["health"]["url"])
                self.assertTrue(Path(spec["health"]["ca_file"]).is_relative_to(root))
            for name in ("ca.pem", "guard.key", "client.key", "grafana.pem"):
                self.assertEqual(
                    (native / "tls" / name).read_bytes(),
                    (root / "tls" / name).read_bytes(),
                )
            with self.assertRaises(FileExistsError):
                prepare_fixture(root, Path(CONTRACT))

    def test_default_prepares_only_and_never_starts_a_process(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("linux_observability.Suite") as suite,
        ):
            root = Path(directory) / "new"
            self.assertEqual(
                main(
                    [
                        "--contract",
                        CONTRACT,
                        "--output",
                        str(root),
                        "--image-id",
                        "sha256:" + "f" * 64,
                    ]
                ),
                0,
            )
            suite.assert_not_called()
            report = json.loads((root / "linux-observability.json").read_bytes())
            self.assertEqual(report["status"], "prepared_not_run")
            self.assertFalse(report["real_engines_executed"])

    def test_failed_execution_closes_owned_suite_and_records_no_pass(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("linux_observability.execution_boundary"),
            patch("linux_observability.Suite") as suite,
        ):
            suite.return_value.execute.side_effect = RuntimeError("synthetic failure")
            suite.return_value.close.return_value = True
            suite.return_value.real_engines_executed = True
            suite.return_value.progress = {
                "phase": "prometheus_targets",
                "state": "deadline_exceeded",
            }
            root = Path(directory) / "new"
            self.assertEqual(
                main(
                    [
                        "--contract",
                        CONTRACT,
                        "--output",
                        str(root),
                        "--image-id",
                        "sha256:" + "f" * 64,
                        "--execute-synthetic",
                    ]
                ),
                1,
            )
            suite.return_value.close.assert_called_once()
            report = json.loads((root / "linux-observability.json").read_bytes())
            self.assertEqual(report["status"], "failed")
            self.assertTrue(report["native_processes_stopped"])
            self.assertTrue(report["real_engines_executed"])
            self.assertFalse(report["completed_all_scenarios"])
            self.assertEqual(report["phase_diagnostics"]["phase"], "prometheus_targets")

    def test_capacity_scenario_only_prepares_small_explicit_fixture_without_engines(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("linux_observability.Suite") as suite,
        ):
            root = Path(directory) / "capacity-new"
            self.assertEqual(
                main(
                    [
                        "--contract",
                        CONTRACT,
                        "--output",
                        str(root),
                        "--image-id",
                        "sha256:" + "f" * 64,
                        "--scenario",
                        "ledger-capacity",
                        "--events-per-source",
                        "1",
                    ]
                ),
                0,
            )
            suite.assert_not_called()
            report = json.loads((root / "linux-observability.json").read_bytes())
            self.assertEqual(
                (report["scenario"], report["status"], report["real_engines_executed"]),
                ("ledger-capacity", "prepared_not_run", False),
            )
            config = json.loads((root / "native/guard.json").read_bytes())
            self.assertEqual(config["ledger_max_events"], 5)
            self.assertEqual(config["interval_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
