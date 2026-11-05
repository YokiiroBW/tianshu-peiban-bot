"""Connect Life's existing plan ledger to the shared physical-attempt policy."""
from tianshu.conversation.codec import signature
from tianshu.conversation.generation import attempt_for
from tianshu.runtime.errors import AppError


MAX_ATTEMPTS = 3


async def generation_on(connection, key, *, lock=False):
    if not key:
        return None
    query = "SELECT * FROM life.plan_generations WHERE generation_key=%s"
    if lock:
        query += " FOR UPDATE"
    return await (await connection.execute(query, (key,))).fetchone()


def policy_for(life):
    policy = getattr(life, "recovery_policy", None)
    if policy is None:
        raise AppError("recovery_policy_unavailable", status=503)
    return policy


def finished_plan_unknown(receipt, attempt_id, data_generation):
    from .computation_retry import finished_unknown

    return (finished_unknown(receipt, attempt_id, data_generation)
        and receipt.finished_at.utcoffset() is not None)


async def reserve_on(life, connection, *, context, scene, material, key, frozen, profile, previous,
                     continuation, model_receipt=None):
    policy = policy_for(life)
    if not getattr(frozen, "data_generation", None):
        raise AppError("job_generation_missing", status=409)
    quota = (profile or {}).get("quota_group")
    if not isinstance(quota, str) or not quota:
        raise AppError("planner_recovery_quota_unavailable", status=503)
    dependency = "models:quota:" + quota
    owner = "life.plan:" + signature(scene)
    claim_token = None
    original_state = "none"
    if continuation:
        if not previous or not previous.get("recovery_lineage_id") or previous.get("recovery_owner") != owner:
            raise AppError("planner_recovery_lineage_missing", status=409)
        lineage_id = previous["recovery_lineage_id"]
        state = previous.get("recovery_state")
        if state not in {"failed", "succeeded", "unknown"}:
            return {"state": "waiting"}
        budget = await policy.computation_budget_on(connection,lineage_id,owner=owner,
            expected_version=previous["recovery_version"])
        if not budget["can_retry"]:
            return {"state":"exhausted","reason":budget["reason"]}
        if state == "unknown":
            if not finished_plan_unknown(model_receipt, attempt_for(previous["generation_key"]), frozen.data_generation):
                return {"state": "waiting"}
        current_dependency = previous.get("recovery_current_dependency") or previous.get("recovery_dependency")
        if current_dependency != dependency:
            await policy.update_dependency_on(connection, lineage_id, owner=owner,
                expected_version=previous["recovery_version"],
                expected_dependency=current_dependency, new_dependency=dependency)
            await connection.execute("UPDATE life.plan_generations SET recovery_current_dependency=%s WHERE generation_key=%s",
                (dependency, previous["generation_key"]))
        if state in {"failed", "unknown"}:
            claim = await policy.claim_lineage_due_on(connection, lineage_id, owner=owner, lease_seconds=30)
            if not claim:
                wait = await policy.inspect_wait_on(connection, lineage_id, owner=owner,
                    expected_version=previous["recovery_version"])
                if wait is not None:
                    raise wait
                return {"state": "waiting"}
            if claim["current_version"] != previous["recovery_version"]:
                return {"state": "waiting"}
            original_state = state
        else:
            reason = "need_context" if previous.get("reason") == "planner_output_incomplete" else "schema_correction"
            claim = await policy.claim_continuation_on(connection, lineage_id, owner=owner,
                expected_version=previous["recovery_version"], prior_attempt_id=attempt_for(previous["generation_key"]),
                reason=reason)
            original_state = "succeeded"
        claim_token = claim["claim_token"]
    else:
        if previous and previous.get("recovery_state") in {"reserved", "failed"}:
            await cancel_on(life, connection, previous, material)
        lineage = await policy.register_on(connection, domain="life", subject_id=context.actor_id,
            owner=owner, material_id=key, material_digest=material, dependency=dependency,
            current_version=material, max_attempts=MAX_ATTEMPTS, data_generation=frozen.data_generation,
            recovery_kind="pure_computation")
        lineage_id = lineage["lineage_id"]
    extra = {"model_receipt": model_receipt} if original_state == "unknown" else {}
    attempt = await policy.reserve_attempt_on(connection, lineage_id, owner=owner, expected_version=material,
        attempt_id=frozen.attempt_id, request_digest=signature(frozen), route=frozen.route_id,
        original_state=original_state, claim_token=claim_token, **extra)
    return {"state": "reserved", "lineage_id": lineage_id, "owner": owner, "number": attempt.number,
        "probe_token": attempt.probe_token, "dependency": dependency}


