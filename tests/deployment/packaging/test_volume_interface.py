import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[3] / "deploy/tianshu"
sys.path.insert(0, str(PACKAGE))
from manifest import Refused, load_manifest
from observability_contract import legacy_view


class VolumeInterfaceTests(unittest.TestCase):
    def test_versions_and_legacy_projection(self):
        old = load_manifest(PACKAGE / "release-manifest.v1.example.json")
        new = load_manifest(PACKAGE / "release-manifest.example.json")
        view = legacy_view(new)
        self.assertEqual(old["volumes"], view["volumes"])
        self.assertEqual(old["products"], view["products"])
        self.assertEqual(5, len(new["volumes"]) - len(old["volumes"]))

    def test_missing_misowned_or_partial_observation_storage_refused(self):
        source = json.loads((PACKAGE / "release-manifest.example.json").read_bytes())
        for change in ("missing", "owner", "file", "path", "old_version"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                value = copy.deepcopy(source)
                if change == "missing":
                    value["volumes"].pop()
                elif change == "old_version":
                    value["schema_version"] = "1.0.0"
                else:
                    key, setting = {"owner": ("owner_service", "platform"),
                                    "file": ("kind", "file"),
                                    "path": ("host_path", "observability/data")}[change]
                    value["volumes"][-1][key] = setting
                path = Path(tmp) / "manifest.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(Refused):
                    load_manifest(path)
