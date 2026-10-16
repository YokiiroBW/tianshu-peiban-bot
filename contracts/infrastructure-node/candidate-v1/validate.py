"""Validate this candidate's schemas and declared positive/negative examples."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def validate() -> int:
    root = Path(__file__).resolve().parent
    validators = {}
    for name in ("request", "response", "config"):
        schema = json.loads((root / f"{name}.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validators[name] = Draft202012Validator(schema, format_checker=FormatChecker())
    cases = json.loads((root / "examples/index.json").read_text(encoding="utf-8"))
    failures = []
    for case in cases:
        value = json.loads((root / case["path"]).read_text(encoding="utf-8"))
        errors = list(validators[case["schema"]].iter_errors(value))
        if bool(errors) == case["valid"]:
            failures.append(case["path"])
            print(f"FAIL {case['path']}: " + (errors[0].message if errors else "accepted negative example"))
    print(f"schemas=3 examples={len(cases)} passed={len(cases) - len(failures)} failed={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(validate())
