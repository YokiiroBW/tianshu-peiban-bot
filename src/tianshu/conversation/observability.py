"""Message facts emitted from the existing conversation and delivery ledgers."""
import logging
from uuid import uuid4

from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass
from .codec import receipt


async def record(statistics, connection, *, event_id, revision, occurred_at, actor_id, state, metrics):
    if statistics is None:
        return False
    return await statistics.record_safely_on(connection, {
        "family": "messages", "event_id": event_id, "revision": revision,
        "occurred_at": occurred_at, "module": "conversation", "actor_id": actor_id,
        "route_id": None, "dimensions": {"state": state}, "metrics": metrics,
    })


async def schedule_repair_on(jobs, connection):
    try:
        async with connection.transaction():
            await jobs.enqueue(connection, JobSpec("conversation.statistics_reconcile",
                "conversation-statistics:"+str(uuid4()), {}, ServiceClass.ELASTIC,
                execution_lane=ExecutionLane.CONTROL))
    except Exception:
        logging.getLogger(__name__).warning("conversation_statistics_repair_enqueue_failed")


async def received_on(statistics, connection, *, input_id, actor_id, occurred_at, reply_required):
    return await record(statistics, connection, event_id="received:" + input_id, revision=1,
                 occurred_at=occurred_at, actor_id=actor_id, state="received",
                 metrics={"received": 1, "reply_required": int(bool(reply_required))})


async def processed_on(statistics, connection, *, input_id, actor_id, occurred_at):
    return await record(statistics, connection, event_id="processed:" + input_id, revision=1,
                 occurred_at=occurred_at, actor_id=actor_id, state="processed", metrics={"processed": 1})


async def withdrawn_on(statistics, connection, *, input_id, actor_id, occurred_at):
    return await record(statistics, connection, event_id="withdrawn:" + input_id, revision=1,
                 occurred_at=occurred_at, actor_id=actor_id, state="withdrawn", metrics={"withdrawn": 1})


async def delivery_on(statistics, connection, *, expression_id, actor_id, revision, occurred_at, value):
    value = receipt(value)
    if value is None:
        return True
    identifiers = value.result.get("message_ids", ())
    if value.delivery == "delivered":
        count, state = max(len(identifiers), 1), "delivered"
    elif identifiers:
        count = len(identifiers)
        state = "unknown" if "unknown" in {value.execution, value.effect, value.delivery} else "failed"
    elif "unknown" in {value.execution, value.effect, value.delivery}:
        count, state = None, "unknown"
    elif value.execution in {"pending", "running"} or value.delivery == "pending":
        count, state = None, "queued" if value.execution == "pending" else "running"
    else:
        count, state = 0, "cancelled" if value.execution == "cancelled" else "failed"
    return await record(statistics, connection, event_id="delivery:" + expression_id, revision=revision,
                 occurred_at=occurred_at, actor_id=actor_id, state=state, metrics={"delivered": count})


async def backfill_inputs(service, *, after_sequence=0, limit=500):
    if service.statistics is None:
        return {"next_cursor": after_sequence, "complete": False, "reason": "statistics_unavailable"}
    limit = min(1000, max(1, int(limit)))
    async with service.database.transaction() as connection:
        rows = await (await connection.execute(
            "SELECT t.turn_id,t.actor_id,t.sequence,t.received_at,COALESCE(t.started_at,p.started_at) AS started_at,"
            "t.reply_required,t.withdrawn_at FROM conversation.turns t LEFT JOIN conversation.turns p ON p.turn_id=t.merged_into "
            "WHERE t.sequence>%s ORDER BY t.sequence LIMIT %s", (after_sequence, limit+1))).fetchall()
        watermark, failed = after_sequence, False
        for row in rows[:limit]:
            recorded = await received_on(service.statistics, connection, input_id=row["turn_id"], actor_id=row["actor_id"],
                              occurred_at=row["received_at"], reply_required=row["reply_required"])
            if row["started_at"]:
                recorded = await processed_on(service.statistics, connection, input_id=row["turn_id"], actor_id=row["actor_id"], occurred_at=row["started_at"]) and recorded
            if row["withdrawn_at"]:
                recorded = await withdrawn_on(service.statistics, connection, input_id=row["turn_id"], actor_id=row["actor_id"], occurred_at=row["withdrawn_at"]) and recorded
            if not recorded:
                failed = True
                break
            watermark = row["sequence"]
        complete = len(rows) <= limit and not failed
        await service.statistics.mark_producer_on(connection, "conversation.messages", watermark=str(watermark),
            complete=complete, detail={"source": "conversation.turns", "write_failed": failed})
    return {"next_cursor": None if complete else watermark, "complete": complete}


async def backfill_deliveries(service, *, after_id="", limit=500):
    if service.statistics is None:
        return {"next_cursor": after_id, "complete": False, "reason": "statistics_unavailable"}
    limit = min(1000, max(1, int(limit)))
    async with service.database.transaction() as connection:
        rows = await (await connection.execute(
            "SELECT expression_id,actor_id,revision,created_at,completed_at,result_observed_at,receipt FROM conversation.expressions "
            "WHERE expression_id>%s ORDER BY expression_id LIMIT %s", (after_id, limit+1))).fetchall()
        watermark, failed = after_id, False
        for row in rows[:limit]:
            recorded = await delivery_on(service.statistics, connection, expression_id=row["expression_id"], actor_id=row["actor_id"],
                revision=row["revision"], occurred_at=row["result_observed_at"] or row["completed_at"] or row["created_at"], value=row["receipt"])
            if not recorded:
                failed = True
                break
            watermark = row["expression_id"]
        complete = len(rows) <= limit and not failed
        await service.statistics.mark_producer_on(connection, "conversation.delivery", watermark=watermark,
            complete=complete, detail={"source": "conversation.expressions", "write_failed": failed})
    return {"next_cursor": None if complete else watermark, "complete": complete}
