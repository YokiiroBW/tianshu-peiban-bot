"""Rehydrate only the canonical typed action persisted by this domain."""

from datetime import datetime
from tianshu.types import Action, Attempt, TrustedContext, SourceRef, OriginKind


def context_from(value):
    value = dict(value)
    value["delivery_allowed"] = value.get("delivery_allowed", False)
    value["scope_ids"] = tuple(value["scope_ids"])
    value["audience"] = tuple(value["audience"])
    value["permissions"] = frozenset(value["permissions"])
    value["origin_kind"] = OriginKind(value["origin_kind"])
    return TrustedContext(**value)


def action_from(value):
    value = dict(value)
    value["context"] = context_from(value["context"])
    value["origin_kind"] = OriginKind(value["origin_kind"])
    value["source_refs"] = tuple(SourceRef(**item) for item in value.get("source_refs", []))
    if value.get("deadline_at"):
        value["deadline_at"] = datetime.fromisoformat(value["deadline_at"])
    return Action(**value)


def receipt_from(value):
    from tianshu.types import Receipt, Coverage

    value = dict(value)
    value["artifacts"] = tuple(artifact_from(x) for x in value.get("artifacts", []))
    if value.get("coverage"):
        value["coverage"] = Coverage(**value["coverage"])
    return Receipt(**value)


def attempt_from(value):
    value = dict(value)
    for key in ("started_at", "finished_at"):
        if value.get(key):
            value[key] = datetime.fromisoformat(value[key])
    return Attempt(**value)


def artifact_from(value):
    from tianshu.types import ArtifactRef, MediaRef, Coverage

    return ArtifactRef(
        value["artifact_id"],
        MediaRef(**value["media"]),
        tuple(SourceRef(**x) for x in value.get("source_refs", [])),
        Coverage(**value["coverage"]) if value.get("coverage") else None,
        value.get("metadata", {}),
    )
