"""Apply independent proposal groups without replaying accepted side effects."""
from dataclasses import replace

from .types import PlanApplyResult


def candidates(decision):
    return ([decision["activity"]] if decision.get("activity") else []) + decision.get("arrangements", [])


def change_goal(change):
    return "new:" + change["key"] if change["operation"] == "create" else change["id"]


def with_candidates(decision, items):
    return {**decision, "activity": items[0] if items else None, "arrangements": items[1:]}


async def apply(life, connection, context, decision, frozen, generation_key, refs, routine, now, fingerprint):
    """One transaction owns directions, slots and the result notification.

    A direction and all activities referring to it form one unit. A failed
    unit is rolled back to a savepoint; unrelated units are then applied once.
    """
    offered = candidates(decision)
    originals = {row["id"]: row for row in frozen.get("goals", ())}
    changed_goals = {change_goal(change) for change in decision["goal_changes"]}
    rejected = set(decision.get("_invalid_goal_ids", ())) & changed_goals
    reasons = {identifier: "dependent_candidate_invalid" for identifier in rejected}
    for change in decision["goal_changes"]:
        if change["operation"] == "create":
            continue
        identifier = change["id"]
        old = await life.lock_row(connection, context,
            "SELECT * FROM life.goals WHERE id=%s AND actor_id=%s FOR UPDATE", (identifier, context.actor_id))
        if not old or identifier not in originals or old["revision"] != originals[identifier]["revision"]:
            rejected.add(identifier)
            reasons[identifier] = "goal_revision_changed"

    invalid = list(decision.get("_invalid_candidates", ()))
    for item in offered:
        if item.get("goal_id") in rejected:
            invalid.append({"candidate_key": item["_candidate_key"], "status": "stale_state" if reasons[item["goal_id"]] == "goal_revision_changed" else "invalid_proposal",
                "reason": reasons[item["goal_id"]]})
    for change in decision["goal_changes"]:
        identifier = change_goal(change)
        if identifier in rejected and not any(item.get("goal_id") == identifier for item in offered):
            invalid.append({"candidate_key": "goal:" + identifier, "status": "stale_state" if reasons[identifier] == "goal_revision_changed" else "invalid_proposal", "reason": reasons[identifier]})
    selected = with_candidates({**decision, "goal_changes": [change for change in decision["goal_changes"] if change_goal(change) not in rejected],
        "_invalid_candidates": invalid}, [item for item in offered if item.get("goal_id") not in rejected])

    if invalid:
        selected = {**selected, "cancel_activity_ids": []}
    removed = []
    await connection.execute("SAVEPOINT life_proposal_groups")
    while True:
        aliases = await life.directions.apply(connection, context, selected["goal_changes"], frozen, generation_key, now)
        result = await life.days.apply(connection, context, selected, frozen, generation_key, aliases, refs, routine, now, fingerprint)
        by_key = {item.candidate_key: item for item in result.items}
        grouped = {change_goal(change) for change in selected["goal_changes"]}
        failed = {item.get("goal_id") for item in candidates(selected)
            if item.get("goal_id") in grouped and by_key[item["_candidate_key"]].status != "scheduled"}
        skip_cancels = bool(selected.get("cancel_activity_ids")) and any(
            item.status in {"invalid_proposal", "stale_state", "deferred_no_slot"} for item in result.items)
        if not failed and not skip_cancels:
            break
        await connection.execute("ROLLBACK TO SAVEPOINT life_proposal_groups")
        for identifier in failed:
            group_items = [by_key[item["_candidate_key"]] for item in candidates(selected) if item.get("goal_id") == identifier]
            failure = next(item for item in group_items if item.status != "scheduled")
            removed.extend(replace(item, status=failure.status, reason="dependent_group_not_applied", activity_id=None,
                slot_id=None, revision=None, next_due_at=failure.next_due_at) if item.status == "scheduled" else item for item in group_items)
        kept = [item for item in candidates(selected) if item.get("goal_id") not in failed]
        selected = with_candidates({**selected,
            "goal_changes": [change for change in selected["goal_changes"] if change_goal(change) not in failed],
            "cancel_activity_ids": [] if skip_cancels else selected["cancel_activity_ids"],
            "_has_deferred_group": bool(removed)}, kept)
    if removed:
        # The final pass has no accepted candidate for a rolled-back group.
        # An artificial idle produced by that pass is not an explicit idle.
        items = [item for item in result.items if item.candidate_key != "idle"] + removed
        dates = [item.next_due_at or now for item in items if item.status == "stale_state"]
        dates.extend(item.next_due_at for item in items if item.status == "deferred_no_slot" and item.next_due_at)
        result = PlanApplyResult(tuple(items), min(dates) if dates else None)
    await connection.execute("RELEASE SAVEPOINT life_proposal_groups")
    return result
