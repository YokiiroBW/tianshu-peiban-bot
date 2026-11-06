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


async def _snapshot_on(connection, data_generation, digest, binding):
    if binding is None or binding["history_state"] != "history_complete":
        return {"eligible": False, "reason": "history_unknown", "history_complete": False,
                "total_executions": None, "failed_executions": None, "unknown_held": None,
                "active_executions": None, "unresolved_executions": [], "unresolved_complete": False,
                "remaining": None, "max_failures": binding["max_failures"] if binding else None, "retry_at": None}
    counts = await (await connection.execute(
        "SELECT count(*) AS total,"
        "count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='failed') AS failures,"
        "count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='started') AS active,"
        "count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='unknown') AS unknown,"
        "max(CASE WHEN COALESCE(resolved_outcome,outcome)='wait' "
        "AND COALESCE(resolved_retry_at,retry_at)>now() THEN COALESCE(resolved_retry_at,retry_at) END) AS retry_at "
        "FROM runtime.rbi_execution_facts WHERE data_generation=%s AND identity_digest=%s AND job_id>0",
        (data_generation, digest))).fetchone()
    unresolved = await (await connection.execute(
        "SELECT job_id,native_attempt,outcome,evidence_ref FROM runtime.rbi_execution_facts "
        "WHERE data_generation=%s AND identity_digest=%s AND job_id>0 AND resolved_outcome IS NULL "
        "AND outcome IN ('started','unknown') ORDER BY recorded_at,job_id,native_attempt LIMIT 33",
        (data_generation, digest))).fetchall()
    reason = ("execution_outcome_unknown" if counts["unknown"] else
              "execution_in_progress" if counts["active"] else
              "execution_failure_budget_exhausted" if counts["failures"] >= binding["max_failures"] else
              "execution_wait" if counts["retry_at"] is not None else None)
    return {"eligible": reason is None, "reason": reason, "history_complete": True,
            "total_executions": counts["total"], "failed_executions": counts["failures"],
            "unknown_held": counts["unknown"], "active_executions": counts["active"],
            "unresolved_executions": unresolved[:32], "unresolved_complete": len(unresolved) <= 32,
            "remaining": max(0, binding["max_failures"] - counts["failures"] - counts["unknown"] - counts["active"]),
            "max_failures": binding["max_failures"], "retry_at": counts["retry_at"] if reason == "execution_wait" else None}


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
            if (outcome != "unknown" and effective not in {"unknown", "started", outcome}
                    and not (effective == "wait" and outcome == "failed")):
                raise AppError("execution_history_conflict", status=409)
            if effective == outcome == "wait" and effective_retry != retry_at:
                raise AppError("execution_history_conflict", status=409)
            continue
        inserted = await (await connection.execute(
            "INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,job_id,"
            "native_attempt,outcome,retry_at,evidence_ref) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING job_id",
            (data_generation, digest, owner, max_failures, job, attempt, outcome, retry_at, evidence_ref))).fetchone()
        if not inserted:
            raise AppError("execution_attempt_identity_conflict", status=409)
    if binding["history_state"] != state:
        await connection.execute("UPDATE runtime.rbi_execution_facts SET outcome=%s WHERE data_generation=%s "
                                 "AND identity_digest=%s AND job_id=0 AND native_attempt=0",
                                 (state, data_generation, digest))
        binding = {**binding, "history_state": state}
    return {"ready": complete, **await _snapshot_on(connection, data_generation, digest, binding)}


async def inspect_execution_on(connection, *, owner, identity, data_generation, max_failures):
    digest = _inputs(owner, identity, data_generation, max_failures)
    await _generation_on(connection, data_generation, lock=False)
    binding = await _binding_on(connection, data_generation, digest, owner, max_failures)
    result = await _snapshot_on(connection, data_generation, digest, binding)
    if binding is None:
        result["max_failures"] = max_failures
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


async def begin_execution_on(connection, *, owner, identity, data_generation, job_id, native_attempt, max_failures):
    digest = _inputs(owner, identity, data_generation, max_failures)
    _physical(job_id, native_attempt)
    await _generation_on(connection, data_generation)
    await _lock_on(connection, data_generation, digest)
    binding = await _binding_on(connection, data_generation, digest, owner, max_failures)
    result = await _snapshot_on(connection, data_generation, digest, binding)
    if binding is None:
        result["max_failures"] = max_failures
        return result
    existing = await (await connection.execute(
        "SELECT outcome FROM runtime.rbi_execution_facts WHERE data_generation=%s AND identity_digest=%s "
        "AND job_id=%s AND native_attempt=%s", (data_generation, digest, job_id, native_attempt))).fetchone()
    if existing:
        result.update(eligible=False, reason="execution_attempt_already_recorded", retry_at=None)
        return result
    if not result["eligible"]:
        return result
    await _current_job_on(connection, data_generation, job_id, native_attempt)
    inserted = await (await connection.execute(
        "INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,job_id,"
        "native_attempt,outcome) VALUES(%s,%s,%s,%s,%s,%s,'started') ON CONFLICT DO NOTHING RETURNING job_id",
        (data_generation, digest, owner, max_failures, job_id, native_attempt))).fetchone()
    if not inserted:
        raise AppError("execution_attempt_identity_conflict", status=409)
    result["total_executions"] += 1
    result["active_executions"] += 1
    result["unresolved_executions"] = [{"job_id": job_id, "native_attempt": native_attempt,
                                        "outcome": "started", "evidence_ref": None}]
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


