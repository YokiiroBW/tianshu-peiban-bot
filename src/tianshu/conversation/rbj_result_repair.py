"""Reconcile one original Life consumer using its own durable proof."""

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import current_job_execution
from .codec import document, signature
from .rbg_result_ledger import TERMINAL, event_identity
from .rbj_result_budget import matching_life_proof
from .rbi_result_wake import EXECUTION_OWNER, MAX_FAILURES, execution_identity


async def progress_on(connection, generation, event, consumer, *, lock=False):
    action_id, receipt_id, revision = event_identity(event)
    query = ("SELECT *,now() AS observed_at FROM conversation.result_consumer_progress "
             "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s")
    if lock:
        query += " FOR UPDATE"
    row = await (await connection.execute(query, (generation, receipt_id, consumer))).fetchone()
    if row and (row["action_id"] != action_id or row["result_revision"] != revision):
        raise AppError("result_receipt_revision_conflict", status=409)
    return row


def completed_progress(generation, event, consumer, row, *, owner_id=None):
    if not row or row["disposition"] not in TERMINAL:
        return None
    if row["disposition"] == "done" and not matching_life_proof(
            event, consumer, row, generation=generation, owner_id=owner_id):
        return {"state": "unknown", "reason": "result_consumer_proof_incomplete"}
    if row["disposition"] == "not_applicable" and owner_id is not None:
        return {"state": "unknown", "reason": "result_owner_disposition_conflict"}
    return {"state": "verified", "reason": row["disposition"], "progress_recorded": True,
            "disposition": row["disposition"], "terminal_reason": row["reason"],
            "consumer": consumer, "proof": row["proof"]}


async def inspect_target_on(connection, life_worker, context, result, revision, *,
                            generation, event, consumer, owner_id, allow_blocked_recheck=False):
    """Caller has already fenced Sources and the exact original Action head."""
    previous = await progress_on(connection, generation, event, consumer)
    completed = completed_progress(generation, event, consumer, previous, owner_id=owner_id)
    if completed:
        return completed
    aggregate = await (await connection.execute(
        "SELECT action_id FROM conversation.result_consumption WHERE receipt_id=%s",
        (event["event_id"],))).fetchone()
    if aggregate:
        if aggregate["action_id"] != event["action_id"]:
            raise AppError("result_event_identity_mismatch", status=409)
        return {"state": "unknown", "reason": "result_consumer_proof_missing"}
    recheckable = {"result_head_advanced_execution_unconfirmed"}
    if (previous and previous["disposition"] == "blocked" and previous["reason"] not in recheckable
            and not allow_blocked_recheck):
        return {"state": "unknown", "reason": previous["reason"] or "result_consumer_blocked"}
    if previous and previous["disposition"] == "running" and previous["lease_until"] \
            and previous["lease_until"] > previous["observed_at"]:
        return {"state": "waiting", "reason": "result_consumer_busy", "due_at": previous["lease_until"]}
    if life_worker is None or not callable(getattr(life_worker, "inspect_result_repair_on", None)):
        return {"state": "blocked", "reason": "life_repair_inspection_unavailable"}
    evidence = await life_worker.inspect_result_repair_on(connection, context, result, revision,
        consumer=consumer, owner_id=owner_id, data_generation=generation)
    if not isinstance(evidence, dict) or evidence.get("state") not in {
            "not_applied", "verified", "superseded", "unknown"} or not evidence.get("reason"):
        raise AppError("life_repair_inspection_invalid", status=503)
    # Domain state is locked by the Life port. Recheck the proof after it to
    # catch a prior atomic commit without deriving success from domain status.
    current = await progress_on(connection, generation, event, consumer)
    completed = completed_progress(generation, event, consumer, current, owner_id=owner_id)
    if completed:
        return completed
    if (current and current["disposition"] == "blocked" and current["reason"] not in recheckable
            and not allow_blocked_recheck):
        return {"state": "unknown", "reason": current["reason"] or "result_consumer_blocked"}
    if current and current["disposition"] == "running" and current["lease_until"] \
            and current["lease_until"] > current["observed_at"]:
        return {"state": "waiting", "reason": "result_consumer_busy", "due_at": current["lease_until"]}
    if evidence["state"] == "not_applied" and not evidence.get("owner_id"):
        return {"state": "unknown", "reason": "result_owner_unconfirmed"}
    if evidence["state"] == "not_applied" and owner_id is not None and evidence["owner_id"] != owner_id:
        return {"state": "unknown", "reason": "result_owner_changed"}
    return evidence


