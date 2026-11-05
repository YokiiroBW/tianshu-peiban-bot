"""Attribution compares proven input identity without exposing material bodies."""
from copy import deepcopy
from datetime import UTC, datetime
import json

from tianshu.memory.consolidation_attribution import attribute_attempts, input_digest, material_digest


def frozen():
    return {
        "batch_id": "b1",
        "episodes": [{"episode_id": "e1", "revision": 1, "sequence": 3,
                      "content_digest": "episode-hash", "before": {"offset": 0}, "after": {"offset": 5},
                      "source_refs": [{"source_id": "s1", "revision": 1, "locator": {"start": 0, "end": 5}}]}],
        "materials": {"m1": {"role": "new", "source_refs": [{"source_id": "s1", "revision": 1,
            "locator": {"start": 0, "end": 5}}], "evidence_kind": "text"}},
        "records": {}, "targets": [{"subject_id": "person-1"}], "read_set": [],
    }


def test_material_identity_requires_version_content_range_and_source():
    first = frozen()
    assert material_digest(first)
    for change in (lambda x: x["episodes"][0].update(revision=2),
                   lambda x: x["episodes"][0].update(content_digest="new-image"),
                   lambda x: x["episodes"][0]["after"].update(offset=6),
                   lambda x: x["materials"]["m1"]["source_refs"][0].update(revision=2)):
        updated = deepcopy(first)
        change(updated)
        assert material_digest(first) != material_digest(updated)
    old = deepcopy(first)
    del old["episodes"][0]["content_digest"]
    assert material_digest(old) is None and input_digest(old, "authority", {}) is None


def test_duplicate_only_when_full_input_and_success_are_proven():
    first = frozen()
    second = deepcopy(first)
    second["batch_id"] = "b2"
    changed = deepcopy(second)
    changed["batch_id"] = "b3"
    changed["records"] = {"r1": {"id": "claim-1", "revision": 2}}
    batches = [
        {"batch_id": bid, "status": "applied", "error_code": None, "created_at": datetime.now(UTC),
         "context_fingerprint": "authority", "manifest": {"frozen": value, "steps": {}}}
        for bid, value in (("b1", first), ("b2", second), ("b3", changed))
    ]
    attempts = [
        {"batch_id": "b1", "step_id": "first", "model_attempt_id": "a1", "kind": "original", "status": "succeeded"},
        {"batch_id": "b2", "step_id": "duplicate", "model_attempt_id": "a2", "kind": "original", "status": "succeeded"},
        {"batch_id": "b3", "step_id": "changed", "model_attempt_id": "a3", "kind": "original", "status": "succeeded"},
        {"batch_id": "b3", "step_id": "context", "model_attempt_id": "a4", "kind": "context", "status": "succeeded"},
        {"batch_id": "b3", "step_id": "repair", "model_attempt_id": "a5", "kind": "repair", "status": "succeeded"},
        {"batch_id": "b3", "step_id": "retry", "model_attempt_id": "a6", "kind": "retry", "status": "failed"},
    ]
    for attempt in attempts:
        attempt["request"] = {"attempt_id": attempt["model_attempt_id"], "intent_id": attempt["step_id"],
            "context": {"scope_ids": [], "audience": [], "permissions": []},
            "system": "stable system prompt", "route_id": "route-1",
            "messages": [{"role": "user", "parts": [{"kind": "text", "text": json.dumps(
                {"batch_id": attempt["batch_id"], "payload": "same input"})}]}]}
    result = attribute_attempts(batches, attempts)
    assert result[0]["steps"][0]["attribution"] == "new_material"
    assert result[1]["steps"][0]["attribution"] == "duplicate_completed_input"
    assert result[1]["steps"][0]["same_input_as"] == "first"
    assert [item["attribution"] for item in result[2]["steps"]] == [
        "same_material_changed_input", "context_supplement", "output_correction", "independent_retry"]
    assert "materials" not in str(result) and "records" not in str(result)


def test_same_material_different_authority_is_not_called_duplicate():
    first = frozen()
    request = {"context": {"scope_ids": [], "audience": [], "permissions": []}, "system": "one",
        "messages": [{"role": "user", "parts": [{"kind": "text", "text": json.dumps(
            {"batch_id": "b1", "payload": "same input"})}]}]}
    assert input_digest(first, "authority-a", request) != input_digest(first, "authority-b", request)
    assert input_digest(first, "authority-a", request) != input_digest(first, "authority-a", {**request, "system": "two"})


def test_missing_actual_request_cannot_prove_duplicate():
    item = frozen()
    assert input_digest(item, "authority", None) is None