async def finish_execution_on(connection, *, owner, identity, data_generation, job_id, native_attempt, outcome, error=None):
    digest = _inputs(owner, identity, data_generation)
    _physical(job_id, native_attempt)
    actual, retry_at = _classify_outcome(outcome, error)
    await _generation_on(connection, data_generation)
    await _lock_on(connection, data_generation, digest)
    binding = await _binding_on(connection, data_generation, digest, owner)
    if not binding:
        raise AppError("execution_history_unknown", status=409)
    fact = await (await connection.execute(
        "SELECT outcome,retry_at,resolved_outcome FROM runtime.rbi_execution_facts WHERE data_generation=%s "
        "AND identity_digest=%s AND job_id=%s AND native_attempt=%s FOR UPDATE",
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
    return {"recorded": recorded, "outcome": actual, **await _snapshot_on(connection, data_generation, digest, binding)}


async def resolve_execution_on(connection, *, owner, identity, data_generation, job_id, native_attempt,
                               outcome, observation_ref, retry_at=None):
    digest = _inputs(owner, identity, data_generation)
    _physical(job_id, native_attempt)
    if (outcome not in {"failed", "wait", "completed"} or not isinstance(observation_ref, str)
            or not observation_ref.strip() or len(observation_ref) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in observation_ref)
            or outcome == "wait" and not _aware(retry_at) or outcome != "wait" and retry_at is not None):
        raise ValueError("invalid_execution_observation")
    await _generation_on(connection, data_generation)
    await _lock_on(connection, data_generation, digest)
    binding = await _binding_on(connection, data_generation, digest, owner)
    if not binding:
        raise AppError("execution_history_unknown", status=409)
    fact = await (await connection.execute(
        "SELECT outcome,resolved_outcome,resolved_retry_at,observation_ref FROM runtime.rbi_execution_facts "
        "WHERE data_generation=%s AND identity_digest=%s AND job_id=%s AND native_attempt=%s FOR UPDATE",
        (data_generation, digest, job_id, native_attempt))).fetchone()
    if not fact or fact["outcome"] not in {"started", "unknown"}:
        raise AppError("execution_attempt_not_unknown", status=409)
    recorded = fact["resolved_outcome"] is None
    if recorded:
        native = await (await connection.execute(
            "SELECT status,attempts FROM procrastinate_jobs WHERE id=%s FOR SHARE", (job_id,))).fetchone()
        if native and native["status"] in {"doing", "todo"} and native["attempts"] == native_attempt:
            result = await _snapshot_on(connection, data_generation, digest, binding)
            result.update(eligible=False, reason="execution_in_progress", retry_at=None)
            return {"recorded": False, "outcome": None, **result}
        await connection.execute(
            "UPDATE runtime.rbi_execution_facts SET resolved_outcome=%s,resolved_retry_at=%s,observation_ref=%s,"
            "resolved_at=now() WHERE data_generation=%s AND identity_digest=%s AND job_id=%s AND native_attempt=%s",
            (outcome, retry_at, observation_ref, data_generation, digest, job_id, native_attempt))
    elif (fact["resolved_outcome"] != outcome or fact["resolved_retry_at"] != retry_at
          or fact["observation_ref"] != observation_ref):
        raise AppError("execution_attempt_conflict", status=409)
    return {"recorded": recorded, "outcome": outcome, **await _snapshot_on(connection, data_generation, digest, binding)}


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
        return {"complete": True, "reason": None, "executions": []}
    rows = await (await connection.execute(
        "SELECT j.id,j.status,j.attempts,j.task_name,EXISTS (SELECT 1 FROM runtime.job_keys k "
        "WHERE k.job_id=j.id AND k.data_generation=%s) AS generation_matches "
        "FROM procrastinate_jobs j WHERE j.id=ANY(%s) ORDER BY j.id",
        (data_generation, ids))).fetchall()
    if len(rows) != len(ids) or any(row["task_name"] != task_name or not row["generation_matches"] for row in rows):
        return {"complete": False, "reason": "execution_history_job_missing_or_changed", "executions": []}
    events = await (await connection.execute(
        "SELECT id,job_id,type FROM procrastinate_events WHERE job_id=ANY(%s) "
        "AND type IN ('started','deferred_for_retry','failed','succeeded','aborted','retried') "
        "ORDER BY job_id,id", (ids,))).fetchall()
    by_job = {job_id: [] for job_id in ids}
    for event in events:
        by_job[event["job_id"]].append(event)
    executions = []
    for row in rows:
        ordinal, active, last = 0, None, None
        for event in by_job[row["id"]]:
            event_type = event["type"]
            if event_type == "started":
                if active is not None:
                    return {"complete": False, "reason": "execution_history_incomplete", "executions": []}
                active, last = (ordinal, event["id"]), None
            elif event_type == "retried":
                if active is not None or last != "failed":
                    return {"complete": False, "reason": "execution_history_incomplete", "executions": []}
                ordinal += 1
                last = "retry"
            else:
                if active is None:
                    return {"complete": False, "reason": "execution_history_incomplete", "executions": []}
                outcome = {"failed": "failed", "succeeded": "completed", "aborted": "unknown",
                           "deferred_for_retry": "unknown"}[event_type]
                executions.append({"job_id": row["id"], "native_attempt": active[0], "outcome": outcome,
                                   "evidence_ref": "native-job-event:" + str(event["id"])})
                active, last = None, event_type
                if event_type == "deferred_for_retry":
                    ordinal += 1
        if row["attempts"] != ordinal:
            return {"complete": False, "reason": "execution_history_incomplete", "executions": []}
        if row["status"] == "doing":
            if active is None:
                return {"complete": False, "reason": "execution_history_incomplete", "executions": []}
            is_current = (current_job is not None and current_job["job_id"] == row["id"]
                          and current_job["native_attempt"] == active[0])
            if not is_current:
                executions.append({"job_id": row["id"], "native_attempt": active[0], "outcome": "unknown",
                                   "evidence_ref": "native-job-event:" + str(active[1])})
        elif active is not None or (row["status"] == "failed" and last != "failed") or (
                row["status"] == "succeeded" and last != "succeeded"):
            return {"complete": False, "reason": "execution_history_incomplete", "executions": []}
    return {"complete": True, "reason": None, "executions": executions}


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

    def result(complete, reason, executions=(), events_read=0):
        return {"complete": complete, "reason": reason, "executions": list(executions),
                "events_read": events_read, "job_status": row["status"] if row else None}

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
    events = await (await connection.execute(
        "SELECT id,type FROM procrastinate_events WHERE job_id=%s AND id>%s ORDER BY id LIMIT %s",
        (job_id, cursor["event_id"], limit + 1))).fetchall()
    more, page = len(events) > limit, events[:limit]
    ordinal, active, active_event, last = (cursor["ordinal"], cursor["active_attempt"],
                                           cursor["active_event_id"], cursor["last_type"])
    event_id = cursor["event_id"]
    executions = []
    for event in page:
        event_id, event_type = event["id"], event["type"]
        if event_type == "started":
            if active is not None:
                return result(False, "execution_history_incomplete", events_read=len(page))
            active, active_event, last = ordinal, event_id, None
        elif event_type == "retried":
            if active is not None or last != "failed":
                return result(False, "execution_history_incomplete", events_read=len(page))
            ordinal, last = ordinal + 1, "retry"
        elif event_type in {"deferred_for_retry", "failed", "succeeded", "aborted"}:
            if active is None:
                return result(False, "execution_history_incomplete", events_read=len(page))
            outcome = {"failed": "failed", "succeeded": "completed", "aborted": "unknown",
                       "deferred_for_retry": "unknown"}[event_type]
            executions.append({"job_id": job_id, "native_attempt": active, "outcome": outcome,
                               "evidence_ref": "native-job-event:" + str(event_id)})
            active, active_event, last = None, None, event_type
            if event_type == "deferred_for_retry":
                ordinal += 1
    if not more:
        status = row["status"]
        if (row["attempts"] != ordinal or status in {"doing", "aborting"} and active is None
                or status not in {"doing", "aborting"} and active is not None
                or status == "failed" and last != "failed"
                or status == "succeeded" and last != "succeeded"
                or status == "aborted" and last != "aborted"):
            return result(False, "execution_history_incomplete", events_read=len(page))
        is_current = (current_job is not None and current_job["job_id"] == job_id
                      and current_job["native_attempt"] == active)
        if status in {"doing", "aborting"} and not is_current:
            executions.append({"job_id": job_id, "native_attempt": active, "outcome": "unknown",
                               "evidence_ref": "native-job-event:" + str(active_event)})
    await seed_execution_history_on(connection, owner=owner, identity=identity,
        data_generation=data_generation, max_failures=max_failures, history=executions, complete=False)
    await connection.execute(
        "UPDATE runtime.rbi_execution_history_cursors SET event_id=%s,ordinal=%s,active_attempt=%s,"
        "active_event_id=%s,last_type=%s WHERE data_generation=%s AND identity_digest=%s AND job_id=%s",
        (event_id, ordinal, active, active_event, last, data_generation, digest, job_id))
    return result(not more, "execution_history_more" if more else None, executions, len(page))
