"""Reopen only the original batch affected by the RBI local bookkeeping failure."""

from copy import deepcopy
from datetime import timedelta

from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire

from .consolidation_retry import retryable_code
from .model_outcome import confirmed_computation_unknown, interrupted_computation, observe_original
from .rbh_transport import validate_frozen_attempt_on
from .rbi_outcome import lock_attempt_on, lock_batch_on, lock_original_recovery_facts_on, reconcile_locked_on


LOCAL_FAILURE = "recovery_original_not_failed"


def _frozen_for(batch):
    selected = batch.get("selected_step_id") or (batch["step_ids"][-1] if batch["step_ids"] else None)
    return batch["manifest"].get("steps", {}).get(selected) or batch["manifest"].get("frozen") or {}


def _same_fragment(original_batch, episode, applied_batch, other):
    fields = ("episode_id", "revision", "sequence", "before", "after", "material_ids", "complete", "missing", "content_digest")
    if any(episode.get(key) != other.get(key) for key in fields):
        return False
    material_ids = episode.get("material_ids")
    if not isinstance(material_ids, list) or not material_ids:
        return False
    original_frozen, applied_frozen = _frozen_for(original_batch), _frozen_for(applied_batch)
    if body_hash(original_frozen) != body_hash(applied_frozen):
        return False
    original = original_frozen.get("materials", {})
    applied = applied_frozen.get("materials", {})
    if not all(key in original and key in applied and body_hash(original[key]) == body_hash(applied[key])
               for key in material_ids):
        return False
    return any(item.get("episode_id") == episode["episode_id"] and item.get("revision") == episode["revision"]
        and item.get("sequence") == episode["sequence"] and item.get("cursor") == episode["after"]
        and item.get("state") in {"pending", "completed", "completed_with_missing"}
        for item in applied_batch.get("commit_refs", {}).get("coverage", ()))


async def _coverage_on(conn, batch):
    if not batch["manifest"].get("episodes"):
        return "coverage_inconclusive", []
    covered = []
    for episode in batch["manifest"]["episodes"]:
        selector = Jsonb([{"episode_id": episode["episode_id"], "revision": episode["revision"],
                           "sequence": episode["sequence"]}])
        candidates = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE partition_id=%s "
            "AND batch_id<>%s AND status='applied' AND manifest->>'data_generation'=%s AND manifest->'episodes' @> %s "
            "ORDER BY created_at DESC,batch_id LIMIT 513",
            (batch["partition_id"], batch["batch_id"], batch["manifest"]["data_generation"], selector))).fetchall()
        match = None
        for candidate in candidates[:512]:
            for other in candidate["manifest"].get("episodes", ()):
                if _same_fragment(batch, episode, candidate, other):
                    coverage = next(item for item in candidate["commit_refs"]["coverage"]
                        if item.get("episode_id") == episode["episode_id"] and item.get("revision") == episode["revision"]
                        and item.get("sequence") == episode["sequence"] and item.get("cursor") == episode["after"])
                    match = {"batch_id": candidate["batch_id"], "coverage": coverage}
                    break
            if match:
                break
        if match:
            covered.append(match)
        elif len(candidates) > 512:
            return "coverage_inconclusive", covered
    if len(covered) == len(batch["manifest"]["episodes"]):
        return "already_covered", covered
    if covered:
        return "partially_covered", covered
    return None, covered


