"""Canonical meaning-only index documents, independent of source IDs and index backends."""

import json

from tianshu.runtime.serialization import wire


def index_text(unit):
    fields = {"kind": unit.kind, "content": unit.content, "assertion": unit.assertion,
              "conditions": unit.conditions, "state": unit.state, "polarity": unit.polarity,
              "occurred_at": unit.occurred_at, "valid_from": unit.valid_from,
              "valid_until": unit.valid_until, "due_at": unit.due_at,
              "interpretation": unit.interpretation, "origin_kind": unit.origin_kind}
    if unit.completion_basis:
        fields["completion_basis"] = unit.completion_basis.get("kind")
    if unit.actual_result:
        fields["actual_result"] = {key: value for key, value in unit.actual_result.items()
            if key in {"execution", "effect", "delivery", "event_kind", "software_action",
                       "fictional_world", "representation", "internal"}}
    if unit.counterexamples:
        fields["counterexamples"] = unit.counterexamples
    return json.dumps(wire(fields), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_text(unit):
    """The answer gets all evidence fields; the index gets the smaller semantic document."""
    return json.dumps(wire(unit), ensure_ascii=False, separators=(",", ":"))
