"""Trusted repair registrations and bounded grants over the existing ledger."""

from .errors import AppError
from . import rbi_execution_budget as ledger


def _text(value, name):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid_execution_repair_" + name)
    return value


def _selector(repair_ref, consumer):
    if (repair_ref is None) != (consumer is None):
        raise ValueError("execution_repair_selector_required")
    if repair_ref is not None:
        _text(repair_ref, "ref")
        _text(consumer, "consumer")


async def register_execution_repair_on(connection, *, owner, repair_ref, repair_revision, defect_key, consumer):
    """Register one verified fix from trusted deployment/recovery code only."""
    for value, name in ((owner, "owner"), (repair_ref, "ref"), (repair_revision, "revision"),
                        (defect_key, "defect"), (consumer, "consumer")):
        _text(value, name)
    inserted = await (await connection.execute(
        "INSERT INTO runtime.rbj_execution_repairs(owner,defect_key,consumer,repair_ref,repair_revision) "
        "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",
        (owner, defect_key, consumer, repair_ref, repair_revision))).fetchone()
    if inserted:
        return {"registered": True, "owner": owner, "defect_key": defect_key, "consumer": consumer,
                "repair_ref": repair_ref, "repair_revision": repair_revision}
    existing = await (await connection.execute(
        "SELECT owner,defect_key,consumer,repair_ref,repair_revision FROM runtime.rbj_execution_repairs "
        "WHERE owner=%s AND consumer=%s AND (defect_key=%s OR repair_ref=%s) FOR UPDATE",
        (owner, consumer, defect_key, repair_ref))).fetchall()
    if (len(existing) != 1 or existing[0] != {"owner": owner, "defect_key": defect_key,
            "consumer": consumer, "repair_ref": repair_ref, "repair_revision": repair_revision}):
        raise AppError("execution_repair_registration_conflict", status=409)
    return {"registered": False, **existing[0]}


async def repair_snapshot_on(connection, *, data_generation, identity_digest, owner, normal, repair_ref, consumer):
    _selector(repair_ref, consumer)
    if repair_ref is None:
        return None
    base = {"eligible": False, "reason": "repair_not_authorized", "remaining": None,
            "ref": repair_ref, "consumer": consumer, "retry_at": None}
    if not normal["history_complete"]:
        return {**base, "reason": "history_unknown"}
    grant = await (await connection.execute(
        "SELECT * FROM runtime.rbj_execution_repair_grants WHERE data_generation=%s AND identity_digest=%s "
        "AND owner=%s AND repair_ref=%s AND consumer=%s",
        (data_generation, identity_digest, owner, repair_ref, consumer))).fetchone()
    if not grant:
        return base
    facts = await (await connection.execute(
        "SELECT count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='failed') AS failures,"
        "count(*) FILTER (WHERE COALESCE(resolved_outcome,outcome)='completed') AS completed "
        "FROM runtime.rbi_execution_facts WHERE data_generation=%s AND identity_digest=%s "
        "AND repair_grant_id=%s AND job_id>0", (data_generation, identity_digest, grant["id"]))).fetchone()
    wait = await (await connection.execute(
        "SELECT max(COALESCE(resolved_retry_at,retry_at)) AS retry_at FROM runtime.rbi_execution_facts "
        "WHERE data_generation=%s AND identity_digest=%s AND job_id>0 "
        "AND COALESCE(resolved_outcome,outcome)='wait' AND COALESCE(resolved_retry_at,retry_at)>now()",
        (data_generation, identity_digest))).fetchone()
    remaining = 0 if grant["closed_at"] or facts["failures"] or facts["completed"] else 1
    reason = ("execution_outcome_unknown" if normal["unknown_held"] else
              "execution_in_progress" if normal["active_executions"] or normal["native_inflight_blocking"] else
              "repair_closed" if grant["closed_at"] else
              "repair_failure_budget_exhausted" if facts["failures"] else
              "repair_already_completed" if facts["completed"] else
              "repair_failure_history_changed" if grant["failure_digest"] != normal["failure_digest"] else
              "repair_normal_budget_available" if normal["failed_executions"] < normal["max_failures"] else
              "execution_wait" if wait["retry_at"] is not None else None)
    return {"eligible": reason is None, "reason": reason, "remaining": remaining,
            "ref": repair_ref, "consumer": consumer, "retry_at": wait["retry_at"] if reason == "execution_wait" else None,
            "grant_id": grant["id"], "defect_key": grant["defect_key"],
            "failure_digest": grant["failure_digest"], "failed_executions": facts["failures"]}


