"""Trusted final Action checks for frozen learned behavior dependencies."""

from tianshu.runtime.errors import AppError


async def validate_action_behaviors(growth, context, refs, *, purpose):
    if not refs:
        return True
    try:
        current = await growth.resolve_behaviors(context, refs, purpose=purpose)
    except AppError as exc:
        if exc.status not in {502, 503, 504}:
            raise
        if any(ref.get("semantic_role") == "constraint" for ref in refs):
            raise AppError("behavior_constraint_pending", status=409) from None
        return True
    decisions = {
        (row["rule_id"], row.get("requested_revision", row["revision"]),
         row.get("requested_semantic_revision", row["semantic_revision"])): row
        for row in current
    }
    for ref in refs:
        key = (ref["rule_id"], ref["revision"], ref["semantic_revision"])
        row = decisions.get(key)
        if row is None:
            raise AppError("behavior_resolution_incomplete", status=503)
        if not row["eligible"] or not row["compatible"]:
            raise AppError("behavior_dependency_invalid", status=409)
    return True
