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
        return False
    success = True
    for event in events(generation_key, actor_id, occurred_at, application):
        if await safe_call(connection, life.statistics.record_safely_on, event) is False:
            success = False
    if not success:
        await safe_call(connection, life.statistics.mark_producer_on, PRODUCER, watermark=generation_key,
            complete=False, detail={"reason": "plan_event_pending_replay"})
        if enqueue_replay:
            await safe_call(connection, life.jobs.enqueue, JobSpec("life.statistics_replay", "life-statistics:" + signature((generation_key, 0)),
                {"generation_key": generation_key, "sequence": 0}, subject_id=actor_id))
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
    """Replay one bounded ledger page; a full live sweep remains incomplete."""
    if life.statistics is None:
        return {"state": "unavailable", "next_cursor": cursor, "complete": False}
    limit = max(1, min(int(limit), 250))
    after_time = datetime.fromisoformat(cursor["reviewed_at"]) if cursor else None
    after_key = cursor["generation_key"] if cursor else None
    async with life.database.transaction() as connection:
        rows = await (await connection.execute("SELECT generation_key,actor_id,reviewed_at,application_results FROM life.plan_generations "
            "WHERE state='applied' AND application_results IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND (%s::timestamptz IS NULL OR (reviewed_at,generation_key)>(%s::timestamptz,%s)) "
            "ORDER BY reviewed_at,generation_key LIMIT %s", (after_time, after_time, after_key, limit + 1))).fetchall()
        current, failure = cursor, False
        for row in rows[:limit]:
            if not await record_on(life, connection, row["generation_key"], row["actor_id"], row["reviewed_at"],
                    row["application_results"], enqueue_replay=False):
                failure = True
                break
            current = {"reviewed_at": row["reviewed_at"].isoformat(), "generation_key": row["generation_key"]}
        more = failure or len(rows) > limit
        complete = not more and source_quiescent
        await safe_call(connection, life.statistics.mark_producer_on, PRODUCER,
            watermark=signature(current) if current else "empty", complete=complete,
            detail={"reason": "replay_failed" if failure else "scan_in_progress" if more else "complete" if complete else "live_producer"})
    return {"state": "retry" if failure else "pending" if more else "scanned", "next_cursor": current if more else None,
        "complete": complete}


async def enqueue_backfill(worker, cursor=None, *, delay=0):
    if worker.life.statistics is None:
        return
    async with worker.life.database.transaction() as connection:
        await worker.life.jobs.enqueue(connection, JobSpec("life.statistics_backfill", "life-statistics-scan:" + str(uuid4()),
            {"cursor": cursor}, scheduled_at=datetime.now(UTC) + timedelta(seconds=delay)))


async def scan(worker, cursor=None):
    result = await backfill(worker.life, cursor=cursor)
    if result["next_cursor"] is not None or result["state"] == "retry":
        await enqueue_backfill(worker, result["next_cursor"], delay=30 if result["state"] == "retry" else 0)
