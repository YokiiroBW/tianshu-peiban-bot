"""Validate the candidate's wire documents, semantic invariants and file hashes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

HERE = Path(__file__).resolve().parent


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def normalized(path):
    return path.read_bytes().replace(b"\r\n", b"\n")


def validate_document(schema, name, payload):
    document = {"$schema": schema["$schema"], "$defs": schema["$defs"],
                "$ref": f"#/$defs/{name}"}
    Draft202012Validator(document, format_checker=FormatChecker()).validate(payload)
    if name in {"source", "source_envelope"}:
        event = payload["event"]
        if sha(canonical(event)) != payload["source_digest"]:
            raise ValueError("source_digest mismatch")
        if payload["scope_version"] != event["scope_revision"]:
            raise ValueError("scope revision mismatch")
        if event["self_id"] == event["account_id"]:
            raise ValueError("self message")
        if event["conversation_id"].startswith("private:") and (
            event["conversation_id"].split(":", 1)[1] != event["account_id"]
            or event["mentioned"]
        ):
            raise ValueError("private identity mismatch")
        if datetime.fromisoformat(event["sent_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("timezone required")
    if name == "policy" and payload["mode"] != "observe_only" and payload["actor_id"] is None:
        raise ValueError("reply actor required")


def main():
    schema = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    examples = json.loads((HERE / "examples.json").read_text(encoding="utf-8"))
    count = 0
    for name, payload in examples.items():
        mapped = "policy" if name == "default_policy" else name
        validate_document(schema, mapped, payload)
        count += 1
    negatives = json.loads((HERE / "negative-examples.json").read_text(encoding="utf-8"))
    for case in negatives["cases"]:
        try:
            validate_document(schema, case["document"], case["payload"])
        except (ValueError, ValidationError):
            pass
        else:
            raise AssertionError(f"negative accepted: {case['name']}")
        count += 1
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    if manifest["documents_validated"] != count:
        raise AssertionError("manifest document count mismatch")
    for name, expected in manifest["sha256"].items():
        actual = sha(normalized(HERE / name))
        if actual != expected:
            raise AssertionError(f"sha256 mismatch: {name}")
    print(f"validated {count} positive and negative documents; manifest hashes match")


if __name__ == "__main__":
    main()
