"""Memory-owned retries of a frozen, side-effect-free consolidation batch.

Queue delivery, logical application and physical model attempts are separate.
No original model request is submitted again, and all candidates still pass the
consolidation writer's source/version/authority checks and one application CAS.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import RequestBudget
from . import codec
from .generation import MODEL_REQUEST_SECONDS
from .model_outcome import confirmed_computation_unknown, observe_original, original_status

OBSERVE_SECONDS = 30
EXPLICIT_RETRY_LIMIT = 1
RPC_WRAPPERS = frozenset({"internal_error","broker_error","model_transport_unknown"})


def failure_code(error):
    return getattr(error, "code", type(error).__name__)


def retryable_code(code, status):
    # These are the pre-upgrade transport wrappers, not arbitrary semantic
    # errors. Preserve their stored value while using the shared retry policy.
    if status == "unknown" and code in {None, "model_transport_unknown", "memory_consolidation_unavailable"}:
        code = "model_outcome_unknown"
    return transient_model_failure(code, cancelled=status == "cancelled" and code in {None, "cancelled", "model_cancelled"})


def semantic_output_failure(batch):
    if not batch or batch["status"] != "failed" or not batch["step_ids"]:
        return False
    code = batch.get("error_code") or ""
    if code in {"memory_consolidation_output_truncated", "memory_consolidation_output_incomplete"}:
        return False
    if not code.startswith(("memory_consolidation_", "memory_context_", "claim_self_", "claim_speaker_", "claim_actor_")):
        return False
    if any(part in code for part in ("budget", "authority", "source", "lease", "episode_changed", "read_changed")):
        return False
    return True


def explicit_retry_available(batch):
    """Malformed semantic output has a finite user-requested recovery path."""
    return bool(semantic_output_failure(batch)
                and len(batch["manifest"].get("explicit_recoveries", ())) < EXPLICIT_RETRY_LIMIT)


def rpc_recovery_available(batch):
    return bool(batch and batch["status"]=="failed" and batch["step_ids"]
        and batch.get("selected_step_id") is None
        and batch.get("proposal_digest") is None
        and batch.get("error_code") in RPC_WRAPPERS)


class ConsolidationRetries:
    def __init__(self, consolidation):
        self.consolidation = consolidation
        self.database = consolidation.database

    @property
    def steps(self):
        return self.consolidation.steps

    async def track_on(self, conn, batch_id, step_id, *, kind, previous_step_id=None, due=None):
        step = await (await conn.execute("SELECT request,state,error_code FROM memory.model_steps WHERE step_id=%s", (step_id,))).fetchone()
        if step is None:
            raise AppError("memory_model_step_unavailable", status=404)
        if step["state"] == "source_revoked":
            raise AppError("source_unavailable", status=403)
        await conn.execute(
            "INSERT INTO memory.consolidation_attempts(batch_id,number,step_id,model_attempt_id,previous_step_id,kind,due_at,status,error_code) "
            "SELECT %s,COALESCE(max(number),0)+1,%s,%s,%s,%s,%s,%s,%s FROM memory.consolidation_attempts WHERE batch_id=%s "
            "ON CONFLICT(step_id) DO NOTHING",
            (batch_id, step_id, step["request"]["attempt_id"], previous_step_id, kind,
             due or datetime.now(UTC), step["state"], step["error_code"], batch_id))

    async def reject(self, batch_id, step_id, code):
        async with self.database.transaction() as conn:
            await conn.execute("UPDATE memory.consolidation_attempts SET rejected_error=COALESCE(rejected_error,%s),"
                "updated_at=now() WHERE batch_id=%s AND step_id=%s", (code, batch_id, step_id))

    async def succeeded(self, batch_id, step_id):
        async with self.database.transaction() as conn:
            await conn.execute("UPDATE memory.consolidation_attempts SET status='succeeded',error_code=NULL,updated_at=now() "
                "WHERE batch_id=%s AND step_id=%s", (batch_id,step_id))

    async def _postpone(self, batch, *, code=None, delay=OBSERVE_SECONDS, owner=None):
        async with self.database.transaction() as conn:
            await self.consolidation._assert_batch_generation_on(conn, batch)
            await self.consolidation._lock_owned_partition(conn, batch["partition_id"], owner)
            fresh = await (await conn.execute("UPDATE memory.consolidation_batches "
                "SET retry_due_at=now()+%s::double precision*interval '1 second',error_code=COALESCE(%s,error_code),updated_at=now() "
                "WHERE batch_id=%s AND status='quarantined' RETURNING retry_due_at", (delay, code, batch["batch_id"]))).fetchone()
            if fresh:
                await self.consolidation._schedule_on(conn, batch["partition_id"], fresh["retry_due_at"], suffix="retry")

    async def choose(self, batch, context, *, owner=None):
        """Consume known original success before allocating any new attempt."""
        async with self.database.transaction() as conn:
            await self.consolidation._assert_batch_generation_on(conn, batch)
            await self.consolidation._lock_owned_partition(conn, batch["partition_id"], owner)
            attempts = await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s ORDER BY number",
                                                (batch["batch_id"],))).fetchall()
            if not attempts:
                raise AppError("memory_consolidation_attempt_missing",status=409)
        if batch.get("selected_step_id"):
            return batch["selected_step_id"], True
        current = attempts[-1]
        outcome = None
        if batch["status"] == "quarantined":
            current_step,request=await self.steps.recorded(current["step_id"])
            outcome=await observe_original(self.steps.models,request)
            await self._recovery_observation(batch,current["step_id"],outcome,owner=owner)
            if outcome.receipt and outcome.status in {"failed","cancelled","succeeded","unknown"}:
                await self.consolidation._policy_outcome(batch["batch_id"],current["step_id"],
                    status=outcome.status,code=outcome.receipt.error_code,
                    observation_ref="models:"+request.attempt_id)
            if outcome.explicit_cancel:
                raise AppError("explicit_cancel", status=409, details={"status": "cancelled"})
        # Rejected semantic outputs and attempts superseded by a successor
        # cannot be selected again merely because their receipt succeeded.
        for candidate in attempts:
            # Once a successor is selected, an older physical result may be
            # observed for audit but cannot become this batch's application.
            if candidate["step_id"] != current["step_id"]:
                continue
            if candidate["rejected_error"]:
                continue
            if candidate["status"] == "prepared" and not (candidate["step_id"]==current["step_id"] and outcome and outcome.result):
                continue
            if candidate["step_id"]==current["step_id"] and outcome is not None:
                observed=outcome
            else:
                _,request=await self.steps.recorded(candidate["step_id"])
                observed=await observe_original(self.steps.models,request)
            if observed.result is not None and observed.status == "succeeded":
                return candidate["step_id"], True
        if batch["status"] != "quarantined":
            return current["step_id"], False
        status,code=outcome.status,outcome.code
        if outcome.receipt is None:
            if (current_step["state"] in {"prepared", "dispatching"}
                    and current["status"] in {"prepared", "observing"} and not current["rejected_error"]):
                return current["step_id"],False
            # Persisted old wrapper codes never substitute for a missing
            # current authoritative receipt, including after another restart.
            await self._postpone(batch,code=code,owner=owner)
            return None
        if status in {"queued", "running", "dispatching"} or status == "succeeded" and not current["rejected_error"]:
            await self._postpone(batch, code=code, owner=owner)
            return None
        if current["status"] == "prepared" and current_step["state"] == "prepared" and not current["rejected_error"]:
            return current["step_id"], False
        if batch.get("retry_due_at") and batch["retry_due_at"] > datetime.now(UTC):
            return None
        if not batch.get("retry_requested"):
            code = code or ("model_outcome_unknown" if status == "unknown" else "model_outcome_" + status)
            if not retryable_code(code, status):
                raise AppError(code, status=409, details={"status": status})
        return await self._successor(batch, context, current, owner=owner)

    async def _recovery_observation(self,batch,step_id,outcome,*,owner=None):
        if not batch["manifest"].get("rpc_recoveries"):
            return
        async with self.database.transaction() as conn:
            await self.consolidation._assert_batch_generation_on(conn, batch)
            await self.consolidation._lock_owned_partition(conn,batch["partition_id"],owner)
            fresh=await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s "
                "AND status='quarantined' FOR UPDATE",(batch["batch_id"],))).fetchone()
            if fresh:
                manifest=deepcopy(fresh["manifest"])
                for recovery in reversed(manifest.get("rpc_recoveries",())):
                    if recovery["step_id"]==step_id:
                        recovery["latest_original_observation"]=outcome.record()
                        await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s WHERE batch_id=%s",
                            (Jsonb(manifest),batch["batch_id"]))
                        break

    async def _successor(self, batch, context, previous, *, owner):
        _, original = await self.steps.recorded(previous["step_id"])
        receipt = None
        later_failed = False
        if previous["status"] == "unknown":
            fresh = await original_status(self.steps.models, original)
            if confirmed_computation_unknown(fresh):
                receipt = fresh
            elif fresh is not None and fresh.status == "failed" and transient_model_failure(fresh.error_code):
                later_failed = True
            else:
                # A newly terminal original needs another ordinary observation;
                # absent, pending, or unavailable control never authorizes retry.
                delay = 0 if fresh is not None and fresh.status in {"succeeded", "failed", "cancelled"} else OBSERVE_SECONDS
                await self._postpone(batch, code="model_outcome_unconfirmed", delay=delay, owner=owner)
                return None
        step_id = batch["batch_id"] + ":retry:" + str(previous["number"] + 1)
        # Copy the original native request, including old protocol, media,
        # parameters and route. Only this independent execution identity/time
        # changes. Ordinary queue time remains outside its execution duration.
        request = replace(original, intent_id=step_id, attempt_id=str(uuid5(NAMESPACE_URL, step_id)),
            budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=MODEL_REQUEST_SECONDS)),
            execution_timeout_seconds=MODEL_REQUEST_SECONDS)
        async with self.database.transaction() as conn:
            await self.consolidation._assert_batch_generation_on(conn, batch)
            if not await self.consolidation.sources.validate_persistence(conn, context, codec.refs(batch["dependency_refs"])):
                raise AppError("source_unavailable", status=403)
            await self.consolidation.identity.check_context(context, connection=conn)
            await self.consolidation._lock_owned_partition(conn, batch["partition_id"], owner)
            fresh = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                                             (batch["batch_id"],))).fetchone()
            if fresh["status"] != "quarantined" or fresh.get("selected_step_id") or fresh["step_ids"][-1] != previous["step_id"]:
                return None
            for episode in fresh["manifest"]["episodes"]:
                row = await (await conn.execute("SELECT revision,sequence,extraction_cursor,active FROM memory.episodes "
                    "WHERE episode_id=%s FOR UPDATE", (episode["episode_id"],))).fetchone()
                if (not row or not row["active"] or row["revision"] != episode["revision"]
                        or row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"]):
                    raise AppError("memory_consolidation_episode_changed", status=409)
            await self.consolidation.memory.claims.revisions.validate_read_set_on(
                conn, context, fresh["manifest"]["steps"][previous["step_id"]])
            recovery = fresh["manifest"].get("recovery")
            policy = self.consolidation.memory.recovery_policy
            prior = await (await conn.execute("SELECT recovery_policy_state FROM memory.consolidation_attempts "
                "WHERE batch_id=%s AND step_id=%s FOR UPDATE", (batch["batch_id"],previous["step_id"]))).fetchone()
            if later_failed and recovery and policy and prior["recovery_policy_state"] != "failed":
                due = datetime.now(UTC)
                await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=%s WHERE batch_id=%s",
                                   (due, batch["batch_id"]))
                await self.consolidation._schedule_on(conn, batch["partition_id"], due, suffix="original-settled")
                return None
            continuation = bool(prior and prior["recovery_policy_state"] == "succeeded")
            if recovery and policy and not continuation:
                claim = await (await conn.execute("SELECT * FROM memory.recovery_due_claims WHERE lineage_id=%s "
                    "AND expires_at>now() FOR UPDATE",(recovery["lineage_id"],))).fetchone()
                if not claim or claim["current_version"] != recovery["version"]:
                    waiting = await policy.inspect_wait_on(conn, recovery["lineage_id"],
                        owner=recovery["owner"], expected_version=recovery["version"])
                    if waiting is not None:
                        raise waiting
                    claim = await policy.claim_lineage_due_on(conn, recovery["lineage_id"],
                        owner=recovery["owner"], lease_seconds=30)
                    if claim is None:
                        # No observed wait is not permission to reserve.
                        due = datetime.now(UTC)+timedelta(seconds=OBSERVE_SECONDS)
                        await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=%s WHERE batch_id=%s",
                            (due,batch["batch_id"]))
                        await self.consolidation._schedule_on(conn,batch["partition_id"],due,suffix="recovery-await-claim")
                        return None
                    await conn.execute("INSERT INTO memory.recovery_due_claims(lineage_id,claim_token,current_version,expires_at) "
                        "VALUES(%s,%s,%s,%s) ON CONFLICT(lineage_id) DO UPDATE SET claim_token=excluded.claim_token,"
                        "current_version=excluded.current_version,expires_at=excluded.expires_at",
                        (recovery["lineage_id"],claim["claim_token"],claim["current_version"],claim["claim_expires_at"]))
            if not await self.consolidation._consume_recovery_on(conn, fresh, "retry"):
                raise AppError("memory_reorganization_recovery_exhausted", status=409)
            if recovery and policy and continuation:
                await self.consolidation._reserve_continuation_on(conn,recovery,request,
                    prior_attempt_id=previous["model_attempt_id"],reason="schema_correction")
            elif recovery and policy:
                from .authority import request_identity
                options = {"model_receipt": receipt} if receipt is not None else {}
                await policy.reserve_attempt_on(conn,recovery["lineage_id"],owner=recovery["owner"],
                    expected_version=recovery["version"],attempt_id=request.attempt_id,
                    request_digest=body_hash(request_identity(wire(request))),route=request.route_id or request.function,
                    original_state="unknown" if receipt is not None else "failed",claim_token=claim["claim_token"],
                    **options)
                await conn.execute("DELETE FROM memory.recovery_due_claims WHERE lineage_id=%s",(recovery["lineage_id"],))
            await self.steps.prepare_on(conn, step_id, request)
            await self.track_on(conn, batch["batch_id"], step_id, kind="retry", previous_step_id=previous["step_id"],
                                due=fresh.get("retry_due_at"))
            await self.consolidation._track_policy_on(conn,batch["batch_id"],step_id,recovery)
            manifest = deepcopy(fresh["manifest"])
            manifest["steps"][step_id] = deepcopy(manifest["steps"][previous["step_id"]])
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,step_ids=array_append(step_ids,%s),"
                "retry_due_at=NULL,retry_requested=false,error_code=NULL,updated_at=now() WHERE batch_id=%s",
                (Jsonb(manifest), step_id, batch["batch_id"]))
        return step_id, False

    async def defer(self, batch_id, error, *, owner=None):
        """Release the active slot after transient failure, preserving input CAS."""
        async with self.database.transaction(read_only=True) as conn:
            batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s", (batch_id,))).fetchone()
        if not batch or batch["status"] not in {"prepared", "waiting_model", "ready", "quarantined"} or not batch["step_ids"]:
            return False
        code = failure_code(error)
        details = getattr(error, "details", None) or {}
        if not (transient_model_failure(code) or details.get("status") in {"queued","running","succeeded"}
                or details.get("original_observation") in {"confirmed","missing","unavailable"}):
            return False
        recorded_step, original = await self.steps.recorded(batch["step_ids"][-1])
        outcome=await observe_original(self.steps.models,original)
        receipt=outcome.receipt
        control_unknown=receipt is None
        status="prepared" if receipt is None and recorded_step["state"]=="prepared" else outcome.status
        code=outcome.code
        if outcome.explicit_cancel:
            await self.consolidation._failure(batch["partition_id"],batch_id,
                AppError("explicit_cancel",status=409,details={"status":"cancelled"}),owner=owner)
            return True
        if not (status in {"queued", "running", "succeeded"} or control_unknown or retryable_code(code, status)):
            if receipt is not None:
                await self.consolidation._failure(batch["partition_id"],batch_id,
                    AppError(code or "model_outcome_"+status,status=409,details={"status":status}),owner=owner)
                return True
            return False
        async with self.database.transaction() as conn:
            await self.consolidation._assert_batch_generation_on(conn, batch)
            state = await self.consolidation._lock_owned_partition(conn, batch["partition_id"], None)
            if owner is not None and state["lease_owner"] != owner:
                return True
            fresh = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE", (batch_id,))).fetchone()
            if fresh["status"] not in {"prepared", "waiting_model", "ready", "quarantined"}:
                return True
            attempt = await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s AND step_id=%s",
                                               (batch_id, original.intent_id))).fetchone()
            if not attempt:
                raise AppError("memory_consolidation_attempt_missing",status=409)
            delay = OBSERVE_SECONDS if control_unknown or status in {"queued", "running", "succeeded"} else retry_delay_seconds(attempt["number"])
            failure = outcome.record()
            if details.get("rpc_failure"):
                failure["rpc_failure"]=details["rpc_failure"]
            await conn.execute("UPDATE memory.consolidation_attempts SET status=%s,error_code=%s,first_failure=COALESCE(first_failure,%s),"
                "updated_at=now() WHERE batch_id=%s AND step_id=%s", (status, code, Jsonb(failure), batch_id, original.intent_id))
            manifest = deepcopy(fresh["manifest"])
            manifest.setdefault("quarantine", {"attempt_id": original.attempt_id, **failure, "action": "retry_computation"})
            due = datetime.now(UTC) + timedelta(seconds=delay)
            await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined',manifest=%s,error_code=%s,"
                "retry_due_at=%s,updated_at=now() WHERE batch_id=%s", (Jsonb(manifest), code, due, batch_id))
            for episode in fresh["manifest"]["episodes"]:
                await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_unknown',extraction_started_at=NULL "
                    "WHERE episode_id=%s AND revision=%s AND sequence=%s AND active AND extraction_cursor IS NOT DISTINCT FROM %s",
                    (episode["episode_id"], episode["revision"], episode["sequence"], Jsonb(episode["before"])))
            await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL,last_error=%s "
                "WHERE partition_id=%s AND active_batch_id=%s", (code, batch["partition_id"], batch_id))
            pending = await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s AND active "
                "AND extraction_state='pending' LIMIT 1", (batch["partition_id"],))).fetchone()
            wake = datetime.now(UTC) if pending else None
            await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=%s WHERE partition_id=%s",
                               (wake, wake, batch["partition_id"]))
            await self.consolidation._schedule_on(conn, batch["partition_id"], wake or due, suffix="retry")
        return True

    async def arm_on(self, conn, partition_id):
        """Re-arm one native wake for the earliest remaining durable work."""
        state = await (await conn.execute("SELECT active_batch_id,wake_due_at FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
                                         (partition_id,))).fetchone()
        held = await (await conn.execute("SELECT min(COALESCE(retry_due_at,now())) AS due FROM memory.consolidation_batches "
            "WHERE partition_id=%s AND status='quarantined' AND NOT (manifest ? 'remote_wait_unresolved') "
            "AND manifest->>'data_generation'=(SELECT id FROM runtime.installation_generation WHERE singleton=true)",
            (partition_id,))).fetchone()
        values = [value for value in (state["wake_due_at"], held["due"]) if value is not None]
        if values and state["active_batch_id"] is None:
            await self.consolidation._schedule_on(conn, partition_id, min(values), suffix="retry")

    async def resume_rpc_on(self,conn,batch,context,request_id,*,targeted=False):
        """Reopen only a failed RPC wrapper for authoritative original observation."""
        if not rpc_recovery_available(batch):
            return False
        # The entrypoint proves the complete batch closure under this frozen
        # context before taking partition/batch locks, then rechecks its snapshot.
        await self.consolidation.identity.check_context(context,connection=conn)
        step_id=batch["step_ids"][-1]
        step=await (await conn.execute("SELECT request,state,error_code FROM memory.model_steps WHERE step_id=%s",(step_id,))).fetchone()
        if not step or step["state"]=="source_revoked":
            raise AppError("source_unavailable",status=403)
        rejected=await (await conn.execute("SELECT rejected_error FROM memory.consolidation_attempts WHERE step_id=%s",(step_id,))).fetchone()
        if not rejected:
            raise AppError("memory_consolidation_attempt_missing",status=409)
        if rejected["rejected_error"]:
            return False
        for episode in batch["manifest"]["episodes"]:
            row=await (await conn.execute("SELECT revision,sequence,extraction_cursor,active FROM memory.episodes "
                "WHERE episode_id=%s FOR UPDATE",(episode["episode_id"],))).fetchone()
            if (not row or not row["active"] or row["revision"]!=episode["revision"] or row["sequence"]!=episode["sequence"]
                    or row["extraction_cursor"]!=episode["before"]):
                raise AppError("memory_consolidation_episode_changed",status=409)
        previous={"status":step["state"],"error_code":step["error_code"],"authority":"memory_step_snapshot",
                  "rpc_failure":{"error_code":batch["error_code"]}}
        await conn.execute("UPDATE memory.consolidation_attempts SET first_failure=COALESCE(first_failure,%s) WHERE step_id=%s",
            (Jsonb(previous),step_id))
        manifest=deepcopy(batch["manifest"])
        manifest.setdefault("rpc_recoveries",[]).append({"request_id":request_id,"step_id":step_id,
            "attempt_id":step["request"]["attempt_id"],"previous_batch_status":batch["status"],
            "previous_error_code":batch["error_code"],"previous_step":previous,"queued_at":wire(datetime.now(UTC))})
        await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined',manifest=%s,retry_due_at=now(),"
            "retry_requested=false,updated_at=now() WHERE batch_id=%s",(Jsonb(manifest),batch["batch_id"]))
        for episode in batch["manifest"]["episodes"]:
            before=Jsonb(episode["before"]) if episode["before"] is not None else None
            await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_unknown',extraction_started_at=NULL "
                "WHERE episode_id=%s AND revision=%s AND sequence=%s AND active AND extraction_cursor IS NOT DISTINCT FROM %s",
                (episode["episode_id"],episode["revision"],episode["sequence"],before))
        await self.consolidation._schedule_on(conn,batch["partition_id"],datetime.now(UTC),suffix="rpc-recovery:"+request_id,
            target_batch_id=batch["batch_id"] if targeted else None)
        return True

    async def resume_on(self, conn, batch, context, request_id, *, targeted=False):
        if not explicit_retry_available(batch):
            return False
        await self.consolidation.identity.check_context(context, connection=conn)
        attempt = await (await conn.execute("SELECT 1 FROM memory.consolidation_attempts WHERE batch_id=%s AND step_id=%s",
            (batch["batch_id"],batch["step_ids"][-1]))).fetchone()
        if not attempt:
            raise AppError("memory_consolidation_attempt_missing",status=409)
        for episode in batch["manifest"]["episodes"]:
            row = await (await conn.execute("SELECT revision,sequence,extraction_cursor,active FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                                           (episode["episode_id"],))).fetchone()
            if (not row or not row["active"] or row["revision"] != episode["revision"] or row["sequence"] != episode["sequence"]
                    or row["extraction_cursor"] != episode["before"]):
                raise AppError("memory_consolidation_episode_changed", status=409)
        manifest = deepcopy(batch["manifest"])
        manifest.setdefault("explicit_recoveries", []).append({"request_id": request_id, "failed_step_id": batch["step_ids"][-1],
                                                              "error_code": batch["error_code"]})
        await conn.execute("UPDATE memory.consolidation_attempts SET rejected_error=COALESCE(rejected_error,%s) WHERE batch_id=%s",
                           (batch["error_code"], batch["batch_id"]))
        # Keep the old compiler receipt, but never select its rejected proposal
        # again when a bounded semantic continuation has been authorized.
        await conn.execute("UPDATE memory.consolidation_compilations SET selected=false WHERE batch_id=%s AND selected",
                           (batch["batch_id"],))
        await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined',retry_due_at=now(),retry_requested=true,"
            "manifest=%s,proposal_digest=NULL,selected_step_id=NULL,updated_at=now() WHERE batch_id=%s", (Jsonb(manifest), batch["batch_id"]))
        await self.consolidation._schedule_on(conn, batch["partition_id"], datetime.now(UTC), suffix="explicit:" + request_id,
            target_batch_id=batch["batch_id"] if targeted else None)
        return True

    async def wake_due_on(self, conn, batch, context, request_id):
        """Wake exactly this already-eligible retry; do not allocate an attempt."""
        if batch["status"] != "quarantined" or batch.get("retry_due_at") and batch["retry_due_at"] > datetime.now(UTC):
            return False
        await self.consolidation.identity.check_context(context,connection=conn)
        await self.consolidation._schedule_on(conn,batch["partition_id"],datetime.now(UTC),
            suffix="operations:"+request_id,target_batch_id=batch["batch_id"])
        return True