async def assert_applicable_on(life, connection, key):
    row = await generation_on(connection, key, lock=True)
    if not row or not row.get("recovery_lineage_id"):
        raise AppError("planner_recovery_lineage_missing", status=409)
    await policy_for(life).assert_attempt_applicable_on(connection, attempt_for(key),
        owner=row["recovery_owner"], expected_version=row["recovery_version"])


async def cancel_on(life, connection, row, current_version):
    if not row or not row.get("recovery_lineage_id") or row.get("recovery_state") == "cancelled":
        return
    policy = policy_for(life)
    await policy.update_version_on(connection, row["recovery_lineage_id"], owner=row["recovery_owner"],
        expected_version=row["recovery_version"], current_version=current_version, cancelled=True)
    await connection.execute("UPDATE life.plan_generations SET recovery_state='cancelled' WHERE generation_key=%s",
        (row["generation_key"],))


async def succeed_on(life, connection, key):
    row = await generation_on(connection, key, lock=True)
    if not row or not row.get("recovery_lineage_id") or row.get("recovery_state") in {"succeeded", "cancelled"}:
        return
    attempt_id = attempt_for(key)
    if row.get("recovery_state") == "unknown":
        await policy_for(life).resolve_unknown_on(connection, attempt_id,
            observation_ref="models:" + attempt_id + ":succeeded", state="succeeded", probe_token=row["recovery_probe_token"])
    else:
        await policy_for(life).succeed_attempt_on(connection, attempt_id, probe_token=row["recovery_probe_token"])
    await connection.execute("UPDATE life.plan_generations SET recovery_state='succeeded' WHERE generation_key=%s", (key,))


async def unknown_on(life, connection, key, receipt):
    row = await generation_on(connection, key, lock=True)
    if not row or not row.get("recovery_lineage_id") or row.get("recovery_state") not in {"reserved", "unknown"}:
        return
    due = await policy_for(life).record_computation_unknown_on(connection, attempt_for(key),
        owner=row["recovery_owner"], expected_version=row["recovery_version"], model_receipt=receipt,
        retry_after=getattr(receipt, "hold_until", None))
    await connection.execute("UPDATE life.plan_generations SET recovery_state='unknown' WHERE generation_key=%s", (key,))
    return due


async def fail_on(life, connection, key, code, *, retry_after=None):
    row = await generation_on(connection, key, lock=True)
    if not row or not row.get("recovery_lineage_id") or row.get("recovery_state") in {"succeeded", "failed", "cancelled"}:
        return row
    from tianshu.runtime.recovery_policy import FailureCategory, classify_failure
    attempt_id = attempt_for(key)
    category = FailureCategory.STATE_CHANGED if code == "cancelled" else classify_failure(code)
    if row.get("recovery_state") == "unknown":
        if category == FailureCategory.UNKNOWN_EFFECT:
            return row
        await policy_for(life).resolve_unknown_on(connection, attempt_id,
            observation_ref="models:" + attempt_id + ":" + code, state="failed", code=code,
            category=category, retry_after=retry_after, probe_token=row["recovery_probe_token"])
    else:
        await policy_for(life).fail_attempt_on(connection, attempt_id, code=code,
            category=category, retry_after=None if category == FailureCategory.UNKNOWN_EFFECT else retry_after,
            probe_token=row["recovery_probe_token"])
    state = "unknown" if category == FailureCategory.UNKNOWN_EFFECT else "failed"
    await connection.execute("UPDATE life.plan_generations SET recovery_state=%s WHERE generation_key=%s", (state, key))
    return {**row, "recovery_state": state}


async def advance_version_on(life, connection, key, version):
    row = await generation_on(connection, key, lock=True)
    if not row or not row.get("recovery_lineage_id") or row["recovery_version"] == version:
        return
    await policy_for(life).update_version_on(connection, row["recovery_lineage_id"], owner=row["recovery_owner"],
        expected_version=row["recovery_version"], current_version=version)
    await connection.execute("UPDATE life.plan_generations SET recovery_version=%s WHERE generation_key=%s", (version, key))
