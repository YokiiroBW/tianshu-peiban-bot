"""Durable, independently recoverable consumers of an original Action result."""
from uuid import NAMESPACE_URL, uuid4, uuid5

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from .codec import document


CONSUMERS = ("renderer", "dialogue", "life_activity", "life_commitment", "detached", "memory")
TERMINAL = frozenset({"done", "not_applicable", "superseded", "invalidated"})
NONATOMIC_CONSUMERS = frozenset({"renderer", "dialogue"})


def event_identity(event):
    if event.get("type") != "capability.result" or event.get("version") != 1:
        raise AppError("unsupported_result_event", status=500)
    actual = event.get("receipt") or {}
    revision = event.get("result_revision")
    if (not isinstance(event.get("action_id"), str) or not isinstance(event.get("event_id"), str)
            or actual.get("receipt_id") != event["event_id"]
            or actual.get("action_id") != event["action_id"]
            or type(revision) is not int or revision < 0):
        raise AppError("result_event_identity_mismatch", status=409)
    return event["action_id"], event["event_id"], revision


async def register_on(connection, generation, event, original_job_id=None):
    action_id, receipt_id, revision = event_identity(event)
    inserted = await (await connection.execute(
        "INSERT INTO conversation.result_deliveries(data_generation,receipt_id,action_id,result_revision,original_job_id) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(data_generation,receipt_id) DO NOTHING RETURNING receipt_id",
        (generation, receipt_id, action_id, revision, original_job_id))).fetchone()
    row = await (await connection.execute(
        "SELECT * FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s FOR UPDATE",
        (generation, receipt_id))).fetchone()
    if row["action_id"] != action_id:
        raise AppError("result_event_identity_mismatch", status=409)
    if row["result_revision"] != revision:
        raise AppError("result_receipt_revision_conflict", status=409)
    if original_job_id is not None and row["original_job_id"] is None:
        await connection.execute(
            "UPDATE conversation.result_deliveries SET original_job_id=%s WHERE data_generation=%s AND receipt_id=%s",
            (original_job_id, generation, receipt_id))
        row = {**row, "original_job_id": original_job_id}
    return {**row, "_new_delivery": bool(inserted)}


async def resolve_owners_on(connection, row, action, actor_id):
    if row["owner_resolved"]:
        return row["owner_activity_id"], row["owner_commitment_id"]
    activity = await (await connection.execute(
        "SELECT id FROM life.activities WHERE actor_id=%s AND action_id=%s", (actor_id, row["action_id"]))).fetchone()
    commitment = await (await connection.execute(
        "SELECT id FROM life.commitments WHERE actor_id=%s AND action_id=%s", (actor_id, row["action_id"]))).fetchone()
    activity_id = activity["id"] if activity else action.get("activity_id")
    intent = action.get("intent_key") or ""
    if not commitment and intent.startswith("commitment:") and str(uuid5(NAMESPACE_URL, intent)) == row["action_id"]:
        parts = intent.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            candidate = parts[0][len("commitment:"):]
            # The immutable Action intent and deterministic id keep ownership
            # even after recurrence has cleared or removed its original row.
            commitment = {"id": candidate}
    commitment_id = commitment["id"] if commitment else None
    await connection.execute(
        "UPDATE conversation.result_deliveries SET owner_activity_id=%s,owner_commitment_id=%s,"
        "owner_resolved=true,updated_at=now() WHERE data_generation=%s AND receipt_id=%s",
        (activity_id, commitment_id, row["data_generation"], row["receipt_id"]))
    return activity_id, commitment_id


