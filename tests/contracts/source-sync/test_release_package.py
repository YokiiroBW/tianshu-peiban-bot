"""Release relocation/integrity and explicit equivalence to the reviewed P/A shapes."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from release_support import RELEASE

ROOT = Path(__file__).resolve().parents[3]
CANDIDATE = ROOT / "docs/development/candidates/source-sync"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class ReleasePackage(unittest.TestCase):
    def test_versioned_schemas_preserve_reviewed_shapes_with_inline_admission(self):
        old = read(CANDIDATE / "schema.json")
        actor = read(CANDIDATE / "multi-actor.schema.json")
        shared_id = "https://contracts.tianshu.invalid/source-sync/v1/shared.json"
        source_id = "https://contracts.tianshu.invalid/source-sync/v1/sources.json"
        def normalize(value):
            if isinstance(value, list):
                return ["schema_version" if item == "candidate_version" else normalize(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            for key, item in value.items():
                if key == "candidate_version":
                    result["schema_version"] = {"const": 1}
                elif key == "$ref":
                    if item.startswith(old["$id"]):
                        item = item.replace(old["$id"], shared_id)
                    elif item.startswith("#/"):
                        item = source_id + item
                    result[key] = item
                else:
                    result[key] = normalize(item)
            return result
        expected = normalize(actor["$defs"])
        admission = {"$ref": source_id + "#/$defs/admission_fact"}
        expected["actor_outcome"]["properties"]["admission"] = {"anyOf": [admission, {"type": "null"}]}
        expected["actor_outcome"]["required"].append("admission")
        expected["actor_outcome"]["allOf"][0]["then"]["properties"]["admission"] = {"type": "null"}
        expected["actor_outcome"]["allOf"][0]["else"]["properties"]["admission"] = admission
        self.assertEqual(read(RELEASE / "schemas/sources.json")["$defs"], expected)
        for path in [*sorted((RELEASE / "schemas").glob("*.json")), RELEASE / "interfaces.json", RELEASE / "rules.py", RELEASE / "validate.py", RELEASE / "examples/documents.json"]:
            with self.subTest(path=path.name):
                data = path.read_text(encoding="utf-8")
                self.assertNotIn("candidate.1", data)
                self.assertNotIn("candidate.2", data)
                self.assertNotIn("candidates.tianshu.invalid", data)

    def test_standalone_relocation_and_integrity_failures(self):
        runtime = (Path(__file__).resolve().parent / ".runtime").resolve()
        runtime.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="release-check-", dir=runtime) as directory:
            temporary = Path(directory).resolve()
            # Validate the final absolute disposable path before automatic recursive cleanup.
            self.assertTrue(temporary.is_relative_to(runtime) and temporary != runtime)
            contracts = temporary / "contracts"
            published = contracts / "source-sync/v1"
            shutil.copytree(RELEASE, published, ignore=shutil.ignore_patterns("__pycache__"))
            for name in ("text-dialogue/v1", "profile-memory/v1"):
                shutil.copytree(ROOT / "contracts" / name, contracts / name, ignore=shutil.ignore_patterns("__pycache__"))
            environment = os.environ.copy()
            # Only preinstalled validator libraries are added, not repository/test/product modules.
            environment["PYTHONPATH"] = str(Path("C:/YOKI/Codex/tianshu-peiban-bot/contracts/.deps"))
            def execute():
                return subprocess.run([sys.executable, "-B", str(published / "validate.py")], cwd=temporary, env=environment, capture_output=True, text=True, encoding="utf-8")
            result = execute()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Offline only", result.stdout)
            target = published / "semantics.md"
            original = target.read_bytes()
            target.write_bytes(original + b"\nsynthetic tamper\n")
            result = execute()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("package_hash", result.stderr)
            target.write_bytes(original)
            dependency = contracts / "text-dialogue/v1/semantics.md"
            dependency.write_bytes(dependency.read_bytes() + b"\nsynthetic tamper\n")
            result = execute()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dependency_hash", result.stderr)
