"""Actual software checkpoints for clearly marked fictional-world activity."""

import json

from tianshu.conversation.codec import source_refs
from tianshu.conversation.types import InputPart
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import OriginKind
async def record_completed_imagined(life, context, activity, activity_revision, occurred_at,
                                    connection, *, support_refs=()):
    """One source constructor for a completed checkpoint, never a physical action."""
    recorded_at = (await (await connection.execute("SELECT now() AS recorded_at")).fetchone())["recorded_at"]
    completion = {"activity_id": activity["id"], "activity_revision": activity_revision,
        "actor_id": context.actor_id, "title": activity["title"], "completed_at": occurred_at,
        "recorded_at": recorded_at, "experience_kind": "imagined", "imagined": True,
        "representation": "fictional_world", "software_action": False,
        "completion_basis": "actor_activity_checkpoint"}
    # A routine rest checkpoint has a title and timestamps, not an observed
    # lesson. Keep it available to the diary without treating every repetition
    # as a separate learning opportunity.
    details = activity.get("checkpoint_data") or {}
    if activity.get("kind") == "rest" and not any(details.get(key) for key in ("observation", "actual_result", "subjective_observation")):
        completion.update(derived_kind="routine_rest", learning_value="low", experience_eligible=False)
    dependencies = unique_refs(source_refs(activity["source_refs"]), tuple(support_refs))
    original = await life.sources.record_event(
        context, producer="life:actor:" + context.actor_id,
        event_id="imagined-activity:" + activity["id"], revision=activity_revision,
        occurred_at=occurred_at, origin_kind=OriginKind.ACTOR,
        parts=(InputPart("text", json.dumps(wire(completion), ensure_ascii=False), metadata={
            "experience_kind": "imagined", "imagined": True, "activity_id": activity["id"],
            "actor_id": context.actor_id, "recorded_at": recorded_at.isoformat()}),),
        support_refs=dependencies, connection=connection)
    return completion, unique_refs(dependencies, original)
