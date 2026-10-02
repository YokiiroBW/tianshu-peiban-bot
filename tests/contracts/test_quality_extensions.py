"""Compatibility evidence for the independent-life and full-scope sync additions."""

import copy
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2] / "contracts"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def rules(package):
    namespace = {}
    path = ROOT / package / "v1/rules.py"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return SimpleNamespace(**namespace)


class QualityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry()
        for package in (
            "text-dialogue",
            "profile-memory",
            "source-sync",
            "source-sync-batch",
            "life-read",
        ):
            for path in (ROOT / package / "v1/schemas").glob("*.json"):
                schema = read(path)
                cls.registry = cls.registry.with_resource(
                    schema["$id"], Resource.from_contents(schema)
                )
        cls.source = rules("source-sync")
        cls.batch = rules("source-sync-batch")

    def validate(self, package, name, definition, value):
        schema = read(ROOT / package / f"v1/schemas/{name}.json")
        Draft202012Validator(
            {"$ref": schema["$id"] + "#/$defs/" + definition},
            registry=self.registry,
            format_checker=FormatChecker(),
        ).validate(value)

    def test_manifest_files_match_fingerprints(self):
        for package in (
            "source-sync-batch",
            "life-read",
            "diagnostics",
            "model-origin-renewal",
        ):
            folder = ROOT / package / "v1"
            manifest = read(folder / "manifest.json")
            for relative, expected in manifest.get(
                "sha256", manifest.get("files", {})
            ).items():
                with self.subTest(package=package, file=relative):
                    content = (folder / relative).read_bytes()
                    if "sha256" in manifest:
                        content = content.replace(b"\r\n", b"\n")
                    actual = hashlib.sha256(content).hexdigest()
                    self.assertEqual(actual, expected)

    def test_life_examples_and_closed_payload(self):
        examples = read(ROOT / "life-read/v1/examples.json")
        for definition, document in examples.items():
            with self.subTest(definition=definition):
                self.validate("life-read", "life", definition, document)
        invalid = copy.deepcopy(examples["timeline_request"])
        invalid["reader_id"] = "untrusted"
        with self.assertRaises(ValidationError):
            self.validate("life-read", "life", "timeline_request", invalid)
        invalid = copy.deepcopy(examples["today_response"])
        invalid["plan"]["entries"][0]["minute"] = 1440
        with self.assertRaises(ValidationError):
            self.validate("life-read", "life", "today_response", invalid)

    def observation(self):
        sample = next(
            item["document"]
            for item in read(ROOT / "source-sync/v1/examples/documents.json")
            if item["id"] == "self_private/sync-input"
        )
        batches = []
        for i, selector in enumerate(sample["request"]["selectors"]):
            batch = copy.deepcopy(sample)
            batch["request"].update(request_id=f"batch-{i}", selectors=[selector])
            if i:
                batch["request"]["turn_ids"] = []
                batch["snapshot"]["turns"] = []
            batch["snapshot"].update(
                request_id=f"batch-{i}", request_digest=digest(batch["request"])
            )
            batch["snapshot"]["admissions"] = [
                item
                for item in batch["snapshot"]["admissions"]
                if item["selector"] == selector
            ]
            batch["access_request"].update(
                request_id=f"access-{i}", admissions=batch["snapshot"]["admissions"]
            )
            batch["access"].update(
                request_id=f"access-{i}", request_digest=digest(batch["access_request"])
            )
            batch["access"]["grants"] = [
                item
                for item in batch["access"]["grants"]
                if item["selector"] == selector
            ]
            batches.append(batch)
        request = copy.deepcopy(sample["access_request"])
        request.update(request_id="final-access", admissions=[])
        response = copy.deepcopy(sample["access"])
        response.update(
            request_id="final-access", request_digest=digest(request), grants=[]
        )
        return dict(
            schema_version=1,
            observations=batches,
            final_access_request=request,
            final_access=response,
            final_core_head=sample["final_head"],
        )

    def test_same_owner_batches_preserve_original_relations(self):
        observation = self.observation()
        self.validate("source-sync-batch", "batch", "barrier", observation)
        for batch in observation["observations"]:
            self.source.sync_barrier(batch)
        self.assertTrue(self.batch.batch_barrier(observation, "2026-09-14T01:03:00Z"))

    def test_mixed_owner_or_duplicate_coverage_is_rejected(self):
        for mutation in ("core", "platform", "duplicate", "turn", "correlation"):
            with self.subTest(mutation=mutation):
                observation = self.observation()
                second = observation["observations"][1]
                if mutation == "core":
                    second["snapshot"]["head"]["sequence"] += 1
                elif mutation == "platform":
                    second["access"]["head"]["sequence"] += 1
                elif mutation == "duplicate":
                    second["request"]["selectors"] = observation["observations"][0][
                        "request"
                    ]["selectors"]
                elif mutation == "turn":
                    second["request"]["turn_ids"] = ["late-turn"]
                else:
                    observation["final_access"]["request_digest"] = "0" * 64
                with self.assertRaises(self.batch.Violation):
                    self.batch.batch_barrier(observation, "2026-09-14T01:03:00Z")


if __name__ == "__main__":
    unittest.main()
