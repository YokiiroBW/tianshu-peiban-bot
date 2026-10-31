"""Trusted source facts used by both subscription scans and rule previews.

These facts are deliberately separate from the published metadata/NFO document. In particular a
favorite timestamp belongs to one folder member, never to the video or the browser's input.
"""

from dataclasses import asdict, dataclass

from .types import ROLE_COLLABORATOR, MediaMetadata

ADVANCED_FIELDS = (
    "published_at",
    "favorited_at",
    "part_count",
    "duration_seconds",
    "is_collaboration",
)


@dataclass(frozen=True)
class RuleSourceContext:
    bvid: str
    source_kind: str
    source_id: str
    favorited_at: str | None = None


@dataclass(frozen=True)
class RuleContext:
    source_kind: str | None = None
    source_id: str | None = None
    member: bool | None = None
    favorited_at: str | None = None
    is_collaboration: bool | None = None


def build_rule_context(metadata, resolved, source_value=None, member=None):
    """Combine normalized metadata with connector-owned, video-bound source observations."""
    collaboration = resolved.get("rule_context", {}).get("is_collaboration")
    if type(collaboration) is not bool:
        collaboration = (
            True
            if any(creator.role == ROLE_COLLABORATOR for creator in metadata.creators)
            else None
        )
    if member is not None:
        if (
            not isinstance(member, RuleSourceContext)
            or member.bvid != metadata.bvid
            or source_value is None
            or (member.source_kind, member.source_id) != (source_value["kind"], source_value["id"])
        ):
            raise ValueError("rule source context does not match the selected video/source")
    return RuleContext(
        source_kind=source_value["kind"] if source_value else None,
        source_id=source_value["id"] if source_value else None,
        member=member is not None if source_value else None,
        favorited_at=member.favorited_at if member and member.source_kind == "favorite" else None,
        is_collaboration=collaboration,
    )


def advanced_field_value(metadata: MediaMetadata, field: str, context: RuleContext | None = None):
    """Return an actual value or None; incomplete duration is never a partial sum."""
    if field == "published_at":
        return metadata.published_at
    if field == "favorited_at":
        return context.favorited_at if context else None
    if field == "part_count":
        return len(metadata.parts) if metadata.parts else None
    if field == "duration_seconds":
        if not metadata.parts or any(part.duration_seconds is None for part in metadata.parts):
            return None
        return sum(part.duration_seconds for part in metadata.parts)
    if field == "is_collaboration":
        if context and context.is_collaboration is not None:
            return context.is_collaboration
        return (
            True
            if any(creator.role == ROLE_COLLABORATOR for creator in metadata.creators)
            else None
        )
    raise ValueError(f"unknown advanced media rule field: {field}")


def public_rule_context(metadata, context):
    fields = {}
    for field in ADVANCED_FIELDS:
        value = advanced_field_value(metadata, field, context)
        fields[field] = {"available": value is not None, "value": value}
    return {
        "source": {"kind": context.source_kind, "id": context.source_id}
        if context.source_kind
        else None,
        "member": context.member,
        "fields": fields,
    }


def uses_advanced_fields(policy):
    return any(
        rule.field in ADVANCED_FIELDS
        for group in policy.whitelist + policy.blacklist
        for rule in group.rules
    )


def rule_snapshot_revision(snapshot, context, policy):
    """Keep legacy text evidence stable; advanced evidence includes its exact source facts."""
    # Projection and the standalone evaluator stay standard-library-only on import. The HTTP/scan
    # owners call this evidence helper, where the existing platform encoding helpers are available.
    from ..contracts import canonical, digest

    evidence = (
        {"snapshot": snapshot, "rule_context": asdict(context)}
        if uses_advanced_fields(policy)
        else snapshot
    )
    return digest(canonical(evidence))
