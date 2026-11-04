"""Compile the small model proposal into the existing transactional contract."""

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from tianshu.runtime.errors import AppError
from .extraction_contract import MODEL_PROTOCOL as MODEL_PROTOCOL, SCHEMA_VERSION, _object, _text, consolidation_schema, validate_proposal
from .proposal_diagnostics import proposal_error, record_context_error, schema_error

COMPILER_VERSION = "memory.alias-compiler.v1"


def model_schema(frozen):
    """Share semantic payloads; the server supplies batch, CAS and coverage fields."""
    formal = consolidation_schema(frozen)
    definitions = deepcopy(formal["$defs"])
    complete_records = sorted(ref for ref, item in frozen["records"].items() if not item.get("content_omitted"))
    definitions["record"] = {"enum": complete_records} if complete_records else {"not": {}}
    definitions["context_record"] = {"enum": sorted(frozen["records"])} if frozen["records"] else {"not": {}}
    definitions["record_target"] = _object({"ref": {"$ref": "#/$defs/record"}})
    # A bare nN is local to the enclosing target. Cross-target uses must name
    # the declaring subject explicitly; creation itself remains a local nN.
    definitions["endpoint"] = {"anyOf": [
        {"$ref": "#/$defs/record"}, {"$ref": "#/$defs/local"},
        _object({"subject_id": {"$ref": "#/$defs/target_subject"},
                 "ref": {"$ref": "#/$defs/local"}}),
    ]}
    operation = deepcopy(formal["properties"]["operations"]["items"])
    for key in ("op_id", "subject_id"):
        operation["properties"].pop(key)
        operation["required"].remove(key)
    unresolved = deepcopy(formal["properties"]["unresolved"]["items"])
    for key in ("issue_id", "subject_id"):
        unresolved["properties"].pop(key)
        unresolved["required"].remove(key)
    reason = {"reason": _text(1000)}
    context_request = {"oneOf": [
        _object({"kind": {"const": "material"}, "ref": {"$ref": "#/$defs/material"}, **reason}),
        _object({"kind": {"const": "record"}, "ref": {"$ref": "#/$defs/context_record"}, **reason}),
        _object({"kind": {"const": "conversation_range"},
                 "start": {"type": "string", "format": "date-time"},
                 "end": {"type": "string", "format": "date-time"}, **reason}),
        _object({"kind": {"const": "identity"}, "query": _text(512), **reason}),
    ]}
    decision = _object({"subject_id": {"$ref": "#/$defs/target_subject"},
        "status": {"enum": ["changed", "no_change", "unresolved"]},
        "operations": {"type": "array", "maxItems": 64, "items": operation},
        "unresolved": {"type": "array", "maxItems": 32, "items": unresolved},
        "reason": _text(1500)}, required=["subject_id", "status", "operations", "unresolved"])
    # A changed/unresolved result can use its operation/issue reason. A bare
    # no-change decision has no such evidence and must supply its own reason.
    decision["allOf"] = [{"if": {"properties": {"status": {"const": "no_change"}}},
                          "then": {"required": ["reason"]}}]
    schema = _object({
        "targets": {"type": "array", "minItems": len(frozen["targets"]), "maxItems": len(frozen["targets"]),
            "items": decision},
        "needs_context": {"type": "array", "maxItems": 4, "items": context_request},
    })
    schema["$defs"] = definitions
    return schema


def _record_uses(operation, path):
    target, payload = operation.get("target"), operation.get("payload")
    if isinstance(target, dict) and isinstance(target.get("ref"), str):
        yield [*path, "target", "ref"], target["ref"]
    if isinstance(operation.get("reads"), list):
        for index, ref in enumerate(operation["reads"]):
            if isinstance(ref, str):
                yield [*path, "reads", index], ref
    if isinstance(payload, dict):
        for key in ("from_ref", "to_ref"):
            if isinstance(payload.get(key), str):
                yield [*path, "payload", key], payload[key]
        if isinstance(payload.get("counterexample_refs"), list):
            for index, ref in enumerate(payload["counterexample_refs"]):
                if isinstance(ref, str):
                    yield [*path, "payload", "counterexample_refs", index], ref


