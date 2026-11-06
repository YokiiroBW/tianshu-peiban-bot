"""One native wake for a frozen result identity, shared by every entry point."""

from uuid import uuid4

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass

from .codec import document, signature
from .rbg_result_ledger import (CONSUMERS, NONATOMIC_CONSUMERS, TERMINAL, event_identity,
                                finish_on, invalidate_on, register_on)


EXECUTION_OWNER = "conversation.result"
MAX_FAILURES = 4
HISTORY_JOBS_PER_TICK = 16
HISTORY_EVENTS_PER_JOB = 128


def execution_identity(generation, event):
    action_id, receipt_id, revision = event_identity(event)
    return "conversation.result:" + signature([generation, action_id, revision, receipt_id])


async def lock_consumer_leases_on(connection, generation, event):
    """Keep existing claims stable while native history and wakes are reconciled."""
    action_id, receipt_id, revision = event_identity(event)
    rows = await (await connection.execute(
        "SELECT action_id,result_revision,disposition,owner_token,lease_until,now() AS observed_at "
        "FROM conversation.result_consumer_progress WHERE data_generation=%s AND receipt_id=%s "
        "ORDER BY consumer FOR UPDATE", (generation, receipt_id))).fetchall()
    if any((row["action_id"], row["result_revision"]) != (action_id, revision) for row in rows):
        raise AppError("result_receipt_revision_conflict", status=409)
    running = [row for row in rows if row["disposition"] == "running"]
    unknown = any(not row["owner_token"] or row["lease_until"] is None for row in running)
    due = min((row["lease_until"] for row in running if row["owner_token"] and row["lease_until"] is not None
               and row["lease_until"] > row["observed_at"]), default=None)
    return {"due_at": due, "unknown": unknown}


async def execution_budget_on(connection, jobs, generation, event, delivery, *, current_job=None,
                              initial_key=None, known_keys=(), reconcile_stranded=False,
                              repair_ref=None, consumer=None):
    """Advance bounded native history under the already locked delivery row.

    Runtime persists the event cursor for each job; this domain persists the
    receipt's frozen job-ID range, known jobs and incomplete open-job sweep.
    """
    action_id, receipt_id, revision = event_identity(event)
    claims = await lock_consumer_leases_on(connection, generation, event) if reconcile_stranded else None
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
    inspect_args = {"owner": EXECUTION_OWNER, "identity": identity, "data_generation": generation,
                    "max_failures": MAX_FAILURES}
    if repair_ref is not None:
        inspect_args.update(repair_ref=repair_ref, consumer=consumer)
    budget = await jobs.inspect_execution_on(connection, **inspect_args)
    if claims and (claims["due_at"] is not None or claims["unknown"]):
        # A claim is not linked to a native ordinal. Keep the imported failure
        # proof and unresolved Runtime fact until the claim can be reconciled.
        return {**budget, "consumer_running_due_at": claims["due_at"],
                "consumer_lease_unknown": claims["unknown"]}
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
                    "SELECT consumer,data_generation,action_id,receipt_id,result_revision,disposition,reason,proof,owner_token "
                    "FROM conversation.result_consumer_progress "
                    "WHERE data_generation=%s AND receipt_id=%s ORDER BY consumer FOR UPDATE",
                    (generation, receipt_id))).fetchall()
                if item.get("purpose") == "repair":
                    # A failed native job is not proof that an atomic repair's
                    # COMMIT failed. Only its exact original consumer proof can
                    # resolve the held physical attempt as completed.
                    from .rbj_result_budget import matching_life_proof
                    from .rbj_result_repair import evidence_on, result_proof_observation
                    target_consumer, target_ref = item.get("consumer"), item.get("repair_ref")
                    if not target_consumer or not target_ref:
                        continue
                    proof = next((part for part in progress if part["consumer"] == target_consumer), None)
                    evidence = await evidence_on(connection, generation, event, target_consumer, target_ref)
                    if not evidence or not matching_life_proof(event, target_consumer, proof, generation=generation,
                                                               owner_id=evidence["owner_id"]):
                        continue
                    resolved = await jobs.resolve_execution_on(connection, owner=EXECUTION_OWNER,
                        identity=identity, data_generation=generation, job_id=item["job_id"],
                        native_attempt=item["native_attempt"], outcome="completed",
                        observation_ref=result_proof_observation(generation, event, target_consumer, target_ref,
                            owner_id=evidence["owner_id"], disposition=proof["disposition"],
                            reason=proof["reason"], proof=proof["proof"]))
                    if resolved.get("recorded"):
                        proven.pop(proof_key, None)
                    continue
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
            budget = await jobs.inspect_execution_on(connection, **inspect_args)
    return budget


