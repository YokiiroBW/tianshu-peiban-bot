import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2] / "contracts/provider-self-service/v1"


class ContractTests(unittest.TestCase):
    def test_all_examples_and_private_public_boundary(self):
        schema = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        examples = json.loads((ROOT / "examples.json").read_text(encoding="utf-8"))
        for example in examples:
            validator = Draft202012Validator(
                {"$ref": "#/$defs/" + example["schema"], "$defs": schema["$defs"]},
                format_checker=FormatChecker(),
            )
            validator.validate(example["document"])
        public = {"$ref": "#/$defs/provider", "$defs": schema["$defs"]}
        provider = next(e["document"] for e in examples if e["schema"] == "provider")
        with self.assertRaises(Exception):
            Draft202012Validator(public).validate({**provider, "api_key": "forbidden"})
        private = {"$ref": "#/$defs/runtime_request", "$defs": schema["$defs"]}
        runtime = next(e["document"] for e in examples if e["schema"] == "runtime_request")
        with self.assertRaises(Exception):
            Draft202012Validator(private).validate({**runtime, "provider_id": "injected"})


if __name__ == "__main__":
    unittest.main()
