"""Memory-owned retries of a frozen, side-effect-free consolidation batch.

Queue delivery, logical application and physical model attempts are separate.
No original model request is submitted again, and all candidates still pass the
consolidation writer's source/version/authority checks and one application CAS.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from aiohttp import ClientError
from psycopg.types.json import Jsonb

from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import RequestBudget
from . import codec
from .generation import MODEL_REQUEST_SECONDS
from .materials import batch_frozen

OBSERVE_SECONDS = 30
EXPLICIT_RETRY_LIMIT = 1


def failure_code(error):
    return getattr(error, "code", type(error).__name__)


def retryable_code(code, status):
    # These are the pre-upgrade transport wrappers, not arbitrary semantic
    # errors. Preserve their stored value while using the shared retry policy.
    if status == "unknown" and code in {None, "model_transport_unknown", "memory_consolidation_unavailable"}:
        code = "model_outcome_unknown"
    return transient_model_failure(code, cancelled=status == "cancelled" and code in {None, "cancelled", "model_cancelled"})


def semantic_output_failure(batch):
    if (not batch or batch["status"] != "failed" or not batch["step_ids"]
            or batch["schema_version"] == "memory.legacy-extraction.v0"):
        return False
    code = batch.get("error_code") or ""
    if not code.startswith(("memory_consolidation_", "memory_context_", "claim_self_", "claim_speaker_", "claim_actor_")):
        return False
    if any(part in code for part in ("budget", "authority", "source", "lease", "episode_changed", "read_changed")):
        return False
    return True


def explicit_retry_available(batch):
    """Malformed semantic output has a finite user-requested recovery path."""
    return bool(semantic_output_failure(batch)
                and len(batch["manifest"].get("explicit_recoveries", ())) < EXPLICIT_RETRY_LIMIT)


def protocol_refresh_available(batch):
    # Known bad output frozen before the small model protocol must be rebuilt
    # through MaterialBuilder. Replaying its old verbose prompt is ineffective.
    return bool(explicit_retry_available(batch)
                and batch_frozen(batch).get("model_protocol") != "memory.proposal.v2")


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

    async def adopt_on(self, conn, batch):
        """Old holds are adopted without rewriting their prompts or receipts."""
        existing = {row["step_id"] for row in await (await conn.execute(
            "SELECT step_id FROM memory.consolidation_attempts WHERE batch_id=%s", (batch["batch_id"],))).fetchall()}
        previous = None
        for step_id in batch["step_ids"]:
            kind = "repair" if ":repair:" in step_id else "context" if previous else "original"
            if step_id not in existing:
                await self.track_on(conn, batch["batch_id"], step_id, kind=kind, previous_step_id=previous,
                                    due=batch.get("retry_due_at") or batch["created_at"])
            previous = step_id

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
            await self.consolidation._lock_owned_partition(conn, batch["partition_id"], owner)
            fresh = await (await conn.execute("UPDATE memory.consolidation_batches "
                "SET retry_due_at=now()+%s::double precision*interval '1 second',error_code=COALESCE(%s,error_code),updated_at=now() "
                "WHERE batch_id=%s AND status='quarantined' RETURNING retry_due_at", (delay, code, batch["batch_id"]))).fetchone()
            if fresh:
                await self.consolidation._schedule_on(conn, batch["partition_id"], fresh["retry_due_at"], suffix="retry")

    async def choose(self, batch, context, *, owner=None):
        """Consume known original success before allocating any new attempt."""
        async with self.database.transaction() as conn:
            await self.consolidation._lock_owned_partition(conn, batch["partition_id"], owner)
            await self.adopt_on(conn, batch)
            attempts = await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s ORDER BY number",
                                                (batch["batch_id"],))).fetchall()
        if batch.get("selected_step_id"):
            return batch["selected_step_id"], True
        current = attempts[-1]
        receipt = None
        if batch["status"] == "quarantined":
            try:
                receipt = await self.steps.models.status(current["model_attempt_id"])
            except (AppError, TimeoutError, OSError, ClientError):
                await self._postpone(batch, owner=owner)
                return None
            if receipt and getattr(receipt, "cancellation_reason", None) == "explicit_cancel":
                raise AppError("explicit_cancel", status=409, details={"status": "cancelled"})
        # A late original is only a candidate; application below selects the
        # actual winner under the batch lock. Rejected semantic outputs cannot
        # be selected again merely because their physical receipt succeeded.
        for candidate in attempts:
            if candidate["rejected_error"] or candidate["status"] == "prepared":
                continue
            result = await self.steps.models.result(candidate["model_attempt_id"], context=context)
            if result is not None and result.receipt.status == "succeeded":
                return candidate["step_id"], True
        if batch["status"] != "quarantined":
            return current["step_id"], False
        status = receipt.status if receipt else current["status"]
        code = (receipt.error_code if receipt else None) or current["error_code"] or batch["error_code"]
        if status in {"queued", "running", "dispatching"} or status == "succeeded" and not current["rejected_error"]:
            await self._postpone(batch, code=code, owner=owner)
            return None
        if current["status"] == "prepared" and not current["rejected_error"]:
            return current["step_id"], False
        if batch.get("retry_due_at") and batch["retry_due_at"] > datetime.now(UTC):
            return None
        if not batch.get("retry_requested"):
            code = code or ("model_outcome_unknown" if status == "unknown" else "model_outcome_" + status)
            if not retryable_code(code, status):
                raise AppError(code, status=409, details={"status": status})
        return await self._successor(batch, context, current, owner=owner)

    async def _successor(self, batch, context, previous, *, owner):
        _, original = await self.steps.recorded(previous["step_id"])
        step_id = batch["batch_id"] + ":retry:" + str(previous["number"] + 1)
        # Copy the original native request, including old protocol, media,
        # parameters and route. Only this independent execution identity/time
        # changes. Ordinary queue time remains outside its execution duration.
        request = replace(original, intent_id=step_id, attempt_id=str(uuid5(NAMESPACE_URL, step_id)),
            budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=MODEL_REQUEST_SECONDS)),
            execution_timeout_seconds=MODEL_REQUEST_SECONDS)
        async with self.database.transaction() as conn:
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
            await self.steps.prepare_on(conn, step_id, request)
            await self.track_on(conn, batch["batch_id"], step_id, kind="retry", previous_step_id=previous["step_id"],
                                due=fresh.get("retry_due_at"))
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
        recorded_step, original = await self.steps.recorded(batch["step_ids"][-1])
        try:
            receipt = await self.steps.models.status(original.attempt_id)
        except (AppError, TimeoutError, OSError, ClientError):
            receipt = None
            control_unknown = True
        else:
            control_unknown = False
        status = receipt.status if receipt else ("prepared" if recorded_step["state"] == "prepared" else details.get("status") or "unknown")
        code = (receipt.error_code if receipt else None) or code
        explicit = bool(receipt and getattr(receipt, "cancellation_reason", None) == "explicit_cancel")
        if explicit:
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
            state = await self.consolidation._lock_owned_partition(conn, batch["partition_id"], None)
            if owner is not None and state["lease_owner"] != owner:
                return True
            fresh = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE", (batch_id,))).fetchone()
            if fresh["status"] not in {"prepared", "waiting_model", "ready", "quarantined"}:
                return True
            await self.adopt_on(conn, fresh)
            attempt = await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s AND step_id=%s",
                                               (batch_id, original.intent_id))).fetchone()
            delay = OBSERVE_SECONDS if control_unknown or status in {"queued", "running", "succeeded"} else retry_delay_seconds(attempt["number"])
            failure = wire(receipt) if receipt else {"status": status, "error_code": code, "control_unavailable": control_unknown}
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
            "WHERE partition_id=%s AND status='quarantined'", (partition_id,))).fetchone()
        values = [value for value in (state["wake_due_at"], held["due"]) if value is not None]
        if values and state["active_batch_id"] is None:
            await self.consolidation._schedule_on(conn, partition_id, min(values), suffix="retry")

    async def resume_on(self, conn, batch, context, request_id):
        if not explicit_retry_available(batch):
            return False
        await self.consolidation.identity.check_context(context, connection=conn)
        await self.adopt_on(conn, batch)
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
        await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined',retry_due_at=now(),retry_requested=true,"
            "manifest=%s,proposal_digest=NULL,updated_at=now() WHERE batch_id=%s", (Jsonb(manifest), batch["batch_id"]))
        await self.consolidation._schedule_on(conn, batch["partition_id"], datetime.now(UTC), suffix="explicit:" + request_id)
        return True
