"""Canonical meaning-only index documents, independent of source IDs and index backends."""

import json

from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from .domain_utils import lexical


async def put_learning_reference_on(connection, context, *, rule_id, revision, semantic_revision,
                                    kind, source_refs, scope_ids, search_text):
    """Write only reference metadata and search lexemes in the Growth transaction."""
    if (not context.actor_id or not isinstance(rule_id, str) or not rule_id or
            type(revision) is not int or revision < 1 or type(semantic_revision) is not int or
            semantic_revision < 1 or not isinstance(kind, str) or not kind or
            not isinstance(search_text, str) or len(search_text) > 32000):
        raise AppError("invalid_memory_learning_reference")
    scopes = tuple(sorted(set(scope_ids)))
    refs = sorted(wire(tuple(source_refs)), key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    terms = lexical(search_text)
    await connection.execute(
        "INSERT INTO memory.learning_index_refs(actor_id,principal_id,rule_id,revision,semantic_revision,kind,"
        "source_refs,scope_ids,search_vector) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('simple',%s)) "
        "ON CONFLICT(actor_id,rule_id,revision) DO NOTHING",
        (context.actor_id, context.principal_id, rule_id, revision, semantic_revision, kind,
         Jsonb(refs), list(scopes), terms))
    existing = await (await connection.execute(
        "SELECT principal_id,semantic_revision,kind,source_refs,scope_ids,"
        "search_vector=to_tsvector('simple',%s) AS same_search FROM memory.learning_index_refs "
        "WHERE actor_id=%s AND rule_id=%s AND revision=%s",
        (terms, context.actor_id, rule_id, revision))).fetchone()
    if (existing is None or existing["principal_id"] != context.principal_id or
            existing["semantic_revision"] != semantic_revision or existing["kind"] != kind or
            existing["source_refs"] != refs or sorted(existing["scope_ids"]) != list(scopes) or
            not existing["same_search"]):
        raise AppError("memory_learning_reference_conflict", status=409)


async def purge_learning_references_on(connection, source_refs, kind, *, actor_id):
    """Clear forgotten lexemes while retaining version order and reference audit."""
    if not isinstance(actor_id, str) or not actor_id:
        raise AppError("actor_required")
    refs = wire(tuple(source_refs))
    if not refs:
        return
    patterns = [Jsonb([{"source_id": ref["source_id"], "revision": ref["revision"]}]) for ref in refs]
    await connection.execute(
        "UPDATE memory.learning_index_refs SET search_vector=''::tsvector "
        "WHERE actor_id=%s AND source_refs @> ANY(%s::jsonb[]) AND sources.dependencies_intersect(source_refs,%s,%s) "
        "AND NOT sources.any_support_usable(jsonb_build_array(source_refs),scope_ids)",
        (actor_id, patterns, Jsonb(refs), kind))


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
