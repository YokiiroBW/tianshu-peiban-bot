"""Observable media acquisition facts, derived from the channel supplement ledger."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import logging
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass


async def schedule_repair(service, connection, *, after_id=""):
    try:
        async with connection.transaction():
            await service.runtime.jobs.enqueue(connection, JobSpec("channels.statistics_reconcile",
                "channels-statistics:"+str(uuid4()), {"after_id": after_id}, ServiceClass.ELASTIC,
                datetime.now(UTC)+timedelta(seconds=10), execution_lane=ExecutionLane.CONTROL))
    except Exception:
        logging.getLogger(__name__).warning("channel_statistics_repair_enqueue_failed")


async def media_on(service, connection, *, identifier, actor_id, attempt, state, occurred_at):
    if service.statistics is None:
        return False
    metrics = {"media_completed": int(state == "complete"), "media_failed": int(state in {"fetching", "waiting_retry", "waiting_config", "unavailable"})}
    revision = attempt if state == "fetching" and attempt else attempt + 1
    value = await service.statistics.record_safely_on(connection, {"family": "media",
        "event_id": "channel-media:"+identifier, "revision": revision, "occurred_at": occurred_at,
        "module": "channels", "actor_id": actor_id, "route_id": None,
        "dimensions": {"state": "succeeded" if state == "complete" else "failed"}, "metrics": metrics})
    return bool(value)


async def recovery_on(service, connection, *, identifier, actor_id, attempt, state, occurred_at):
    if service.statistics is None:
        return False
    if attempt < 1:
        return True
    public_state = "verified" if state == "complete" else "failed" if state in {"waiting_retry", "waiting_config", "unavailable"} else "eligible"
    return bool(await service.statistics.record_safely_on(connection, {"family": "recovery",
        "event_id": "channel-media-recovery:"+identifier, "revision": attempt*2-1 if state == "fetching" else attempt*2, "occurred_at": occurred_at,
        "module": "channels", "actor_id": actor_id, "route_id": None, "dimensions": {"state": public_state},
        "metrics": {"recovery_eligible": 1, "recovery_verified": int(public_state == "verified"),
                    "recovery_failed": int(public_state == "failed")}}))


async def record_status(service, connection, *, identifier, actor_id, attempt, state, occurred_at):
    if service.statistics is None:
        return
    first = True if state == "fetching" else await media_on(service, connection, identifier=identifier, actor_id=actor_id,
                                                             attempt=attempt, state=state, occurred_at=occurred_at)
    second = await recovery_on(service, connection, identifier=identifier, actor_id=actor_id,
                               attempt=attempt, state=state, occurred_at=occurred_at)
    if not first or not second:
        await schedule_repair(service, connection)


async def backfill_media(service, *, after_id="", limit=500):
    if service.statistics is None:
        return {"next_cursor": after_id, "complete": False, "reason": "statistics_unavailable"}
    limit = min(1000, max(1, int(limit)))
    async with service.runtime.database.transaction() as connection:
        rows = await (await connection.execute(
            "SELECT s.id,s.attempts,s.state,s.observed_at,s.attempt_started_at,e.actor_id FROM channels.media_supplements s "
            "JOIN channels.events e ON e.id=s.event_key WHERE s.id>%s AND s.state NOT IN ('waiting_ingest','withdrawn') "
            "ORDER BY s.id LIMIT %s", (after_id, limit+1))).fetchall()
        watermark, failed = after_id, False
        for row in rows[:limit]:
            first = await media_on(service, connection, identifier=row["id"], actor_id=row["actor_id"],
                attempt=row["attempts"], state=row["state"], occurred_at=row["observed_at"])
            second = await recovery_on(service, connection, identifier=row["id"], actor_id=row["actor_id"],
                attempt=row["attempts"], state=row["state"],
                occurred_at=row["attempt_started_at"] if row["state"] == "fetching" else row["observed_at"])
            if not first or not second:
                failed = True
                break
            watermark = row["id"]
        complete = len(rows)<=limit and not failed
        await service.statistics.mark_producer_on(connection, "channels.media", watermark=watermark,
            complete=complete, detail={"source": "channels.media_supplements", "write_failed": failed})
    return {"next_cursor": None if complete else watermark, "complete": complete}
