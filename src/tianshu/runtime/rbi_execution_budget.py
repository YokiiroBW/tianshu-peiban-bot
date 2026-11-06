"""Bounded native-job failure facts, independent of domain and Models ledgers.

The owner supplies a stable opaque identity and validates its business head. A
history seed asserts only verified mechanical facts; this module never scans a
domain, creates a job or performs a business action.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
import hashlib
import json

from procrastinate.exceptions import ConnectorException
from psycopg import Error as PsycopgError
from psycopg_pool import PoolTimeout

from .database import DatabaseFailure, classify_database_failure
from .errors import AppError
from .recovery_policy import RecoveryWait, recovery_wait_retryable


_OUTCOMES = frozenset({"failed", "wait", "completed", "unknown"})


def _inputs(owner, identity, data_generation, max_failures=None):
    if not all(isinstance(value, str) and value for value in (owner, identity, data_generation)):
        raise ValueError("invalid_execution_budget_identity")
    if max_failures is not None and (type(max_failures) is not int or max_failures < 1):
        raise ValueError("invalid_execution_failure_budget")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _physical(job_id, native_attempt):
    if type(job_id) is not int or job_id < 1 or type(native_attempt) is not int or native_attempt < 0:
        raise ValueError("invalid_execution_attempt")


def _aware(value):
    return isinstance(value, datetime) and value.utcoffset() is not None


async def _wake_exit_on(connection, generation, job_id, native_attempt):
    return await (await connection.execute(
        "SELECT kind FROM runtime.rbj_wake_exits WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (generation, job_id, native_attempt))).fetchone()


async def _generation_on(connection, data_generation, *, lock=True):
    query = "SELECT id FROM runtime.installation_generation WHERE singleton=true"
    if lock:
        query += " FOR SHARE"
    generation = await (await connection.execute(query)).fetchone()
    if not generation or generation["id"] != data_generation:
        raise AppError("job_generation_expired", status=409)


async def _lock_on(connection, data_generation, digest):
    # The domain can hold its own business row first, but all this ledger's
    # readers/writers serialize by one stable, generation-scoped identity.
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             ("rbi.execution:" + data_generation + ":" + digest,))


async def _binding_on(connection, data_generation, digest, owner, max_failures=None):
    row = await (await connection.execute(
        "SELECT owner,max_failures,outcome AS history_state FROM runtime.rbi_execution_facts "
        "WHERE data_generation=%s AND identity_digest=%s AND job_id=0 AND native_attempt=0",
        (data_generation, digest))).fetchone()
    if row and (row["owner"] != owner or max_failures is not None and row["max_failures"] != max_failures):
        raise AppError("execution_budget_identity_conflict", status=409)
    return row


async def _prebegin_binding_on(connection, *, generation, job_id, identity_digest=None):
    """Read an immutable trusted repair binding or new normal admission."""
    from .jobs import JobSpec, _identity_digest

    repair = await (await connection.execute(
        "SELECT b.data_generation,b.job_id,b.identity_digest,b.grant_id,g.owner,g.repair_ref,g.consumer,"
        "j.args,k.subject_id,k.matter_id,k.payload_version,k.payload_digest "
        "FROM runtime.rbj_execution_repair_jobs b JOIN runtime.rbj_execution_repair_grants g ON g.id=b.grant_id "
        "AND g.data_generation=b.data_generation AND g.identity_digest=b.identity_digest "
        "JOIN runtime.rbj_execution_repairs r ON r.owner=g.owner AND r.defect_key=g.defect_key "
        "AND r.consumer=g.consumer AND r.repair_ref=g.repair_ref AND r.repair_revision=g.repair_revision "
        "JOIN procrastinate_jobs j ON j.id=b.job_id AND j.task_name=g.owner "
        "JOIN LATERAL (SELECT * FROM runtime.job_keys WHERE job_id=j.id AND task=g.owner "
        "AND data_generation=b.data_generation AND cancelled_at IS NULL ORDER BY created_at,key LIMIT 1) k ON true "
        "WHERE b.data_generation=%s AND b.job_id=%s AND (%s::text IS NULL OR b.identity_digest=%s) "
        "AND g.owner='conversation.result' AND j.args->>'repair_ref'=g.repair_ref "
        "AND j.args->>'repair_consumer'=g.consumer AND NOT EXISTS "
        "(SELECT 1 FROM runtime.rbj_execution_admissions a WHERE a.data_generation=b.data_generation AND a.job_id=b.job_id)",
        (generation, job_id, identity_digest, identity_digest))).fetchone()
    normal = await (await connection.execute(
        "SELECT a.data_generation,a.job_id,a.identity_digest,NULL::bigint AS grant_id,a.owner,"
        "j.args,k.subject_id,k.matter_id,k.payload_version,k.payload_digest "
        "FROM runtime.rbj_execution_admissions a JOIN procrastinate_jobs j ON j.id=a.job_id AND j.task_name=a.owner "
        "JOIN LATERAL (SELECT * FROM runtime.job_keys WHERE job_id=j.id AND task=a.owner "
        "AND data_generation=a.data_generation AND cancelled_at IS NULL AND payload_digest=a.payload_digest "
        "ORDER BY created_at,key LIMIT 1) k ON true "
        "WHERE a.data_generation=%s AND a.job_id=%s AND (%s::text IS NULL OR a.identity_digest=%s) "
        "AND a.owner='conversation.result' AND a.policy='normal_begin_required' "
        "AND NOT (j.args ? 'repair_ref' OR j.args ? 'repair_consumer') AND NOT EXISTS "
        "(SELECT 1 FROM runtime.rbj_execution_repair_jobs b WHERE b.data_generation=a.data_generation AND b.job_id=a.job_id)",
        (generation, job_id, identity_digest, identity_digest))).fetchone()
    if bool(repair) == bool(normal):
        return None
    row, purpose = (repair, "repair") if repair else (normal, "normal")
    if not isinstance(row["args"], Mapping) or row["payload_digest"] is None:
        return None
    digest = _identity_digest(JobSpec(row["owner"], "native-admission", row["args"],
        subject_id=row["subject_id"], matter_id=row["matter_id"], payload_version=row["payload_version"],
        data_generation=generation))
    invalidated = False
    if digest != row["payload_digest"]:
        from .rbj_wake import payload_digest
        event = row["args"].get("event")
        redaction = await (await connection.execute(
            "SELECT effective_digest FROM runtime.rbj_wake_redactions WHERE job_id=%s AND task=%s "
            "ORDER BY id DESC LIMIT 1", (job_id, row["owner"]))).fetchone()
        invalidated = bool(redaction and redaction["effective_digest"] == payload_digest(row["args"])
                           and isinstance(event, Mapping) and event.get("source_invalidated") is True)
        if not invalidated:
            return None
    return {"data_generation": generation, "job_id": job_id, "identity_digest": row["identity_digest"],
            "grant_id": row["grant_id"], "purpose": purpose, "source_invalidated": invalidated}


async def _prebegin_tail_on(connection, *, generation, job_id, native_attempt, started_event_id,
                            identity_digest=None):
    """Recognize an anchored, trusted native tail without a business fact."""
    if type(started_event_id) is not int:
        return None
    binding = await _prebegin_binding_on(connection, generation=generation, job_id=job_id,
                                         identity_digest=identity_digest)
    if not binding:
        return None
    native = await (await connection.execute(
        "SELECT 1 FROM procrastinate_jobs j WHERE j.id=%s AND j.status='doing' AND NOT j.abort_requested "
        "AND j.attempts=%s AND EXISTS (SELECT 1 FROM procrastinate_events e WHERE e.job_id=j.id "
        "AND e.id=%s AND e.type='started') AND NOT EXISTS (SELECT 1 FROM procrastinate_events e "
        "WHERE e.job_id=j.id AND e.id>%s AND e.type IN "
        "('started','deferred_for_retry','failed','succeeded','aborted','retried','cancelled','abort_requested')) "
        "AND NOT EXISTS (SELECT 1 FROM runtime.rbi_execution_facts f WHERE f.data_generation=%s "
        "AND f.job_id=j.id AND f.native_attempt=j.attempts) "
        "AND NOT EXISTS (SELECT 1 FROM runtime.rbj_wake_exits x WHERE x.data_generation=%s "
        "AND x.job_id=j.id AND x.native_attempt=j.attempts)",
        (job_id, native_attempt, started_event_id, started_event_id, generation, generation))).fetchone()
    if not native:
        return None
    return {"data_generation": generation, "job_id": job_id, "native_attempt": native_attempt,
            "grant_id": binding["grant_id"], "started_event_id": started_event_id, "purpose": binding["purpose"],
            "source_invalidated": binding["source_invalidated"]}


async def _native_inflight_on(connection, data_generation, digest, current_job=None):
    """Inspect bounded trusted native tails under the execution identity mutex."""
    rows = await (await connection.execute(
        "WITH tracked AS (SELECT data_generation,job_id,identity_digest,grant_id,'repair'::text AS purpose "
        "FROM runtime.rbj_execution_repair_jobs UNION ALL "
        "SELECT data_generation,job_id,identity_digest,NULL::bigint,'normal'::text "
        "FROM runtime.rbj_execution_admissions) "
        "SELECT t.data_generation,t.job_id,j.attempts AS native_attempt,t.grant_id,t.purpose,j.status,j.abort_requested,"
        "c.ordinal AS cursor_ordinal,c.active_attempt,c.active_event_id AS started_event_id,"
        "c.event_id AS cursor_event_id,c.task_name AS cursor_task,"
        "EXISTS (SELECT 1 FROM procrastinate_events e WHERE e.job_id=j.id AND e.id=c.event_id) AS cursor_anchor_exists,"
        "EXISTS (SELECT 1 FROM procrastinate_events e WHERE e.job_id=j.id AND e.id=c.active_event_id "
        "AND e.type='started') AS started_anchor_exists,"
        "EXISTS (SELECT 1 FROM runtime.rbj_wake_exits x WHERE x.data_generation=t.data_generation "
        "AND x.job_id=j.id AND x.native_attempt=j.attempts) AS exit_recorded "
        "FROM tracked t JOIN procrastinate_jobs j ON j.id=t.job_id "
        "LEFT JOIN runtime.rbi_execution_history_cursors c ON c.data_generation=t.data_generation "
        "AND c.identity_digest=t.identity_digest AND c.job_id=t.job_id "
        "WHERE t.data_generation=%s AND t.identity_digest=%s AND j.status IN ('doing','aborting') "
        "AND NOT EXISTS (SELECT 1 FROM runtime.rbi_execution_facts f WHERE f.data_generation=t.data_generation "
        "AND f.job_id=j.id AND f.native_attempt=j.attempts) ORDER BY j.id,t.purpose LIMIT 33",
        (data_generation, digest))).fetchall()
    items = []
    complete = len(rows) <= 32
    for row in rows[:32]:
        binding = await _prebegin_binding_on(connection, generation=data_generation, job_id=row["job_id"],
                                              identity_digest=digest)
        valid_binding = (binding is not None and binding["purpose"] == row["purpose"]
                         and binding["grant_id"] == row["grant_id"] and row["status"] == "doing"
                         and not row["abort_requested"])
        verified = None
        if not valid_binding:
            anchor_state = "invalid"
        elif row["cursor_event_id"] is not None and (
                row["cursor_task"] != "conversation.result" or row["cursor_ordinal"] > row["native_attempt"]
                or row["cursor_event_id"] > 0 and not row["cursor_anchor_exists"]
                or row["active_attempt"] is not None and (
                    not row["started_anchor_exists"] or row["started_event_id"] > row["cursor_event_id"])):
            anchor_state = "invalid"
        elif row["exit_recorded"]:
            anchor_state = "exiting"
        elif row["active_attempt"] != row["native_attempt"]:
            anchor_state = "unscanned"
        elif (row["cursor_task"] != "conversation.result" or not row["cursor_anchor_exists"]
              or row["started_event_id"] is None or row["cursor_event_id"] is None
              or row["cursor_event_id"] < row["started_event_id"]):
            anchor_state = "invalid"
        else:
            verified = await _prebegin_tail_on(connection, generation=data_generation, job_id=row["job_id"],
                native_attempt=row["native_attempt"], started_event_id=row["started_event_id"], identity_digest=digest)
            anchor_state = "verified" if verified else "invalid"
        if anchor_state == "invalid":
            complete = False
        own = (verified is not None and not verified["source_invalidated"] and current_job is not None
               and current_job.get("data_generation") == data_generation
               and current_job.get("job_id") == row["job_id"]
               and current_job.get("native_attempt") == row["native_attempt"])
        if own:
            await _current_job_on(connection, data_generation, row["job_id"], row["native_attempt"])
        items.append({"data_generation": data_generation, "job_id": row["job_id"],
                      "native_attempt": row["native_attempt"], "grant_id": row["grant_id"], "purpose": row["purpose"],
                      "started_event_id": row["started_event_id"] if row["active_attempt"] == row["native_attempt"] else None,
                      "verified": verified is not None, "is_current": own, "anchor_state": anchor_state,
                      "source_invalidated": bool(binding and binding["source_invalidated"])})
    return items, complete


async def _snapshot_on(connection, data_generation, digest, binding, *, current_job=None):
    inflight, inflight_complete = await _native_inflight_on(connection, data_generation, digest, current_job)
    native_blocked = not inflight_complete or any(not item["is_current"] for item in inflight)
    native_fields = {"native_inflight": inflight, "native_inflight_complete": inflight_complete,
                     "native_inflight_blocking": native_blocked}
    if binding is None or binding["history_state"] != "history_complete" or not inflight_complete:
        known = await (await connection.execute(
            "SELECT count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='unknown') AS unknown,"
            "count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='started') AS active "
            "FROM runtime.rbi_execution_facts WHERE data_generation=%s AND identity_digest=%s AND job_id>0",
            (data_generation, digest))).fetchone()
        return {"eligible": False, "reason": "history_unknown", "history_complete": False,
                "known_unknown_held": known["unknown"], "known_active_executions": known["active"],
                "total_executions": None, "failed_executions": None, "unknown_held": None,
                "active_executions": None, "unresolved_executions": [], "unresolved_complete": False,
                "remaining": None, "max_failures": binding["max_failures"] if binding else None, "retry_at": None,
                "normal_failed_executions": None, "repair_failed_executions": None,
                "total_failed_executions": None, "failure_digest": None, **native_fields}
    counts = await (await connection.execute(
        "SELECT count(*) AS total,"
        "count(*) FILTER (WHERE purpose='normal' AND COALESCE(resolved_outcome,outcome)='failed') AS failures,"
        "count(*) FILTER (WHERE purpose='repair' AND COALESCE(resolved_outcome,outcome)='failed') AS repair_failures,"
        "count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='started') AS active,"
        "count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='unknown') AS unknown,"
        "max(CASE WHEN COALESCE(resolved_outcome,outcome)='wait' "
        "AND COALESCE(resolved_retry_at,retry_at)>now() THEN COALESCE(resolved_retry_at,retry_at) END) AS retry_at "
        "FROM runtime.rbi_execution_facts WHERE data_generation=%s AND identity_digest=%s AND job_id>0",
        (data_generation, digest))).fetchone()
    unresolved = await (await connection.execute(
        "SELECT f.job_id,f.native_attempt,f.outcome,f.evidence_ref,f.purpose,"
        "g.repair_ref,g.consumer FROM runtime.rbi_execution_facts f "
        "LEFT JOIN runtime.rbj_execution_repair_grants g ON g.id=f.repair_grant_id "
        "WHERE f.data_generation=%s AND f.identity_digest=%s AND f.job_id>0 AND f.resolved_outcome IS NULL "
        "AND f.outcome IN ('started','unknown') ORDER BY f.recorded_at,f.job_id,f.native_attempt LIMIT 33",
        (data_generation, digest))).fetchall()
    failures = await (await connection.execute(
        "SELECT job_id,native_attempt,evidence_ref,observation_ref FROM runtime.rbi_execution_facts "
        "WHERE data_generation=%s AND identity_digest=%s AND job_id>0 AND purpose='normal' "
        "AND COALESCE(resolved_outcome,outcome)='failed' ORDER BY job_id,native_attempt",
        (data_generation, digest))).fetchall()
    failure_digest = hashlib.sha256(json.dumps(
        [[item["job_id"], item["native_attempt"], item["evidence_ref"], item["observation_ref"]] for item in failures],
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    reason = ("execution_outcome_unknown" if counts["unknown"] else
              "execution_in_progress" if counts["active"] or native_blocked else
              "execution_failure_budget_exhausted" if counts["failures"] >= binding["max_failures"] else
              "execution_wait" if counts["retry_at"] is not None else None)
    return {"eligible": reason is None, "reason": reason, "history_complete": True,
            "total_executions": counts["total"], "failed_executions": counts["failures"],
            "unknown_held": counts["unknown"], "active_executions": counts["active"],
            "known_unknown_held": counts["unknown"], "known_active_executions": counts["active"],
            "unresolved_executions": unresolved[:32], "unresolved_complete": len(unresolved) <= 32,
            "remaining": max(0, binding["max_failures"] - counts["failures"] - counts["unknown"] - counts["active"]),
            "max_failures": binding["max_failures"], "retry_at": counts["retry_at"] if reason == "execution_wait" else None,
            "normal_failed_executions": counts["failures"], "repair_failed_executions": counts["repair_failures"],
            "total_failed_executions": counts["failures"] + counts["repair_failures"], "failure_digest": failure_digest,
            **native_fields}


def _history(history):
    if not isinstance(history, (list, tuple)):
        raise ValueError("invalid_execution_history")
    unique = {}
    for item in history:
        if not isinstance(item, Mapping):
            raise ValueError("invalid_execution_history")
        job_id, attempt, outcome = item.get("job_id"), item.get("native_attempt"), item.get("outcome")
        _physical(job_id, attempt)
        if not isinstance(outcome, str) or outcome not in _OUTCOMES:
            raise ValueError("invalid_execution_history")
        retry_at, evidence_ref = item.get("retry_at"), item.get("evidence_ref")
        if (outcome == "wait" and not _aware(retry_at) or outcome != "wait" and retry_at is not None
                or not isinstance(evidence_ref, str) or not evidence_ref.strip() or len(evidence_ref) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in evidence_ref)):
            raise ValueError("invalid_execution_history")
        previous = unique.get((job_id, attempt))
        fact = (outcome, retry_at, evidence_ref)
        if previous is not None and previous != fact:
            raise AppError("execution_history_conflict", status=409)
        unique[(job_id, attempt)] = fact
    return [(job, attempt, outcome, retry_at, evidence_ref)
            for (job, attempt), (outcome, retry_at, evidence_ref) in sorted(unique.items())]


async def seed_execution_history_on(connection, *, owner, identity, data_generation, max_failures, history, complete):
    """Append verified history; incomplete enumeration persistently fences use."""
    digest = _inputs(owner, identity, data_generation, max_failures)
    if type(complete) is not bool:
        raise ValueError("invalid_execution_history")
    normalized = _history(history)
    await _generation_on(connection, data_generation)
    await _lock_on(connection, data_generation, digest)
    binding = await _binding_on(connection, data_generation, digest, owner, max_failures)
    state = "history_complete" if complete else "history_unknown"
    if binding is None:
        await connection.execute(
            "INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,job_id,"
            "native_attempt,outcome) VALUES(%s,%s,%s,%s,0,0,'history_unknown')",
            (data_generation, digest, owner, max_failures))
        binding = {"owner": owner, "max_failures": max_failures, "history_state": "history_unknown"}
    elif binding["history_state"] != "history_unknown":
        # If the caller catches an import conflict inside its transaction, a
        # partial history must never remain marked complete.
        await connection.execute("UPDATE runtime.rbi_execution_facts SET outcome='history_unknown' "
                                 "WHERE data_generation=%s AND identity_digest=%s AND job_id=0 AND native_attempt=0",
                                 (data_generation, digest))
        binding = {**binding, "history_state": "history_unknown"}
    for job, attempt, outcome, retry_at, evidence_ref in normalized:
        existing = await (await connection.execute(
            "SELECT outcome,retry_at,resolved_outcome,resolved_retry_at FROM runtime.rbi_execution_facts "
            "WHERE data_generation=%s AND identity_digest=%s AND job_id=%s AND native_attempt=%s",
            (data_generation, digest, job, attempt))).fetchone()
        if existing:
            effective = existing["resolved_outcome"] or existing["outcome"]
            effective_retry = existing["resolved_retry_at"] if existing["resolved_outcome"] else existing["retry_at"]
            # Coarse native observations never downgrade typed waits or an
            # established outcome. Unknown/started need explicit resolution.
            if (outcome != "unknown" and effective not in {"unknown", "started", "not_started", outcome}
                    and not (effective == "wait" and outcome == "failed")):
                raise AppError("execution_history_conflict", status=409)
            if effective == outcome == "wait" and effective_retry != retry_at:
                raise AppError("execution_history_conflict", status=409)
            continue
        if await _wake_exit_on(connection, data_generation, job, attempt):
            continue
        purpose = await (await connection.execute(
            "SELECT b.grant_id,b.identity_digest FROM runtime.rbj_execution_repair_jobs b "
            "WHERE b.data_generation=%s AND b.job_id=%s", (data_generation, job))).fetchone()
        if purpose and purpose["identity_digest"] != digest:
            raise AppError("execution_attempt_identity_conflict", status=409)
        admission = await (await connection.execute(
            "SELECT identity_digest,owner FROM runtime.rbj_execution_admissions "
            "WHERE data_generation=%s AND job_id=%s", (data_generation, job))).fetchone()
        if admission and (admission["identity_digest"] != digest or admission["owner"] != owner):
            raise AppError("execution_attempt_identity_conflict", status=409)
        if purpose and admission:
            raise AppError("execution_attempt_identity_conflict", status=409)
        if (purpose or admission) and outcome == "completed":
            # A native job can finish normally while its handler reports
            # blocked. Without an existing begin/finish fact this is not proof
            # that the result target was applied (also for a marked normal job).
            outcome = "unknown"
        if not purpose:
            native = await (await connection.execute(
                "SELECT task_name,args FROM procrastinate_jobs WHERE id=%s", (job,))).fetchone()
            if (native and native["task_name"] == "conversation.result" and
                    any(key in (native["args"] or {}) for key in ("repair_ref", "repair_consumer"))):
                # A payload is not a grant. Never import a pre-begin repair
                # attempt as normal just because its binding is missing.
                raise AppError("execution_repair_job_unbound", status=409)
        inserted = await (await connection.execute(
            "INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,job_id,"
            "native_attempt,outcome,retry_at,evidence_ref,purpose,repair_grant_id) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING job_id",
            (data_generation, digest, owner, max_failures, job, attempt, outcome, retry_at, evidence_ref,
             "repair" if purpose else "normal", purpose["grant_id"] if purpose else None))).fetchone()
        if not inserted:
            raise AppError("execution_attempt_identity_conflict", status=409)
    if binding["history_state"] != state:
        await connection.execute("UPDATE runtime.rbi_execution_facts SET outcome=%s WHERE data_generation=%s "
                                 "AND identity_digest=%s AND job_id=0 AND native_attempt=0",
                                 (state, data_generation, digest))
        binding = {**binding, "history_state": state}
    return {"ready": complete, **await _snapshot_on(connection, data_generation, digest, binding)}


async def inspect_execution_on(connection, *, owner, identity, data_generation, max_failures,
                               repair_ref=None, consumer=None, current_job=None):
    from .rbj_execution_repair import repair_snapshot_on
    digest = _inputs(owner, identity, data_generation, max_failures)
    await _generation_on(connection, data_generation, lock=False)
    await _lock_on(connection, data_generation, digest)
    binding = await _binding_on(connection, data_generation, digest, owner, max_failures)
    result = await _snapshot_on(connection, data_generation, digest, binding, current_job=current_job)
    if binding is None:
        result["max_failures"] = max_failures
    result["repair"] = await repair_snapshot_on(connection, data_generation=data_generation,
        identity_digest=digest, owner=owner, normal=result, repair_ref=repair_ref, consumer=consumer)
    return result


async def _current_job_on(connection, data_generation, job_id, native_attempt):
    row = await (await connection.execute(
        "SELECT j.status,j.attempts FROM procrastinate_jobs j WHERE j.id=%s AND EXISTS "
        "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s "
        "AND k.cancelled_at IS NULL) FOR SHARE OF j", (job_id, data_generation))).fetchone()
    if not row:
        raise AppError("execution_job_mismatch", status=409)
    if row["status"] != "doing" or row["attempts"] != native_attempt:
        raise AppError("execution_attempt_changed", status=409)


async def begin_execution_on(connection, *, owner, identity, data_generation, job_id, native_attempt, max_failures,
                             repair_ref=None, consumer=None):
    from .rbj_execution_repair import repair_snapshot_on
    digest = _inputs(owner, identity, data_generation, max_failures)
    _physical(job_id, native_attempt)
    await _generation_on(connection, data_generation)
    await _lock_on(connection, data_generation, digest)
    await _current_job_on(connection, data_generation, job_id, native_attempt)
    current_job = {"data_generation": data_generation, "job_id": job_id, "native_attempt": native_attempt}
    admission = await (await connection.execute(
        "SELECT owner,identity_digest FROM runtime.rbj_execution_admissions WHERE data_generation=%s AND job_id=%s",
        (data_generation, job_id))).fetchone()
    if admission:
        verified = await _prebegin_binding_on(connection, generation=data_generation, job_id=job_id, identity_digest=digest)
        if (admission["owner"] != owner or admission["identity_digest"] != digest or repair_ref is not None
                or not verified or verified["purpose"] != "normal" or verified["source_invalidated"]):
            raise AppError("execution_admission_identity_conflict", status=409)
    binding = await _binding_on(connection, data_generation, digest, owner, max_failures)
    result = await _snapshot_on(connection, data_generation, digest, binding, current_job=current_job)
    repair = await repair_snapshot_on(connection, data_generation=data_generation, identity_digest=digest,
        owner=owner, normal=result, repair_ref=repair_ref, consumer=consumer)
    result["repair"] = repair
    if repair is not None:
        result.update(normal_eligible=result["eligible"], normal_reason=result["reason"],
                      eligible=repair["eligible"], reason=repair["reason"], retry_at=repair["retry_at"])
    if binding is None:
        result["max_failures"] = max_failures
        return result
    existing = await (await connection.execute(
        "SELECT outcome FROM runtime.rbi_execution_facts WHERE data_generation=%s AND identity_digest=%s "
        "AND job_id=%s AND native_attempt=%s", (data_generation, digest, job_id, native_attempt))).fetchone()
    if existing:
        result.update(eligible=False, reason="execution_attempt_already_recorded", retry_at=None)
        return result
    stop = await _wake_exit_on(connection, data_generation, job_id, native_attempt)
    if stop:
        result.update(eligible=False, reason="wake_covered" if stop["kind"] == "covered"
                      else "execution_preflight_stopped", retry_at=None)
        return result
    if not result["eligible"]:
        return result
    purpose = await (await connection.execute(
        "SELECT grant_id,identity_digest FROM runtime.rbj_execution_repair_jobs "
        "WHERE data_generation=%s AND job_id=%s", (data_generation, job_id))).fetchone()
    if (repair is not None and (not purpose or purpose["identity_digest"] != digest
                                or purpose["grant_id"] != repair["grant_id"])):
        result.update(eligible=False, reason="execution_repair_job_unbound", retry_at=None)
        return result
    if repair is None and purpose:
        raise AppError("execution_repair_purpose_mismatch", status=409)
    inserted = await (await connection.execute(
        "INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,job_id,"
        "native_attempt,outcome,purpose,repair_grant_id) VALUES(%s,%s,%s,%s,%s,%s,'started',%s,%s) "
        "ON CONFLICT DO NOTHING RETURNING job_id",
        (data_generation, digest, owner, max_failures, job_id, native_attempt,
         "repair" if repair else "normal", repair["grant_id"] if repair else None))).fetchone()
    if not inserted:
        raise AppError("execution_attempt_identity_conflict", status=409)
    result["total_executions"] += 1
    result["active_executions"] += 1
    result["known_active_executions"] += 1
    result["unresolved_executions"] = [{"job_id": job_id, "native_attempt": native_attempt,
                                        "outcome": "started", "evidence_ref": None,
                                        "purpose": "repair" if repair else "normal",
                                        "repair_ref": repair_ref, "consumer": consumer}]
    result["remaining"] = max(0, result["remaining"] - 1)
    return result


def _classify_outcome(outcome, error):
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        raise ValueError("invalid_execution_outcome")
    if outcome == "completed":
        if error is not None:
            raise ValueError("invalid_execution_outcome")
        return "completed", None
    if outcome == "unknown":
        return "unknown", None
    if isinstance(error, AppError) and error.code == "local_result_application_unconfirmed":
        # A missing atomic proof after begin is not a definitive failed apply.
        return "unknown", None
    if isinstance(error, RecoveryWait):
        return ("wait", error.retry_at) if recovery_wait_retryable(error) else ("unknown", None)
    failure = error if isinstance(error, DatabaseFailure) else classify_database_failure(error)
    if failure:
        if failure.retryable and failure.rollback_confirmed and _aware(failure.retry_at):
            return "wait", failure.retry_at
        return "unknown", None
    if isinstance(error, (asyncio.CancelledError, TimeoutError, PoolTimeout, ConnectorException)):
        return "unknown", None
    if isinstance(error, PsycopgError) and not (
            getattr(error, "failure_phase", None) == "transaction"
            and getattr(error, "_tianshu_pending_rollbacks", None) == 0
            and getattr(error, "_tianshu_rollbacks_ok", None) is True):
        return "unknown", None
    return ("unknown", None) if outcome == "wait" else ("failed", None)


async def record_execution_preflight_stop_on(connection, *, owner, identity, data_generation, job_id,
                                             native_attempt, reason, observation_ref):
    """Persist a trusted marked normal worker's precise pre-begin no-op."""
    from .jobs import current_job_execution
    if reason not in {"closed", "blocked", "unknown", "history_more", "execution_wait", "execution_in_progress"}:
        raise ValueError("invalid_execution_preflight_stop")
    if (not isinstance(observation_ref, str) or not observation_ref.strip() or len(observation_ref) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in observation_ref)):
        raise ValueError("invalid_execution_observation")
    _physical(job_id, native_attempt)
    if current_job_execution() != {"job_id": job_id, "native_attempt": native_attempt,
                                   "data_generation": data_generation}:
        raise AppError("execution_current_job_mismatch", status=409)
    digest = _inputs(owner, identity, data_generation)
    await _generation_on(connection, data_generation)
    await _lock_on(connection, data_generation, digest)
    admission = await (await connection.execute(
        "SELECT data_generation,owner,identity_digest FROM runtime.rbj_execution_admissions WHERE job_id=%s",
        (job_id,))).fetchone()
    if not admission:
        return {"state": "unregistered", "reason": "execution_admission_unregistered",
                "job_id": job_id, "native_attempt": native_attempt}
    if (admission["data_generation"] != data_generation or admission["owner"] != owner
            or admission["identity_digest"] != digest):
        raise AppError("execution_admission_identity_conflict", status=409)
    binding = await _prebegin_binding_on(connection, generation=data_generation, job_id=job_id, identity_digest=digest)
    if not binding or binding["purpose"] != "normal" or binding["source_invalidated"]:
        raise AppError("execution_admission_identity_conflict", status=409)
    await _current_job_on(connection, data_generation, job_id, native_attempt)
    fact = await (await connection.execute(
        "SELECT 1 FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (data_generation, job_id, native_attempt))).fetchone()
    if fact:
        raise AppError("execution_already_started", status=409)
    if reason == "history_more":
        unresolved = await (await connection.execute(
            "SELECT 1 FROM runtime.rbi_execution_facts WHERE data_generation=%s AND identity_digest=%s "
            "AND job_id>0 AND COALESCE(resolved_outcome,outcome) IN ('started','unknown') LIMIT 1",
            (data_generation, digest))).fetchone()
        if unresolved:
            raise AppError("execution_preflight_wait_unconfirmed", status=409)
    await connection.execute(
        "INSERT INTO runtime.rbj_wake_exits(data_generation,job_id,native_attempt,kind,reason,observation_ref) "
        "VALUES(%s,%s,%s,'normal_preflight',%s,%s) ON CONFLICT DO NOTHING",
        (data_generation, job_id, native_attempt, reason, observation_ref))
    saved = await (await connection.execute(
        "SELECT kind,reason,observation_ref FROM runtime.rbj_wake_exits "
        "WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (data_generation, job_id, native_attempt))).fetchone()
    if (not saved or saved["kind"] != "normal_preflight" or saved["reason"] != reason
            or saved["observation_ref"] != observation_ref):
        raise AppError("execution_preflight_conflict", status=409)
    return {"state": "preflight_stopped", "reason": reason, "job_id": job_id,
            "native_attempt": native_attempt, "purpose": "normal"}


async def finish_execution_on(connection, *, owner, identity, data_generation, job_id, native_attempt, outcome, error=None):
    from .rbj_execution_repair import repair_snapshot_on
    digest = _inputs(owner, identity, data_generation)
    _physical(job_id, native_attempt)
    actual, retry_at = _classify_outcome(outcome, error)
    await _generation_on(connection, data_generation)
    await _lock_on(connection, data_generation, digest)
    binding = await _binding_on(connection, data_generation, digest, owner)
    if not binding:
        raise AppError("execution_history_unknown", status=409)
    fact = await (await connection.execute(
        "SELECT f.outcome,f.retry_at,f.resolved_outcome,f.repair_grant_id,g.repair_ref,g.consumer "
        "FROM runtime.rbi_execution_facts f LEFT JOIN runtime.rbj_execution_repair_grants g ON g.id=f.repair_grant_id "
        "WHERE f.data_generation=%s AND f.identity_digest=%s AND f.job_id=%s AND f.native_attempt=%s FOR UPDATE OF f",
        (data_generation, digest, job_id, native_attempt))).fetchone()
    if not fact:
        raise AppError("execution_attempt_not_started", status=409)
    if fact["resolved_outcome"] is not None or fact["outcome"] not in {"started", actual}:
        raise AppError("execution_attempt_conflict", status=409)
    recorded = fact["outcome"] == "started"
    if recorded:
        await _current_job_on(connection, data_generation, job_id, native_attempt)
        await connection.execute(
            "UPDATE runtime.rbi_execution_facts SET outcome=%s,retry_at=%s WHERE data_generation=%s "
            "AND identity_digest=%s AND job_id=%s AND native_attempt=%s",
            (actual, retry_at, data_generation, digest, job_id, native_attempt))
    elif fact["retry_at"] != retry_at:
        raise AppError("execution_attempt_conflict", status=409)
    result = await _snapshot_on(connection, data_generation, digest, binding)
    if (actual == "completed" and fact["repair_grant_id"] is not None and result["history_complete"]
            and not result["unknown_held"] and not result["active_executions"]
            and not result["native_inflight_blocking"]):
        await connection.execute("UPDATE runtime.rbj_execution_repair_grants SET closed_at=now(),"
            "close_observation_ref=%s WHERE id=%s AND closed_at IS NULL",
            (f"execution:{job_id}:{native_attempt}:completed", fact["repair_grant_id"]))
    result["repair"] = await repair_snapshot_on(connection, data_generation=data_generation, identity_digest=digest,
        owner=owner, normal=result, repair_ref=fact["repair_ref"], consumer=fact["consumer"])
    return {"recorded": recorded, "outcome": actual, **result}


async def _validate_not_started_on(connection, *, owner, data_generation, digest, job_id, native_attempt, fact):
    """Verify mechanical provenance; the trusted caller owns domain evidence."""
    if (owner != "conversation.result" or fact["outcome"] != "unknown" or fact["purpose"] != "repair"
            or fact["repair_grant_id"] is None or fact["retry_at"] is not None):
        raise AppError("execution_not_started_unproven", status=409)
    evidence = fact["evidence_ref"]
    prefix = "native-job-event:"
    if (not isinstance(evidence, str) or not evidence.startswith(prefix)
            or not evidence[len(prefix):].isascii() or not evidence[len(prefix):].isdigit()
            or evidence[len(prefix):].startswith("0") or len(evidence[len(prefix):]) > 18):
        raise AppError("execution_not_started_unproven", status=409)
    started_event_id = int(evidence[len(prefix):])
    binding = await _prebegin_binding_on(connection, generation=data_generation, job_id=job_id, identity_digest=digest)
    if (not binding or binding["purpose"] != "repair" or binding["grant_id"] != fact["repair_grant_id"]
            or binding["source_invalidated"]):
        raise AppError("execution_not_started_unproven", status=409)
    native = await (await connection.execute(
        "SELECT status,attempts FROM procrastinate_jobs WHERE id=%s FOR SHARE", (job_id,))).fetchone()
    if (not native or native["status"] in {"doing", "aborting"} or native["attempts"] <= native_attempt):
        raise AppError("execution_not_started_unproven", status=409)
    other_doing = await (await connection.execute(
        "SELECT 1 FROM (SELECT data_generation,job_id,identity_digest FROM runtime.rbj_execution_repair_jobs "
        "UNION ALL SELECT data_generation,job_id,identity_digest FROM runtime.rbj_execution_admissions) b "
        "JOIN procrastinate_jobs j ON j.id=b.job_id WHERE b.data_generation=%s AND b.identity_digest=%s "
        "AND j.status IN ('doing','aborting') LIMIT 1", (data_generation, digest))).fetchone()
    if other_doing:
        raise AppError("execution_in_progress", status=409)
    conflict = await (await connection.execute(
        "SELECT 1 FROM runtime.rbi_execution_facts WHERE data_generation=%s AND identity_digest=%s AND job_id>0 "
        "AND (job_id<>%s OR native_attempt<>%s) AND COALESCE(resolved_outcome,outcome) IN ('started','unknown') LIMIT 1",
        (data_generation, digest, job_id, native_attempt))).fetchone()
    if conflict or await _wake_exit_on(connection, data_generation, job_id, native_attempt):
        raise AppError("execution_not_started_unproven", status=409)
    cursor = await (await connection.execute(
        "SELECT event_id,ordinal,active_attempt,active_event_id,last_type,task_name "
        "FROM runtime.rbi_execution_history_cursors WHERE data_generation=%s AND identity_digest=%s AND job_id=%s",
        (data_generation, digest, job_id))).fetchone()
    events = await (await connection.execute(
        "SELECT id,type FROM procrastinate_events WHERE job_id=%s ORDER BY id LIMIT 129", (job_id,))).fetchall()
    if (not cursor or cursor["task_name"] != owner or not events or len(events) > 128
            or cursor["event_id"] != events[-1]["id"] or cursor["ordinal"] != native["attempts"]
            or cursor["active_attempt"] is not None or cursor["active_event_id"] is not None):
        raise AppError("execution_history_incomplete", status=409)
    ordinal, active, last, matched = 0, None, None, False
    for event in events:
        event_type = event["type"]
        if event_type == "started":
            if active is not None:
                raise AppError("execution_history_incomplete", status=409)
            active, last = (ordinal, event["id"]), None
        elif event_type == "abort_requested":
            raise AppError("execution_history_incomplete", status=409)
        elif event_type == "cancelled":
            if active is not None:
                raise AppError("execution_history_incomplete", status=409)
            last = "cancelled"
        elif event_type == "retried":
            if active is not None or last not in {"failed", "cancelled"}:
                raise AppError("execution_history_incomplete", status=409)
            ordinal, last = ordinal + 1, "retry"
        elif event_type in {"deferred_for_retry", "failed", "succeeded", "aborted"}:
            if active is None:
                raise AppError("execution_history_incomplete", status=409)
            if active == (native_attempt, started_event_id):
                matched = True
            active, last = None, event_type
            ordinal += 1
    status = native["status"]
    if (not matched or active is not None or ordinal != native["attempts"] or cursor["last_type"] != last
            or status == "failed" and last not in {"failed", "cancelled"}
            or status == "succeeded" and last not in {"succeeded", "cancelled"}
            or status == "aborted" and last != "aborted"
            or status == "cancelled" and last != "cancelled"):
        raise AppError("execution_history_incomplete", status=409)


async def resolve_execution_on(connection, *, owner, identity, data_generation, job_id, native_attempt,
                               outcome, observation_ref, retry_at=None, current_job=None):
    from .rbj_execution_repair import repair_snapshot_on
    digest = _inputs(owner, identity, data_generation)
    _physical(job_id, native_attempt)
    if (outcome not in {"failed", "wait", "completed", "not_started"} or not isinstance(observation_ref, str)
            or not observation_ref.strip() or len(observation_ref) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in observation_ref)
            or outcome == "wait" and not _aware(retry_at) or outcome != "wait" and retry_at is not None):
        raise ValueError("invalid_execution_observation")
    await _generation_on(connection, data_generation)
    await _lock_on(connection, data_generation, digest)
    binding = await _binding_on(connection, data_generation, digest, owner)
    if not binding:
        raise AppError("execution_history_unknown", status=409)
    before = await _snapshot_on(connection, data_generation, digest, binding, current_job=current_job)
    if before["native_inflight_blocking"]:
        before.update(eligible=False, reason=("history_unknown" if not before["native_inflight_complete"]
                                              else "execution_in_progress"), retry_at=None)
        return {"recorded": False, "outcome": None, **before}
    fact = await (await connection.execute(
        "SELECT f.outcome,f.retry_at,f.evidence_ref,f.purpose,f.resolved_outcome,f.resolved_retry_at,f.observation_ref,"
        "f.repair_grant_id,g.repair_ref,g.consumer "
        "FROM runtime.rbi_execution_facts f LEFT JOIN runtime.rbj_execution_repair_grants g ON g.id=f.repair_grant_id "
        "WHERE f.data_generation=%s AND f.identity_digest=%s AND f.job_id=%s AND f.native_attempt=%s FOR UPDATE OF f",
        (data_generation, digest, job_id, native_attempt))).fetchone()
    if not fact or fact["outcome"] not in {"started", "unknown"}:
        raise AppError("execution_attempt_not_unknown", status=409)
    if outcome == "not_started":
        if not binding or binding["history_state"] != "history_complete":
            raise AppError("execution_history_unknown", status=409)
        await _validate_not_started_on(connection, owner=owner, data_generation=data_generation, digest=digest,
                                       job_id=job_id, native_attempt=native_attempt, fact=fact)
    recorded = fact["resolved_outcome"] is None
    if recorded:
        native = await (await connection.execute(
            "SELECT status,attempts FROM procrastinate_jobs WHERE id=%s FOR SHARE", (job_id,))).fetchone()
        if native and native["status"] in {"doing", "todo"} and native["attempts"] == native_attempt:
            result = await _snapshot_on(connection, data_generation, digest, binding, current_job=current_job)
            result.update(eligible=False, reason="execution_in_progress", retry_at=None)
            return {"recorded": False, "outcome": None, **result}
        await connection.execute(
            "UPDATE runtime.rbi_execution_facts SET resolved_outcome=%s,resolved_retry_at=%s,observation_ref=%s,"
            "resolved_at=now() WHERE data_generation=%s AND identity_digest=%s AND job_id=%s AND native_attempt=%s",
            (outcome, retry_at, observation_ref, data_generation, digest, job_id, native_attempt))
    elif (fact["resolved_outcome"] != outcome or fact["resolved_retry_at"] != retry_at
          or fact["observation_ref"] != observation_ref):
        raise AppError("execution_attempt_conflict", status=409)
    result = await _snapshot_on(connection, data_generation, digest, binding, current_job=current_job)
    if (outcome == "completed" and fact["repair_grant_id"] is not None and result["history_complete"]
            and not result["unknown_held"] and not result["active_executions"]
            and not result["native_inflight_blocking"]):
        await connection.execute("UPDATE runtime.rbj_execution_repair_grants SET closed_at=now(),"
            "close_observation_ref=%s WHERE id=%s AND closed_at IS NULL", (observation_ref, fact["repair_grant_id"]))
    result["repair"] = await repair_snapshot_on(connection, data_generation=data_generation, identity_digest=digest,
        owner=owner, normal=result, repair_ref=fact["repair_ref"], consumer=fact["consumer"])
    return {"recorded": recorded, "outcome": outcome, **result}


async def observe_execution_history_on(connection, *, job_ids, data_generation, task_name, current_job=None):
    """Read native event histories for precisely the supplied job IDs.

    Completeness is scoped to those IDs; only the domain can prove it has listed
    every job associated with its original result. A retry event lacks typed
    rollback evidence, so it is an unresolved historical execution, not a wait.
    """
    if (not isinstance(data_generation, str) or not data_generation or not isinstance(task_name, str)
            or not task_name or not isinstance(job_ids, (list, tuple))
            or any(type(job_id) is not int or job_id < 1 for job_id in job_ids)):
        raise ValueError("invalid_execution_history_query")
    if current_job is not None and (not isinstance(current_job, Mapping)
            or set(current_job) != {"job_id", "native_attempt", "data_generation"}
            or current_job["data_generation"] != data_generation):
        raise ValueError("invalid_execution_history_query")
    if current_job is not None:
        _physical(current_job["job_id"], current_job["native_attempt"])
    await _generation_on(connection, data_generation, lock=False)
    ids = sorted(set(job_ids))
    if not ids:
        return {"complete": True, "reason": None, "executions": [], "native_inflight": []}
    bindings = await (await connection.execute(
        "SELECT DISTINCT identity_digest FROM ("
        "SELECT identity_digest FROM runtime.rbj_execution_repair_jobs WHERE data_generation=%s AND job_id=ANY(%s::bigint[]) "
        "UNION ALL SELECT identity_digest FROM runtime.rbj_execution_admissions "
        "WHERE data_generation=%s AND job_id=ANY(%s::bigint[])) b ORDER BY identity_digest",
        (data_generation, ids, data_generation, ids))).fetchall()
    for item in bindings:
        await _lock_on(connection, data_generation, item["identity_digest"])
    rows = await (await connection.execute(
        "SELECT j.id,j.status,j.attempts,j.task_name,EXISTS (SELECT 1 FROM runtime.job_keys k "
        "WHERE k.job_id=j.id AND k.data_generation=%s) AS generation_matches "
        "FROM procrastinate_jobs j WHERE j.id=ANY(%s) ORDER BY j.id",
        (data_generation, ids))).fetchall()
    if len(rows) != len(ids) or any(row["task_name"] != task_name or not row["generation_matches"] for row in rows):
        return {"complete": False, "reason": "execution_history_job_missing_or_changed", "executions": [], "native_inflight": []}
    events = await (await connection.execute(
        "SELECT id,job_id,type FROM procrastinate_events WHERE job_id=ANY(%s) "
        "AND type IN ('started','deferred_for_retry','failed','succeeded','aborted','retried','cancelled','abort_requested') "
        "ORDER BY job_id,id", (ids,))).fetchall()
    by_job = {job_id: [] for job_id in ids}
    for event in events:
        by_job[event["job_id"]].append(event)
    executions, native_inflight = [], []
    for row in rows:
        ordinal, active, last = 0, None, None
        for event in by_job[row["id"]]:
            event_type = event["type"]
            if event_type == "started":
                if active is not None:
                    return {"complete": False, "reason": "execution_history_incomplete", "executions": [], "native_inflight": []}
                active, last = (ordinal, event["id"]), None
            elif event_type == "abort_requested":
                # Native retry-on-abort may finish without incrementing the
                # counter; event rows alone cannot prove which path won.
                return {"complete": False, "reason": "execution_history_incomplete", "executions": [], "native_inflight": []}
            elif event_type == "cancelled":
                if active is not None:
                    return {"complete": False, "reason": "execution_history_incomplete", "executions": [], "native_inflight": []}
                last = "cancelled"
            elif event_type == "retried":
                if active is not None or last not in {"failed", "cancelled"}:
                    return {"complete": False, "reason": "execution_history_incomplete", "executions": [], "native_inflight": []}
                ordinal += 1
                last = "retry"
            else:
                if active is None:
                    return {"complete": False, "reason": "execution_history_incomplete", "executions": [], "native_inflight": []}
                outcome = {"failed": "failed", "succeeded": "completed", "aborted": "unknown",
                           "deferred_for_retry": "unknown"}[event_type]
                if not await _wake_exit_on(connection, data_generation, row["id"], active[0]):
                    executions.append({"job_id": row["id"], "native_attempt": active[0], "outcome": outcome,
                                       "evidence_ref": "native-job-event:" + str(event["id"])})
                active, last = None, event_type
                # Native finish as well as doing->todo increments attempts.
                ordinal += 1
        if row["attempts"] != ordinal:
            return {"complete": False, "reason": "execution_history_incomplete", "executions": [], "native_inflight": []}
        if row["status"] == "doing":
            if active is None:
                return {"complete": False, "reason": "execution_history_incomplete", "executions": [], "native_inflight": []}
            is_current = (current_job is not None and current_job["job_id"] == row["id"]
                          and current_job["native_attempt"] == active[0])
            tail = (await _prebegin_tail_on(connection, generation=data_generation, job_id=row["id"],
                native_attempt=active[0], started_event_id=active[1]) if task_name == "conversation.result" else None)
            if tail:
                native_inflight.append(tail)
                if len(native_inflight) > 32:
                    return {"complete": False, "reason": "execution_history_incomplete",
                            "executions": [], "native_inflight": []}
            elif not is_current and not await _wake_exit_on(connection, data_generation, row["id"], active[0]):
                executions.append({"job_id": row["id"], "native_attempt": active[0], "outcome": "unknown",
                                   "evidence_ref": "native-job-event:" + str(active[1])})
        elif (active is not None or row["status"] == "failed" and last not in {"failed", "cancelled"}
                or row["status"] == "succeeded" and last not in {"succeeded", "cancelled"}
                or row["status"] == "aborted" and last != "aborted"
                or row["status"] == "cancelled" and last != "cancelled"):
            return {"complete": False, "reason": "execution_history_incomplete", "executions": [], "native_inflight": []}
    return {"complete": True, "reason": None, "executions": executions, "native_inflight": native_inflight}


async def advance_execution_history_on(connection, *, owner, identity, data_generation, max_failures,
                                       job_id, task_name, current_job=None, limit=128):
    """Import at most one bounded page of a job's native events and save its cursor.

    The caller owns enumeration of every job and must explicitly mark the whole
    identity complete only after that enumeration and all pages are complete.
    Repeated calls resume a job, including new events after a previous tail.
    """
    digest = _inputs(owner, identity, data_generation, max_failures)
    _physical(job_id, 0)
    if not isinstance(task_name, str) or not task_name or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid_execution_history_query")
    if current_job is not None:
        if (not isinstance(current_job, Mapping)
                or set(current_job) != {"job_id", "native_attempt", "data_generation"}
                or current_job["data_generation"] != data_generation):
            raise ValueError("invalid_execution_history_query")
        _physical(current_job["job_id"], current_job["native_attempt"])

    # Even a missing or inconsistent native job makes any previous complete
    # claim uncertain. This update, imported facts and cursor commit together.
    await seed_execution_history_on(connection, owner=owner, identity=identity,
        data_generation=data_generation, max_failures=max_failures, history=[], complete=False)
    row = await (await connection.execute(
        "SELECT j.status,j.attempts,j.task_name,EXISTS (SELECT 1 FROM runtime.job_keys k "
        "WHERE k.job_id=j.id AND k.data_generation=%s) AS generation_matches "
        "FROM procrastinate_jobs j WHERE j.id=%s FOR SHARE OF j", (data_generation, job_id))).fetchone()

    def result(complete, reason, executions=(), events_read=0, native_inflight=()):
        return {"complete": complete, "reason": reason, "executions": list(executions),
                "native_inflight": list(native_inflight), "events_read": events_read,
                "job_status": row["status"] if row else None}

    if not row or row["task_name"] != task_name or not row["generation_matches"]:
        return result(False, "execution_history_job_missing_or_changed")
    await connection.execute(
        "INSERT INTO runtime.rbi_execution_history_cursors(data_generation,identity_digest,job_id,task_name) "
        "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING", (data_generation, digest, job_id, task_name))
    cursor = await (await connection.execute(
        "SELECT event_id,ordinal,active_attempt,active_event_id,last_type,task_name "
        "FROM runtime.rbi_execution_history_cursors WHERE data_generation=%s AND identity_digest=%s "
        "AND job_id=%s FOR UPDATE", (data_generation, digest, job_id))).fetchone()
    if not cursor:
        raise AppError("execution_attempt_identity_conflict", status=409)
    if cursor["task_name"] != task_name:
        return result(False, "execution_history_job_missing_or_changed")
    if cursor["event_id"]:
        anchor = await (await connection.execute(
            "SELECT 1 FROM procrastinate_events WHERE id=%s AND job_id=%s",
            (cursor["event_id"], job_id))).fetchone()
        if not anchor:
            return result(False, "execution_history_incomplete")
    events = await (await connection.execute(
        "SELECT id,type FROM procrastinate_events WHERE job_id=%s AND id>%s ORDER BY id LIMIT %s",
        (job_id, cursor["event_id"], limit + 1))).fetchall()
    more, page = len(events) > limit, events[:limit]
    ordinal, active, active_event, last = (cursor["ordinal"], cursor["active_attempt"],
                                           cursor["active_event_id"], cursor["last_type"])
    event_id = cursor["event_id"]
    executions, native_inflight = [], []
    for event in page:
        event_id, event_type = event["id"], event["type"]
        if event_type == "started":
            if active is not None:
                return result(False, "execution_history_incomplete", events_read=len(page))
            active, active_event, last = ordinal, event_id, None
        elif event_type == "abort_requested":
            # The retry-on-abort path can terminate without incrementing the
            # attempt counter; do not persist guessed physical ordinals.
            return result(False, "execution_history_incomplete", events_read=len(page))
        elif event_type == "cancelled":
            if active is not None:
                return result(False, "execution_history_incomplete", events_read=len(page))
            last = "cancelled"
        elif event_type == "retried":
            if active is not None or last not in {"failed", "cancelled"}:
                return result(False, "execution_history_incomplete", events_read=len(page))
            ordinal, last = ordinal + 1, "retry"
        elif event_type in {"deferred_for_retry", "failed", "succeeded", "aborted"}:
            if active is None:
                return result(False, "execution_history_incomplete", events_read=len(page))
            outcome = {"failed": "failed", "succeeded": "completed", "aborted": "unknown",
                       "deferred_for_retry": "unknown"}[event_type]
            if not await _wake_exit_on(connection, data_generation, job_id, active):
                executions.append({"job_id": job_id, "native_attempt": active, "outcome": outcome,
                                   "evidence_ref": "native-job-event:" + str(event_id)})
            active, active_event, last = None, None, event_type
            ordinal += 1
    if active is not None:
        anchor = await (await connection.execute(
            "SELECT 1 FROM procrastinate_events WHERE id=%s AND job_id=%s AND type='started'",
            (active_event, job_id))).fetchone()
        if not anchor:
            return result(False, "execution_history_incomplete", events_read=len(page))
    if not more:
        status = row["status"]
        if (row["attempts"] != ordinal or status in {"doing", "aborting"} and active is None
                or status not in {"doing", "aborting"} and active is not None
                or status == "failed" and last not in {"failed", "cancelled"}
                or status == "succeeded" and last not in {"succeeded", "cancelled"}
                or status == "aborted" and last != "aborted"
                or status == "cancelled" and last != "cancelled"):
            return result(False, "execution_history_incomplete", events_read=len(page))
        is_current = (status == "doing" and current_job is not None and current_job["job_id"] == job_id
                      and current_job["native_attempt"] == active)
        if status in {"doing", "aborting"}:
            tail = (await _prebegin_tail_on(connection, generation=data_generation, job_id=job_id,
                native_attempt=active, started_event_id=active_event, identity_digest=digest)
                if task_name == "conversation.result" else None)
            if tail:
                native_inflight.append(tail)
            elif not is_current and not await _wake_exit_on(connection, data_generation, job_id, active):
                executions.append({"job_id": job_id, "native_attempt": active, "outcome": "unknown",
                                   "evidence_ref": "native-job-event:" + str(active_event)})
    await seed_execution_history_on(connection, owner=owner, identity=identity,
        data_generation=data_generation, max_failures=max_failures, history=executions, complete=False)
    await connection.execute(
        "UPDATE runtime.rbi_execution_history_cursors SET event_id=%s,ordinal=%s,active_attempt=%s,"
        "active_event_id=%s,last_type=%s WHERE data_generation=%s AND identity_digest=%s AND job_id=%s",
        (event_id, ordinal, active, active_event, last, data_generation, digest, job_id))
    return result(not more, "execution_history_more" if more else None, executions, len(page), native_inflight)
