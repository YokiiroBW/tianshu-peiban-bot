"""One native wake for a frozen result identity, shared by every entry point."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass

from .codec import document, signature
from .rbg_result_ledger import CONSUMERS, TERMINAL, event_identity, finish_on, invalidate_on, register_on


EXECUTION_OWNER = "conversation.result"
MAX_FAILURES = 4
HISTORY_JOBS_PER_TICK = 16
HISTORY_EVENTS_PER_JOB = 128


def execution_identity(generation, event):
    action_id, receipt_id, revision = event_identity(event)
    return "conversation.result:" + signature([generation, action_id, revision, receipt_id])


async def execution_budget_on(connection, jobs, generation, event, delivery, *, current_job=None,
                              initial_key=None, known_keys=(), reconcile_stranded=False):
    """Advance bounded native history under the already locked delivery row.

    Runtime persists the event cursor for each job; this domain persists the
    receipt's frozen job-ID range, known jobs and incomplete open-job sweep.
    """
    action_id, receipt_id, revision = event_identity(event)
    identity = execution_identity(generation, event)
    known = set(delivery.get("rbi_job_ids") or ())
    opened = set(delivery.get("rbi_open_job_ids") or ())
    cursor = delivery.get("rbi_history_cursor") or 0
    upper = delivery.get("rbi_history_upper")
    gap = bool(delivery.get("rbi_history_gap"))
    open_cursor = delivery.get("rbi_open_cursor") or 0
    proven = dict(delivery.get("rbi_failed_native") or {})
    original_id = delivery.get("original_job_id")
    if original_id is not None:
        known.add(original_id)
    elif not (delivery.get("_new_delivery") and initial_key and not known):
        gap = True
    if current_job:
        known.add(current_job["job_id"])
        opened.add(current_job["job_id"])
        if current_job["job_id"] <= open_cursor:
            open_cursor = current_job["job_id"] - 1

    head_key = f"result-head:{generation}:{action_id}:{revision}:{receipt_id}"
    expected_keys = tuple(dict.fromkeys(("capability.result:" + receipt_id, head_key, *(known_keys or ()))))
    keys = await (await connection.execute(
        "SELECT key,job_id FROM runtime.job_keys WHERE task='conversation.result' "
        "AND data_generation=%s AND key=ANY(%s::text[])",
        (generation, list(expected_keys)))).fetchall()
    key_gap = False
    for item in keys:
        if item["job_id"] is None:
            key_gap = True
        else:
            if item["job_id"] not in known and item["job_id"] <= cursor:
                cursor = 0
            known.add(item["job_id"])

    latest = await (await connection.execute(
        "SELECT j.id FROM procrastinate_jobs j WHERE j.task_name='conversation.result' "
        "AND j.args->'event'->>'event_id'=%s AND j.args->'event'->>'action_id'=%s "
        "AND j.args->'event'->>'result_revision'=%s AND EXISTS "
        "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s) "
        "ORDER BY j.id DESC LIMIT 1", (receipt_id, action_id, str(revision), generation))).fetchone()
    latest_id = max([cursor, latest["id"] if latest else 0, *known])
    if upper is None:
        if gap:
            # A repaired or restored history receives a fresh bounded pass.
            cursor, gap = 0, False
        upper = latest_id
    gap = gap or key_gap

    missing = await (await connection.execute(
        "SELECT ref.id FROM unnest(%s::bigint[]) ref(id) "
        "LEFT JOIN procrastinate_jobs j ON j.id=ref.id AND j.task_name='conversation.result' "
        "WHERE j.id IS NULL OR j.args->'event' IS DISTINCT FROM %s::jsonb OR NOT EXISTS "
        "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=ref.id AND k.task='conversation.result' "
        "AND k.data_generation=%s) LIMIT 1", (list(known), document(event), generation))).fetchone()
    if missing:
        gap = True
    if original_id is None and not (delivery.get("_new_delivery") and initial_key and not known):
        gap = True

    pages = HISTORY_JOBS_PER_TICK
    processed = set()

    async def advance(job_id):
        nonlocal pages, gap
        pages -= 1
        original = await (await connection.execute(
            "SELECT j.args FROM procrastinate_jobs j WHERE j.id=%s AND j.task_name='conversation.result' "
            "AND EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id "
            "AND k.task='conversation.result' AND k.data_generation=%s)", (job_id, generation))).fetchone()
        if not original:
            gap = True
            return True
        saved = (original["args"] or {}).get("event") or {}
        if saved.get("source_invalidated"):
            gap = True
            return True
        if signature(saved) != signature(event):
            raise AppError("result_receipt_revision_conflict", status=409)
        known.add(job_id)
        observation = await jobs.advance_execution_history_on(connection, owner=EXECUTION_OWNER,
            identity=identity, data_generation=generation, max_failures=MAX_FAILURES,
            job_id=job_id, task_name="conversation.result",
            current_job=current_job if current_job and current_job["job_id"] == job_id else None,
            limit=HISTORY_EVENTS_PER_JOB)
        for item in observation.get("executions", []):
            if item.get("outcome") == "failed":
                proven[f"{item['job_id']}:{item['native_attempt']}"] = item.get("evidence_ref") or "native_failed"
        if not observation.get("complete"):
            if observation.get("reason") == "execution_history_more":
                return False
            gap = True
            return True
        processed.add(job_id)
        if observation.get("job_status") in {"failed", "succeeded", "aborted", "cancelled"}:
            opened.discard(job_id)
        else:
            opened.add(job_id)
        return True

    # Freeze this pass's maximum ID. Rows discovered or scheduled afterwards
    # are picked up by the next pass, never by an unbounded loop in one tick.
    while pages and cursor < upper:
        native = await (await connection.execute(
            "SELECT j.id FROM procrastinate_jobs j WHERE j.task_name='conversation.result' "
            "AND j.args->'event'->>'event_id'=%s AND j.args->'event'->>'action_id'=%s "
            "AND j.args->'event'->>'result_revision'=%s AND j.id>%s AND j.id<=%s "
            "AND EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s) "
            "ORDER BY j.id LIMIT 1", (receipt_id, action_id, str(revision), cursor, upper, generation))).fetchone()
        expected = min((item for item in known if cursor < item <= upper), default=None)
        candidates = [item for item in (native["id"] if native else None, expected) if item is not None]
        if not candidates:
            cursor = upper
            break
        job_id = min(candidates)
        if not await advance(job_id):
            break
        cursor = job_id

    scan_done = cursor >= upper
    open_swept = False
    if scan_done:
        while pages:
            job_id = min((item for item in opened if item > open_cursor), default=None)
            if job_id is None:
                open_cursor, open_swept = 0, True
                break
            if job_id not in processed and not await advance(job_id):
                break
            open_cursor = job_id
        if not any(item > open_cursor for item in opened):
            open_cursor, open_swept = 0, True

    # A job created after the frozen upper bound requires a subsequent pass.
    current_latest = await (await connection.execute(
        "SELECT j.id FROM procrastinate_jobs j WHERE j.task_name='conversation.result' "
        "AND j.args->'event'->>'event_id'=%s AND j.args->'event'->>'action_id'=%s "
        "AND j.args->'event'->>'result_revision'=%s AND EXISTS "
        "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s) "
        "ORDER BY j.id DESC LIMIT 1", (receipt_id, action_id, str(revision), generation))).fetchone()
    more_ids = max([current_latest["id"] if current_latest else 0, *known, 0]) > upper
    complete = scan_done and open_swept and not gap and not more_ids
    next_upper = None if scan_done and open_swept else upper
    await connection.execute(
        "UPDATE conversation.result_deliveries SET rbi_job_ids=%s::bigint[],rbi_open_job_ids=%s::bigint[],"
        "rbi_history_cursor=%s,rbi_history_upper=%s,rbi_history_gap=%s,rbi_open_cursor=%s,rbi_failed_native=%s "
        "WHERE data_generation=%s AND receipt_id=%s",
        (sorted(known), sorted(opened), cursor, next_upper, gap, open_cursor, document(proven), generation, receipt_id))
    await jobs.seed_execution_history_on(connection, owner=EXECUTION_OWNER, identity=identity,
        data_generation=generation, max_failures=MAX_FAILURES, history=[], complete=complete)
    budget = await jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER, identity=identity,
                                             data_generation=generation, max_failures=MAX_FAILURES)
    if reconcile_stranded and complete and budget.get("unresolved_executions"):
        # Sources and Action are already fenced by the caller. Do not revoke a
        # progress token while another native worker is still executing.
        other_doing = await (await connection.execute(
            "SELECT 1 FROM procrastinate_jobs WHERE id=ANY(%s::bigint[]) AND status='doing' "
            "AND id IS DISTINCT FROM %s LIMIT 1", (list(known),
            current_job["job_id"] if current_job else None))).fetchone()
        if not other_doing:
            for item in budget["unresolved_executions"]:
                proof_key = f"{item['job_id']}:{item['native_attempt']}"
                if proof_key not in proven:
                    continue
                progress = await (await connection.execute(
                    "SELECT consumer,disposition,owner_token FROM conversation.result_consumer_progress "
                    "WHERE data_generation=%s AND receipt_id=%s ORDER BY consumer FOR UPDATE",
                    (generation, receipt_id))).fetchall()
                resolved = await jobs.resolve_execution_on(connection, owner=EXECUTION_OWNER,
                    identity=identity, data_generation=generation, job_id=item["job_id"],
                    native_attempt=item["native_attempt"], outcome="failed",
                    observation_ref=f"result-progress:{receipt_id}:{proof_key}")
                if not resolved.get("recorded"):
                    continue
                for part in progress:
                    if part["disposition"] != "running":
                        continue
                    atomic = part["consumer"] in {"life_activity", "life_commitment", "detached", "memory"}
                    await connection.execute(
                        "UPDATE conversation.result_consumer_progress SET disposition=%s,reason=%s,"
                        "owner_token=NULL,lease_until=NULL,rbi_retry_at=NULL,updated_at=now() "
                        "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s "
                        "AND disposition='running' AND owner_token=%s",
                        ("pending" if atomic else "blocked",
                         "execution_interrupted" if atomic else "database_outcome_unconfirmed",
                         generation, receipt_id, part["consumer"], part["owner_token"]))
                proven.pop(proof_key, None)
            await connection.execute(
                "UPDATE conversation.result_deliveries SET rbi_failed_native=%s "
                "WHERE data_generation=%s AND receipt_id=%s", (document(proven), generation, receipt_id))
            budget = await jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER, identity=identity,
                                                     data_generation=generation, max_failures=MAX_FAILURES)
    return budget


def runnable_consumers(progress):
    """Life consumers are independent; dialogue and fallback need renderer proof."""
    by_name = {item["consumer"]: item for item in progress}
    renderer = by_name.get("renderer")
    renderer_done = bool(renderer and renderer["disposition"] in TERMINAL)
    available, blocked = [], []
    for name in ("renderer", "dialogue", "life_activity", "life_commitment", "detached", "memory"):
        item = by_name.get(name)
        if item and item["disposition"] in TERMINAL:
            continue
        if item and item["disposition"] == "blocked":
            blocked.append(name)
            continue
        if name in {"dialogue", "detached", "memory"} and not renderer_done:
            continue
        if item and item.get("rbi_retry_at") and item.get("observed_at") and item["rbi_retry_at"] > item["observed_at"]:
            continue
        available.append(name)
    return tuple(available), tuple(blocked)


def waiting_consumers(progress):
    by_name = {item["consumer"]: item for item in progress}
    renderer = by_name.get("renderer")
    renderer_done = bool(renderer and renderer["disposition"] in TERMINAL)
    return tuple(item for item in progress if item["disposition"] == "pending"
        and item.get("rbi_retry_at") and item.get("observed_at")
        and item["rbi_retry_at"] > item["observed_at"]
        and (item["consumer"] not in {"dialogue", "detached", "memory"} or renderer_done))


async def work_on(connection, generation, event):
    """Read live native work and leases for the complete original identity."""
    action_id, receipt_id, revision = event_identity(event)
    progress = await (await connection.execute(
        "SELECT consumer,disposition,reason,lease_until,rbi_retry_at,now() AS observed_at "
        "FROM conversation.result_consumer_progress WHERE data_generation=%s AND receipt_id=%s "
        "ORDER BY consumer", (generation, receipt_id))).fetchall()
    jobs = await (await connection.execute(
        "SELECT j.id,j.status,j.scheduled_at,j.args,"
        "(j.status='doing' AND w.last_heartbeat>now()-interval '30 seconds') AS live_doing "
        "FROM procrastinate_jobs j LEFT JOIN procrastinate_workers w ON w.id=j.worker_id "
        "WHERE j.task_name='conversation.result' AND j.status IN ('todo','doing') "
        "AND j.args->'event'->>'event_id'=%s AND j.args->'event'->>'action_id'=%s "
        "AND j.args->'event'->>'result_revision'=%s "
        "AND EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id "
        "AND k.data_generation=%s AND k.cancelled_at IS NULL) ORDER BY j.id",
        (receipt_id, action_id, str(revision), generation))).fetchall()
    live = []
    for job in jobs:
        saved = (job["args"] or {}).get("event") or {}
        if saved.get("source_invalidated"):
            continue
        if signature(saved) != signature(event):
            raise AppError("result_receipt_revision_conflict", status=409)
        if job["status"] == "todo" or job["live_doing"]:
            live.append(job)
    running = [item for item in progress if item["disposition"] == "running"
               and item["lease_until"] and item["lease_until"] > item["observed_at"]]
    ready, blocked = runnable_consumers(progress)
    return {"progress": progress, "jobs": live, "running": running,
            "runnable": ready, "blocked": blocked, "waiting": waiting_consumers(progress)}


async def ensure_on(connection, jobs, generation, event, *, initial_key=None, scheduled_at=None,
                    allow_successor=False, known_keys=(), known_job_ids=(), reconcile_stranded=False):
    """Serialize a result identity; never reuse a terminal physical job key."""
    action_id, receipt_id, revision = event_identity(event)
    await jobs.assert_generation_on(connection, generation)
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("conversation.result.recovery:" + generation + ":" + receipt_id,))
    delivery = await register_on(connection, generation, event)
    if any(type(job_id) is not int or job_id < 1 for job_id in known_job_ids):
        raise ValueError("invalid_result_job_reference")
    added = set(known_job_ids) - set(delivery.get("rbi_job_ids") or ())
    if added:
        ids = sorted(set(delivery.get("rbi_job_ids") or ()) | added)
        opened = sorted(set(delivery.get("rbi_open_job_ids") or ()) | added)
        restart = any(job_id <= delivery.get("rbi_history_cursor", 0) for job_id in added)
        await connection.execute(
            "UPDATE conversation.result_deliveries SET rbi_job_ids=%s::bigint[],rbi_open_job_ids=%s::bigint[],"
            "rbi_history_cursor=CASE WHEN %s THEN 0 ELSE rbi_history_cursor END,"
            "rbi_history_upper=CASE WHEN %s THEN NULL ELSE rbi_history_upper END "
            "WHERE data_generation=%s AND receipt_id=%s", (ids, opened, restart, restart, generation, receipt_id))
        delivery = {**delivery, "rbi_job_ids": ids, "rbi_open_job_ids": opened,
                    "rbi_history_cursor": 0 if restart else delivery.get("rbi_history_cursor", 0),
                    "rbi_history_upper": None if restart else delivery.get("rbi_history_upper")}
    if event.get("source_invalidated"):
        await invalidate_on(connection, generation, event, "source_unavailable")
        return {"state": "verified", "reason": "source_withdrawn"}
    original_id = delivery.get("original_job_id")
    if original_id is None and initial_key:
        original_key = await (await connection.execute(
            "SELECT job_id,cancelled_at,data_generation FROM runtime.job_keys WHERE key=%s", (initial_key,))).fetchone()
        if original_key:
            if original_key["cancelled_at"] or original_key["data_generation"] != generation:
                raise AppError("original_result_job_unavailable", status=409)
            original_id = original_key["job_id"]
            if original_id is None:
                raise AppError("original_result_job_unavailable", status=409)
            delivery = await register_on(connection, generation, event, original_id)
    if original_id is not None:
        original = await (await connection.execute(
            "SELECT j.args,k.cancelled_at FROM procrastinate_jobs j JOIN runtime.job_keys k ON k.job_id=j.id "
            "WHERE j.id=%s AND j.task_name='conversation.result' AND k.data_generation=%s "
            "ORDER BY k.cancelled_at NULLS FIRST LIMIT 1", (original_id, generation))).fetchone()
        if not original or original["cancelled_at"]:
            raise AppError("original_result_job_unavailable", status=409)
        stored = (original["args"] or {}).get("event") or {}
        if stored.get("source_invalidated"):
            raise AppError("source_withdrawn", status=409)
        if signature(stored) != signature(event):
            raise AppError("result_receipt_revision_conflict", status=409)
        event = stored
    if delivery["state"] != "pending":
        return {"state": "verified", "reason": delivery["state"]}
    consumed = await (await connection.execute(
        "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
    if consumed:
        return {"state": "verified", "reason": "result_already_consumed"}
    work = await work_on(connection, generation, event)
    if (len(work["progress"]) == len(CONSUMERS)
            and all(item["disposition"] in TERMINAL for item in work["progress"])):
        if await finish_on(connection, generation, receipt_id):
            return {"state": "verified", "reason": "result_already_consumed"}
    pending = [job for job in work["jobs"] if job["status"] == "todo"]
    if pending:
        job = pending[0]
        return {"state": "accepted", "job_id": job["id"], "reason": "already_queued",
                "due_at": job["scheduled_at"]}
    if work["running"] and not allow_successor:
        return {"state": "accepted", "reason": "consumer_running"}
    doing = [job for job in work["jobs"] if job["status"] == "doing"]
    if doing and not allow_successor:
        return {"state": "accepted", "job_id": doing[0]["id"], "reason": "already_executing"}
    if not work["runnable"] and not work["waiting"]:
        return {"state": "blocked", "reason": "result_consumer_blocked" if work["blocked"] else "result_no_runnable_consumer",
                "blocked_consumers": work["blocked"]}
    if not work["runnable"]:
        scheduled_at = min(item["rbi_retry_at"] for item in work["waiting"])
    budget = await execution_budget_on(connection, jobs, generation, event, delivery,
                                       initial_key=initial_key, known_keys=known_keys,
                                       reconcile_stranded=reconcile_stranded)
    if not budget.get("eligible"):
        reason, retry_at = budget.get("reason") or "execution_ineligible", budget.get("retry_at")
        if reason == "execution_in_progress":
            retry_at = datetime.now(UTC) + timedelta(seconds=30)
        if reason in {"execution_wait", "execution_in_progress"} and retry_at is not None:
            await connection.execute(
                "UPDATE conversation.result_deliveries SET rbi_reconcile_due_at=%s,rbi_reconcile_reason=%s "
                "WHERE data_generation=%s AND receipt_id=%s AND state='pending'",
                (retry_at, reason, generation, receipt_id))
            return {"state": "accepted", "reason": reason, "due_at": retry_at}
        return {"state": "blocked", "reason": reason, "budget": budget}
    if reconcile_stranded:
        work = await work_on(connection, generation, event)
        if not work["runnable"] and not work["waiting"]:
            return {"state": "blocked", "reason": "result_consumer_blocked" if work["blocked"] else "result_no_runnable_consumer",
                    "blocked_consumers": work["blocked"]}
        if not work["runnable"]:
            scheduled_at = min(item["rbi_retry_at"] for item in work["waiting"])
    key = (initial_key if initial_key and original_id is None else
           "result-wake:" + execution_identity(generation, event) + ":" + str(uuid4()))
    # A permanent legacy key may already identify a failed job. Never accept it
    # as the newly requested wake, even when an Operations attempt is reused.
    job_id = await jobs.enqueue(connection, JobSpec("conversation.result", key, {"event": event},
        ServiceClass.INTERACTIVE, scheduled_at=scheduled_at,
        subject_id=event["context"]["actor_id"], data_generation=generation),
        queueing_lock=f"conversation.result:{generation}:{action_id}:{revision}:{receipt_id}")
    await connection.execute(
        "UPDATE conversation.result_deliveries SET rbi_job_ids=ARRAY("
        "SELECT DISTINCT unnest(rbi_job_ids || ARRAY[%s]::bigint[])),rbi_open_job_ids=ARRAY("
        "SELECT DISTINCT unnest(rbi_open_job_ids || ARRAY[%s]::bigint[])) "
        "WHERE data_generation=%s AND receipt_id=%s", (job_id, job_id, generation, receipt_id))
    if original_id is None:
        await register_on(connection, generation, event, job_id)
    return {"state": "accepted", "job_id": job_id, "reason": "scheduled", "due_at": scheduled_at}
