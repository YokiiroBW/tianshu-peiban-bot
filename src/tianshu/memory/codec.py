"""Explicit reconstruction of the persisted domain projections."""

from tianshu.types import Coverage, OriginKind, SourceRef
from .types import ClaimRevision, Episode, MatterView, ReflectionView


def refs(value):
    return tuple(SourceRef(**item) if isinstance(item, dict) else item for item in value or ())


def episode(row):
    return Episode(row["episode_id"], row["event_id"], row["revision"], row["actor_id"],
        OriginKind(row["origin_kind"]), row["occurred_at"], row["summary"], refs(row["source_refs"]),
        tuple(row["scope_ids"]), row["actual_result"], Coverage(**row["coverage"]), row["sequence"],
        "e:"+str(row["sequence"]), row["person_id"], row["matter_id"])


def claim(row, selected_refs=None):
    return ClaimRevision(row["claim_id"], row["revision"], row["actor_id"], row["speaker_id"],
        row["subject_id"], row["predicate"], row["value"], row["assertion"], row["polarity"], row["conditions"],
        row["valid_from"], row["valid_until"], row["observed_at"], row["state"],
        tuple(selected_refs) if selected_refs is not None else refs(row["source_refs"]), tuple(row["scope_ids"]),
        row["matter_id"], tuple(row["supersedes"]), tuple(row["contradictions"]))


def matter(row, claim_ids=()):
    return MatterView(row["matter_id"], row["revision"], row["actor_id"], row["subject_id"], row["title"],
        row["state"], row["due_at"], refs(row["source_refs"]), tuple(claim_ids), row["summary"],
        tuple(row["scope_ids"]), row["conditions"])


def reflection(row):
    return ReflectionView(row["proposal_id"], row["revision"], row["actor_id"], row["person_id"],
        tuple(row["episode_ids"]), row["content"], row["kind"], row["occurred_at"], refs(row["source_refs"]),
        tuple(row["scope_ids"]), row["conditions"], tuple(row["counterexamples"]), True)
