"""Offline TS-001 contract conformance; no product services or network calls.

Checks producer schemas, synthetic cross-boundary observations and pinned files.
This is not a companion scheduler, authorization implementation or gateway.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".deps"))
try:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
except ImportError as exc:
    raise SystemExit("Install contracts/requirements-validation.txt for this Python interpreter") from exc

PACKAGE = ROOT / "text-dialogue" / "v1"
TERMINAL = {"sent", "failed", "cancelled", "observed", "closed_unknown"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    # All pinned files are UTF-8 text; checkout line endings must not change identity.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def require(condition, code):
    if not condition:
        raise Violation(code)


class Violation(Exception):
    pass


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def check_bundle(d):
    b = d["bundle"]
    seq = [m["ingest_sequence"] for m in b["messages"]]
    require(seq == sorted(set(seq)), "message_order")
    require(len({key(m["message_key"]) for m in b["messages"]}) == len(seq), "duplicate_message")
    for m in b["messages"]:
        require(m["author"] == b["collection_key"]["author"], "collection_author")
        require(m["message_key"]["channel"] == b["collection_key"]["channel"], "collection_channel")
        require(m["message_key"] == m["source"]["message_key"], "source_revision")
        require(stamp(m["accepted_at"]) <= stamp(b["sealed_at"]), "seal_time")
    if b["close_reason"] in {"resource_limit", "max_wait"}:
        require(b["possibly_incomplete"], "incomplete_input")


def check_ingest(d):
    request, response, context = d["request"], d["response"], d["context"]
    require(request["author"] == context["verified_account"], "origin_account")
    require(request["command"]["origin"]["assertion_ref"] == context["assertion_ref"], "origin_reference")
    require(request["author"]["namespace"] == request["message_key"]["channel"]["namespace"], "channel_namespace")
    require(context["audience_service"] == "companion", "origin_audience")
    require(stamp(response["accepted_at"]) < stamp(context["expires_at"]), "origin_expired")
    require(response["collection_key"] == {"channel": request["message_key"]["channel"], "author": request["author"]}, "collection_key")
    identity, conversation = d["identity_binding"], d["conversation_binding"]
    require(context["verified_channel"] == request["message_key"]["channel"] == conversation["channel"], "conversation_mapping")
    require(response["conversation_id"] == conversation["conversation_id"], "conversation_mapping")
    require(identity["account"] == request["author"] and response["person_id"] == identity["person_id"], "person_mapping")
    for field, current in (("person_id", identity["person_id"]), ("conversation_id", conversation["conversation_id"])):
        require(context["allowed_scope"][field] in {None, current}, "stale_origin_binding")
    if "registration" in d:
        require(d["resolution"]["state"] == "unregistered" and d["resolution_request"]["account"] == identity["account"], "registration_resolution")
        require(d["registration_request"]["account"] == identity["account"] == d["memory_context"]["verified_account"], "registration_account")
        require(d["memory_context"]["audience_service"] == "memory", "origin_audience")
        require(d["registration"]["person_id"] == identity["person_id"] and d["registration"]["binding_version"] == identity["binding_version"], "registration_binding")


def check_memory(d):
    request, response = d["request"], d["response"]
    require(d["context"]["audience_service"] == "memory", "origin_audience")
    require(response["effective_scope"] == request["requested_scope"] == d["context"]["allowed_scope"], "scope_expansion")
    require(d["requester_binding"]["account"] == d["context"]["verified_account"], "requester_binding")
    require(d["requester_binding"]["person_id"] == request["requested_scope"]["person_id"], "cross_subject_not_supported")
    require(response["scope_version"] == d["current_scope_version"], "stale_scope")
    require(request["known_scope_version"] in {None, response["scope_version"]}, "scope_changed")
    require(stamp(response["verified_at"]) < stamp(response["valid_until"]), "memory_validity")
    for field in ("tokens", "bytes"):
        require(response["budget_used"][field] <= request["budget"][field], "budget_exceeded")
    units = {u["record_id"]: u for u in response["selected_units"]}
    require(len(units) == len(response["selected_units"]), "duplicate_record")
    grouped = []
    for group in response["dependency_groups"]:
        ids = group["record_ids"]
        require(len(ids) == len(set(ids)), "duplicate_group_member")
        require(set(ids) == {rid for rid, u in units.items() if u["semantic_group_id"] == group["semantic_group_id"]}, "split_semantic_group")
        require(ids == d["authoritative_groups"][group["semantic_group_id"]], "split_semantic_group")
        grouped.extend(ids)
    require(sorted(grouped) == sorted(units), "ungrouped_record")
    for rid, unit in units.items():
        current = d["authoritative_records"][rid]
        require(not current["tombstoned"] and unit["record_version"] == current["version"], "stale_record")
        require(unit["statement"] == current["statement"] and unit["negations"] == current["negations"] and unit["conditions"] == current["conditions"], "semantic_loss")
        require(unit["subject_person_id"] == response["effective_scope"]["person_id"], "wrong_subject")
        if response["effective_scope"]["audience"] == "group":
            require(unit["visibility"] == "shared_projection", "private_memory")
            require(all(s["kind"] == "shareable_projection" for s in unit["sources"]), "private_source")
            for source in unit["sources"]:
                require(source["projection_ref"] in d["authorized_projection_refs"], "private_source")
                projection = d["projection_catalog"][source["projection_ref"]]
                require(source["projection_version"] == projection["version"], "stale_projection")


def check_identity(d):
    if d["operation"] == "register":
        require(d["context"]["audience_service"] == "memory", "origin_audience")
        require(d["request"]["account"] == d["context"]["verified_account"], "origin_account")
        require(d["response"]["person_id"] == d["registry"][key(d["request"]["account"])], "identity_mapping")
    else:
        req, proof = d["request"], d["proof"]
        require(req["verification_ref"] == proof["id"] and not proof["expired"] and not proof["consumed"], "invalid_link_proof")
        require([req["source_account"], req["target_account"]] == proof["accounts"], "proof_accounts")
        require([req["source_binding_version"], req["target_binding_version"]] == proof["binding_versions"], "binding_version")


def check_commit(d):
    event, receipt = d["event"], d["receipt"]
    require((receipt["event_id"], receipt["turn_id"], receipt["input_revision"]) == (event["event_id"], event["aggregate_id"], event["input_revision"]), "commit_correlation")
    seen = [event["aggregate_id"], event["input_revision"]] in d["consumed_inputs"]
    if seen:
        require(receipt["state"] == "duplicate" and d["new_relation_increments"] == 0, "duplicate_memory_write")
    elif d["current_input_revision"] != event["input_revision"]:
        require(receipt["state"] == "stale_source" and receipt["candidate_job_ref"] is None, "stale_source")
    else:
        require(receipt["state"] == "accepted" and receipt["candidate_job_ref"] is not None, "candidate_receipt")


def check_capacity(d):
    require(d["active"] <= 2, "third_active_turn")
    fits = (d["queued"] + d["reserved_collectors"] + 1 <= d["policy"]["max_queued_turns"]
            and d["reserved_collectors"] + 1 <= d["policy"]["max_collectors_per_conversation"])
    require(d["new_collector_accepted"] == fits, "capacity_admission")
    require(d["persisted_receipts_after"] >= d["persisted_receipts_before"], "accepted_input_lost")
    if not fits:
        require(d["response_code"] == "queue_full" and not d["success_receipt"], "queue_full_receipt")


def check_idempotency(d):
    old, new = copy.deepcopy(d["original"]), copy.deepcopy(d["retry"])
    for value in (old, new):
        for field in ("request_id", "deadline_at", "origin"):
            value["command"].pop(field)
    require(d["authorized_again"], "retry_authorization")
    if old != new:
        require(d["result"] == "idempotency_conflict" and d["execution_count"] == 1, "idempotency_conflict")
    else:
        require(d["result"] == "original_result" and d["execution_count"] == 1, "duplicate_execution")


def check_cancel(d):
    req, response = d["request"], d["response"]
    require(req["expected_version"] == d["current_version"], "cancel_version")
    require(response["already_sent_reply_ids"] == d["known_sent"], "cancel_erases_sent")
    require(response["unresolved_reply_ids"] == d["known_unknown"], "cancel_erases_unknown")
    if d["known_sent"] or d["known_unknown"]:
        require(response["state"] in {"partially_cancelled", "too_late", "unknown"}, "cancel_claims_complete")


def check_revision(d):
    require(d["response"]["record_version"] > d["request"]["expected_version"], "revision_version")
    if d["response"]["semantic_state"] == "invalidated":
        require(not d["recalled_units"], "old_semantics_after_revision")
    else:
        require(d["recalled_units"] == d["rebuilt_units"], "old_semantics_after_revision")


def check_gateway(d):
    config, route, incoming, outgoing = d["config"], d["receipt"], d["incoming"], d["outgoing"]
    require(config["config_version"] == d["pinned_version"], "config_version")
    require(stamp(d["started_at"]) < stamp(config["usable_until"]) and not d["revoked"], "config_expired_or_revoked")
    providers = {p["provider_id"]: p for p in config["providers"]}
    require(len(providers) == len(config["providers"]), "duplicate_provider")
    for binding in config["bindings"]:
        require(binding["provider_id"] in providers, "unknown_provider")
    p = providers[route["provider_id"] if route is not None else d["provider_id"]]
    if "model" in incoming and incoming["model"] is None:
        require(outgoing is None and route is None and d.get("error", {}).get("code") == "invalid_input", "unresolved_model_sent")
        return
    expected, applied = copy.deepcopy(incoming), []
    for policy_name in ("model_policy", "reasoning_policy"):
        policy = p[policy_name]
        for field, value in policy["fields"].items():
            if policy["mode"] == "force" or (policy["mode"] == "default_if_absent" and field not in incoming):
                expected[field] = value
                applied.append({"field": field, "mode": policy["mode"], "config_version": config["config_version"]})
    if "model" not in incoming and "model" not in expected and d.get("internal_binding"):
        binding = next(b for b in config["bindings"] if b["workload"] == "companion.text")
        expected["model"] = binding["model_id"]
        applied.append({"field": "model", "mode": "workload_binding", "config_version": config["config_version"]})
    if not isinstance(expected.get("model"), str) or not expected["model"]:
        require(outgoing is None and route is None and d.get("error", {}).get("code") == "invalid_input", "unresolved_model_sent")
        return
    require(route is not None and route["config_version"] == config["config_version"], "config_version")
    require(route["credential_namespace"] == p["credential_namespace"], "credential_namespace")
    require(outgoing == expected, "native_field_loss_or_override")
    require(route["requested_model"] == incoming.get("model") and route["resolved_model"] == outgoing["model"], "model_receipt")
    require(route["requested_reasoning"] == {k: v for k, v in incoming.items() if k == "reasoning_effort"}, "reasoning_receipt")
    require(route["effective_reasoning"] == {k: v for k, v in outgoing.items() if k == "reasoning_effort"}, "reasoning_receipt")
    require(route["applied_policies"] == applied, "policy_receipt")
    require(route["native_usage"] == d["upstream_usage"], "usage_fabricated")
    if d["upstream_usage"] is None:
        require(route["usage"] is None and not route["usage_complete"], "usage_fabricated")
    if d["reference_binding"] is not None:
        actual = [route[x] for x in ("caller_service", "provider_id", "credential_namespace", "config_version", "resolved_model")]
        require(actual == d["reference_binding"], "state_reference_binding")


def check_web(d):
    snapshot, events = d["snapshot"], d["events"]
    require(d["generation_calls_after_reconnect"] == 0 and d["channel_send_calls_after_reconnect"] == 0, "reconnect_side_effect")
    version, seen = snapshot["object_version"], set()
    for event in events:
        require(event["aggregate_id"] == snapshot["conversation_id"], "projection_conversation")
        if event["event_id"] in seen:
            continue
        seen.add(event["event_id"])
        if event["aggregate_version"] <= version:
            continue
        if event["change"] == "turn_changed":
            require(event["reply"] is None and d["snapshot_reloaded"] and d.get("readonly_snapshot_fetches", 0) > 0, "turn_invalidation_snapshot")
        require(event["aggregate_version"] == version + 1 or d["snapshot_reloaded"], "projection_gap")
        version = event["aggregate_version"]
    if d["cursor_expired"]:
        require(d["snapshot_reloaded"], "expired_cursor")


def check_stream(d):
    frames = [line[6:] for line in d["wire"].splitlines() if line.startswith("data: ")]
    require(bool(frames), "empty_stream")
    complete = frames[-1] == "[DONE]"
    require(frames.count("[DONE]") == (1 if complete else 0), "stream_terminal_order")
    chunks = [json.loads(frame) for frame in frames if frame != "[DONE]"]
    require(all(isinstance(chunk, dict) for chunk in chunks), "stream_frame_shape")
    require(d["receipt_outcome"] == ("succeeded" if complete else "unknown"), "stream_unknown_result")
    require(d["restart_upstream_calls"] == 0, "stream_blind_retry")


def check_trace(d):
    """Validate an observed synthetic trace; does not schedule or execute work."""
    inputs, sealed, active, closed, sent = {}, set(), set(), {}, set()
    send_times = {}
    collectors, turns, times = {}, {}, []
    for e in d["events"]:
        times.append(e["at_ms"])
        require(times == sorted(times), "trace_time")
        kind, now = e["kind"], e["at_ms"]
        if kind in {"input", "duplicate"}:
            mid, collection = e["message_id"], e["collection"]
            if kind == "duplicate":
                require(mid in inputs and e["deadline_ms"] == collectors[collection]["deadline"], "duplicate_resets_window")
                continue
            require(mid not in inputs, "duplicate_input")
            previous = collectors.get(collection)
            if previous:
                require(now < previous["deadline"] and collection not in sealed, "deadline_boundary")
                require(previous["author"] == e["author"], "collection_author")
            inputs[mid] = collection
            expected_deadline = now + d["silence_ms"]
            require(e["deadline_ms"] == expected_deadline, "sliding_deadline")
            collectors[collection] = {"deadline": expected_deadline, "author": e["author"], "revision": e["revision"]}
        elif kind == "stale_timer":
            require(e["revision"] < collectors[e["collection"]]["revision"] and e["ignored"], "stale_timer")
        elif kind == "seal":
            collection, turn = e["collection"], e["turn"]
            require(collection not in sealed and now >= collectors[collection]["deadline"], "seal_deadline")
            require(e["revision"] == collectors[collection]["revision"], "seal_stale_revision")
            require(e["messages"] == [m for m, c in inputs.items() if c == collection], "bundle_members")
            sealed.add(collection)
            require(turn == len(turns) + 1, "turn_sequence")
            turns[turn] = "queued"
        elif kind == "start":
            turn = e["turn"]
            require(turns[turn] == "queued" and len(active) < 2, "third_active_turn")
            active.add(turn)
            turns[turn] = "preparing"
        elif kind == "generated":
            require(e["turn"] in active and not e["released_slot"], "early_slot_release")
            turns[e["turn"]] = "ready_to_send"
        elif kind == "send":
            turn = e["turn"]
            require(turn in active and turn not in sent, "blind_replay")
            require(all(t in closed for t in turns if t < turn), "send_order")
            require(turns[turn] == "ready_to_send", "send_before_ready")
            sent.add(turn)
            send_times[turn] = now
            turns[turn] = "reconciling" if e["outcome"] == "unknown" else "sending"
        elif kind == "close":
            turn = e["turn"]
            require(turn in active and e["phase"] in TERMINAL, "invalid_terminal")
            if e["phase"] == "closed_unknown":
                require(turns[turn] == "reconciling" and e["unresolved_delivery"], "unknown_fabricated")
                require(now - send_times[turn] >= d["delivery_reconcile_timeout_ms"], "unknown_closed_early")
            active.remove(turn)
            closed[turn] = e["phase"]
        elif kind == "late_receipt":
            require(closed[e["turn"]] == "closed_unknown" and not e["resend"] and not e["new_memory_commit"], "late_receipt_replay")
        else:
            raise Violation("unknown_trace_event")
    require(not active and len(closed) == len(turns), "unfinished_trace")
    start2 = next(e["at_ms"] for e in d["events"] if e["kind"] == "start" and e["turn"] == 2)
    close1 = next(e["at_ms"] for e in d["events"] if e["kind"] == "close" and e["turn"] == 1)
    require(start2 < close1, "no_second_turn_overlap")


CHECKS = {"bundle": check_bundle, "ingest": check_ingest, "memory": check_memory,
          "identity": check_identity, "commit": check_commit, "gateway": check_gateway,
          "web": check_web, "trace": check_trace, "capacity": check_capacity,
          "idempotency": check_idempotency, "cancel": check_cancel, "stream": check_stream,
          "revision": check_revision}


def main():
    require({"date-time", "uri"} <= set(FormatChecker.checkers), "missing_format_dependencies")
    manifest = read(PACKAGE / "manifest.json")
    expected_files = set(manifest["sha256"])
    actual_files = {p.relative_to(ROOT).as_posix() for p in PACKAGE.rglob("*")
                    if p.is_file() and p.name != "manifest.json"}
    require(actual_files == expected_files, "manifest_file_inventory")
    for relative, expected_digest in manifest["sha256"].items():
        path = (ROOT / relative).resolve()
        require(path.is_relative_to(PACKAGE), "manifest_path")
        require(digest(path) == expected_digest, "manifest_hash:" + relative)

    schemas, registry = {}, Registry()
    for path in sorted((PACKAGE / "schemas").glob("*.json")):
        schema = read(path)
        Draft202012Validator.check_schema(schema)
        schemas[path.stem] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    # Resolve every reference locally, including definitions not selected by one sample.
    def refs(node):
        if isinstance(node, dict):
            if "$ref" in node:
                registry.resolver().lookup(node["$ref"])
            for value in node.values():
                refs(value)
        elif isinstance(node, list):
            for value in node:
                refs(value)
    for schema in schemas.values():
        refs(schema)

    def validator(selector):
        file, definition = selector.split("#")
        return Draft202012Validator({"$ref": schemas[file]["$id"] + "#/$defs/" + definition},
                                    registry=registry, format_checker=FormatChecker())

    documents = read(PACKAGE / "examples/documents.json")
    require(len({d["id"] for d in documents}) == len(documents), "duplicate_fixture_id")
    for doc in documents:
        validator(doc["schema"]).validate(doc["document"])
    require(set(manifest["wire_types"]) <= {d["schema"] for d in documents}, "missing_wire_example")
    negatives = read(PACKAGE / "examples/negative-documents.json")
    for case in negatives:
        errors = list(validator(case["schema"]).iter_errors(case["document"]))
        def keywords(errors):
            return {e.validator for e in errors} | {k for e in errors for k in keywords(e.context)}
        require(case["expected_keyword"] in keywords(errors), "wrong_schema_rejection:" + case["id"])

    by_id = {d["id"]: d["document"] for d in documents}
    cases = read(PACKAGE / "examples/relations.json")
    for case in cases:
        values = copy.deepcopy(case.get("data", {}))
        values.update({name: copy.deepcopy(by_id[ident]) for name, ident in case.get("documents", {}).items()})
        for mutation in case.get("mutations", []):
            target = values
            for part in mutation["path"][:-1]:
                target = target[part]
            target[mutation["path"][-1]] = mutation["value"]
        for name, ident in case.get("documents", {}).items():
            selector = next(doc["schema"] for doc in documents if doc["id"] == ident)
            validator(selector).validate(values[name])
        actual = None
        try:
            CHECKS[case["check"]](values)
        except Violation as exc:
            actual = str(exc)
        require(actual == case.get("expected_error"), "wrong_relation_result:" + case["id"] + ":" + str(actual))
    print(f"PASS: {len(schemas)} schemas, {len(documents)} positive documents, "
          f"{len(negatives)} schema negatives, {len(cases)} relation/trace cases, "
          f"{len(expected_files)} file hashes. Offline candidate conformance only; L0/L1 not run.")


if __name__ == "__main__":
    try:
        main()
    except (Violation, KeyError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
