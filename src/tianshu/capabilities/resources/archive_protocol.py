"""The real Archive JSON protocol; records are evidence, never current instructions."""

import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError
from ..contracts import Fault, canonical

SCHEMA = json.loads(Path(__file__).with_name("archive_read.json").read_text(encoding="utf-8"))


def validate(name, value, *, status=502):
    try:
        Draft202012Validator({"$ref": "#/$defs/" + name, "$defs": SCHEMA["$defs"]}).validate(value)
    except ValidationError:
        raise Fault("archive_protocol_invalid", status) from None


def unique_records(operation, response, requested):
    if operation == "search":
        rows = response["records"]
        if response["coverage"]["returned"] != len(rows) or len(rows) > requested["limit"]:
            raise Fault("archive_protocol_invalid", 502)
    elif response["status"] == "archive_current":
        context = response["context"]
        if (
            response["target"]["locator"] != requested["locator"]
            or response["target"]["stored_content_sha256"]
            != requested["expected_stored_content_sha256"]
            or len(context["before"]) > requested["before"]
            or len(context["after"]) > requested["after"]
        ):
            raise Fault("archive_protocol_invalid", 502)
        rows = [response["target"], *context["before"], *context["after"]]
    else:
        rows = []
    found = {}
    for row in rows:
        if row["locator"] in found and canonical(found[row["locator"]]) != canonical(row):
            raise Fault("archive_record_conflict", 502)
        found[row["locator"]] = row
    return list(found.values())


def current_records(rows, check):
    proofs = {row["locator"]: row for row in check["results"]}
    if len(proofs) != len(check["results"]) or set(proofs) != {row["locator"] for row in rows}:
        raise Fault("archive_check_incomplete", 502)
    # The content digest is Archive-defined and opaque. Never substitute a
    # locally computed raw-text hash or call HTTP200 currentness evidence.
    return [
        row
        for row in rows
        if proofs[row["locator"]]["status"] == "archive_current"
        and proofs[row["locator"]].get("current_stored_content_sha256")
        == row["stored_content_sha256"]
    ]