def unconfirmed_consumers(progress):
    """Find claims that cannot be safely resumed from a native failure alone."""
    unconfirmed, missing_lease = [], []
    for item in progress:
        if item["disposition"] != "running" or "lease_until" not in item:
            continue
        lease = item["lease_until"]
        if not item.get("owner_token") or lease is None:
            unconfirmed.append(item["consumer"])
            missing_lease.append(item["consumer"])
        elif item["consumer"] in NONATOMIC_CONSUMERS and lease <= item["observed_at"]:
            unconfirmed.append(item["consumer"])
    return tuple(unconfirmed), tuple(missing_lease)


def runnable_consumers(progress):
    """Life consumers are independent; dialogue and fallback need renderer proof."""
    by_name = {item["consumer"]: item for item in progress}
    renderer = by_name.get("renderer")
    renderer_done = bool(renderer and renderer["disposition"] in TERMINAL)
    available, blocked = [], []
    unconfirmed, _ = unconfirmed_consumers(progress)
    for name in ("renderer", "dialogue", "life_activity", "life_commitment", "detached", "memory"):
        item = by_name.get(name)
        if item and item["disposition"] in TERMINAL:
            continue
        if item and item["disposition"] == "blocked":
            blocked.append(name)
            continue
        if name in unconfirmed:
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


async def work_on(connection, generation, event, *, jobs=None):
    """Read live native work and leases for the complete original identity."""
    action_id, receipt_id, revision = event_identity(event)
    progress = await (await connection.execute(
        "SELECT consumer,data_generation,action_id,receipt_id,result_revision,disposition,reason,proof,"
        "owner_token,lease_until,rbi_retry_at,now() AS observed_at "
        "FROM conversation.result_consumer_progress WHERE data_generation=%s AND receipt_id=%s "
        "ORDER BY consumer", (generation, receipt_id))).fetchall()
    job_rows = await (await connection.execute(
        "SELECT j.id,j.status,j.scheduled_at,j.args,j.queueing_lock,"
        "(j.status='doing' AND w.last_heartbeat>now()-interval '30 seconds') AS live_doing "
        "FROM procrastinate_jobs j LEFT JOIN procrastinate_workers w ON w.id=j.worker_id "
        "WHERE j.task_name='conversation.result' AND j.status IN ('todo','doing') "
        "AND j.args->'event'->>'event_id'=%s AND j.args->'event'->>'action_id'=%s "
        "AND j.args->'event'->>'result_revision'=%s "
        "AND EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id "
        "AND k.data_generation=%s AND k.cancelled_at IS NULL) ORDER BY j.id",
        (receipt_id, action_id, str(revision), generation))).fetchall()
    live = []
    for job in job_rows:
        saved = (job["args"] or {}).get("event") or {}
        if saved.get("source_invalidated"):
            continue
        if signature(saved) != signature(event):
            raise AppError("result_receipt_revision_conflict", status=409)
        observation = await jobs.observe_on(connection, job["id"]) if jobs is not None else None
        if observation and observation.get("covered_by") is not None:
            continue
        if job["status"] == "todo" or job["live_doing"]:
            live.append(job)
    running = [item for item in progress if item["disposition"] == "running" and item["owner_token"]
               and item["lease_until"] and item["lease_until"] > item["observed_at"]]
    ready, blocked = runnable_consumers(progress)
    unconfirmed, missing_lease = unconfirmed_consumers(progress)
    return {"progress": progress, "jobs": live, "running": running,
            "runnable": ready, "blocked": blocked, "waiting": waiting_consumers(progress),
            "unconfirmed": unconfirmed, "missing_lease": missing_lease}


