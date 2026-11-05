"""Report Life plan outcomes from their existing immutable generation ledger."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.conversation.codec import signature
from tianshu.runtime.jobs import JobSpec


PRODUCER = "life.planning"
STATUSES = ("scheduled", "intentional_idle", "deferred_no_slot", "stale_state", "invalid_proposal")


def events(generation_key, actor_id, occurred_at, application):
    items = application.get("items", ()) if isinstance(application, dict) else application.items
    for item in items:
        status = item.get("status") if isinstance(item, dict) else item.status
        key = item.get("candidate_key") if isinstance(item, dict) else item.candidate_key
        if status not in STATUSES:
            continue
        yield {"family": "life", "event_id": "plan:" + signature((generation_key, key)), "revision": 1,
            "occurred_at": occurred_at, "module": "life", "actor_id": actor_id, "route_id": None,
            "dimensions": {"state": status}, "metrics": {"life_" + name: int(name == status) for name in STATUSES}}


async def safe_call(connection, operation, *args, **kwargs):
    # A broken statistics backend cannot roll back the domain transaction.
    await connection.execute("SAVEPOINT life_statistics")
    try:
        value = await operation(connection, *args, **kwargs)
    except Exception:
        await connection.execute("ROLLBACK TO SAVEPOINT life_statistics")
        value = False
    finally:
        await connection.execute("RELEASE SAVEPOINT life_statistics")
    return value


async def record_on(life, connection, generation_key, actor_id, occurred_at, application, *, enqueue_replay):
    if life.statistics is None:
        await connection.execute("INSERT INTO life.plan_statistics_pending(generation_key) VALUES (%s) ON CONFLICT DO NOTHING",(generation_key,))
        return False
    success = True
    for event in events(generation_key, actor_id, occurred_at, application):
        if await safe_call(connection, life.statistics.record_safely_on, event) is False:
            success = False
    if not success:
        # Coordinate the completeness boundary with a live failed projection.
        # This lock is not taken on the normal successful planning path.
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('life.planning.statistics',0))")
        await connection.execute("INSERT INTO life.plan_statistics_pending(generation_key) VALUES (%s) ON CONFLICT DO NOTHING",(generation_key,))
        await safe_call(connection, life.statistics.mark_producer_on, PRODUCER, watermark=generation_key,
            complete=False, detail={"reason": "plan_event_pending_replay"})
        if enqueue_replay:
            await safe_call(connection, life.jobs.enqueue, JobSpec("life.statistics_replay", "life-statistics:" + signature((generation_key, 0)),
                {"generation_key": generation_key, "sequence": 0}, subject_id=actor_id))
    else:
        await connection.execute("DELETE FROM life.plan_statistics_pending WHERE generation_key=%s",(generation_key,))
    return success


async def replay(worker, generation_key, sequence):
    life = worker.life
    if life.statistics is None:
        return
    async with life.database.transaction() as connection:
        row = await (await connection.execute("SELECT actor_id,reviewed_at,application_results FROM life.plan_generations "
            "WHERE generation_key=%s AND state='applied'", (generation_key,))).fetchone()
        if not row or not row["application_results"]:
            return
        success = await record_on(life, connection, generation_key, row["actor_id"], row["reviewed_at"],
            row["application_results"], enqueue_replay=False)
        if not success:
            delay = min(900, 30 * 2 ** min(sequence, 5))
            await life.jobs.enqueue(connection, JobSpec("life.statistics_replay", "life-statistics:" + signature((generation_key, sequence + 1)),
                {"generation_key": generation_key, "sequence": sequence + 1},
                scheduled_at=datetime.now(UTC) + timedelta(seconds=delay), subject_id=row["actor_id"]))


async def backfill(life, *, cursor=None, limit=100, source_quiescent=False):
    """Replay through one fixed ledger boundary while live writes continue."""
    if life.statistics is None:
        return {"state": "unavailable", "next_cursor": cursor, "complete": False}
    limit = max(1, min(int(limit), 250))
    async with life.database.transaction() as connection:
        if not cursor or "as_of" not in cursor:
            as_of = (await (await connection.execute("SELECT now() AS value")).fetchone())["value"]
            upper = await (await connection.execute(
                "SELECT reviewed_at,generation_key FROM life.plan_generations WHERE state='applied' "
                "AND application_results IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed_at<=%s "
                "ORDER BY reviewed_at DESC,generation_key DESC LIMIT 1",(as_of,))).fetchone()
            cursor = {"as_of":as_of.isoformat(),"upper":({"reviewed_at":upper["reviewed_at"].isoformat(),
                "generation_key":upper["generation_key"]} if upper else None),"reviewed_at":None,"generation_key":None}
        as_of = datetime.fromisoformat(cursor["as_of"])
        upper = cursor["upper"]
        after_time = datetime.fromisoformat(cursor["reviewed_at"]) if cursor.get("reviewed_at") else None
        after_key = cursor.get("generation_key")
        upper_time = datetime.fromisoformat(upper["reviewed_at"]) if upper else None
        upper_key = upper["generation_key"] if upper else None
        rows = await (await connection.execute("SELECT generation_key,actor_id,reviewed_at,application_results FROM life.plan_generations "
            "WHERE state='applied' AND application_results IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND (%s::timestamptz IS NULL OR (reviewed_at,generation_key)>(%s::timestamptz,%s)) "
            "AND %s::timestamptz IS NOT NULL AND (reviewed_at,generation_key)<=(%s::timestamptz,%s) "
            "ORDER BY reviewed_at,generation_key LIMIT %s", (after_time,after_time,after_key,upper_time,upper_time,upper_key,limit+1))).fetchall()
        current, failure = cursor, False
        for row in rows[:limit]:
            if not await record_on(life, connection, row["generation_key"], row["actor_id"], row["reviewed_at"],
                    row["application_results"], enqueue_replay=False):
                failure = True
                break
            current = {**current,"reviewed_at":row["reviewed_at"].isoformat(),"generation_key":row["generation_key"]}
        more = failure or len(rows) > limit
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('life.planning.statistics',0))")
        pending = await (await connection.execute(
            "SELECT 1 FROM runtime.job_keys k JOIN procrastinate_jobs j ON j.id=k.job_id "
            "WHERE k.task='life.statistics_replay' AND k.cancelled_at IS NULL AND j.status IN ('todo','doing') LIMIT 1")).fetchone()
        pending_projection = await (await connection.execute("SELECT 1 FROM life.plan_statistics_pending LIMIT 1")).fetchone()
        producer = await (await connection.execute(
            "SELECT last_seen,detail FROM operations.statistic_producers WHERE producer=%s FOR UPDATE",(PRODUCER,))).fetchone()
        concurrent_failure = bool(producer and producer["last_seen"] >= as_of and
            (producer["detail"] or {}).get("reason") in {"plan_event_pending_replay","replay_failed"})
        complete = not more and not pending and not pending_projection and not concurrent_failure
        backfill_state = "failed" if failure else "pending" if not complete else "complete"
        await safe_call(connection, life.statistics.mark_producer_on, PRODUCER,
            watermark=signature(current) if current else "empty", complete=complete,
            detail={"mode":"quiescent" if source_quiescent else "live",
                "reason":"replay_failed" if failure else "scan_in_progress" if not complete else "complete",
                "backfill":{"state":backfill_state,"as_of":as_of.isoformat(),"watermark":upper}})
    next_cursor = current if not complete and (more or pending) else None
    return {"state": "retry" if failure else "pending" if not complete else "scanned", "next_cursor": next_cursor,
        "complete": complete}


async def enqueue_backfill(worker, cursor=None, *, delay=0):
    if worker.life.statistics is None:
        return
    async with worker.life.database.transaction() as connection:
        await worker.life.jobs.enqueue(connection, JobSpec("life.statistics_backfill", "life-statistics-scan:" + str(uuid4()),
            {"cursor": cursor}, scheduled_at=datetime.now(UTC) + timedelta(seconds=delay)))


async def scan(worker, cursor=None):
    result = await backfill(worker.life, cursor=cursor)
    if result["next_cursor"] is not None or result["state"] in {"retry","pending"}:
        await enqueue_backfill(worker, result["next_cursor"], delay=30 if result["state"] in {"retry","pending"} else 0)