async def authorize_execution_repair_on(connection, *, owner, identity, data_generation, max_failures,
                                        repair_ref, repair_revision, defect_key, failure_digest, consumer):
    for value, name in ((repair_ref, "ref"), (repair_revision, "revision"), (defect_key, "defect"),
                        (failure_digest, "failure_digest"), (consumer, "consumer")):
        _text(value, name)
    digest = ledger._inputs(owner, identity, data_generation, max_failures)
    await ledger._generation_on(connection, data_generation)
    await ledger._lock_on(connection, data_generation, digest)
    registration = await (await connection.execute(
        "SELECT * FROM runtime.rbj_execution_repairs WHERE owner=%s AND defect_key=%s AND consumer=%s FOR SHARE",
        (owner, defect_key, consumer))).fetchone()
    if (not registration or registration["repair_ref"] != repair_ref
            or registration["repair_revision"] != repair_revision):
        return {"eligible": False, "reason": "repair_not_registered", "ref": repair_ref, "consumer": consumer}
    binding = await ledger._binding_on(connection, data_generation, digest, owner, max_failures)
    normal = await ledger._snapshot_on(connection, data_generation, digest, binding)
    base = {"eligible": False, "ref": repair_ref, "consumer": consumer, "failure_digest": normal["failure_digest"]}
    if not normal["history_complete"]:
        return {**base, "reason": "history_unknown"}
    if normal["unknown_held"] or normal["active_executions"] or normal["native_inflight_blocking"]:
        return {**base, "reason": "execution_outcome_unknown" if normal["unknown_held"] else "execution_in_progress"}
    if normal["failure_digest"] != failure_digest:
        return {**base, "reason": "repair_failure_history_changed"}
    if normal["failed_executions"] < max_failures:
        return {**base, "reason": "repair_normal_budget_available"}
    other = await (await connection.execute(
        "SELECT g.id FROM runtime.rbj_execution_repair_grants g WHERE g.data_generation=%s "
        "AND g.identity_digest=%s AND g.closed_at IS NULL AND (g.consumer<>%s OR g.defect_key<>%s) AND NOT EXISTS "
        "(SELECT 1 FROM runtime.rbi_execution_facts f WHERE f.repair_grant_id=g.id "
        "AND COALESCE(f.resolved_outcome,f.outcome) IN ('failed','completed')) LIMIT 1",
        (data_generation, digest, consumer, defect_key))).fetchone()
    if other:
        return {**base, "reason": "repair_other_target_active"}
    await connection.execute(
        "INSERT INTO runtime.rbj_execution_repair_grants(data_generation,identity_digest,owner,defect_key,consumer,"
        "repair_ref,repair_revision,failure_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (data_generation, digest, owner, defect_key, consumer, repair_ref, repair_revision, failure_digest))
    grant = await (await connection.execute(
        "SELECT * FROM runtime.rbj_execution_repair_grants WHERE data_generation=%s AND identity_digest=%s "
        "AND defect_key=%s AND consumer=%s FOR UPDATE", (data_generation, digest, defect_key, consumer))).fetchone()
    if (not grant or grant["owner"] != owner or grant["repair_ref"] != repair_ref
            or grant["repair_revision"] != repair_revision or grant["failure_digest"] != failure_digest):
        raise AppError("execution_repair_grant_conflict", status=409)
    return await repair_snapshot_on(connection, data_generation=data_generation, identity_digest=digest,
                                    owner=owner, normal=normal, repair_ref=repair_ref, consumer=consumer)


async def bind_execution_repair_job_on(connection, *, owner, identity, data_generation, max_failures,
                                       repair_ref, consumer, job_id):
    """Bind a new native job to its already authorized grant before commit."""
    _selector(repair_ref, consumer)
    if repair_ref is None:
        raise ValueError("execution_repair_selector_required")
    ledger._physical(job_id, 0)
    digest = ledger._inputs(owner, identity, data_generation, max_failures)
    await ledger._generation_on(connection, data_generation)
    await ledger._lock_on(connection, data_generation, digest)
    grant = await (await connection.execute(
        "SELECT * FROM runtime.rbj_execution_repair_grants WHERE data_generation=%s AND identity_digest=%s "
        "AND owner=%s AND repair_ref=%s AND consumer=%s", (data_generation, digest, owner, repair_ref, consumer))).fetchone()
    if not grant:
        return {"bound": False, "reason": "repair_not_authorized", "job_id": job_id}
    existing = await (await connection.execute(
        "SELECT identity_digest,grant_id FROM runtime.rbj_execution_repair_jobs WHERE data_generation=%s AND job_id=%s",
        (data_generation, job_id))).fetchone()
    if existing:
        if existing["identity_digest"] != digest or existing["grant_id"] != grant["id"]:
            raise AppError("execution_attempt_identity_conflict", status=409)
        if grant["closed_at"] is not None:
            return {"bound": False, "reason": "repair_closed", "job_id": job_id}
        binding = await ledger._binding_on(connection, data_generation, digest, owner, max_failures)
        normal = await ledger._snapshot_on(connection, data_generation, digest, binding)
        if not normal["history_complete"]:
            return {"bound": False, "reason": "history_unknown", "job_id": job_id}
        if normal["failure_digest"] != grant["failure_digest"]:
            return {"bound": False, "reason": "repair_failure_history_changed", "job_id": job_id}
        if normal["native_inflight_blocking"]:
            return {"bound": False, "reason": "execution_in_progress", "job_id": job_id}
        return {"bound": True, "reason": "already_bound", "job_id": job_id, "grant_id": grant["id"]}
    binding = await ledger._binding_on(connection, data_generation, digest, owner, max_failures)
    normal = await ledger._snapshot_on(connection, data_generation, digest, binding)
    repair = await repair_snapshot_on(connection, data_generation=data_generation, identity_digest=digest,
        owner=owner, normal=normal, repair_ref=repair_ref, consumer=consumer)
    waiting = repair["reason"] == "execution_wait" and repair["retry_at"] is not None
    if not repair["eligible"] and not waiting:
        return {"bound": False, "reason": repair["reason"], "job_id": job_id, "retry_at": repair["retry_at"]}
    active_job = await (await connection.execute(
        "SELECT b.job_id FROM runtime.rbj_execution_repair_jobs b JOIN procrastinate_jobs j ON j.id=b.job_id "
        "WHERE b.data_generation=%s AND b.grant_id=%s AND b.job_id<>%s "
        "AND j.status IN ('todo','doing') AND NOT EXISTS (SELECT 1 FROM runtime.job_keys k "
        "WHERE k.job_id=b.job_id AND k.wake_covered_by IS NOT NULL) LIMIT 1",
        (data_generation, grant["id"], job_id))).fetchone()
    if active_job:
        return {"bound": False, "reason": "repair_job_active", "job_id": active_job["job_id"]}
    native = await (await connection.execute(
        "SELECT j.task_name,j.status,j.args,j.scheduled_at FROM procrastinate_jobs j WHERE j.id=%s AND EXISTS "
        "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.task=j.task_name "
        "AND k.data_generation=%s AND k.cancelled_at IS NULL) FOR SHARE OF j", (job_id, data_generation))).fetchone()
    if (not native or native["task_name"] != owner or native["status"] != "todo"
            or (native["args"] or {}).get("repair_ref") != repair_ref
            or (native["args"] or {}).get("repair_consumer") != consumer):
        raise AppError("execution_repair_job_mismatch", status=409)
    if waiting and (native["scheduled_at"] is None or native["scheduled_at"] < repair["retry_at"]):
        return {"bound": False, "reason": "execution_wait", "job_id": job_id, "retry_at": repair["retry_at"]}
    prior = await (await connection.execute(
        "SELECT 1 FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s LIMIT 1",
        (data_generation, job_id))).fetchone()
    if prior:
        raise AppError("execution_repair_job_already_started", status=409)
    inserted = await (await connection.execute(
        "INSERT INTO runtime.rbj_execution_repair_jobs(data_generation,job_id,identity_digest,grant_id) "
        "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING job_id",
        (data_generation, job_id, digest, grant["id"]))).fetchone()
    if not inserted:
        raise AppError("execution_attempt_identity_conflict", status=409)
    return {"bound": True, "reason": "bound", "job_id": job_id, "grant_id": grant["id"],
            "retry_at": repair["retry_at"] if waiting else None}


async def close_execution_repair_on(connection, *, owner, identity, data_generation, max_failures,
                                    repair_ref, consumer, observation_ref, current_job=None):
    """Close a grant using domain-owned proof without creating an attempt."""
    _selector(repair_ref, consumer)
    if repair_ref is None:
        raise ValueError("execution_repair_selector_required")
    _text(observation_ref, "observation")
    digest = ledger._inputs(owner, identity, data_generation, max_failures)
    await ledger._generation_on(connection, data_generation)
    await ledger._lock_on(connection, data_generation, digest)
    grant = await (await connection.execute(
        "SELECT g.* FROM runtime.rbj_execution_repair_grants g JOIN runtime.rbj_execution_repairs r "
        "ON r.owner=g.owner AND r.defect_key=g.defect_key AND r.consumer=g.consumer "
        "AND r.repair_ref=g.repair_ref AND r.repair_revision=g.repair_revision "
        "WHERE g.data_generation=%s AND g.identity_digest=%s AND g.owner=%s "
        "AND g.repair_ref=%s AND g.consumer=%s FOR UPDATE OF g",
        (data_generation, digest, owner, repair_ref, consumer))).fetchone()
    base = {"closed": False, "ref": repair_ref, "consumer": consumer}
    if not grant:
        return {**base, "reason": "repair_not_authorized"}
    binding = await ledger._binding_on(connection, data_generation, digest, owner, max_failures)
    normal = await ledger._snapshot_on(connection, data_generation, digest, binding, current_job=current_job)
    if not normal["history_complete"]:
        return {**base, "reason": "history_unknown"}
    if normal["unknown_held"] or normal["active_executions"] or normal["native_inflight_blocking"]:
        return {**base, "reason": "execution_outcome_unknown" if normal["unknown_held"] else "execution_in_progress"}
    if grant["closed_at"] is not None:
        # A successful finish closes using its physical execution reference;
        # the domain proof can only be checked afterwards. Bind that first
        # trusted proof to the exact completed fact while preserving the
        # original mechanical close reference. Manual/proof-based closes and
        # later different proofs retain the existing conflict semantics.
        completed = await (await connection.execute(
            "SELECT job_id,native_attempt,observation_ref FROM runtime.rbi_execution_facts "
            "WHERE data_generation=%s AND identity_digest=%s AND owner=%s AND repair_grant_id=%s "
            "AND purpose='repair' AND outcome='completed' AND resolved_outcome IS NULL "
            "AND %s=('execution:' || job_id::text || ':' || native_attempt::text || ':completed') "
            "FOR UPDATE", (data_generation, digest, owner, grant["id"], grant["close_observation_ref"]))).fetchone()
        if completed:
            if completed["observation_ref"] is not None and completed["observation_ref"] != observation_ref:
                raise AppError("execution_repair_close_conflict", status=409)
            if completed["observation_ref"] is None:
                await connection.execute(
                    "UPDATE runtime.rbi_execution_facts SET observation_ref=%s WHERE data_generation=%s "
                    "AND identity_digest=%s AND job_id=%s AND native_attempt=%s",
                    (observation_ref, data_generation, digest, completed["job_id"], completed["native_attempt"]))
        elif grant["close_observation_ref"] != observation_ref:
            raise AppError("execution_repair_close_conflict", status=409)
        return {**base, "closed": True, "reason": "already_closed"}
    await connection.execute("UPDATE runtime.rbj_execution_repair_grants SET closed_at=now(),close_observation_ref=%s "
                             "WHERE id=%s", (observation_ref, grant["id"]))
    return {**base, "closed": True, "reason": "closed"}


async def record_repair_preflight_stop_on(connection, *, owner, identity, data_generation, repair_ref,
                                          consumer, job_id, native_attempt, reason, observation_ref):
    """Record a trusted preflight stop before this physical attempt can begin."""
    _selector(repair_ref, consumer)
    if repair_ref is None or reason not in {"closed", "blocked", "unknown"}:
        raise ValueError("invalid_execution_repair_preflight_stop")
    _text(observation_ref, "observation")
    ledger._physical(job_id, native_attempt)
    from .jobs import current_job_execution
    if current_job_execution() != {"job_id": job_id, "native_attempt": native_attempt,
                                   "data_generation": data_generation}:
        raise AppError("execution_attempt_changed", status=409)
    digest = ledger._inputs(owner, identity, data_generation)
    await ledger._generation_on(connection, data_generation)
    await ledger._lock_on(connection, data_generation, digest)
    grant = await (await connection.execute(
        "SELECT g.id,g.closed_at FROM runtime.rbj_execution_repair_grants g "
        "JOIN runtime.rbj_execution_repairs r ON r.owner=g.owner AND r.defect_key=g.defect_key "
        "AND r.consumer=g.consumer AND r.repair_ref=g.repair_ref AND r.repair_revision=g.repair_revision "
        "JOIN runtime.rbj_execution_repair_jobs b ON b.grant_id=g.id AND b.data_generation=g.data_generation "
        "AND b.identity_digest=g.identity_digest WHERE g.data_generation=%s AND g.identity_digest=%s "
        "AND g.owner=%s AND g.repair_ref=%s AND g.consumer=%s AND b.job_id=%s FOR SHARE OF g",
        (data_generation, digest, owner, repair_ref, consumer, job_id))).fetchone()
    if not grant:
        raise AppError("execution_repair_job_unbound", status=409)
    if reason == "closed" and grant["closed_at"] is None:
        raise AppError("execution_repair_not_closed", status=409)
    if not await ledger._binding_on(connection, data_generation, digest, owner):
        raise AppError("execution_history_unknown", status=409)
    await ledger._current_job_on(connection, data_generation, job_id, native_attempt)
    native = await (await connection.execute("SELECT task_name FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
    if not native or native["task_name"] != owner:
        raise AppError("execution_repair_job_mismatch", status=409)
    fact = await (await connection.execute(
        "SELECT 1 FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (data_generation, job_id, native_attempt))).fetchone()
    if fact:
        raise AppError("execution_repair_already_started", status=409)
    await connection.execute(
        "INSERT INTO runtime.rbj_wake_exits(data_generation,job_id,native_attempt,kind,repair_grant_id,reason,observation_ref) "
        "VALUES(%s,%s,%s,'repair_preflight',%s,%s,%s) ON CONFLICT DO NOTHING",
        (data_generation, job_id, native_attempt, grant["id"], reason, observation_ref))
    saved = await (await connection.execute(
        "SELECT kind,repair_grant_id,reason,observation_ref FROM runtime.rbj_wake_exits "
        "WHERE data_generation=%s AND job_id=%s AND native_attempt=%s", (data_generation, job_id, native_attempt))).fetchone()
    if (not saved or saved["kind"] != "repair_preflight" or saved["repair_grant_id"] != grant["id"]
            or saved["reason"] != reason or saved["observation_ref"] != observation_ref):
        raise AppError("execution_repair_preflight_conflict", status=409)
    return {"state": "preflight_stopped", "reason": reason, "job_id": job_id, "native_attempt": native_attempt}
