"""Standalone profile contract checks; no product package or running service required."""
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parent
CONTRACTS = PACKAGE.parents[1]
sys.path.insert(0, str(CONTRACTS / ".deps"))
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def require(value, reason):
    if not value:
        raise ValueError(reason)


def relation(request, response):
    require(response["request_id"] == request["query"]["request_id"], "correlation")
    require(response["requester_scope"] == request["requester_scope"], "requester")
    require(response["target"] == request["target"], "target")
    if request["target"]["kind"] == "group":
        require(request["requester_scope"]["audience"] == "group" and
                request["target"]["conversation_id"] == request["requester_scope"]["conversation_id"], "group_scope")
    require(datetime.fromisoformat(response["verified_at"].replace("Z", "+00:00")) <
            datetime.fromisoformat(response["valid_until"].replace("Z", "+00:00")), "validity")
    if request["known_scope_version"] is not None:
        require(response["scope_version"] == request["known_scope_version"], "version")
    for dimension in ("tokens", "bytes"):
        require(response["budget_used"][dimension] <= request["budget"][dimension], "budget")
    units = response["selected_units"]
    require(len({u["record_id"] for u in units}) == len(units), "duplicate_unit")
    for unit in units:
        require(unit["subject"] == request["target"], "subject")
        require(unit["category"] in request["selection"], "selection")
        if unit["sharing"] == "group_only":
            require(request["requester_scope"]["audience"] == "group", "private_group_only")
    groups = response["dependency_groups"]
    require(len({g["semantic_group_id"] for g in groups}) == len(groups), "duplicate_group")
    covered = set()
    for group in groups:
        members = {u["record_id"] for u in units if u["semantic_group_id"] == group["semantic_group_id"]}
        require(group["complete"] and members and members == set(group["record_ids"]) and
                len(members) == len(group["record_ids"]), "incomplete_group")
        covered.update(members)
    require(covered == {u["record_id"] for u in units}, "ungrouped_unit")
    cost = len(json.dumps({"selected_units": units, "dependency_groups": groups},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) if units else 0
    require(response["budget_used"] == {"tokens": cost, "bytes": cost}, "budget_accounting")


def main():
    manifest = read(PACKAGE / "manifest.json")
    actual = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob("*")
              if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
    require(actual == set(manifest["sha256"]), "package inventory")
    for name, checksum in manifest["sha256"].items():
        path = (PACKAGE / name).resolve()
        require(path.is_relative_to(PACKAGE) and digest(path) == checksum, "package hash")
    dependency = CONTRACTS / "text-dialogue/v1"
    require(digest(dependency / "manifest.json") == manifest["dependency"]["manifest_sha256"], "dependency manifest")
    for name, checksum in read(dependency / "manifest.json")["sha256"].items():
        path = (CONTRACTS / name).resolve()
        require(path.is_relative_to(dependency) and digest(path) == checksum, "dependency hash")
    registry = Registry()
    schemas = [read(p) for p in sorted((dependency / "schemas").glob("*.json"))]
    profile = read(PACKAGE / "schemas/profiles.json")
    for schema in schemas + [profile]:
        Draft202012Validator.check_schema(schema)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    require({"date-time", "uri"} <= set(FormatChecker.checkers), "missing format dependencies")
    cases = read(PACKAGE / "examples.json")
    require(len({c["id"] for c in cases}) == len(cases), "duplicate case")
    for case in cases:
        validator = Draft202012Validator({"$ref": profile["$id"] + "#/$defs/" + case["definition"]},
                                        registry=registry, format_checker=FormatChecker())
        require(validator.is_valid(case["document"]) == case["valid"], case["id"])
    docs = {c["id"]: c["document"] for c in cases}
    # Cross-field checks have named failing reasons, not arbitrary exception-as-success.
    request, response = copy.deepcopy(docs["A-asks-B"]), copy.deepcopy(docs["approved-public-interest"])
    relation(request, response)
    checks = [
        (lambda q, a: a.update(request_id="wrong"), "correlation"),
        (lambda q, a: a["selected_units"][0].update(subject={"kind":"person","person_id":"wrong"}), "subject"),
        (lambda q, a: q.update(selection=["style"]), "selection"),
        (lambda q, a: q.update(budget={"tokens":0,"bytes":0}), "budget"),
        (lambda q, a: a.update(dependency_groups=[]), "ungrouped_unit"),
        (lambda q, a: a["budget_used"].update(bytes=a["budget_used"]["bytes"] - 1), "budget_accounting"),
    ]
    for mutation, expected in checks:
        q, a = copy.deepcopy(request), copy.deepcopy(response)
        mutation(q, a)
        try:
            relation(q, a)
        except ValueError as error:
            require(str(error) == expected, "wrong relation failure")
        else:
            raise ValueError("missing relation rejection")
    print(f"PASS: {len(cases)} structural cases, {len(checks)+1} relation cases, package/dependency hashes; runtime authorization and L0/L1 not run.")


if __name__ == "__main__":
    main()
