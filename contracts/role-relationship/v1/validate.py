"""Offline published-byte, DTO and internal-envelope validation."""
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
for name, expected in manifest["sha256"].items():
    actual = hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert actual == expected, f"published bytes changed: {name}"
dto = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
http = json.loads((ROOT / "http-schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(dto)
Draft202012Validator.check_schema(http)
registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in (dto, http))
counts = {"dto_valid": 0, "dto_invalid": 0, "http_valid": 0, "http_invalid": 0}
for prefix, filename, schema in (("dto", "examples.json", dto), ("http", "http-examples.json", http)):
    examples = json.loads((ROOT / filename).read_text(encoding="utf-8"))
    for category in ("valid", "invalid"):
        for example in examples[category]:
            validator = Draft202012Validator({**schema, "$ref": "#/$defs/" + example["type"]}, registry=registry, format_checker=FormatChecker())
            value = example["value"]
            valid = validator.is_valid(value)
            if example["type"] == "ManageRequest" and valid:
                valid = value["request_id"] == value["command"]["request_id"]
            assert valid == (category == "valid"), example["name"]
            counts[prefix + "_" + category] += 1
candidate = json.loads((ROOT.parent / "candidate-v1/schema.json").read_text(encoding="utf-8"))
assert dto["$defs"] == candidate["$defs"] and dto["oneOf"] == candidate["oneOf"], "business DTO drift"
print(json.dumps({"version": manifest["version"], "status": "passed", **counts}, sort_keys=True))
