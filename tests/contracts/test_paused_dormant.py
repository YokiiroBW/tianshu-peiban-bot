"""PC-04 wire invariants. Product state-machine and production tests are separate."""

import hashlib
import json
from pathlib import Path
import subprocess
import unittest

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "contracts/life-runtime/v2"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class PausedDormantContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = read(PACKAGE / "schemas/life.json")
        cls.registry = Registry()
        for folder in (
            ROOT / "contracts/text-dialogue/v1/schemas",
            PACKAGE / "dependencies",
        ):
            for path in folder.glob("*.json"):
                schema = read(path)
                if "$id" in schema:
                    cls.registry = cls.registry.with_resource(
                        schema["$id"], Resource.from_contents(schema)
                    )
        cls.registry = cls.registry.with_resource(
            cls.schema["$id"], Resource.from_contents(cls.schema)
        )
        cls.examples = read(PACKAGE / "examples/paused-dormant.json")

    def validator(self, definition, registry=None):
        return Draft202012Validator(
            {"$ref": self.schema["$id"] + "#/$defs/" + definition},
            registry=self.registry if registry is None else registry,
        )

    def test_paused_dormant_and_existing_running_completed_records(self):
        for case in self.examples["valid"]:
            with self.subTest(case=case["id"]):
                self.validator(case["definition"]).validate(case["value"])

    def test_blackholes_missing_dormant_evidence_and_forged_server_timestamps_are_rejected(
        self,
    ):
        for case in self.examples["invalid"]:
            with self.subTest(case=case["id"]), self.assertRaises(ValidationError):
                self.validator(case["definition"]).validate(case["value"])

    def test_original_pinned_release_requires_explicit_consumer_update(self):
        # A changed enum is not silently compatible with the frozen current loader.
        old = json.loads(
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(ROOT),
                    "show",
                    "0fc51195ba98eb2552a76592d48ec1bf4693c3f7:contracts/life-runtime/v2/schemas/life.json",
                ]
            )
        )
        registry = self.registry.with_resource(old["$id"], Resource.from_contents(old))
        dormant = next(
            case
            for case in self.examples["valid"]
            if case["id"] == "dormant_projection_has_timestamp"
        )
        with self.assertRaises(ValidationError):
            self.validator("activities_record", registry).validate(dormant["value"])

    def test_candidate_manifest_lf_files_are_complete_and_current(self):
        manifest = read(PACKAGE / "manifest.json")
        for relative, expected in manifest["sha256"].items():
            with self.subTest(file=relative):
                actual = hashlib.sha256(
                    (PACKAGE / relative).read_bytes().replace(b"\r\n", b"\n")
                ).hexdigest()
                self.assertEqual(actual, expected)
        self.assertEqual(manifest["runtime_joint"], "not_run")


if __name__ == "__main__":
    unittest.main()
