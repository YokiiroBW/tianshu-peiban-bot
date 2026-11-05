"""Observable media acquisition facts, derived from the channel supplement ledger."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import logging
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass

PRODUCER = "channels.media"
SOURCE = "channels.media_supplements"


async def schedule_repair(service, connection, *, after_id="", scope="media"):
    try:
        async with connection.transaction():
            await service.runtime.jobs.enqueue(connection, JobSpec("channels.statistics_reconcile",
                "channels-statistics:"+scope+":"+str(uuid4()), {"after_id": after_id, "scope": scope}, ServiceClass.ELASTIC,
                datetime.now(UTC)+timedelta(seconds=10), execution_lane=ExecutionLane.CONTROL))
    except Exception:
        logging.getLogger(__name__).warning("channel_statistics_repair_enqueue_failed")


LEDGERS = {"ingress": ("channels.ingress", "channels.events"),
           "outbound": ("channels.outbound", "channels.calls")}


def ledger_event(scope, row):
    """Project channel transport facts, not a second count of conversation messages."""
    state = row["state"]
    if scope == "ingress":
        revisions = {"accepted": 1, "consumed": 2, "withdrawn": 3}
        public_states = {"accepted": "received", "consumed": "processed", "withdrawn": "withdrawn"}
        if state not in revisions:
            return None
        metrics = {"channel_ingress_accepted": 1,
                   "channel_ingress_processed": None if state == "withdrawn" else int(state == "consumed"),
                   "channel_ingress_withdrawn": int(state == "withdrawn")}
    elif scope == "outbound":
        revisions = {"prepared": 1, "sending": 2, "unknown": 3, "rejected": 4, "confirmed": 5}
        public_states = {"prepared": "queued", "sending": "running", "unknown": "unknown",
                         "rejected": "failed", "confirmed": "succeeded"}
        if state not in revisions:
            return None
        metrics = {"channel_calls_total": 1, "channel_calls_inflight": int(state == "sending"),
                   "channel_calls_confirmed": int(state == "confirmed"),
                   "channel_calls_rejected": int(state == "rejected"),
                   "channel_calls_unknown": int(state == "unknown")}
    else:
        raise ValueError("invalid_channel_statistics_scope")
    return {"family": "channels", "event_id": "channel-" + scope + ":" + row["id"],
            "revision": revisions[state], "occurred_at": row["created_at"],
            "module": "channels", "actor_id": None, "route_id": None,
            "dimensions": {"state": public_states[state]}, "metrics": metrics}


async def _record_ledger_event(service, connection, scope, row):
    event = ledger_event(scope, row)
    if event is None:
        return False
    # A call can safely return from sending to prepared before submission, so
    # state names do not form a monotone revision. The caller holds the source
    # row lock; advance the existing projection only when its facts changed.
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             ("operations.statistic:channels:" + event["event_id"],))
    old = await (await connection.execute(
        "SELECT revision,digest FROM operations.statistic_events WHERE family='channels' AND event_id=%s",
        (event["event_id"],))).fetchone()
    if old:
        event["revision"] = old["revision"]
        if service.statistics.matches_projection(event, old):
            return True
        event["revision"] += 1
    return bool(await service.statistics.record_safely_on(connection, event))


async def record_ledger_on(service, connection, *, scope, row):
    if service.statistics is None or not row:
        return
    producer, source = LEDGERS[scope]
    created, recorded = False, False
    try:
        async with connection.transaction():
            recorded = await _record_ledger_event(service, connection, scope, row)
            created = await service.statistics.register_live_producer_on(connection, producer, source=source)
            if not recorded:
                await service.statistics.mark_producer_on(connection, producer, complete=False,
                    detail={"source": source, "mode": "live", "reason": "projection_failed",
                            "backfill": {"state": "pending", "reason": "projection_failed"}})
    except Exception:
        recorded = False
        logging.getLogger(__name__).warning("channel_statistics_producer_unavailable")
    if created or not recorded:
        await schedule_repair(service, connection, scope=scope)


async def _unprojected_ledger(service, connection, *, scope, batch_size=500):
    _, source = LEDGERS[scope]
    after = ""
    while True:
        rows = await (await connection.execute(
            "SELECT s.id,s.state,s.created_at,p.revision AS projection_revision,p.digest AS projection_digest "
            "FROM " + source + " s LEFT JOIN operations.statistic_events p ON p.family='channels' "
            "AND p.event_id=%s||s.id WHERE s.id>%s ORDER BY s.id LIMIT %s",
            ("channel-" + scope + ":", after, batch_size))).fetchall()
        for row in rows:
            event = ledger_event(scope, row)
            stored = {"revision": row["projection_revision"], "digest": row["projection_digest"]}
            if event is not None and stored["revision"] is not None:
                event["revision"] = stored["revision"]
            if event is None or not service.statistics.matches_projection(event, stored):
                return row["id"]
        if len(rows) < batch_size:
            return None
        after = rows[-1]["id"]


async def backfill_ledger(service, *, scope, after_id="", limit=500):
    if scope not in LEDGERS:
        raise ValueError("invalid_channel_statistics_scope")
    if service.statistics is None:
        return {"next_cursor": after_id, "complete": False, "reason": "statistics_unavailable"}
    producer, source = LEDGERS[scope]
    limit = min(1000, max(1, int(limit)))
    async with service.runtime.database.transaction() as connection:
        rows = await (await connection.execute(
            "SELECT id,state,created_at FROM " + source + " WHERE id>%s ORDER BY id LIMIT %s FOR UPDATE",
            (after_id, limit + 1))).fetchall()
        watermark, failed = after_id, False
        for row in rows[:limit]:
            if not await _record_ledger_event(service, connection, scope, row):
                failed = True
                break
            watermark = row["id"]
        more, missing = len(rows) > limit, None
        if not more and not failed:
            await service.statistics.lock_producer_on(connection, producer, source=source)
            missing = await _unprojected_ledger(service, connection, scope=scope)
        complete = not more and not failed and missing is None
        state = "complete" if complete else "failed" if failed else "pending"
        await service.statistics.mark_producer_on(connection, producer, watermark=watermark,
            complete=complete, detail={"source": source, "mode": "live",
                "backfill": {"state": state, "reason": "projection_missing" if missing else None,
                             "as_of": datetime.now(UTC).isoformat()}})
    return {"next_cursor": None if complete else "" if missing else watermark, "complete": complete}


def media_event(*, identifier, actor_id, attempt, state, occurred_at):
    withdrawn = state == "withdrawn"
    metrics = {"media_completed": None if withdrawn else int(state == "complete"),
               "media_failed": None if withdrawn else int(state in {"waiting_retry", "waiting_config", "unavailable"})}
    # A new attempt replaces the preceding failure while it is in flight.
    # This also advances beyond revisions written by the former scheme.
    revision = 2 * attempt + (2 if withdrawn else 0 if state == "fetching" else 1)
    public_state = "withdrawn" if withdrawn else "running" if state == "fetching" else "succeeded" if state == "complete" else "failed"
    return {"family": "media",
        "event_id": "channel-media:"+identifier, "revision": revision, "occurred_at": occurred_at,
        "module": "channels", "actor_id": None if withdrawn else actor_id, "route_id": None,
        "dimensions": {"state": public_state}, "metrics": metrics}


def recovery_event(*, identifier, actor_id, attempt, state, occurred_at):
    if attempt < 1:
        return None
    withdrawn = state == "withdrawn"
    public_state = "withdrawn" if withdrawn else "verified" if state == "complete" else "failed" if state in {"waiting_retry", "waiting_config", "unavailable"} else "eligible"
    # Increment the projection revision scheme so existing rows with the old
    # metrics can be replaced without reusing a revision with a new digest.
    revision = 4 * attempt + 1 if withdrawn else 2 * (attempt * 2 - 1 if state == "fetching" else attempt * 2)
    return {"family": "recovery",
        "event_id": "channel-media-recovery:"+identifier, "revision": revision, "occurred_at": occurred_at,
        "module": "channels", "actor_id": None if withdrawn else actor_id, "route_id": None, "dimensions": {"state": public_state},
        "metrics": {"recovery_eligible": None if withdrawn else 1,
                    "recovery_verified": None if withdrawn else int(public_state == "verified"),
                    "recovery_evaluated": None if withdrawn else int(public_state in {"verified", "failed"}),
                    "recovery_failed": None if withdrawn else int(public_state == "failed")}}


async def media_on(service, connection, **facts):
    if service.statistics is None:
        return False
    event = media_event(**facts)
    return True if event is None else bool(await service.statistics.record_safely_on(connection, event))


async def recovery_on(service, connection, **facts):
    if service.statistics is None:
        return False
    event = recovery_event(**facts)
    return True if event is None else bool(await service.statistics.record_safely_on(connection, event))


async def record_status(service, connection, *, identifier, actor_id, attempt, state, occurred_at):
    if service.statistics is None:
        return
    first = await media_on(service, connection, identifier=identifier, actor_id=actor_id,
                           attempt=attempt, state=state, occurred_at=occurred_at)
    second = await recovery_on(service, connection, identifier=identifier, actor_id=actor_id,
                               attempt=attempt, state=state, occurred_at=occurred_at)
    created, registration_failed = False, False
    try:
        async with connection.transaction():
            created = await service.statistics.register_live_producer_on(connection, PRODUCER, source=SOURCE)
            if not first or not second:
                await service.statistics.mark_producer_on(connection, PRODUCER, complete=False,
                    detail={"source": SOURCE, "mode": "live", "reason": "projection_failed",
                            "backfill": {"state": "pending", "reason": "projection_failed"}})
    except Exception:
        registration_failed = True
        logging.getLogger(__name__).warning("channel_statistics_producer_unavailable")
    if created or registration_failed or not first or not second:
        await schedule_repair(service, connection)


async def withdraw_media_on(service, connection, event_keys):
    if service.statistics is None or not event_keys:
        return
    rows = await (await connection.execute(
        "SELECT id,attempts,observed_at FROM channels.media_supplements WHERE event_key=ANY(%s) ORDER BY id",
        (event_keys,))).fetchall()
    for row in rows:
        await record_status(service, connection, identifier=row["id"], actor_id=None,
                            attempt=row["attempts"], state="withdrawn", occurred_at=row["observed_at"])


async def _unprojected_media(service, connection, *, batch_size=500):
    """Verify current source revisions after locking the producer marker."""
    after = ""
    while True:
        rows = await (await connection.execute(
            "SELECT s.id,s.attempts,CASE WHEN e.state='withdrawn' OR s.state='withdrawn' "
            "THEN 'withdrawn' ELSE s.state END AS state,s.observed_at,s.attempt_started_at,e.actor_id,"
            "m.revision AS media_revision,m.digest AS media_digest,"
            "r.revision AS recovery_revision,r.digest AS recovery_digest "
            "FROM channels.media_supplements s JOIN channels.events e ON e.id=s.event_key "
            "LEFT JOIN operations.statistic_events m ON m.family='media' AND m.event_id='channel-media:'||s.id "
            "LEFT JOIN operations.statistic_events r ON r.family='recovery' AND r.event_id='channel-media-recovery:'||s.id "
            "WHERE s.id>%s AND (s.state<>'waiting_ingest' OR e.state='withdrawn') ORDER BY s.id LIMIT %s",
            (after, batch_size))).fetchall()
        for row in rows:
            facts = {"identifier": row["id"], "actor_id": row["actor_id"], "attempt": row["attempts"],
                     "state": row["state"], "occurred_at": row["attempt_started_at"]
                     if row["state"] == "fetching" else row["observed_at"]}
            media = media_event(**facts)
            recovery = recovery_event(**facts)
            if (media is not None and not service.statistics.matches_projection(media,
                    {"revision": row["media_revision"], "digest": row["media_digest"]})):
                return row["id"]
            if (recovery is not None and not service.statistics.matches_projection(recovery,
                    {"revision": row["recovery_revision"], "digest": row["recovery_digest"]})):
                return row["id"]
        if len(rows) < batch_size:
            return None
        after = rows[-1]["id"]


async def backfill_media(service, *, after_id="", limit=500):
    if service.statistics is None:
        return {"next_cursor": after_id, "complete": False, "reason": "statistics_unavailable"}
    limit = min(1000, max(1, int(limit)))
    async with service.runtime.database.transaction() as connection:
        rows = await (await connection.execute(
            "SELECT s.id,s.attempts,CASE WHEN e.state='withdrawn' OR s.state='withdrawn' "
            "THEN 'withdrawn' ELSE s.state END AS state,s.observed_at,s.attempt_started_at,e.actor_id FROM channels.media_supplements s "
            "JOIN channels.events e ON e.id=s.event_key WHERE s.id>%s AND (s.state<>'waiting_ingest' OR e.state='withdrawn') "
            "ORDER BY s.id LIMIT %s FOR UPDATE OF s", (after_id, limit+1))).fetchall()
        watermark, failed = after_id, False
        for row in rows[:limit]:
            first = await media_on(service, connection, identifier=row["id"], actor_id=row["actor_id"],
                attempt=row["attempts"], state=row["state"],
                occurred_at=row["attempt_started_at"] if row["state"] == "fetching" else row["observed_at"])
            second = await recovery_on(service, connection, identifier=row["id"], actor_id=row["actor_id"],
                attempt=row["attempts"], state=row["state"],
                occurred_at=row["attempt_started_at"] if row["state"] == "fetching" else row["observed_at"])
            if not first or not second:
                failed = True
                break
            watermark = row["id"]
        more = len(rows) > limit
        missing = None
        if not more and not failed:
            # Failed live writes update this same marker before their business
            # transaction commits. Holding its lock while checking the actual
            # source/projection pair prevents a concurrent miss being hidden.
            await service.statistics.lock_producer_on(connection, PRODUCER, source=SOURCE)
            missing = await _unprojected_media(service, connection)
        complete = not more and not failed and missing is None
        state = "complete" if complete else "failed" if failed else "pending"
        await service.statistics.mark_producer_on(connection, PRODUCER, watermark=watermark,
            complete=complete, detail={"source": SOURCE, "mode": "live", "write_failed": failed,
                "backfill": {"state": state, "reason": "projection_missing" if missing else None,
                             "as_of": datetime.now(UTC).isoformat()}})
    # A late, previously failed row can sort before this page's cursor. Start
    # the next bounded sweep from the beginning instead of skipping it.
    return {"next_cursor": None if complete else "" if missing else watermark, "complete": complete}
