"""Fixture/tool checks; no claim of native Linux binary execution."""

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from linux_observability_fixture import SOURCES, prepare_fixture

# isort: split
from linux_log_retention import MAXIMUM, native_name, prepare
from linux_observability import main
from source_retention import native_segment

CONTRACT = os.environ.get("DEP_B_CONTRACT")


class FilenameChecks(unittest.TestCase):
    def test_real_five_producer_names_supply_actual_guard_sealing_evidence(self):
        instance = str(uuid.uuid4())
        for source in SOURCES:
            for index in (0, 1, 123):
                with self.subTest(source=source, index=index):
                    self.assertEqual(
                        native_segment(source, native_name(source, instance, index)),
                        ("companion" if source == "companion" else instance, index),
                    )


@unittest.skipUnless(CONTRACT, "DEP_B_CONTRACT required for authoritative raw bytes")
class FixtureChecks(unittest.TestCase):
    def test_only_pacing_changes_from_native_retention_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = prepare_fixture(Path(directory) / "new", Path(CONTRACT))
            root = Path(fixture["root"])
            original = {
                path: path.read_bytes()
                for path in (root / "native").rglob("*")
                if path.is_file()
            }
            guard = json.loads((root / "native/guard.json").read_bytes())
            prepare(fixture)
            expected = {**guard, "interval_seconds": 0.5}
            self.assertEqual(
                json.loads((root / "native/guard.json").read_bytes()), expected
            )
            self.assertEqual(expected["log_max_events"], MAXIMUM)
            for path, data in original.items():
                if path.name != "guard.json":
                    self.assertEqual(path.read_bytes(), data)

    def test_cli_default_prepares_retention_without_starting_any_engine(self):
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
                        "--scenario",
                        "log-retention",
                    ]
                ),
                0,
            )
            suite.assert_not_called()
            report = json.loads((root / "linux-observability.json").read_bytes())
            self.assertEqual(report["scenario"], "log-retention")
            self.assertEqual(report["status"], "prepared_not_run")
            self.assertFalse(report["real_engines_executed"])


if __name__ == "__main__":
    unittest.main()
