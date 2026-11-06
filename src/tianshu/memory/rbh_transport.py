"""Bounded reconciliation of original Memory transport receipts.

This is an adapter to the existing consolidation recovery path. Runtime owns
all evidence classification and budgets; the cursor below only bounds reads.
"""

from copy import deepcopy
from dataclasses import fields
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from tianshu.models.ledger import submission_digest
from tianshu.models.codec import receipt_from_wire
from tianshu.models.types import ModelReceipt
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire

from . import codec
from .authority import context_record, request_identity
from .consolidation_wait import batch_snapshot
from .model_outcome import interrupted_computation, original_status


FAILED_WAITS = {
    "recovery_budget_exhausted", "recovery_connection_budget_exhausted", "transport_not_sent",
    "recovery_dependency_cooling", "recovery_dependency_probe_in_progress", "recovery_claim_in_progress",
}
INELIGIBLE = {
    "source_unavailable", "access_changed", "delivery_access_changed", "actor_forbidden",
    "conversation_forbidden", "memory_consolidation_episode_changed", "memory_read_set_changed",
    "memory_read_revision_conflict", "claim_revision_conflict", "matter_revision_conflict",
    "memory_record_unavailable", "memory_consolidation_authority_changed", "memory_consolidation_read_changed",
    "model_route_binding_changed", "model_route_revision_conflict", "revision_conflict",
}


def validate_original_receipt(request, receipt):
    """Bind a Models receipt to the existing frozen request identity."""
    if (not isinstance(receipt,ModelReceipt) or receipt.attempt_id != request.attempt_id
            or receipt.intent_id != request.intent_id or receipt.data_generation != request.data_generation
            or receipt.request_digest is not None and receipt.request_digest != submission_digest(request)):
        raise AppError("memory_model_result_identity_conflict",status=409)


async def validate_frozen_attempt_on(conn, attempt_id, recovery, request):
    """Match the domain's frozen request to its existing Runtime reservation."""
    ledger = await (await conn.execute("SELECT lineage_id,version,request_digest FROM runtime.recovery_attempts "
        "WHERE attempt_id=%s",(attempt_id,))).fetchone()
    if not ledger:
        raise AppError("recovery_attempt_not_active",status=409)
    if ledger["version"] != recovery["version"]:
        raise AppError("recovery_version_changed",status=409)
    if (ledger["lineage_id"] != recovery["lineage_id"]
            or ledger["request_digest"] != body_hash(request_identity(request))):
        raise AppError("memory_consolidation_attempt_changed",status=409)


def historical_failed_receipt_matches(request, receipt, facts):
    """Keep historical transport proof distinct from present retry eligibility."""
    if not facts or not isinstance(receipt, ModelReceipt) or receipt.status != "failed":
        return False
    if receipt.cancellation_reason is not None or not receipt.request_digest:
        return False
    finished = receipt.finished_at
    if finished is None or finished.tzinfo is None or finished.utcoffset() is None:
        return False
    return (facts.get("attempt_id") == request.attempt_id == receipt.attempt_id
        and facts.get("intent_id") == request.intent_id == receipt.intent_id
        and facts.get("data_generation") == request.data_generation == receipt.data_generation
        and facts.get("request_digest") == receipt.request_digest == submission_digest(request)
        and facts.get("status") == receipt.status and facts.get("dispatch") == receipt.dispatch
        and facts.get("error_code") == receipt.error_code and facts.get("finished_at") == finished)


def historical_receipt_before_cancel(attempt, current_receipt, request, facts):
    """Use an unchanged previously observed receipt, never strip cancellation from one."""
    if current_receipt.cancellation_reason is None:
        return current_receipt if historical_failed_receipt_matches(request,current_receipt,facts) else None
    saved = attempt.get("first_failure")
    if not isinstance(saved,dict):
        return None
    allowed = {item.name for item in fields(ModelReceipt)}
    try:
        original = receipt_from_wire({key:value for key,value in saved.items() if key in allowed})
    except (TypeError,ValueError,KeyError):
        return None
    evidence_fields = ("route_id","route_revision","provider_id","model","protocol","quota_group",
                       "queued_at","started_at","http_timing","diagnostic","first_output_ms",
                       "provider_handle","wire_digest","route_selection","stream_coverage")
    if any(getattr(original,key) != getattr(current_receipt,key) for key in evidence_fields):
        return None
    return original if historical_failed_receipt_matches(request,original,facts) else None


