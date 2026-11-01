"""Actual software checkpoints for imagined activity, including finite source repair."""

import json

from tianshu.conversation.codec import signature, source_refs
from tianshu.conversation.types import InputPart
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import OriginKind
from .validation import revision, text


OPERATION = "life.imagined.repair"


async def record_completed_imagined(life, context, activity, activity_revision, occurred_at,
                                    connection, *, support_refs=()):
    """One source constructor for a completed checkpoint, never a physical action."""
    recorded_at = (await (await connection.execute("SELECT now() AS recorded_at")).fetchone())["recorded_at"]
    completion = {"activity_id": activity["id"], "activity_revision": activity_revision,
        "actor_id": context.actor_id, "title": activity["title"], "completed_at": occurred_at,
        "recorded_at": recorded_at, "experience_kind": "imagined", "imagined": True,
        "representation": "fictional_world", "software_action": False,
        "completion_basis": "actor_activity_checkpoint"}
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


async def _episode(life, context, identifier, expected_revision, connection):
    page = await life.memory.episodes_by_refs(context, ((identifier, expected_revision),), connection=connection)
    if (not page.coverage.complete or len(page.items) != 1
            or page.items[0].episode_id != identifier or page.items[0].revision != expected_revision):
        raise AppError("imagined_episode_unavailable", status=409,
            details={"episode_id": identifier, "expected_revision": expected_revision,
                     "missing": page.coverage.missing})
    episode = page.items[0]
    if (episode.actor_id != context.actor_id or not set(episode.scope_ids) <= set(context.scope_ids)
            or episode.person_id != context.person_id):
        raise AppError("imagined_episode_context_mismatch", status=403)
    return episode


def _activity(activity, context, expected_activity_revision):
    if activity is None or not set(activity["scope_ids"]) <= set(context.scope_ids):
        raise AppError("activity_not_found", status=404)
    if expected_activity_revision is not None:
        revision(activity, expected_activity_revision)
    if (activity["state"] != "completed" or activity["kind"] not in {"rest", "reflection", "room"}
            or activity["action_id"] is not None or activity["last_receipt_id"] is not None
            or activity["checkpoint_data"].get("capability_id") is not None or activity["checkpoint_step"] < 1):
        raise AppError("imagined_activity_not_repairable", status=409)


def _original(activity, episode):
    prefix = "fiction:" + activity["id"] + ":"
    try:
        suffix = episode.event_id[len(prefix):] if episode.event_id.startswith(prefix) else ""
        activity_revision = int(suffix)
        if activity_revision < 1 or str(activity_revision) != suffix:
            raise ValueError
    except (TypeError, ValueError):
        raise AppError("imagined_episode_activity_mismatch", status=409) from None
    actual = episode.actual_result or {}
    if (episode.origin_kind != OriginKind.ACTOR or actual.get("representation") != "fictional_world"
            or actual.get("software_action") is not False or actual.get("action_id")
            or actual.get("execution") or getattr(episode, "action_id", None)):
        raise AppError("imagined_episode_not_repairable", status=409)
    # Completed activities have no mutable continuation. Without an immutable
    # checkpoint history, an edited later revision cannot reconstruct past title
    # or plan as if it were the original completion.
    if activity["revision"] != activity_revision + 1:
        raise AppError("imagined_activity_checkpoint_changed", status=409)
    if actual.get("completion_basis") == "actor_activity_checkpoint" and episode.source_refs:
        raise AppError("imagined_episode_original_already_present", status=409)
    return activity_revision


def _result(client_id, episode, activity_revision, previous_revision):
    repair = episode.actual_result["source_repair"]
    return CommandResult(client_id, {"state": "repaired", "activity_id": repair["activity_id"],
        "activity_revision": activity_revision, "episode_id": episode.episode_id, "event_id": episode.event_id,
        "previous_revision": previous_revision, "episode_revision": episode.revision,
        "source_refs": wire(episode.source_refs), "occurred_at": wire(episode.occurred_at),
        "recorded_at": wire(repair["recorded_at"])}, episode.revision)


async def repair_completed_imagined_episode(life, context, activity_id, episode_id,
                                          expected_revision, expected_activity_revision=None):
    """Repair a fixed, authorized legacy item without another activity or model.

    The original completion time remains occurred_at. The new ACTOR original is
    recorded now. Command replay rechecks the repaired Episode and its sources;
    it never returns a cached private projection after revocation or drift.
    """
    await life.identity.check(context)
    if not context.actor_id or not context.permissions & {"*", "life.manage"}:
        raise AppError("forbidden", status=403)
    activity_id = text(activity_id, "活动标识", 128)
    episode_id = text(episode_id, "经历标识", 512)
    if type(expected_revision) is not int or expected_revision < 1:
        raise AppError("invalid_episode_revision")
    if expected_activity_revision is not None and (type(expected_activity_revision) is not int or expected_activity_revision < 1):
        raise AppError("invalid_activity_revision")
    intent = {"actor_id": context.actor_id, "activity_id": activity_id,
              "episode_id": episode_id, "expected_revision": expected_revision}
    client_id = "imagined-repair:" + signature(intent)

    async def apply(connection):
        episode = await _episode(life, context, episode_id, expected_revision, connection)
        activity = await life.lock_row(connection, context,
            "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id),
            additional_refs=episode.source_refs)
        _activity(activity, context, expected_activity_revision)
        old_activity_revision = _original(activity, episode)
        completion, refs = await record_completed_imagined(life, context, activity, old_activity_revision,
            episode.occurred_at, connection, support_refs=episode.source_refs)
        completion["source_repair"] = {"operation": OPERATION, "client_request_id": client_id,
            "activity_id": activity_id, "activity_revision": activity["revision"],
            "previous_revision": expected_revision, "recorded_at": completion["recorded_at"]}
        repaired = await life.memory.observe_episode(context, {
            "episode_id": episode.episode_id, "event_id": episode.event_id, "revision": expected_revision + 1,
            "actor_id": context.actor_id, "origin_kind": "actor", "occurred_at": episode.occurred_at,
            "summary": "想象活动完成：" + activity["title"], "source_refs": refs,
            "matter_id": episode.matter_id, "coverage": episode.coverage, "actual_result": completion}, connection=connection)
        if repaired.episode_id != episode_id or repaired.event_id != episode.event_id or repaired.revision != expected_revision + 1:
            raise AppError("episode_revision_conflict", status=409)
        return _result(client_id, repaired, activity["revision"], expected_revision)

    async def replay(connection, identity):
        episode = await _episode(life, context, episode_id, identity.revision, connection)
        repair = (episode.actual_result or {}).get("source_repair") or {}
        if (repair.get("operation") != OPERATION or repair.get("client_request_id") != client_id
                or repair.get("activity_id") != activity_id or repair.get("previous_revision") != expected_revision):
            raise AppError("imagined_repair_result_changed", status=409)
        activity = await life.lock_row(connection, context,
            "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (activity_id, context.actor_id),
            additional_refs=episode.source_refs)
        _activity(activity, context, repair["activity_revision"])
        return _result(client_id, episode, activity["revision"], expected_revision)

    return await life.commands.execute(context, OPERATION, client_id,
        signature({**intent, "expected_activity_revision": expected_activity_revision}), apply, on_replay=replay)
