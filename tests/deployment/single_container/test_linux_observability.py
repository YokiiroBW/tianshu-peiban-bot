"""Local fixture/verification checks; these do not claim Linux engine execution."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linux_observability import exact_lines, main, read_five

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
            self.assertFalse(report["real_engines_executed"])


if __name__ == "__main__":
    unittest.main()
