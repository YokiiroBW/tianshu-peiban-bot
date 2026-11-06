"""Reconcile one fresh Models observation under Memory's batch and Runtime fences."""

from copy import deepcopy

from psycopg.types.json import Jsonb

from tianshu.models.retry import transient_model_failure
from tianshu.models.ledger import submission_digest
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import FailureCategory, RecoveryWait, classify_failure

from . import codec
from .authority import context_record
from .model_outcome import confirmed_computation_unknown, interrupted_computation
from .rbh_transport import validate_frozen_attempt_on, validate_original_receipt


def same_head(before, after, step_id):
    """Compare the immutable inputs and selected head observed before the RPC."""
    if not after or not before.get("step_ids") or before["step_ids"][-1] != step_id:
        return False
    fields = ("partition_id", "status", "dependency_refs", "read_set", "context_fingerprint", "step_ids",
              "selected_step_id", "proposal_digest", "retry_due_at", "retry_requested")
    if any(before.get(key) != after.get(key) for key in fields):
        return False
    a, b = before["manifest"], after["manifest"]
    return all(a.get(key) == b.get(key) for key in ("authority_bound", "data_generation", "recovery", "episodes")) and (
        a.get("steps", {}).get(step_id) == b.get("steps", {}).get(step_id))


async def lock_batch_on(consolidation, conn, snapshot, recorded, request, *, owner=None,
                        require_unleased=False):
    """Lock authority, partition, batch, materials and the original head, in that order."""
    from .consolidation import context_from_bound

    context = context_from_bound(snapshot["manifest"]["authority_bound"])
    valid = await consolidation.sources.validate_persistence(conn, context, codec.refs(snapshot["dependency_refs"]))
    if valid:
        try:
            await consolidation.identity.check_context(context, connection=conn)
        except RecoveryWait:
            raise
        except AppError as error:
            if error.status not in {401, 403, 409}:
                raise
            valid = False
    state = await consolidation._lock_owned_partition(conn, snapshot["partition_id"], owner)
    if not state:
        return None, None, "memory_consolidation_batch_changed"
    if require_unleased:
        clock = await (await conn.execute("SELECT now() AS now")).fetchone()
        if state["lease_until"] is not None and state["lease_until"] > clock["now"]:
            return None, None, "memory_consolidation_busy"
    current = await (await conn.execute(
        "SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
        (snapshot["batch_id"],))).fetchone()
    if not same_head(snapshot, current, request.intent_id):
        return None, None, "memory_consolidation_batch_changed"
    if current["context_fingerprint"] != body_hash(context_record(context)):
        return None, None, "memory_consolidation_batch_changed"
    if current.get("selected_step_id") not in {None, request.intent_id}:
        return None, None, "memory_consolidation_batch_changed"
    if (current["status"] in {"prepared", "waiting_model", "ready"}
            and state["active_batch_id"] != current["batch_id"]):
        return None, None, "memory_consolidation_batch_changed"
    step = await (await conn.execute("SELECT request,state FROM memory.model_steps WHERE step_id=%s",
        (request.intent_id,))).fetchone()
    if step and step["state"] == "source_revoked":
        return None, None, "source_unavailable"
    if (not step or step["request"] != recorded["request"]
            or request.data_generation != current["manifest"]["data_generation"]):
        return None, None, "memory_consolidation_batch_changed"
    return current, context, None if valid else "source_unavailable"


async def lock_attempt_on(conn, current, request):
    attempt = await (await conn.execute("SELECT * FROM memory.consolidation_attempts "
        "WHERE batch_id=%s AND step_id=%s FOR UPDATE", (current["batch_id"], request.intent_id))).fetchone()
    latest = await (await conn.execute("SELECT step_id,number FROM memory.consolidation_attempts "
        "WHERE batch_id=%s ORDER BY number DESC LIMIT 1", (current["batch_id"],))).fetchone()
    if (not attempt or not latest or latest["step_id"] != request.intent_id
            or attempt["model_attempt_id"] != request.attempt_id):
        return None
    return attempt