async def resume_local_failure(recovery, batch_id, *, repair_ref):
    if not isinstance(batch_id, str) or not batch_id or not isinstance(repair_ref, str) or not 1 <= len(repair_ref) <= 200:
        raise ValueError("invalid_memory_local_recovery")
    consolidation = recovery.memory.consolidation
    async with recovery.database.transaction(read_only=True) as conn:
        snapshot = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s", (batch_id,))).fetchone()
    if not snapshot:
        return {"batch_id": batch_id, "queued": False, "reason": "memory_batch_unavailable"}
    if snapshot["status"] == "applied":
        return {"batch_id": batch_id, "queued": False, "reason": "already_applied"}
    if snapshot["status"] == "quarantined" and snapshot["manifest"].get("rbi_local_recovery"):
        return {"batch_id": batch_id, "queued": False, "reason": "already_reopened"}
    if (snapshot["status"] != "failed" or snapshot.get("error_code") != LOCAL_FAILURE or not snapshot["step_ids"]
            or snapshot.get("selected_step_id") or snapshot.get("proposal_digest")
            or snapshot["manifest"].get("remote_wait_unresolved") or snapshot["manifest"].get("original_observation_only")):
        return {"batch_id": batch_id, "queued": False, "reason": "memory_batch_not_local_failure"}
    recorded, request = await consolidation.steps.recorded(snapshot["step_ids"][-1])
    outcome = await observe_original(consolidation.steps.models, request)
    if outcome.receipt is None:
        return {"batch_id": batch_id, "queued": False, "reason": "original_unavailable"}
    if outcome.explicit_cancel:
        return {"batch_id": batch_id, "queued": False, "reason": "recovery_model_cancelled"}
    if outcome.status == "unknown" and not confirmed_computation_unknown(outcome.receipt):
        return {"batch_id": batch_id, "queued": False, "reason": "original_unconfirmed"}
    if outcome.status == "succeeded" and outcome.result is None:
        return {"batch_id": batch_id, "queued": False, "reason": "original_result_unavailable"}
    if outcome.status not in {"queued", "running", "unknown", "failed", "succeeded"} and not interrupted_computation(outcome.receipt):
        return {"batch_id": batch_id, "queued": False, "reason": "original_" + outcome.status}
    if (outcome.status in {"unknown", "failed"} or interrupted_computation(outcome.receipt)) and not retryable_code(outcome.code, outcome.status):
        return {"batch_id": batch_id, "queued": False, "reason": "original_not_retryable"}
    async with recovery.database.transaction() as conn:
        batch, context, block = await lock_batch_on(consolidation, conn, snapshot, recorded, request,
                                                    require_unleased=True)
        if not batch or block:
            return {"batch_id": batch_id, "queued": False, "reason": block}
        if batch.get("error_code") != LOCAL_FAILURE or batch.get("selected_step_id") or batch.get("proposal_digest"):
            return {"batch_id": batch_id, "queued": False, "reason": "memory_batch_changed"}
        rows = []
        for episode in sorted(batch["manifest"]["episodes"], key=lambda value: value["episode_id"]):
            row = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                                          (episode["episode_id"],))).fetchone()
            rows.append((episode, row))
        for episode, row in rows:
            if (not row or not row["active"] or row["revision"] != episode["revision"]
                    or row["sequence"] != episode["sequence"]
                    or episode.get("content_digest") is not None and row["content_hash"] != episode["content_digest"]):
                return {"batch_id": batch_id, "queued": False, "reason": "memory_episode_changed"}
        covered_reason, coverage = await _coverage_on(conn, batch)
        if covered_reason:
            await consolidation._assert_batch_generation_on(conn, batch)
            return {"batch_id": batch_id, "queued": False, "reason": covered_reason, "coverage": coverage}
        for episode, row in rows:
            if row["extraction_cursor"] != episode["before"]:
                return {"batch_id": batch_id, "queued": False, "reason": "memory_episode_changed"}
            if row["extraction_state"] not in {"consolidation_failed", "consolidation_unknown"}:
                return {"batch_id": batch_id, "queued": False, "reason": "memory_episode_not_owned"}
            competing = await (await conn.execute("SELECT batch_id FROM memory.consolidation_batches "
                "WHERE partition_id=%s AND batch_id<>%s AND status IN ('prepared','waiting_model','ready','quarantined') "
                "AND manifest->>'data_generation'=%s AND manifest->'episodes' @> %s LIMIT 1",
                (batch["partition_id"], batch_id, batch["manifest"]["data_generation"], Jsonb([{"episode_id": episode["episode_id"], "revision": episode["revision"],
                            "sequence": episode["sequence"]}])))).fetchone()
            if competing:
                return {"batch_id": batch_id, "queued": False, "reason": "memory_episode_busy",
                        "competing_batch_id": competing["batch_id"]}
        await consolidation._validate_submission_on(conn, batch, context, request.intent_id)
        attempt = await lock_attempt_on(conn, batch, request)
        await consolidation._assert_batch_generation_on(conn, batch)
        lineage_ref = batch["manifest"].get("recovery")
        policy = recovery.memory.recovery_policy
        if not attempt or attempt.get("rejected_error") or not lineage_ref or not policy:
            return {"batch_id": batch_id, "queued": False, "reason": "memory_recovery_lineage_unavailable"}
        if outcome.status in {"unknown", "failed"} or interrupted_computation(outcome.receipt):
            reason, _ = await lock_original_recovery_facts_on(conn, request, outcome.receipt)
            if reason:
                return {"batch_id": batch_id, "queued": False, "reason": reason}
        await validate_frozen_attempt_on(conn, request.attempt_id, lineage_ref, recorded["request"])
        lineage = await (await conn.execute("SELECT *,now() AS db_now FROM runtime.recovery_lineages "
            "WHERE lineage_id=%s FOR UPDATE", (lineage_ref["lineage_id"],))).fetchone()
        latest = await (await conn.execute("SELECT * FROM runtime.recovery_attempts WHERE lineage_id=%s "
            "ORDER BY attempt_number DESC LIMIT 1", (lineage_ref["lineage_id"],))).fetchone()
        if (not lineage or not latest or lineage["owner"] != "memory" or lineage["domain"] != "memory"
                or lineage["recovery_kind"] != "pure_computation"
                or lineage["current_version"] != lineage_ref["version"] or lineage["data_generation"] != request.data_generation
                or lineage["paused"] or lineage["cancelled"] or lineage["source_withdrawn"]
                or latest["attempt_id"] != request.attempt_id or latest["attempt_number"] != lineage["attempts"]):
            return {"batch_id": batch_id, "queued": False, "reason": "memory_recovery_not_available"}
        root_id = batch["manifest"].get("reorganization", {}).get("root_batch_id") or batch_id
        root = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
                                        (root_id,))).fetchone()
        if (not root or lineage["subject_id"] != context.actor_id
                or lineage["material_digest"] != body_hash(root["manifest"].get("frozen"))):
            return {"batch_id": batch_id, "queued": False, "reason": "memory_recovery_material_changed"}
        if lineage["claim_token"] and lineage["claim_expires_at"] and lineage["claim_expires_at"] > lineage["db_now"]:
            return {"batch_id": batch_id, "queued": False, "reason": "memory_recovery_claimed"}
        if outcome.status in {"unknown", "failed"} or interrupted_computation(outcome.receipt):
            budget = await policy.computation_budget_on(conn, lineage_ref["lineage_id"],
                owner=lineage_ref["owner"], expected_version=lineage_ref["version"])
            if not budget["can_retry"]:
                return {"batch_id": batch_id, "queued": False, "reason": budget["reason"]}
        result = await reconcile_locked_on(consolidation, conn, batch, attempt, recorded, request, outcome)
        if result.get("authority_block"):
            return {"batch_id": batch_id, "queued": False, "reason": result["authority_block"]}
        if outcome.status in {"unknown", "failed"} or interrupted_computation(outcome.receipt):
            due = result.get("retry_at") or lineage["db_now"]
        elif outcome.status == "succeeded":
            due = lineage["db_now"]
        else:
            due = lineage["db_now"] + timedelta(seconds=30)
        manifest = deepcopy(batch["manifest"])
        manifest["rbi_local_recovery"] = {"repair_ref": repair_ref, "original_error_code": LOCAL_FAILURE,
            "original_step_id": request.intent_id, "original_attempt_id": request.attempt_id,
            "original_observation": outcome.record(), "reopened_at": wire(lineage["db_now"])}
        manifest.pop("recovery_budget_blocked", None)
        await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined',error_code=%s,manifest=%s,"
            "retry_due_at=%s,retry_requested=false,updated_at=now() WHERE batch_id=%s",
            (outcome.code, Jsonb(manifest), due, batch_id))
        for episode, _ in rows:
            await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_unknown',"
                "extraction_started_at=NULL WHERE episode_id=%s", (episode["episode_id"],))
        await consolidation.retries.arm_on(conn, batch["partition_id"])
    return {"batch_id": batch_id, "queued": True, "reason": "reopened", "retry_due_at": wire(due),
            "original_attempt_id": request.attempt_id, "original_state": outcome.status}
