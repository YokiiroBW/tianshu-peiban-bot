"""Explicit reconstruction of the persisted domain projections."""

from tianshu.types import Coverage, OriginKind, SourceRef
from .types import ClaimRevision, Episode, MatterView, RecordRef, ReflectionView, SemanticLink


def refs(value):
    return tuple(SourceRef(**item) if isinstance(item, dict) else item for item in value or ())


def episode(row):
    return Episode(
        row["episode_id"],
        row["event_id"],
        row["revision"],
        row["actor_id"],
        OriginKind(row["origin_kind"]),
        row["occurred_at"],
        row["summary"],
        refs(row["source_refs"]),
        tuple(row["scope_ids"]),
        row["actual_result"],
        Coverage(**row["coverage"]),
        row["sequence"],
        "e:" + str(row["sequence"]),
        row["person_id"],
        row["matter_id"],
        row["extraction_state"],
        Coverage(
            row["extraction_state"] == "completed",
            missing=tuple(row["extraction_missing"]),
            representation="evidence_extraction",
        ),
        row.get("recorded_at"),
        row.get("speaker_id"),
        row.get("conversation_id"),
        row.get("consolidation", {}),
    )


def claim(row, selected_refs=None, *, relations=()):
    # Compatibility fields are derived from authorized semantic links, never the legacy arrays.
    supersedes = tuple(link.to_ref.id for link in relations
                       if link.relation_kind == "supersedes" and link.from_ref.id == row["claim_id"])
    contradictions = tuple(link.to_ref.id if link.from_ref.id == row["claim_id"] else link.from_ref.id
                           for link in relations if link.relation_kind == "contradicts")
    matter_id = next((link.to_ref.id if link.to_ref.kind == "matter" else link.from_ref.id
                      for link in relations if link.relation_kind == "about_matter"), None)
    return ClaimRevision(
        row["claim_id"],
        row["revision"],
        row["actor_id"],
        row["speaker_id"],
        row["subject_id"],
        row["predicate"],
        row["value"],
        row["assertion"],
        row["polarity"],
        row["conditions"],
        row["valid_from"],
        row["valid_until"],
        row["observed_at"],
        row["state"],
        tuple(selected_refs) if selected_refs is not None else refs(row["source_refs"]),
        tuple(row["scope_ids"]),
        matter_id,
        supersedes,
        contradictions,
        row.get("recorded_at"),
        row.get("change_kind", "legacy"),
        row.get("memory_kind", "fact"),
        row.get("current_status", "current"),
        row.get("semantic_fingerprint"),
        refs(row.get("derivation_refs")),
        (tuple(selected_refs),) if selected_refs is not None else tuple(refs(group) for group in row.get("support_groups", ())),
        row.get("change_reason", ""),
        refs(row.get("counterexample_refs")),
        tuple(relations),
    )


def matter(row, claim_ids=(), selected_refs=None, *, relations=()):
    return MatterView(
        row["matter_id"],
        row["revision"],
        row["actor_id"],
        row["subject_id"],
        row["title"],
        row["state"],
        row["due_at"],
        refs(row["source_refs"]) if selected_refs is None else tuple(selected_refs),
        tuple(claim_ids),
        row["summary"],
        tuple(row["scope_ids"]),
        row["conditions"],
        row["interpretation"],
        row.get("recorded_at"),
        row.get("observed_at"),
        row.get("change_kind", "legacy"),
        row.get("completion_basis"),
        refs(row.get("derivation_refs")),
        (tuple(selected_refs),) if selected_refs is not None else tuple(refs(group) for group in row.get("support_groups", ())),
        row.get("current_status", "current"),
        row.get("change_reason", ""),
        tuple(relations),
    )


def reflection(row):
    return ReflectionView(
        row["proposal_id"],
        row["revision"],
        row["actor_id"],
        row["person_id"],
        tuple(row["episode_ids"]),
        row["content"],
        row["kind"],
        row["occurred_at"],
        refs(row["source_refs"]),
        tuple(row["scope_ids"]),
        row["conditions"],
        tuple(row["counterexamples"]),
        True,
        row.get("recorded_at"),
    )


def semantic_link(row, selected_refs=None):
    return SemanticLink(
        row["link_id"], row["actor_id"],
        RecordRef(row["from_kind"], row["from_id"], row["from_revision"]),
        RecordRef(row["to_kind"], row["to_id"], row["to_revision"]),
        row["relation_kind"], row["valid_from"], row["valid_until"], row["recorded_at"],
        refs(row["source_refs"]) if selected_refs is None else tuple(selected_refs),
        tuple(row["scope_ids"]), row["state"],
        (tuple(selected_refs),) if selected_refs is not None else tuple(refs(group) for group in row["support_groups"]),
        refs(row["derivation_refs"]), row["current_status"],
        row.get("from_evidence"), row.get("to_evidence"),
    )