async def lock_observation_on(consolidation, conn, snapshot, recorded, request, *, owner=None,
                              require_unleased=False, expected_attempt=None):
    current, context, block = await lock_batch_on(consolidation, conn, snapshot, recorded, request,
        owner=owner, require_unleased=require_unleased)
    if current is None:
        return None, None, block
    if not block:
        await consolidation._validate_submission_on(conn, current, context, request.intent_id)
    attempt = await lock_attempt_on(conn, current, request)
    if (not attempt or expected_attempt is not None and any(
            attempt.get(key) != expected_attempt.get(key) for key in
            ("number", "step_id", "model_attempt_id", "rejected_error", "recovery_lineage_id"))):
        return None, None, "memory_consolidation_batch_changed"
    await consolidation._assert_batch_generation_on(conn, current)
    return current, attempt, block


async def lock_original_recovery_facts_on(conn, request, receipt):
    """Hold Models facts before Runtime and bind them to Memory's frozen request."""
    from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
    from tianshu.runtime.recovery_policy import validate_model_recovery_facts

    facts = await lock_recovery_facts_on(conn, request.attempt_id)
    try:
        validate_model_recovery_facts(facts, receipt=receipt, expected_status=receipt.status,
                                      data_generation=request.data_generation)
    except AppError as error:
        if error.code in {"recovery_model_receipt_unconfirmed", "recovery_model_cancelled",
                          "recovery_model_receipt_mismatch", "recovery_model_outcome_changed"}:
            return error.code, facts
        raise
    if not receipt.request_digest:
        return "recovery_model_receipt_unconfirmed", facts
    if (receipt.attempt_id != request.attempt_id or receipt.intent_id != request.intent_id
            or receipt.data_generation != request.data_generation
            or receipt.request_digest != submission_digest(request)):
        return "recovery_model_receipt_mismatch", facts
    return None, facts


