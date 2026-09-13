"""Standalone source-sync/v1 package, schema and synthetic relation validator."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

import rules

PACKAGE = Path(__file__).resolve().parent


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            rules.require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    def invalid_constant(value):
        raise rules.Violation("nonfinite_json")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant)


def file_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def checked_path(root, name):
    path = (root / name).resolve()
    rules.require(path.is_relative_to(root.resolve()), "package_path")
    return path


class Contract:
    def __init__(self, contracts_root, package=PACKAGE):
        self.package, self.contracts_root = package.resolve(), contracts_root.resolve()
        manifest = read(self.package / "manifest.json")
        rules.require(manifest["package"] == "source-sync/v1" and manifest["version"] == "1.0.0" and manifest["schema_version"] == 1, "package_version")
        actual = {p.relative_to(self.package).as_posix() for p in self.package.rglob("*") if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
        rules.require(actual == set(manifest["sha256"]), "package_inventory")
        for name, checksum in manifest["sha256"].items():
            rules.require(file_hash(checked_path(self.package, name)) == checksum, "package_hash:" + name)
        dependencies = manifest["dependencies"]
        rules.require({d["package"] for d in dependencies} == {"text-dialogue/v1", "profile-memory/v1"} and len(dependencies) == 2, "dependency_set")
        self.schemas, self.registry = {}, Registry()
        for dependency in dependencies:
            root = checked_path(self.contracts_root, dependency["package"])
            dep_manifest = root / "manifest.json"
            rules.require(file_hash(dep_manifest) == dependency["manifest_sha256"], "dependency_manifest")
            release = read(dep_manifest)
            rules.require(release["version"] == dependency["version"] == "1.0.0", "dependency_version")
            base = self.contracts_root if dependency["package"] == "text-dialogue/v1" else root
            for name, checksum in release["sha256"].items():
                target = checked_path(base, name)
                rules.require(target.is_relative_to(root) and file_hash(target) == checksum, "dependency_hash:" + name)
            prefix = "text-" if dependency["package"] == "text-dialogue/v1" else "profile-"
            for path in sorted((root / "schemas").glob("*.json")):
                self.add_schema(prefix + path.stem, path)
        for path in sorted((self.package / "schemas").glob("*.json")):
            schema = read(path)
            rules.require(schema["$id"] == "https://contracts.tianshu.invalid/source-sync/v1/" + path.name, "schema_id")
            self.add_schema(path.stem, path)
        def refs(node, resolver):
            if isinstance(node, dict):
                if "$ref" in node:
                    resolver.lookup(node["$ref"])
                for child in node.values():
                    refs(child, resolver)
            elif isinstance(node, list):
                for child in node:
                    refs(child, resolver)
        for schema in self.schemas.values():
            refs(schema, self.registry.resolver(schema["$id"]))
        rules.require({"date-time", "uri"} <= set(FormatChecker.checkers), "format_dependencies")
        catalog = read(self.package / "interfaces.json")
        rules.require(catalog["package"] == "source-sync/v1" and catalog["version"] == "1.0.0" and catalog["issuer"] == "platform", "catalog_version")
        ids, routes = set(), set()
        for interface in catalog["interfaces"]:
            rules.require(interface["id"] not in ids, "duplicate_interface")
            ids.add(interface["id"])
            for field in ("request", "response"):
                self.schema(interface[field])
            if interface["boundary"] == "https":
                route = (interface["method"], interface["path"], interface.get("operation"))
                rules.require(route not in routes, "duplicate_route")
                routes.add(route)
        self.manifest, self.catalog = manifest, catalog

    def add_schema(self, name, path):
        schema = read(path)
        Draft202012Validator.check_schema(schema)
        self.schemas[name] = schema
        self.registry = self.registry.with_resource(schema["$id"], Resource.from_contents(schema))

    def schema(self, selector):
        module, name = selector.split("#")
        rules.require(module in self.schemas and name in self.schemas[module]["$defs"], "unknown_schema:" + selector)
        return Draft202012Validator({"$ref": self.schemas[module]["$id"] + "#/$defs/" + name}, registry=self.registry, format_checker=FormatChecker())


def run_relation(name, values):
    if name == "input":
        rules.input_authority(values["request"], values["authority"], values["now"])
    elif name == "mapping":
        rules.fanout_mapping(values["request"], values["response"], values.get("identity"), values["input_request"], values["authority"], values["now"], values.get("frozen_route"), values.get("ingress_service", "platform"))
    elif name == "snapshot":
        rules.source_snapshot(values["request"], values["response"])
    elif name == "access":
        rules.current_access(values["request"], values["response"], values["snapshot"], values.get("now"))
    elif name == "event":
        rules.actor_event(values["event"], values["turn"], values["snapshot"])
    elif name == "check":
        rules.background_check(values["request"], values["response"], values["snapshot"])
    elif name == "confirmation":
        rules.confirmation(values["proof"], values["request"], values["context"], values["binding_version"], values["now"])
    elif name == "barrier":
        rules.sync_barrier(values["observation"], values.get("previous_heads"))
    else:
        raise rules.Violation("unknown_relation:" + name)


def mutations(values, changes):
    for change in changes:
        target = values
        for key in change["path"][:-1]:
            target = target[key]
        if change.get("delete"):
            del target[change["path"][-1]]
        else:
            target[change["path"][-1]] = copy.deepcopy(change["value"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts-root", type=Path, default=PACKAGE.parents[1])
    args = parser.parse_args()
    contract = Contract(args.contracts_root)
    documents = read(PACKAGE / "examples/documents.json")
    by_id = {d["id"]: d for d in documents}
    rules.require(len(by_id) == len(documents), "duplicate_document")
    for item in documents:
        contract.schema(item["schema"]).validate(item["document"])
    required = {entry[field] for entry in contract.catalog["interfaces"] if not entry["existing"] for field in ("request", "response")}
    rules.require(required <= {d["schema"] for d in documents}, "missing_interface_example")
    negatives = read(PACKAGE / "examples/negative-documents.json")
    for case in negatives:
        value = copy.deepcopy(case.get("document", by_id.get(case.get("base"), {}).get("document")))
        mutations(value, case.get("mutations", []))
        errors = list(contract.schema(case["schema"]).iter_errors(value))
        def keywords(errors):
            return {e.validator for e in errors} | {k for e in errors for k in keywords(e.context)}
        rules.require(case["expected_keyword"] in keywords(errors), "schema_negative:" + case["id"])
    relations = read(PACKAGE / "examples/relations.json")
    for case in relations:
        values = copy.deepcopy(case.get("data", {}))
        values.update({name: copy.deepcopy(by_id[id]["document"]) for name, id in case["documents"].items()})
        mutations(values, case.get("mutations", []))
        for name, id in case["documents"].items():
            contract.schema(by_id[id]["schema"]).validate(values[name])
        actual = None
        try:
            run_relation(case["check"], values)
        except rules.Violation as error:
            actual = str(error)
        rules.require(actual == case.get("expected_error"), "relation:" + case["id"] + ":" + str(actual))
    print(f"PASS: {len(documents)} positive documents, {len(negatives)} schema negatives, {len(relations)} relations; package/dependency hashes and local schema references. Offline only, not published or product/L0 validation.")


if __name__ == "__main__":
    main()
