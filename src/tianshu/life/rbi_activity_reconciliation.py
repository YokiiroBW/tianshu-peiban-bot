"""Durable wakes for observing an activity's original Action."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.conversation.codec import document, signature
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec, current_execution_generation
from tianshu.runtime.recovery_policy import RecoveryWait
from .continuation import READ_CAPABILITIES, action_resolved


WAITING = frozenset({"capability_queued", "capability_running", "outcome_unknown",
    "original_action_unresolved", "capability_recovery"})
BLOCKED = frozenset({"video_checkpoint_unavailable", "read_checkpoint_unavailable"})


def timestamp(value):
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return None
    return value if isinstance(value, datetime) and value.utcoffset() is not None else None


def eligible(row):
    if (not row or row["state"] not in {"running", "dormant"} or not row.get("action_id")
            or not row.get("context") or row.get("waiting_reason") in BLOCKED or action_resolved(row)):
        return False
    return (row["state"] == "running" or row.get("waiting_reason") in WAITING
        or (row.get("resume_condition") == "review_failure"
            and (row.get("checkpoint_data") or {}).get("capability_id") in READ_CAPABILITIES))


def generation_matches(row, generation):
    data = (row.get("checkpoint_data") or {}).get("reconciliation") or {}
    return (not isinstance(data, dict) or data.get("action_id") != row.get("action_id")
        or data.get("data_generation") == generation)


def observation(row, generation):
    data = (row.get("checkpoint_data") or {}).get("reconciliation") or {}
    if not isinstance(data, dict) or data.get("action_id") != row.get("action_id"):
        return {}
    if data.get("data_generation") != generation:
        raise AppError("job_generation_expired", status=409)
    return data


def recovery_due(row):
    """The capability retry time is metadata, not the original Action deadline."""
    item = (row.get("checkpoint_data") or {}).get("capability_recovery") or {}
    if not isinstance(item, dict) or item.get("action_id") != row.get("action_id"):
        return None
    return timestamp(item.get("due_at"))


def next_observation(row, generation, now):
    saved = observation(row, generation)
    count = saved.get("observations", 0)
    count = count if type(count) is int and count >= 0 else 0
    return now + timedelta(seconds=min(300, 15 * 2 ** min(count, 5)))


async def generation_on(life, connection, requested=None):
    executing = current_execution_generation()
    if requested is not None and executing is not None and requested != executing:
        raise AppError("job_generation_expired", status=409)
    generation = requested or executing or await life.jobs.current_generation_on(connection)
    await life.jobs.assert_generation_on(connection, generation)
    return generation


async def ensure_reconcile_on(life, connection, *, activity_id, action_id, data_generation=None,
                              due_at=None, reason="observe", observed=False, immediate=False, retire_due=False, not_before=None):
    """Queue one stable original-Action wake under the caller's source/row locks.

    A fresh physical key avoids old permanent-key collisions. The native lock
    merges equivalent todo wakes; a doing job may retain one future successor.
    """
    generation = await generation_on(life, connection, data_generation)
    row = await (await connection.execute(
        "SELECT * FROM life.activities WHERE id=%s FOR UPDATE", (activity_id,))).fetchone()
    if not eligible(row) or row["action_id"] != action_id:
        return None
    now = datetime.now(UTC)
    due_at = timestamp(due_at) if due_at is not None else now
    if due_at is None:
        raise AppError("invalid_activity_reconciliation_due", status=409)
    previous = observation(row, generation)
    old_due = timestamp(previous.get("due_at"))
    if (observed or retire_due) and old_due is not None and old_due <= now:
        old_due = None
    when = min(old_due, due_at) if old_due is not None else due_at
    lower_bound = None
    if not_before is not None:
        lower_bound = timestamp(not_before)
        if lower_bound is None:
            raise AppError("invalid_activity_reconciliation_due",status=409)
        when = max(when,lower_bound)
    count = previous.get("observations", 0)
    count = count if type(count) is int and count >= 0 else 0
    metadata = {"action_id": action_id, "data_generation": generation,
        "due_at": when.isoformat(), "observations": count + int(observed), "reason": reason}
    payload = {"activity_id": activity_id, "action_id": action_id, "data_generation": generation}
    queueing_lock = "life-activity-observe:" + signature((activity_id, action_id))
    scheduled = min(now, when) if immediate else when
    if lower_bound is not None:
        scheduled = max(scheduled, lower_bound)
    job_id = await life.jobs.enqueue(connection, JobSpec("life.activity_reconcile",
        "life-activity-observe:" + str(uuid4()), payload,
        scheduled_at=scheduled, subject_id=row["actor_id"],
        data_generation=generation), queueing_lock=queueing_lock)
    metadata["job_id"] = job_id
    await connection.execute("UPDATE life.activities SET checkpoint_data=checkpoint_data||%s WHERE id=%s",
        (document({"reconciliation": metadata}), activity_id))
    return job_id


async def reconcile_due(worker, *, limit=32):
    """Visit a bounded, rotating page; enqueue only missing original observations."""
    limit = max(1, min(int(limit), 250))
    life = worker.life
    cursor = worker._activity_reconcile_cursor
    async with life.database.transaction(read_only=True) as connection:
        generation = await life.jobs.current_generation_on(connection)
        rows = await (await connection.execute(
            "SELECT id,actor_id,action_id,context FROM life.activities "
            "WHERE id>%s AND state IN ('running','dormant') AND action_id IS NOT NULL "
            "AND context IS NOT NULL ORDER BY id LIMIT %s", (cursor, limit + 1))).fetchall()
    page, more = rows[:limit], len(rows) > limit
    result = {"scanned": 0, "woken": 0, "complete": not more}
    for snapshot in page:
        worker._activity_reconcile_cursor = snapshot["id"]
        result["scanned"] += 1
        try:
            from .recovery import scheduled_context
            context = await scheduled_context(life, snapshot["context"])
            if context.actor_id != snapshot["actor_id"]:
                raise AppError("actor_scope_mismatch", status=403)
            async with life.database.transaction() as connection:
                await generation_on(life, connection, generation)
                row = await life.lock_row(connection, context,
                    "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE",
                    (snapshot["id"], snapshot["actor_id"]))
                if (not eligible(row) or row["action_id"] != snapshot["action_id"]
                        or row["context"] != snapshot["context"] or not generation_matches(row,generation)):
                    continue
                saved = observation(row, generation)
                now = datetime.now(UTC)
                due = timestamp(saved.get("due_at")) or recovery_due(row) or now + timedelta(seconds=15)
                job = (await life.jobs.observe_on(connection, saved["job_id"])
                       if type(saved.get("job_id")) is int else None)
                if job and job["job_state"] == "todo":
                    scheduled = job.get("scheduled_at")
                    if scheduled is None or scheduled <= due:
                        continue
                if job and job["job_state"] == "doing":
                    due = max(due, now + timedelta(seconds=15))
                queued = await ensure_reconcile_on(life, connection, activity_id=row["id"],
                    action_id=row["action_id"], data_generation=generation, due_at=due, reason="maintenance",
                    retire_due=bool(job and job["job_state"] == "doing"))
                result["woken"] += int(queued is not None)
        except AppError as error:
            if isinstance(error,RecoveryWait):
                raise
            if error.code == "job_generation_expired":
                raise
            if error.status not in {401, 403, 404, 410} and error.code not in {"source_unavailable", "access_changed"}:
                raise
    if not more:
        worker._activity_reconcile_cursor = ""
    return result