async def evidence_on(connection, generation, event, consumer, repair_ref):
    action_id, receipt_id, revision = event_identity(event)
    row = await (await connection.execute(
        "SELECT * FROM conversation.rbj_result_repair_evidence "
        "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s AND repair_ref=%s",
        (generation, receipt_id, consumer, repair_ref))).fetchone()
    if row and (row["action_id"] != action_id or row["result_revision"] != revision
                or row["material_digest"] != signature(event)):
        raise AppError("result_repair_evidence_conflict", status=409)
    return row


async def stop_preflight_on(connection, jobs, generation, event, execution, consumer, repair_ref, *,
                            decision, reason, owner_id=None):
    """Persist an established no-apply decision before asking Runtime to seal it."""
    if decision not in {"closed", "blocked", "unknown"} or not isinstance(reason, str) or not reason:
        raise ValueError("invalid_result_preflight_stop")
    action_id, receipt_id, revision = event_identity(event)
    material_digest = signature(event)
    observation = "result-preflight:" + signature([generation, execution["job_id"], execution["native_attempt"],
        action_id, receipt_id, revision, consumer, repair_ref, owner_id, decision, reason, material_digest])
    await connection.execute(
        "INSERT INTO conversation.rbj_result_preflight_stops "
        "(data_generation,job_id,native_attempt,action_id,receipt_id,result_revision,consumer,repair_ref,owner_id,"
        "decision,reason,material_digest,observation_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING",
        (generation, execution["job_id"], execution["native_attempt"], action_id, receipt_id, revision,
         consumer, repair_ref, owner_id, decision, reason, material_digest, observation))
    saved = await (await connection.execute(
        "SELECT observation_ref FROM conversation.rbj_result_preflight_stops "
        "WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (generation, execution["job_id"], execution["native_attempt"]))).fetchone()
    if not saved or saved["observation_ref"] != observation:
        raise AppError("result_preflight_stop_conflict", status=409)
    stopped = await jobs.record_repair_preflight_stop_on(connection, owner=EXECUTION_OWNER,
        identity=execution_identity(generation, event), data_generation=generation, repair_ref=repair_ref,
        consumer=consumer, job_id=execution["job_id"], native_attempt=execution["native_attempt"],
        reason=decision, observation_ref=observation)
    if stopped.get("state") != "preflight_stopped":
        raise AppError(stopped.get("reason") or "result_preflight_stop_unconfirmed", status=409)
    return {"state": "preflight_stopped", "reason": reason, "decision": decision}


def result_proof_observation(generation, event, consumer, repair_ref, *, owner_id, disposition, reason, proof):
    """Identify the persisted consumer outcome, independent of the observing path."""
    action_id, receipt_id, revision = event_identity(event)
    return "result-proof:" + signature([generation, action_id, receipt_id, revision, consumer, repair_ref,
                                        owner_id, disposition, reason or disposition, proof])


async def close_from_proof_on(connection, jobs, generation, event, consumer, repair_ref, proof, *,
                              disposition="done", reason=None, owner_id=None):
    """Close only this grant; unresolved original facts retain their identity."""
    identity = execution_identity(generation, event)
    current_job = current_job_execution()
    budget = await jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER, identity=identity,
        data_generation=generation, max_failures=MAX_FAILURES, repair_ref=repair_ref, consumer=consumer,
        current_job=current_job)
    observation = result_proof_observation(generation, event, consumer, repair_ref, owner_id=owner_id,
        disposition=disposition, reason=reason, proof=proof)
    for item in budget.get("unresolved_executions", ()):
        if (item.get("purpose") != "repair" or item.get("repair_ref") != repair_ref
                or item.get("consumer") != consumer):
            continue
        if disposition != "done" or not matching_life_proof(event, consumer, {
                "data_generation": generation, "action_id": event["action_id"],
                "receipt_id": event["event_id"], "result_revision": event["result_revision"],
                "disposition": "done", "proof": proof}, generation=generation, owner_id=owner_id):
            return {"state": "blocked", "reason": "execution_outcome_unknown", "outcome": "unknown"}
        resolved = await jobs.resolve_execution_on(connection, owner=EXECUTION_OWNER, identity=identity,
            data_generation=generation, job_id=item["job_id"], native_attempt=item["native_attempt"],
            outcome="completed", observation_ref=observation, current_job=current_job)
        if not resolved.get("recorded") and resolved.get("outcome") != "completed":
            return {"state": "waiting", "reason": "execution_in_progress"}
    closed = await jobs.close_execution_repair_on(connection, owner=EXECUTION_OWNER, identity=identity,
        data_generation=generation, max_failures=MAX_FAILURES, repair_ref=repair_ref,
        consumer=consumer, observation_ref=observation, current_job=current_job)
    if not closed.get("closed"):
        reason = closed.get("reason") or "repair_close_unconfirmed"
        return {"state": "waiting" if reason == "execution_in_progress" else "blocked",
                "reason": reason, "outcome": "unknown" if reason == "execution_outcome_unknown" else None,
                "budget": closed}
    return {"state": "verified", "reason": reason or disposition, "disposition": disposition,
            "consumer": consumer, "proof": proof}


async def record_target_on(connection, generation, event, consumer, disposition, *, reason, proof=None, owner_id=None):
    """Record a domain-proven no-op or full proof without stealing a live claim."""
    if disposition not in {"done", "superseded", "invalidated"}:
        raise ValueError("invalid_result_reconciliation")
    action_id, receipt_id, revision = event_identity(event)
    candidate = {"data_generation": generation, "action_id": action_id, "receipt_id": receipt_id,
                 "result_revision": revision, "disposition": disposition, "proof": proof or {}}
    if disposition == "done" and not matching_life_proof(
            event, consumer, candidate, generation=generation, owner_id=owner_id):
        return {"state": "unknown", "reason": "result_consumer_proof_incomplete"}
    await connection.execute(
        "INSERT INTO conversation.result_consumer_progress(data_generation,receipt_id,consumer,action_id,result_revision) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (generation, receipt_id, consumer, action_id, revision))
    row = await progress_on(connection, generation, event, consumer, lock=True)
    completed = completed_progress(generation, event, consumer, row, owner_id=owner_id)
    if completed:
        return completed
    if row["disposition"] == "running" and row["lease_until"] and row["lease_until"] > row["observed_at"]:
        return {"state": "waiting", "reason": "result_consumer_busy", "due_at": row["lease_until"]}
    await connection.execute(
        "UPDATE conversation.result_consumer_progress SET disposition=%s,reason=%s,proof=%s,"
        "owner_token=NULL,lease_until=NULL,rbi_retry_at=NULL,updated_at=now() "
        "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s",
        (disposition, reason, document(proof or {}), generation, receipt_id, consumer))
    return {"state": "verified", "reason": reason, "disposition": disposition,
            "consumer": consumer, "proof": proof or {}}


async def preserve_target_unknown_on(connection, generation, event, consumer, reason):
    """A newer head cannot replace an unresolved original consumer outcome."""
    action_id, receipt_id, revision = event_identity(event)
    await connection.execute(
        "INSERT INTO conversation.result_consumer_progress(data_generation,receipt_id,consumer,action_id,result_revision) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (generation, receipt_id, consumer, action_id, revision))
    row = await progress_on(connection, generation, event, consumer, lock=True)
    if row["disposition"] == "done":
        return
    await connection.execute(
        "UPDATE conversation.result_consumer_progress SET disposition='blocked',reason=%s,"
        "owner_token=NULL,lease_until=NULL,rbi_retry_at=NULL,updated_at=now() "
        "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s AND disposition<>'done'",
        (reason, generation, receipt_id, consumer))
