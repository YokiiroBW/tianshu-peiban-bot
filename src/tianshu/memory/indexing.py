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


def rerank_text(unit):
    """Rank meaning and its authors/relations without sending the source ledger."""
    fields = json.loads(index_text(unit))
    fields.update(id=unit.id, subject_id=unit.subject_id, speaker_id=unit.speaker_id,
                  current_status=unit.current_status, change_kind=unit.change_kind,
                  change_reason=unit.change_reason)
    if unit.display_subjects:
        fields["people"] = [{key: person.get(key) for key in ("subject_id", "display_name", "aliases", "kind")}
                            for person in unit.display_subjects]
    relations = []
    for relation in unit.relations:
        value = {"kind": relation.relation_kind, "valid_from": relation.valid_from,
                 "valid_until": relation.valid_until, "current_status": relation.current_status}
        for name in ("from_evidence", "to_evidence"):
            endpoint = getattr(relation, name)
            if endpoint:
                # Relation snapshots are evidence units without nested links.
                # Keep both semantic sides, including negation and conditions.
                value[name] = {key: endpoint[key] for key in (
                    "kind", "content", "subject_id", "speaker_id", "assertion", "conditions", "polarity",
                    "occurred_at", "valid_from", "valid_until", "due_at", "current_status", "state",
                    "interpretation", "origin_kind", "predicate", "value", "change_kind", "change_reason",
                    "counterexamples") if key in endpoint}
                if endpoint.get("completion_basis"):
                    value[name]["completion_basis"] = endpoint["completion_basis"].get("kind")
                if endpoint.get("actual_result"):
                    value[name]["actual_result"] = {key: item for key, item in endpoint["actual_result"].items()
                        if key in {"execution", "effect", "delivery", "event_kind", "software_action",
                                   "fictional_world", "representation", "internal"}}
        relations.append(value)
    if relations:
        fields["relations"] = relations
    return json.dumps(wire(fields), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