async def terminal_delivery_on(connection, generation, event, disposition):
    action_id, receipt_id, revision = event_identity(event)
    progress = await (await connection.execute(
        "SELECT consumer,action_id,result_revision,disposition FROM conversation.result_consumer_progress "
        "WHERE data_generation=%s AND receipt_id=%s", (generation, receipt_id))).fetchall()
    if any((item["action_id"], item["result_revision"]) != (action_id, revision) for item in progress):
        raise AppError("result_receipt_revision_conflict", status=409)
    aggregate = await (await connection.execute(
        "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
    if (not aggregate or len(progress) != len(CONSUMERS)
            or any(item["disposition"] not in TERMINAL for item in progress)):
        return {"state": "blocked", "reason": "result_consumer_outcome_unknown", "outcome": "unknown",
                "disposition": disposition}
    return {"state": "verified", "reason": disposition, "disposition": disposition}


async def ensure_on(connection, jobs, generation, event, *, initial_key=None, scheduled_at=None,
                    allow_successor=False, known_keys=(), known_job_ids=(), reconcile_stranded=False,
                    repair_ref=None, repair_consumer=None):
    """Serialize a result identity; never reuse a terminal physical job key."""
    from .rbj_result_budget import validate_repair
    repairing = validate_repair(repair_ref, repair_consumer)
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
        return await terminal_delivery_on(connection, generation, event, "invalidated")
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
        return await terminal_delivery_on(connection, generation, event, delivery["state"])
    consumed = await (await connection.execute(
        "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
    if consumed:
        return await terminal_delivery_on(connection, generation, event, "result_already_consumed")
    claims = await lock_consumer_leases_on(connection, generation, event)
    if claims["due_at"] is not None or claims["unknown"]:
        budget = await execution_budget_on(connection, jobs, generation, event, delivery,
            initial_key=initial_key, known_keys=known_keys, reconcile_stranded=reconcile_stranded,
            repair_ref=repair_ref, consumer=repair_consumer)
        if claims["due_at"] is not None:
            await connection.execute(
                "UPDATE conversation.result_deliveries SET rbi_reconcile_due_at=COALESCE(rbi_reconcile_due_at,%s),"
                "rbi_reconcile_reason=CASE WHEN rbi_reconcile_due_at IS NULL THEN 'consumer_running' "
                "ELSE rbi_reconcile_reason END "
                "WHERE data_generation=%s AND receipt_id=%s AND state='pending'",
                (claims["due_at"], generation, receipt_id))
            result = {"state": "waiting", "reason": "consumer_running", "due_at": claims["due_at"], "budget": budget}
            if claims["unknown"]:
                result["outcome"] = "unknown"
            return result
        return {"state": "blocked", "reason": "result_consumer_lease_unknown", "outcome": "unknown", "budget": budget}
    work = await work_on(connection, generation, event, jobs=jobs)
    wake_key = f"conversation.result:{generation}:{action_id}:{revision}:{receipt_id}"
    for job in work["jobs"]:
        if job["queueing_lock"] is not None:
            continue
        # A legacy unlocked wake is adopted only after the complete immutable
        # envelope and generation have been checked. Extra job arguments are
        # not silently folded into the ordinary purpose.
        payload = job["args"] or {}
        if set(payload) != {"event"}:
            continue
        await jobs.adopt_wake_on(connection, job["id"], expected_task="conversation.result",
                                 expected_generation=generation, expected_payload=payload, wake_key=wake_key)
    work = await work_on(connection, generation, event, jobs=jobs)
    if (len(work["progress"]) == len(CONSUMERS)
            and all(item["disposition"] in TERMINAL for item in work["progress"])):
        if await finish_on(connection, generation, receipt_id):
            return {"state": "verified", "reason": "result_already_consumed"}
    # A native todo/doing record does not establish execution eligibility. In
    # particular, old unlocked wakes can remain after the shared budget ran out.
    budget = await execution_budget_on(connection, jobs, generation, event, delivery,
                                       initial_key=initial_key, known_keys=known_keys,
                                       reconcile_stranded=reconcile_stranded,
                                       repair_ref=repair_ref, consumer=repair_consumer)
    selected = (budget.get("repair") or {}) if repairing else budget
    if not selected.get("eligible"):
        reason, retry_at = selected.get("reason") or ("repair_not_authorized" if repairing else "execution_ineligible"), selected.get("retry_at")
        scheduled_repair_wait = repairing and reason in {"execution_wait", "repair_wait"} and retry_at is not None
        if scheduled_repair_wait:
            scheduled_at = max(value for value in (scheduled_at, retry_at) if value is not None)
        if reason == "execution_in_progress":
            clock = await (await connection.execute("SELECT now()+interval '30 seconds' AS due_at")).fetchone()
            retry_at = clock["due_at"]
        if not scheduled_repair_wait and reason in {"execution_wait", "execution_in_progress", "repair_wait"} and retry_at is not None:
            await connection.execute(
                "UPDATE conversation.result_deliveries SET rbi_reconcile_due_at=%s,rbi_reconcile_reason=%s "
                "WHERE data_generation=%s AND receipt_id=%s AND state='pending'",
                (retry_at, reason, generation, receipt_id))
            return {"state": "waiting", "reason": reason, "due_at": retry_at, "budget": budget}
        if not scheduled_repair_wait:
            return {"state": "blocked", "reason": reason, "budget": budget}
    if reconcile_stranded:
        work = await work_on(connection, generation, event, jobs=jobs)
    if repairing:
        from .rbj_result_repair import completed_progress, evidence_on
        evidence = await evidence_on(connection, generation, event, repair_consumer, repair_ref)
        if not evidence:
            return {"state": "blocked", "reason": "repair_evidence_missing", "budget": budget}
        if not budget.get("failure_digest") or evidence["failure_digest"] != budget["failure_digest"]:
            return {"state": "blocked", "reason": "repair_failure_history_changed", "budget": budget}
        part = next((item for item in work["progress"] if item["consumer"] == repair_consumer), None)
        completed = completed_progress(generation, event, repair_consumer, part, owner_id=evidence["owner_id"])
        if completed:
            return completed
        if part and part["disposition"] == "blocked":
            return {"state": "blocked", "reason": part["reason"] or "result_consumer_blocked", "budget": budget}
        if part and part["rbi_retry_at"] and part["rbi_retry_at"] > part["observed_at"]:
            scheduled_at = max(value for value in (scheduled_at, part["rbi_retry_at"]) if value is not None)
    def same_purpose(job):
        args = job["args"] or {}
        return ((args.get("repair_ref"), args.get("repair_consumer")) == (repair_ref, repair_consumer)
                and set(args) == ({"event", "repair_ref", "repair_consumer"} if repairing else {"event"}))

    async def cover_exhausted_normal(repair_job_id, repair_payload, repair_key):
        coverage_ref = "result-budget:" + signature([execution_identity(generation, event),
            budget.get("failure_digest"), evidence["defect_evidence_ref"], repair_ref, repair_consumer])
        for old in work["jobs"]:
            if old["status"] != "todo" or set(old["args"] or {}) != {"event"}:
                continue
            covered = await jobs.cover_wake_on(connection, old["id"], expected_task="conversation.result",
                expected_generation=generation, expected_payload=old["args"], covered_by_job_id=repair_job_id,
                expected_covering_payload=repair_payload, wake_key=repair_key,
                coverage_ref=coverage_ref, not_before=scheduled_at)
            if covered.get("state") == "waiting":
                return covered
        return None

    if not repairing and not work["runnable"] and not work["waiting"]:
        reason = ("result_consumer_outcome_unknown" if work["unconfirmed"] else
                  "result_consumer_blocked" if work["blocked"] else "result_no_runnable_consumer")
        return {"state": "blocked", "reason": reason, "outcome": "unknown" if work["unconfirmed"] else None,
                "blocked_consumers": work["blocked"], "budget": budget}
    pending = [job for job in work["jobs"] if job["status"] == "todo" and same_purpose(job)]
    if pending:
        job = pending[0]
        if repairing:
            bound = await jobs.bind_execution_repair_job_on(connection, owner=EXECUTION_OWNER,
                identity=execution_identity(generation, event), data_generation=generation, max_failures=MAX_FAILURES,
                repair_ref=repair_ref, consumer=repair_consumer, job_id=job["id"])
            if not bound.get("bound"):
                raise AppError(bound.get("reason") or "repair_job_binding_failed", status=409)
            cover_wait = await cover_exhausted_normal(job["id"], job["args"],
                wake_key + ":repair:" + signature([repair_ref, repair_consumer]))
            if cover_wait:
                return {**cover_wait, "state": "waiting", "job_id": job["id"], "budget": budget}
        clock = await (await connection.execute("SELECT now() AS observed_at")).fetchone()
        due_at = max((value for value in (job["scheduled_at"], scheduled_at) if value is not None), default=None)
        waiting = bool(due_at and due_at > clock["observed_at"])
        return {"state": "waiting" if waiting else "accepted", "job_id": job["id"],
                "reason": "result_dependency_wait" if waiting else "already_queued",
                "due_at": due_at, "budget": budget}
    doing = [job for job in work["jobs"] if job["status"] == "doing"]
    if doing and not allow_successor:
        return {"state": "waiting", "job_id": doing[0]["id"], "reason": "already_executing", "budget": budget}
    cover_wait = None
    if repairing:
        other_repair = [job for job in work["jobs"] if (job["args"] or {}).get("repair_ref")
                        and not same_purpose(job)]
        if other_repair:
            return {"state": "waiting", "reason": "other_repair_target_pending",
                    "job_id": other_repair[0]["id"], "due_at": other_repair[0]["scheduled_at"], "budget": budget}
    if not repairing and not work["runnable"]:
        scheduled_at = min(item["rbi_retry_at"] for item in work["waiting"])
    key = (initial_key if initial_key and original_id is None else
           "result-wake:" + execution_identity(generation, event) + ":" + str(uuid4()))
    # A permanent legacy key may already identify a failed job. Never accept it
    # as the newly requested wake, even when an Operations attempt is reused.
    payload = {"event": event}
    queueing_lock = wake_key
    if repairing:
        payload.update(repair_ref=repair_ref, repair_consumer=repair_consumer)
        queueing_lock = wake_key + ":repair:" + signature([repair_ref, repair_consumer])
        key = "result-repair:" + execution_identity(generation, event) + ":" + str(uuid4())
    job_id = await jobs.enqueue(connection, JobSpec("conversation.result", key, payload,
        ServiceClass.INTERACTIVE, scheduled_at=scheduled_at,
        subject_id=event["context"]["actor_id"], data_generation=generation),
        queueing_lock=queueing_lock, wake_object_key=wake_key)
    if repairing:
        bound = await jobs.bind_execution_repair_job_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(generation, event), data_generation=generation, max_failures=MAX_FAILURES,
            repair_ref=repair_ref, consumer=repair_consumer, job_id=job_id)
        if not bound.get("bound"):
            raise AppError(bound.get("reason") or "repair_job_binding_failed", status=409)
        cover_wait = await cover_exhausted_normal(job_id, payload, queueing_lock)
    await connection.execute(
        "UPDATE conversation.result_deliveries SET rbi_job_ids=ARRAY("
        "SELECT DISTINCT unnest(rbi_job_ids || ARRAY[%s]::bigint[])),rbi_open_job_ids=ARRAY("
        "SELECT DISTINCT unnest(rbi_open_job_ids || ARRAY[%s]::bigint[])) "
        "WHERE data_generation=%s AND receipt_id=%s", (job_id, job_id, generation, receipt_id))
    if original_id is None:
        await register_on(connection, generation, event, job_id)
    if cover_wait:
        return {**cover_wait, "state": "waiting", "job_id": job_id, "budget": budget}
    return {"state": "waiting" if scheduled_at else "accepted", "job_id": job_id,
            "reason": "result_dependency_wait" if scheduled_at else "scheduled", "due_at": scheduled_at,
            "budget": budget}