def compile_proposal(frozen, proposal):
    """No completion or inferred no_change: every target decision is model-authored."""
    groups = proposal.get("targets") if isinstance(proposal, dict) else None
    if isinstance(groups, list):
        seen = set()
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            subject = group.get("subject_id")
            if isinstance(subject, str):
                if subject in seen:
                    raise proposal_error("memory_consolidation_target_coverage", ["targets", index, "subject_id"],
                        actual=subject, expected="each frozen target exactly once", allowed=sorted(frozen["targets"]))
                seen.add(subject)
            if isinstance(group.get("operations"), list):
                for operation_index, operation in enumerate(group["operations"]):
                    if not isinstance(operation, dict):
                        continue
                    path = ["targets", index, "operations", operation_index]
                    uses = list(_record_uses(operation, path))
                    omitted = [(at, ref) for at, ref in uses if frozen["records"].get(ref, {}).get("content_omitted")]
                    if omitted:
                        raise record_context_error(omitted[0][0], [ref for _, ref in omitted])
        missing, extra = set(frozen["targets"]) - seen, seen - set(frozen["targets"])
        if missing or extra:
            raise proposal_error("memory_consolidation_target_coverage", ["targets"],
                actual=sorted(seen), missing=sorted(missing), extra=sorted(extra),
                expected="each frozen target exactly once")
    errors = sorted(Draft202012Validator(model_schema(frozen), format_checker=FormatChecker()).iter_errors(proposal),
                    key=lambda error: str(list(error.absolute_path)))
    if errors:
        raise schema_error(errors[0])

    # Register all declarations before resolving any reference. In particular,
    # a relation may precede its endpoint and two subjects may both use n1.
    declarations = {}
    for group_index, group in enumerate(proposal["targets"]):
        subject = group["subject_id"]
        for operation_index, operation in enumerate(group["operations"]):
            path = ["targets", group_index, "operations", operation_index]
            locations = []
            if "create" in operation["target"]:
                locations.append((operation["target"]["create"], [*path, "target", "create"]))
            if operation["kind"] == "revise_claim" and operation["payload"].get("revision_mode") == "transition":
                locations.append((operation["payload"]["successor_ref"], [*path, "payload", "successor_ref"]))
            for alias, location in locations:
                key = (subject, alias)
                if key in declarations:
                    raise proposal_error("memory_consolidation_duplicate_create", location,
                        actual=alias, expected="unique alias within the subject",
                        first_path=declarations[key], second_path=location, subject_id=subject)
                declarations[key] = location
    aliases = {key: "n" + str(index + 1) for index, key in enumerate(sorted(declarations))}
    alias_map = [{"subject_id": subject, "local_ref": alias, "canonical_ref": canonical,
                  "declaration_path": declarations[(subject, alias)]}
                 for (subject, alias), canonical in aliases.items()]

    def resolve(value, subject, path):
        if isinstance(value, dict):
            owner, alias = value["subject_id"], value["ref"]
        elif value in frozen["records"] or value in frozen["materials"]:
            return value
        else:
            owner, alias = subject, value
        key = (owner, alias)
        if key not in aliases:
            other = [{"subject_id": candidate[0], "path": declarations[candidate]}
                     for candidate in sorted(declarations) if candidate[1] == alias]
            raise proposal_error("memory_consolidation_unknown_endpoint", path,
                actual=value, expected="local declaration or explicit subject-qualified reference",
                candidates=other)
        return aliases[key]

    compiled = {"schema_version": SCHEMA_VERSION, "batch_id": frozen["batch_id"],
                "target_results": [], "operations": [], "unresolved": [], "needs_context": []}
    operation_paths, unresolved_paths = [], []
    for group_index, group in enumerate(proposal["targets"]):
        subject, operation_ids = group["subject_id"], []
        for operation_index, source in enumerate(group["operations"]):
            operation = deepcopy(source)
            operation["op_id"] = "op" + str(len(compiled["operations"]) + 1)
            operation["subject_id"] = subject
            path = ["targets", group_index, "operations", operation_index]
            if "create" in operation["target"]:
                operation["target"]["create"] = aliases[(subject, operation["target"]["create"])]
            payload = operation["payload"]
            if operation["kind"] == "revise_claim" and payload.get("revision_mode") == "transition":
                payload["successor_ref"] = aliases[(subject, payload["successor_ref"])]
            if operation["kind"] == "link":
                for key in ("from_ref", "to_ref"):
                    payload[key] = resolve(payload[key], subject, [*path, "payload", key])
            if operation["kind"] == "revise_hypothesis":
                payload["counterexample_refs"] = [resolve(ref, subject,
                    [*path, "payload", "counterexample_refs", index])
                    for index, ref in enumerate(payload["counterexample_refs"])]
            if "ref" in operation["target"]:
                ref = operation["target"]["ref"]
                operation["target"]["expected_revision"] = frozen["records"][ref]["revision"]
            # Explicit targets/endpoints determine real reads. Preserve every
            # model-declared semantic dependency alongside these fixed reads.
            deterministic = [ref for _, ref in _record_uses(operation, ()) if ref in frozen["records"]]
            operation["reads"] = list(dict.fromkeys([*operation["reads"], *deterministic]))
            operation_ids.append(operation["op_id"])
            compiled["operations"].append(operation)
            operation_paths.append(["targets", group_index, "operations", operation_index])
        compiled["target_results"].append({"subject_id": subject, "status": group["status"],
            "processed_material_ids": list(frozen["targets"][subject]["new_material_ids"]),
            "operation_ids": operation_ids, "reason": group.get("reason") or
                "; ".join(item["reason"] for item in group["operations"] or group["unresolved"])[:1500]})
        for issue_index, issue in enumerate(group["unresolved"]):
            compiled["unresolved"].append({**deepcopy(issue), "subject_id": subject,
                "issue_id": "issue" + str(len(compiled["unresolved"]) + 1)})
            unresolved_paths.append(["targets", group_index, "unresolved", issue_index])
    for index, request in enumerate(proposal["needs_context"]):
        compiled["needs_context"].append({"request_id": "context" + str(index+1),
            "ref": None, "start": None, "end": None, "query": None, **deepcopy(request)})
    try:
        return validate_proposal(frozen, compiled), alias_map
    except AppError as error:
        # Diagnostics must point at the JSON the model actually produced.
        if not error.details:
            raise
        path = error.details.get("path", [])
        if len(path) >= 2 and type(path[1]) is int:
            if path[0] == "operations" and path[1] < len(operation_paths):
                error.details["path"] = [*operation_paths[path[1]], *path[2:]]
            elif path[0] == "target_results":
                field = "operations" if len(path) > 2 and path[2] == "operation_ids" else None
                error.details["path"] = ["targets", path[1], *([field] if field else path[2:])]
            elif path[0] == "unresolved" and path[1] < len(unresolved_paths):
                error.details["path"] = [*unresolved_paths[path[1]], *path[2:]]
        raise


def parse_proposal(frozen, proposal):
    return compile_proposal(frozen, proposal)[0]
