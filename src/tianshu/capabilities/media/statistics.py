"""Media statistics are projections of the existing job ledger."""

from datetime import UTC, datetime
import json

from ..contracts import Fault


PRODUCER = "capabilities.media.jobs"


def event_for_job(job):
    if not isinstance(job, dict) or not job.get("job_id") or type(job.get("revision")) is not int:
        return None
    state = job.get("state")
    if state in {"completed", "published"}:
        observed = "succeeded"
    elif state == "failed":
        observed = "failed"
    elif state in {"auth_required", "waiting_metadata"}:
        observed = "blocked"
    elif state == "unknown":
        observed = "unknown"
    elif state == "cancelled":
        observed = "cancelled"
    elif state in {"queued", "retry_wait"}:
        observed = "queued"
    else:
        observed = "running"
    try:
        occurred_at = datetime.fromtimestamp(job["updated_at"], UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    kind = job.get("media_kind") or "video"
    route = kind if kind in {"video", "book", "comic", "audio"} else None
    context = job.get("context") or {}
    if not isinstance(context, dict):
        context = {}
    return {
        "family": "media", "event_id": job["job_id"], "revision": job["revision"],
        "occurred_at": occurred_at, "module": "media", "actor_id": context.get("actor_id"),
        "route_id": route, "dimensions": {"state": observed},
        "metrics": {
            "media_completed": None if observed == "unknown" else int(observed == "succeeded"),
            "media_failed": None if observed == "unknown" else int(observed == "failed"),
        },
    }


async def record_job_on(statistics, connection, job):
    event = event_for_job(job)
    if statistics is None or event is None:
        return False
    return bool(await statistics.record_safely_on(connection, event))


async def record_action_job_on(statistics, connection, action):
    if statistics is None or action.capability_id != "media.acquire":
        return False
    identifier = action.arguments.get("job_id")
    if not isinstance(identifier, str):
        return False
    row = await (await connection.execute(
        "SELECT body FROM capability_media.jobs WHERE id=%s", (identifier,))).fetchone()
    if not row:
        return False
    try:
        job = json.loads(row["body"])
    except (TypeError, ValueError):
        return False
    return await record_job_on(statistics, connection, job)


async def backfill(database, statistics, *, cursor=None, limit=256):
    """Page current authoritative jobs and report only coverage we can prove."""
    if statistics is None:
        raise Fault("statistics_unavailable", 503)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise Fault("invalid_input", 400)
    async with database.transaction() as conn:
        if cursor is None:
            row = await (await conn.execute("SELECT extract(epoch FROM now()) AS as_of")).fetchone()
            cursor = {"as_of": float(row["as_of"]), "last_id": "", "scanned": 0, "failed": 0}
        else:
            cursor = dict(cursor)
        rows = await (await conn.execute(
            "SELECT id,body FROM capability_media.jobs WHERE id>%s AND created<=%s AND updated<=%s "
            "ORDER BY id LIMIT %s", (cursor["last_id"], cursor["as_of"], cursor["as_of"], limit + 1))).fetchall()
        for row in rows[:limit]:
            try:
                job = json.loads(row["body"])
                recorded = await record_job_on(statistics, conn, job)
            except (TypeError, ValueError, KeyError):
                recorded = False
            cursor["scanned"] += 1
            cursor["failed"] += int(not recorded)
            cursor["last_id"] = row["id"]
        more = len(rows) > limit
        if not more:
            changed = await (await conn.execute(
                "SELECT EXISTS(SELECT 1 FROM capability_media.jobs WHERE created<=%s AND updated>%s) AS changed",
                (cursor["as_of"], cursor["as_of"]))).fetchone()
            complete = not cursor["failed"] and not changed["changed"]
            watermark = datetime.fromtimestamp(cursor["as_of"], UTC)
            await statistics.mark_producer_on(conn, PRODUCER, watermark=watermark, complete=complete,
                detail={"scanned": cursor["scanned"], "failed": cursor["failed"],
                        "changed_during_scan": changed["changed"], "basis": "current_job_ledger"})
        return {"next_cursor": cursor if more else None, "scanned": cursor["scanned"],
                "failed": cursor["failed"], "complete": None if more else complete,
                "as_of": datetime.fromtimestamp(cursor["as_of"], UTC).isoformat()}