async def initialize_on(connection, generation, event, action, actor_id):
    row = await register_on(connection, generation, event)
    if row["state"] != "pending":
        return row
    owners = await resolve_owners_on(connection, row, action, actor_id)
    for consumer in CONSUMERS:
        await connection.execute(
            "INSERT INTO conversation.result_consumer_progress(data_generation,receipt_id,consumer,action_id,result_revision) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (generation, row["receipt_id"], consumer, row["action_id"], row["result_revision"]))
    return {**row, "owner_activity_id": owners[0], "owner_commitment_id": owners[1], "owner_resolved": True}


async def progress_on(connection, generation, receipt_id):
    """Expose only proofs actually recorded for this original receipt."""
    rows = await (await connection.execute(
        "SELECT consumer,data_generation,action_id,receipt_id,result_revision,disposition,reason,proof "
        "FROM conversation.result_consumer_progress "
        "WHERE data_generation=%s AND receipt_id=%s ORDER BY consumer",
        (generation, receipt_id))).fetchall()
    fields = ("data_generation", "action_id", "receipt_id", "result_revision", "disposition", "reason", "proof")
    return {row["consumer"]: {key: row[key] for key in fields} for row in rows}


async def claim_on(connection, generation, receipt_id, consumer):
    row = await (await connection.execute(
        "SELECT *,now() AS now FROM conversation.result_consumer_progress "
        "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s FOR UPDATE",
        (generation, receipt_id, consumer))).fetchone()
    if not row:
        raise AppError("result_consumer_missing", status=409)
    if row["disposition"] in TERMINAL:
        return None, row
    if row["disposition"] == "blocked":
        raise AppError("result_consumer_blocked", status=409, details={"consumer": consumer})
    if row["disposition"] == "running":
        if not row["owner_token"] or row["lease_until"] is None:
            raise AppError("result_consumer_lease_unknown", status=409)
        if row["lease_until"] > row["now"]:
            raise RecoveryWait("result_consumer_busy", row["lease_until"], dependency="result_consumer")
        if consumer in NONATOMIC_CONSUMERS:
            raise AppError("result_consumer_outcome_unknown", status=409)
    if row.get("rbi_retry_at") and row["rbi_retry_at"] > row["now"]:
        raise RecoveryWait("result_consumer_waiting", row["rbi_retry_at"], dependency="result_consumer")
    token = str(uuid4())
    await connection.execute(
        "UPDATE conversation.result_consumer_progress SET disposition='running',owner_token=%s,rbi_retry_at=NULL,"
        "lease_until=now()+interval '5 minutes',attempts=attempts+1,updated_at=now() "
        "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s",
        (token, generation, receipt_id, consumer))
    return token, row


async def complete_on(connection, generation, receipt_id, consumer, token, disposition="done", *, reason=None, proof=None):
    if disposition not in TERMINAL:
        raise ValueError("invalid_result_disposition")
    changed = await (await connection.execute(
        "UPDATE conversation.result_consumer_progress SET disposition=%s,reason=%s,proof=%s,"
        "owner_token=NULL,lease_until=NULL,rbi_retry_at=NULL,updated_at=now() WHERE data_generation=%s AND receipt_id=%s "
        "AND consumer=%s AND disposition='running' AND owner_token=%s AND lease_until>now() RETURNING consumer",
        (disposition, reason, document(proof or {}), generation, receipt_id, consumer, token))).fetchone()
    if not changed:
        raise AppError("result_consumer_claim_lost", status=409)


async def release_on(connection, generation, receipt_id, consumer, token, *, blocked=False, reason=None, retry_at=None):
    await connection.execute(
        "UPDATE conversation.result_consumer_progress SET disposition=%s,reason=%s,rbi_retry_at=%s,"
        "owner_token=NULL,lease_until=NULL,updated_at=now() "
        "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s AND disposition='running' AND owner_token=%s",
        ("blocked" if blocked else "pending", reason, retry_at, generation, receipt_id, consumer, token))


async def finish_on(connection, generation, receipt_id):
    row = await (await connection.execute(
        "SELECT * FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s FOR UPDATE",
        (generation, receipt_id))).fetchone()
    progress = await (await connection.execute(
        "SELECT action_id,result_revision,disposition FROM conversation.result_consumer_progress WHERE data_generation=%s AND receipt_id=%s",
        (generation, receipt_id))).fetchall()
    if row and any((item["action_id"], item["result_revision"]) != (row["action_id"], row["result_revision"])
                   for item in progress):
        raise AppError("result_receipt_revision_conflict", status=409)
    if row and len(progress) == len(CONSUMERS) and all(item["disposition"] in TERMINAL for item in progress):
        await connection.execute(
            "UPDATE conversation.result_deliveries SET state='completed',updated_at=now() WHERE data_generation=%s AND receipt_id=%s AND state='pending'",
            (generation, receipt_id))
        await connection.execute(
            "INSERT INTO conversation.result_consumption(receipt_id,action_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (receipt_id, row["action_id"]))
        return True
    return False


async def queue_head_on(connection, jobs, generation, current):
    actual = current.get("receipt")
    if not actual:
        raise AppError("result_head_unavailable", status=409)
    if actual.get("execution") in {"pending", "running"}:
        return {"state": "waiting", "reason": "action_not_terminal"}
    if actual.get("execution") not in {"succeeded", "failed", "cancelled", "unknown"}:
        raise AppError("result_head_unavailable", status=409)
    action = current["action"]
    observed = current.get("result_observed_at")
    event = {"type": "capability.result", "version": 1, "event_id": actual["receipt_id"],
             "action_id": actual["action_id"], "result_revision": current["result_revision"],
             "result_observed_at": observed.isoformat() if observed else None,
             "context": action["context"], "source_refs": action.get("source_refs", []), "receipt": actual}
    from .rbi_result_wake import ensure_on
    return await ensure_on(connection, jobs, generation, event,
        initial_key=f"result-head:{generation}:{actual['action_id']}:{current['result_revision']}:{actual['receipt_id']}")


async def supersede_on(connection, generation, receipt_id):
    await connection.execute(
        "SELECT receipt_id FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s FOR UPDATE",
        (generation, receipt_id))
    await connection.execute(
        "UPDATE conversation.result_consumer_progress SET disposition='blocked',"
        "reason=COALESCE(reason,'result_head_advanced_execution_unconfirmed'),"
        "owner_token=NULL,lease_until=NULL,updated_at=now() WHERE data_generation=%s AND receipt_id=%s "
        "AND disposition IN ('pending','running')", (generation, receipt_id))
    row = await (await connection.execute(
        "UPDATE conversation.result_deliveries SET state='superseded',reason='newer_action_result',updated_at=now() "
        "WHERE data_generation=%s AND receipt_id=%s AND state='pending' RETURNING action_id",
        (generation, receipt_id))).fetchone()
    progress = await (await connection.execute(
        "SELECT disposition FROM conversation.result_consumer_progress WHERE data_generation=%s AND receipt_id=%s",
        (generation, receipt_id))).fetchall()
    if row and len(progress) == len(CONSUMERS) and all(part["disposition"] in TERMINAL for part in progress):
        await connection.execute("INSERT INTO conversation.result_consumption(receipt_id,action_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                                 (receipt_id, row["action_id"]))


async def invalidate_on(connection, generation, event, reason):
    row = await register_on(connection, generation, event)
    if row["state"] != "pending":
        return
    for consumer in CONSUMERS:
        await connection.execute(
            "INSERT INTO conversation.result_consumer_progress(data_generation,receipt_id,consumer,action_id,result_revision) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (generation, row["receipt_id"], consumer, row["action_id"], row["result_revision"]))
    await connection.execute(
        "UPDATE conversation.result_consumer_progress SET disposition='blocked',"
        "reason=COALESCE(reason,'source_withdrawal_execution_unconfirmed'),owner_token=NULL,lease_until=NULL,updated_at=now() "
        "WHERE data_generation=%s AND receipt_id=%s AND disposition IN ('pending','running')", (generation, row["receipt_id"]))
    await connection.execute(
        "UPDATE conversation.result_deliveries SET state='invalidated',reason=%s,updated_at=now() "
        "WHERE data_generation=%s AND receipt_id=%s AND state='pending'", (reason, generation, row["receipt_id"]))
    progress = await (await connection.execute(
        "SELECT disposition FROM conversation.result_consumer_progress WHERE data_generation=%s AND receipt_id=%s",
        (generation, row["receipt_id"]))).fetchall()
    if len(progress) == len(CONSUMERS) and all(part["disposition"] in TERMINAL for part in progress):
        await connection.execute(
            "INSERT INTO conversation.result_consumption(receipt_id,action_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (row["receipt_id"], row["action_id"]))