async def _checked(consolidation, snapshot):
    """Rotate an ineligible batch without changing its recovery state."""
    async with consolidation.database.transaction() as conn:
        await consolidation._assert_batch_generation_on(conn,snapshot)
        current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
            (snapshot["batch_id"],))).fetchone()
        if current and batch_snapshot(current) == batch_snapshot(snapshot):
            scan = deepcopy(current["manifest"].get("rbh_transport_scan") or {})
            scan["checked_at"] = wire(datetime.now(UTC))
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=jsonb_set(manifest,"
                "'{rbh_transport_scan}',%s,true) WHERE batch_id=%s", (Jsonb(scan),current["batch_id"]))


async def _inspect_one(consolidation, snapshot):
    from .consolidation import context_from_bound

    manifest = snapshot["manifest"]
    recovery = manifest["recovery"]
    scan = deepcopy(manifest.get("rbh_transport_scan") or {})
    latest_step = snapshot["step_ids"][-1]
    if scan.get("latest_step") != latest_step or scan.get("version") != recovery["version"]:
        scan = {"latest_step":latest_step, "version":recovery["version"]}
    next_number = scan.get("next_number")
    if type(next_number) is not int or next_number < 1:
        next_number = 1

    async with consolidation.database.transaction(read_only=True) as conn:
        attempt = await (await conn.execute("SELECT * FROM memory.consolidation_attempts "
            "WHERE batch_id=%s AND number>=%s AND recovery_policy_state='failed' "
            "ORDER BY number LIMIT 1", (snapshot["batch_id"],next_number))).fetchone()
        finalizing = attempt is None
        if finalizing:
            attempt = await (await conn.execute("SELECT * FROM memory.consolidation_attempts "
                "WHERE batch_id=%s AND step_id=%s", (snapshot["batch_id"],latest_step))).fetchone()
    if not attempt:
        raise AppError("memory_consolidation_attempt_missing",status=409)
    if attempt["step_id"] not in snapshot["step_ids"] or attempt.get("recovery_lineage_id") != recovery["lineage_id"]:
        raise AppError("memory_consolidation_attempt_changed",status=409)

    recorded, request = await consolidation.steps.recorded(attempt["step_id"])
    receipt = await original_status(consolidation.steps.models,request)
    if receipt is not None:
        validate_original_receipt(request,receipt)
    context = context_from_bound(manifest["authority_bound"])
    result = {"batch_id":snapshot["batch_id"],"attempt_id":attempt["model_attempt_id"]}

    async with consolidation.database.transaction() as conn:
        if not await consolidation.sources.validate_persistence(conn,context,codec.refs(snapshot["dependency_refs"])):
            raise AppError("source_unavailable",status=403)
        await consolidation.identity.check_context(context,connection=conn)
        state = await consolidation._lock_owned_partition(conn,snapshot["partition_id"],None)
        current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
            (snapshot["batch_id"],))).fetchone()
        if not state or not current or batch_snapshot(current) != batch_snapshot(snapshot):
            return {**result,"reason":"memory_batch_changed"},False,True
        if current["context_fingerprint"] != body_hash(context_record(context)):
            raise AppError("memory_consolidation_authority_changed",status=409)
        fresh_attempt = await (await conn.execute("SELECT * FROM memory.consolidation_attempts "
            "WHERE batch_id=%s AND step_id=%s FOR UPDATE",(current["batch_id"],attempt["step_id"]))).fetchone()
        if not fresh_attempt or any(fresh_attempt.get(key) != attempt.get(key) for key in
                ("number","model_attempt_id","recovery_lineage_id","recovery_policy_state")):
            return {**result,"reason":"memory_batch_changed"},False,True
        if request.attempt_id != attempt["model_attempt_id"] or request.intent_id != attempt["step_id"]:
            raise AppError("memory_consolidation_attempt_changed",status=409)
        step = await (await conn.execute("SELECT request,state FROM memory.model_steps WHERE step_id=%s FOR UPDATE",
            (attempt["step_id"],))).fetchone()
        if not step or step["state"] == "source_revoked" or step["request"] != recorded["request"]:
            raise AppError("memory_consolidation_attempt_changed",status=409)
        await consolidation._validate_submission_on(conn,current,context,attempt["step_id"])
        await consolidation._assert_batch_generation_on(conn,current)
        from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
        from .rbi_outcome import lock_original_recovery_facts_on
        eligibility = None
        historical_failure = False
        proof_receipt = receipt
        if receipt is not None and (receipt.status in {"failed", "unknown"} or interrupted_computation(receipt)):
            eligibility, facts = await lock_original_recovery_facts_on(conn,request,receipt)
            if receipt.status == "failed" and eligibility == "recovery_model_cancelled":
                proof_receipt = historical_receipt_before_cancel(fresh_attempt,receipt,request,facts)
                historical_failure = proof_receipt is not None
            if eligibility and not historical_failure:
                if eligibility == "recovery_model_receipt_mismatch":
                    raise AppError(eligibility,status=409)
                scan["checked_at"] = wire(datetime.now(UTC))
                if eligibility == "recovery_model_cancelled":
                    if attempt["step_id"] == latest_step:
                        scan["completed_latest_step"] = latest_step
                        scan["completed_version"] = recovery["version"]
                        if current["status"] == "quarantined":
                            blocked = {"reason":eligibility,"version":recovery["version"]}
                            await conn.execute("UPDATE memory.consolidation_batches SET "
                                "manifest=jsonb_set(manifest,'{recovery_budget_blocked}',%s,true),retry_due_at=NULL "
                                "WHERE batch_id=%s",(Jsonb(blocked),current["batch_id"]))
                    elif not finalizing:
                        scan["next_number"] = attempt["number"]+1
                await conn.execute("UPDATE memory.consolidation_batches SET manifest=jsonb_set(manifest,"
                    "'{rbh_transport_scan}',%s,true) WHERE batch_id=%s",(Jsonb(scan),current["batch_id"]))
                return {**result,"reason":eligibility},False,True
        else:
            await lock_recovery_facts_on(conn,request.attempt_id)
        root_id = manifest.get("reorganization",{}).get("root_batch_id") or current["batch_id"]
        root = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
            (root_id,))).fetchone()
        lineage = await (await conn.execute("SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
            (recovery["lineage_id"],))).fetchone()
        if (not root or not lineage or lineage["owner"] != recovery["owner"] or lineage["owner"] != "memory"
                or lineage["domain"] != "memory" or lineage["subject_id"] != context.actor_id
                or lineage["current_version"] != recovery["version"]
                or lineage["data_generation"] != manifest["data_generation"]
                or lineage["material_digest"] != body_hash(root["manifest"].get("frozen"))):
            raise AppError("memory_recovery_lineage_changed",status=409)
        if lineage["paused"] or lineage["cancelled"] or lineage.get("source_withdrawn"):
            return {**result,"reason":"memory_recovery_not_available"},False,True
        await validate_frozen_attempt_on(conn,attempt["model_attempt_id"],recovery,step["request"])

        scan["checked_at"] = wire(datetime.now(UTC))
        if receipt is None or receipt.status != "failed":
            reason = "original_unavailable" if receipt is None else "original_"+receipt.status
            # An unknown original stays observable. Never turn absence, success,
            # cancellation or a changed status into permission for a successor.
            ineligible, scheduled = True, False
        elif finalizing and historical_failure:
            # Preserve independently proven historical not-sent evidence, but
            # a later explicit cancellation cannot authorize this latest retry.
            await consolidation.memory.recovery_policy.note_not_sent_on(conn,attempt["model_attempt_id"],
                owner=recovery["owner"],expected_version=recovery["version"],model_receipt=proof_receipt)
            if current["status"] == "quarantined":
                blocked = {"reason":"recovery_model_cancelled","version":recovery["version"]}
                await conn.execute("UPDATE memory.consolidation_batches SET "
                    "manifest=jsonb_set(manifest,'{recovery_budget_blocked}',%s,true),retry_due_at=NULL "
                    "WHERE batch_id=%s",(Jsonb(blocked),current["batch_id"]))
            scan["completed_latest_step"] = latest_step
            scan["completed_version"] = recovery["version"]
            reason, ineligible, scheduled = "recovery_model_cancelled", True, False
        elif finalizing:
            policy = consolidation.memory.recovery_policy
            # Reconcile the latest receipt again after older credits: Runtime
            # alone may restore its durable due and decide the current budget.
            budget = await policy.note_not_sent_on(conn,attempt["model_attempt_id"],
                owner=recovery["owner"],expected_version=recovery["version"],model_receipt=receipt)
            if attempt.get("recovery_policy_state") != "failed" or not budget["connection_attempts"]:
                reason, ineligible, scheduled = "not_confirmed_not_sent", True, False
            elif not budget["can_retry"]:
                reason, ineligible, scheduled = budget["reason"], True, False
                if current["status"] == "quarantined":
                    blocked = {"reason":reason,"version":recovery["version"]}
                    await conn.execute("UPDATE memory.consolidation_batches SET "
                        "manifest=jsonb_set(manifest,'{recovery_budget_blocked}',%s,true),retry_due_at=NULL "
                        "WHERE batch_id=%s",(Jsonb(blocked),current["batch_id"]))
            elif budget.get("retry_at") is None:
                # A budget credit is not permission to invent a due time.
                reason, ineligible, scheduled = "recovery_not_due", True, False
            else:
                due = max(value for value in (datetime.now(UTC),current.get("retry_due_at"),budget.get("retry_at"))
                          if value is not None)
                if current["status"] == "failed":
                    scheduled = await consolidation.waits.reopen_failed_on(conn,current,context,
                        "rbh:"+attempt["model_attempt_id"],transport=True,due=due)
                else:
                    resumed = deepcopy(current["manifest"])
                    resumed.pop("recovery_budget_blocked",None)
                    resumed.pop("rbh_claim_budget",None)
                    if resumed.get("local_wait"):
                        resumed["local_wait"]["retry_at"] = wire(due)
                    await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,"
                        "retry_due_at=%s WHERE batch_id=%s",(Jsonb(resumed),due,current["batch_id"]))
                    await consolidation.retries.arm_on(conn,current["partition_id"])
                    scheduled = True
                reason, ineligible = ("scheduled" if scheduled else "memory_original_not_recoverable"), not scheduled
            scan["completed_latest_step"] = latest_step
            scan["completed_version"] = recovery["version"]
        else:
            classified = await consolidation.memory.recovery_policy.note_not_sent_on(conn,attempt["model_attempt_id"],
                owner=recovery["owner"],expected_version=recovery["version"],model_receipt=proof_receipt)
            reason = classified["reason"]
            if historical_failure:
                result["observed_attempt_eligibility"] = "recovery_model_cancelled"
                if attempt["step_id"] == latest_step and current["status"] == "quarantined":
                    blocked = {"reason":"recovery_model_cancelled","version":recovery["version"]}
                    await conn.execute("UPDATE memory.consolidation_batches SET "
                        "manifest=jsonb_set(manifest,'{recovery_budget_blocked}',%s,true),retry_due_at=NULL "
                        "WHERE batch_id=%s",(Jsonb(blocked),current["batch_id"]))
            scan["next_number"] = attempt["number"]+1
            scheduled, ineligible = False, reason != "confirmed_not_sent"
        await conn.execute("UPDATE memory.consolidation_batches SET manifest=jsonb_set(manifest,"
            "'{rbh_transport_scan}',%s,true) WHERE batch_id=%s", (Jsonb(scan),current["batch_id"]))
    return {**result,"reason":reason},scheduled,ineligible