async def reconcile_locked_on(consolidation, conn, batch, attempt, recorded, request, outcome, *,
                              retry_after=None, authority_block=None):
    """Project exactly the same receipt into Memory and the Runtime attempt ledger."""
    receipt = outcome.receipt
    if receipt is None:
        if authority_block == "source_unavailable":
            await consolidation.cancel_recovery_on(conn, batch["manifest"])
        return {"unconfirmed": True, "authority_block": authority_block}
    if receipt.status in {"failed", "unknown"} or interrupted_computation(receipt):
        reason, _ = await lock_original_recovery_facts_on(conn, request, receipt)
        if reason:
            if authority_block == "source_unavailable":
                await consolidation.cancel_recovery_on(conn, batch["manifest"])
                return {"authority_block": authority_block}
            return {"authority_block": reason}
    validate_original_receipt(request, receipt)
    recovery = batch["manifest"].get("recovery")
    policy = consolidation.memory.recovery_policy
    ledger_status = None
    if recovery and policy:
        if attempt.get("recovery_lineage_id") != recovery["lineage_id"]:
            raise AppError("memory_recovery_lineage_changed", status=409)
        await validate_frozen_attempt_on(conn, request.attempt_id, recovery, recorded["request"])
        lineage = await (await conn.execute("SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
            (recovery["lineage_id"],))).fetchone()
        ledger = await (await conn.execute("SELECT * FROM runtime.recovery_attempts WHERE attempt_id=%s FOR UPDATE",
            (request.attempt_id,))).fetchone()
        if (not lineage or not ledger or lineage["owner"] != recovery["owner"]
                or lineage["current_version"] != recovery["version"] or ledger["version"] != recovery["version"]
                or lineage["data_generation"] != batch["manifest"]["data_generation"]
                or ledger["attempt_number"] != lineage["attempts"]):
            return {"authority_block": "memory_consolidation_batch_changed"}
        root_id = batch["manifest"].get("reorganization", {}).get("root_batch_id") or batch["batch_id"]
        root = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
                                        (root_id,))).fetchone()
        if (lineage["domain"] != "memory" or lineage["owner"] != "memory" or lineage["recovery_kind"] != "pure_computation"
                or lineage["subject_id"] != batch["manifest"]["authority_bound"].get("actor_id")
                or not root or lineage["material_digest"] != body_hash(root["manifest"].get("frozen"))):
            return {"authority_block": "memory_consolidation_batch_changed"}
        if lineage["paused"] or lineage["cancelled"] or lineage["source_withdrawn"]:
            return {"authority_block": "memory_recovery_not_available"}
        ledger_status = ledger["status"]
        status = "cancelled" if outcome.explicit_cancel else outcome.status
        valid_transition = (
            ledger_status == "reserved"
            or ledger_status == "unknown" and status in {"unknown", "failed", "cancelled", "succeeded"}
            or ledger_status == "failed" and status in {"failed", "cancelled"}
            or ledger_status == "succeeded" and status == "succeeded")
        if not valid_transition:
            return {"authority_block": "memory_consolidation_batch_changed"}
        code = "explicit_cancel" if outcome.explicit_cancel else receipt.error_code or "model_outcome_" + status
        if status == "unknown" and confirmed_computation_unknown(receipt):
            await policy.record_computation_unknown_on(conn, request.attempt_id, owner=recovery["owner"],
                expected_version=recovery["version"], model_receipt=receipt, retry_after=retry_after)
            ledger_status = "unknown"
        elif status in {"failed", "cancelled"} and ledger_status in {"reserved", "unknown"}:
            category = classify_failure(code)
            if category == FailureCategory.OTHER and transient_model_failure(code):
                category = FailureCategory.TRANSPORT
            if ledger_status == "unknown":
                await policy.resolve_unknown_on(conn, request.attempt_id, observation_ref="models:" + request.attempt_id,
                    state="failed", code=code, category=category, retry_after=retry_after)
            else:
                await policy.fail_attempt_on(conn, request.attempt_id, code=code, category=category, retry_after=retry_after)
            ledger_status = "failed"
        elif status == "succeeded" and ledger_status in {"reserved", "unknown"}:
            if ledger_status == "unknown":
                await policy.resolve_unknown_on(conn, request.attempt_id, observation_ref="models:" + request.attempt_id,
                    state="succeeded")
            else:
                await policy.succeed_attempt_on(conn, request.attempt_id)
            ledger_status = "succeeded"
    status = "cancelled" if outcome.explicit_cancel else outcome.status
    code = None if status == "succeeded" else receipt.error_code or "model_outcome_" + status
    if outcome.explicit_cancel:
        code = "explicit_cancel"
    policy_state = ("cancelled" if status == "cancelled" and not interrupted_computation(receipt)
                    and ledger_status == "failed" else ledger_status)
    await conn.execute("UPDATE memory.consolidation_attempts SET status=%s,error_code=%s,"
        "recovery_policy_state=COALESCE(%s,recovery_policy_state),updated_at=now() WHERE batch_id=%s AND step_id=%s",
        (status, code, policy_state, batch["batch_id"], request.intent_id))
    history = batch["manifest"].get("rpc_recoveries")
    if history:
        entries = deepcopy(history)
        for entry in reversed(entries):
            if entry["step_id"] == request.intent_id:
                entry["latest_original_observation"] = outcome.record()
                await conn.execute("UPDATE memory.consolidation_batches SET manifest=jsonb_set(manifest,"
                    "'{rpc_recoveries}',%s,true) WHERE batch_id=%s", (Jsonb(entries), batch["batch_id"]))
                batch["manifest"]["rpc_recoveries"] = entries
                break
    if authority_block == "source_unavailable":
        await consolidation.cancel_recovery_on(conn, batch["manifest"])
        return {"authority_block": authority_block}
    result = {"status": status, "policy_state": policy_state}
    if recovery and policy and (status == "failed" or interrupted_computation(receipt)):
        result.update(await policy.note_not_sent_on(conn, request.attempt_id, owner=recovery["owner"],
            expected_version=recovery["version"], model_receipt=receipt))
        if result.get("can_retry") and batch["manifest"].get("rbh_claim_budget"):
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=manifest-'rbh_claim_budget' "
                "WHERE batch_id=%s", (batch["batch_id"],))
            batch["manifest"].pop("rbh_claim_budget", None)
    elif recovery and policy and status == "unknown" and confirmed_computation_unknown(receipt):
        result.update(await policy.computation_budget_on(conn, recovery["lineage_id"],
            owner=recovery["owner"], expected_version=recovery["version"]))
    if (status == "failed" or interrupted_computation(receipt)) and result.get("can_retry") is False and batch["status"] == "quarantined":
        marker = {"reason": result["reason"], "version": recovery["version"]}
        await conn.execute("UPDATE memory.consolidation_batches SET manifest=jsonb_set(manifest,"
            "'{recovery_budget_blocked}',%s,true),retry_due_at=NULL WHERE batch_id=%s", (Jsonb(marker), batch["batch_id"]))
        batch["manifest"]["recovery_budget_blocked"] = marker
    return result
