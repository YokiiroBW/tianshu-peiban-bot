"""Recheck the semantic rules actually used by one expression."""
from dataclasses import asdict, is_dataclass
import json
from .recovery import raise_resource_wait


def unique_behavior_refs(*groups):
    values = {}
    for group in groups:
        for ref in group:
            ref = dict(ref)
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            values.setdefault(key, ref)
    return tuple(values.values())


def material_behavior_refs(item):
    direct = ()
    if item.behavior_ref:
        ref = dict(item.behavior_ref)
        if item.semantic_role and "semantic_role" not in ref:
            ref["semantic_role"] = item.semantic_role
        direct = (ref,)
    return unique_behavior_refs(direct, getattr(item, "behavior_refs", ()))


def matching_behavior(ref, rows):
    key = (ref.get("rule_id"), ref.get("revision"), ref.get("semantic_revision"))
    matches = [row for row in rows if (row.get("rule_id"), row.get("requested_revision"),
                                        row.get("requested_semantic_revision")) == key]
    if len(matches) == 1:
        return matches[0]
    # Older persisted refs may not contain requested-version fields. They can
    # only match an unambiguous single response, never another frozen version.
    same_rule = [row for row in rows if row.get("rule_id") == key[0]]
    if len(same_rule) == 1 and key[1:] == (None, None):
        return same_rule[0]
    return None


async def behavior_status(resolver, context, refs, *, purpose="conversation"):
    refs = tuple(refs)
    if not refs:
        return "valid", ()
    required = any(ref.get("semantic_role") in {"constraint", "prohibition", "explicit_constraint"}
                   or ref.get("required_constraint") is True for ref in refs)
    if resolver is None:
        return ("pending" if required else "unavailable"), ()
    try:
        purpose = {"chat": "conversation", "planning": "life.plan"}.get(purpose, purpose)
        values = await resolver.resolve_behaviors(context, refs, purpose=purpose)
    except Exception as error:
        raise_resource_wait(error)
        return ("pending" if required else "unavailable"), ()
    rows = [asdict(value) if is_dataclass(value) else dict(value) for value in values]
    for ref in refs:
        row = matching_behavior(ref, rows)
        if not row or row.get("state") != "active" or not row.get("eligible") or not row.get("compatible"):
            return "invalid", tuple(rows)
    return "valid", tuple(rows)