async def reconcile_transport_waits(consolidation, *, limit=32):
    if type(limit) is not int or not 1 <= limit <= 128:
        raise AppError("invalid_memory_recovery_candidate_limit")
    if consolidation.memory.recovery_policy is None or consolidation.steps is None:
        raise AppError("memory_recovery_policy_unavailable",status=503)
    async with consolidation.database.transaction(read_only=True) as conn:
        candidates = await (await conn.execute("SELECT b.* FROM memory.consolidation_batches b "
            "JOIN runtime.recovery_lineages l ON l.lineage_id=b.manifest->'recovery'->>'lineage_id' "
            "WHERE (b.status='quarantined' OR b.status='failed' AND b.error_code=ANY(%s)) "
            "AND cardinality(b.step_ids)>0 AND b.selected_step_id IS NULL "
            "AND NOT (b.manifest ? 'remote_wait_unresolved' OR b.manifest ? 'original_observation_only') "
            "AND b.manifest->>'data_generation'=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
            "AND l.data_generation=b.manifest->>'data_generation' AND l.owner='memory' "
            "AND l.current_version=b.manifest->'recovery'->>'version' "
            "AND NOT l.paused AND NOT l.cancelled AND NOT l.source_withdrawn "
            "AND (COALESCE(b.manifest->'rbh_transport_scan'->>'completed_latest_step','') "
            "<> b.step_ids[cardinality(b.step_ids)] OR COALESCE(b.manifest->'rbh_transport_scan'->>'completed_version','') "
            "<> b.manifest->'recovery'->>'version') "
            "AND EXISTS (SELECT 1 FROM memory.consolidation_attempts a WHERE a.batch_id=b.batch_id "
            "AND a.recovery_policy_state='failed') "
            "ORDER BY COALESCE(b.manifest->'rbh_transport_scan'->>'checked_at',''),b.batch_id LIMIT %s",
            (list(FAILED_WAITS),limit))).fetchall()
    result = {"inspected":0,"scheduled":0,"ineligible":0,"changes":[]}
    for batch in candidates:
        result["inspected"] += 1
        try:
            change,scheduled,ineligible = await _inspect_one(consolidation,batch)
        except AppError as error:
            if error.code not in INELIGIBLE:
                raise
            await _checked(consolidation,batch)
            change,scheduled,ineligible = {"batch_id":batch["batch_id"],"reason":error.code},False,True
        result["scheduled"] += int(scheduled)
        result["ineligible"] += int(ineligible)
        result["changes"].append(change)
    return result
