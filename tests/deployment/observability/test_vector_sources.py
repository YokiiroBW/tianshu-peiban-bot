"""Dedicated Knowledge collection uses the same closed Memory event vocabulary."""

import copy
import json
import re
import subprocess
import sys
import unittest

from helpers import PACKAGE, ROOT, SNAPSHOT
from configs import PRODUCTS, vector


class VectorSourceTests(unittest.TestCase):
    def test_legacy_default_remains_four_sources(self):
        config = vector(SNAPSHOT)
        self.assertEqual(tuple(config["transforms"]), tuple(p + "_safe" for p in PRODUCTS))
        self.assertNotIn("knowledge", config["sources"])
        self.assertEqual(config["sinks"]["loki"]["inputs"], list(config["transforms"]))
        self.assertIn('["memory", "memory-knowledge"]', config["transforms"]["memory_safe"]["source"])

    def test_optional_source_preserves_every_legacy_setting_and_custom_buffer(self):
        budget = 2 * 1024**3
        legacy = vector(SNAPSHOT, budget)
        expanded = vector(SNAPSHOT, budget, include_knowledge=True)
        self.assertEqual(tuple(expanded["transforms"]),
                         ("platform_safe", "companion_safe", "memory_safe", "knowledge_safe", "gateway_safe"))
        reduced = copy.deepcopy(expanded)
        del reduced["sources"]["knowledge"]
        del reduced["transforms"]["knowledge_safe"]
        reduced["sinks"]["loki"]["inputs"].remove("knowledge_safe")
        self.assertEqual(reduced, legacy)
        self.assertEqual(expanded["sources"]["knowledge"]["include"],
                         ["/sources/knowledge/*.jsonl", "/sources/knowledge/*.jsonl.[0-9]*"])
        self.assertEqual(expanded["transforms"]["knowledge_safe"]["inputs"], ["knowledge"])
        self.assertEqual(expanded["sinks"]["loki"]["buffer"],
                         {"type": "disk", "max_size": budget, "when_full": "block"})
        self.assertEqual(expanded["sinks"]["loki"]["labels"]["service"], "tianshu_{{ service }}")

    def test_knowledge_is_only_memory_knowledge_with_closed_memory_vocabulary(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        self.assertNotIn("knowledge", snapshot["products"])
        registry = snapshot["products"]["memory"]
        registry["events"] = ["knowledge-only.synthetic-event"]
        registry["error_codes"] = ["knowledge-only-synthetic-error"]
        code = vector(snapshot, include_knowledge=True)["transforms"]["knowledge_safe"]["source"]
        allowed = re.search(r'assert!\(includes\((\[[^\n]+?\]), \.service\)', code)
        self.assertEqual(json.loads(allowed.group(1)), ["memory-knowledge"])
        self.assertIn(f'includes({json.dumps(registry["events"])}, .event)', code)
        self.assertIn(f'includes({json.dumps(registry["error_codes"])}, .error_code)', code)
        self.assertIn('length(keys(.)) == 12', code)
        self.assertIn('length(raw) <= 4095', code)
        self.assertIn('.schema_version == "1.0.0"', code)

    def test_package_and_top_level_script_imports_match_without_pythonpath(self):
        # Isolated interpreters cannot accidentally resolve policy through the
        # test helper's sys.path. Neither mode needs an installed package.
        outputs = []
        for root, statement in (
            (ROOT, "from deploy.observability import configs"),
            (PACKAGE, "import configs"),
        ):
            script = (
                "import json,pathlib,sys;sys.path.insert(0,sys.argv[1]);"
                + statement
                + ";snapshot=json.loads(pathlib.Path(sys.argv[2]).read_bytes());"
                + "print(json.dumps(configs.vector(snapshot,include_knowledge=True),sort_keys=True))"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-c", script, str(root), str(PACKAGE / "vocabulary.json")],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(json.loads(result.stdout))
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
