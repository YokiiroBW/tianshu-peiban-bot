"""Fixed model-step requests; unknown outcomes are observed by original attempt only."""

from math import ceil

from psycopg.types.json import Jsonb

from tianshu.models.codec import request_from_wire
from tianshu.models.timing import waiting_timeout
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.database import classify_database_failure
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from .authority import request_identity
from .model_outcome import needs_rpc_observation, observe_original


def admission_open(request):
    """Honor the original absolute/business deadline without changing its budget."""
    try:
        waiting_timeout(request)
    except TimeoutError:
        return False
    except AppError as error:
        if error.code in {"business_deadline_expired", "queue_wait_expired"}:
            return False
        raise
    return True


class ModelSteps:
    def __init__(self, database, models, sources):
        self.database, self.models, self.sources = database, models, sources

    async def prepare_on(self, conn, step_id, request):
        if not await self.sources.validate_persistence(conn, request.context, request.source_refs):
            raise AppError("source_unavailable", status=403)
        await conn.execute(
            "INSERT INTO memory.model_steps(step_id,request,request_timeout_seconds) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (step_id, Jsonb(wire(request)), max(1,min(900,ceil(
                request.execution_timeout_seconds if request.execution_timeout_seconds is not None
                else request.budget.remaining())))))
        row = await (await conn.execute(
            "SELECT * FROM memory.model_steps WHERE step_id=%s", (step_id,))).fetchone()
        if row["state"] == "source_revoked":
            raise AppError("source_unavailable", status=403)
        if row["request"] != wire(request):
            # Transport deadlines and set iteration order are not execution identity.
            old = request_identity(row["request"])
            new = request_identity(request)
            if body_hash(old) != body_hash(new):
                raise AppError("memory_model_step_identity_conflict", status=409)
        return row

    async def recorded(self, step_id):
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute(
                "SELECT * FROM memory.model_steps WHERE step_id=%s", (step_id,))).fetchone()
        if not row:
            raise AppError("memory_model_step_unavailable", status=404)
        if row["state"] == "source_revoked":
            raise AppError("source_unavailable", status=403)
        return row, request_from_wire(row["request"])

    async def run(self, step_id, request):
        result, _ = await self.run_recorded(step_id, request)
        return result

    async def run_recorded(self, step_id, request):
        async with self.database.transaction() as conn:
            await self.prepare_on(conn, step_id, request)
        return await self.execute_recorded(step_id)

    async def execute_recorded(self, step_id, *, observe_only=False):
        _, original = await self.recorded(step_id)
        # A new frozen request carries an execution duration, allocated only by
        # Broker on admission. A legacy absolute deadline is never extended.
        stored = original
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn, original.context, original.source_refs):
                raise AppError("source_unavailable", status=403)
            fresh = await (await conn.execute(
                "SELECT state FROM memory.model_steps WHERE step_id=%s FOR UPDATE", (step_id,))).fetchone()
            # Persist dispatch ownership before crossing the process boundary. A crash here is unknown,
            # never permission to submit again; Broker.status may establish not_started explicitly.
            dispatch = fresh["state"] == "prepared" and not observe_only
            if dispatch:
                await conn.execute(
                    "UPDATE memory.model_steps SET state='dispatching',updated_at=now() WHERE step_id=%s",
                    (step_id,))
        try:
            if dispatch:
                result = await self.models.generate(stored)
            else:
                outcome=await observe_original(self.models,original)
                result=outcome.result
                if result is None:
                    if outcome.receipt:
                        await self._outcome(step_id,outcome.status,outcome.receipt.error_code)
                    raise outcome.exception(original.attempt_id)
        except BaseException as exc:
            if isinstance(exc,RecoveryWait) or classify_database_failure(exc) is not None:
                raise
            # A generation RPC can fail after Models already persisted its
            # actual outcome. Only that original result/receipt may replace the
            # transport wrapper; arbitrary internal_error is never retry policy.
            reconcile = dispatch and needs_rpc_observation(exc)
            if reconcile:
                # Persist the crossed-boundary uncertainty before any control
                # await, so cancellation during reconciliation still leaves a
                # recoverable original identity and propagates immediately.
                async with self.database.transaction() as conn:
                    await conn.execute("UPDATE memory.model_steps SET state='unknown',error_code=%s,updated_at=now() "
                        "WHERE step_id=%s AND state NOT IN ('source_revoked','succeeded','failed','cancelled')",
                        (getattr(exc,"code","model_transport_unknown"),step_id))
                try:
                    outcome=await observe_original(self.models,original)
                except AppError as authority_error:
                    exc=authority_error
                else:
                    async with self.database.transaction() as conn:
                        await conn.execute("UPDATE memory.consolidation_attempts SET first_failure=COALESCE(first_failure,%s),"
                            "updated_at=now() WHERE step_id=%s",(Jsonb(outcome.record(rpc_error=exc)),step_id))
                    if outcome.result is not None and outcome.status=="succeeded":
                        result=outcome.result
                        exc=None
                    else:
                        if outcome.receipt:
                            await self._outcome(step_id,outcome.status,outcome.receipt.error_code)
                        exc=outcome.exception(original.attempt_id,rpc_error=exc)
            if exc is not None and (not isinstance(exc, AppError) or exc.code not in {"source_unavailable"}):
                details=getattr(exc,"details",None) or {}
                known_cancel = isinstance(exc, AppError) and details.get("dispatch") == "not_started" and details.get("status") == "cancelled"
                observed=details.get("status")
                state=observed if observed in {"queued","running","succeeded","failed","cancelled","unknown"} else "unknown"
                code=getattr(exc,"code","model_transport_unknown")
                if details.get("cancellation_reason")=="explicit_cancel":
                    code=details.get("receipt_error_code",code)
                async with self.database.transaction() as conn:
                    await conn.execute(
                        "UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() "
                        "WHERE step_id=%s AND state NOT IN ('source_revoked','succeeded','failed','cancelled')",
                        ("cancelled" if known_cancel else state,code, step_id))
            if exc is not None:
                raise exc
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn, original.context, original.source_refs):
                raise AppError("source_unavailable", status=403)
            await conn.execute(
                "UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() WHERE step_id=%s AND state<>'source_revoked'",
                (result.receipt.status, result.receipt.error_code, step_id))
        if result.receipt.status != "succeeded":
            explicit = getattr(result.receipt,"cancellation_reason",None) == "explicit_cancel"
            raise AppError("explicit_cancel" if explicit else result.receipt.error_code or "model_outcome_"+result.receipt.status,
                status=503, details={"attempt_id": original.attempt_id, "status": result.receipt.status,
                    "receipt_error_code":result.receipt.error_code,
                    "cancellation_reason":getattr(result.receipt,"cancellation_reason",None)})
        return result, original

    async def submit_or_observe(self, step_id, partition_id, *, data_generation, observe_only=False, owner=None,
                                validate_on=None):
        """Submit the frozen attempt once, then release the worker while Models runs."""
        recorded, original = await self.recorded(step_id)
        if original.data_generation != data_generation:
            raise AppError("job_generation_expired", status=409)
        existing = None
        if not observe_only and recorded["state"] in {"prepared", "dispatching"}:
            # Only a genuinely prepared step uses the pre-submit lookup. An
            # absent observe result after dispatch is inconclusive, but Broker
            # can idempotently admit this exact frozen request and completion.
            existing = (await self.models.status(original.attempt_id, phase="pre_submit")
                        if recorded["state"] == "prepared" else await self.models.status(original.attempt_id))
            if existing and (existing.attempt_id != original.attempt_id or existing.intent_id != original.intent_id
                    or existing.data_generation != original.data_generation):
                raise AppError("memory_model_result_identity_conflict", status=409)
        async with self.database.transaction() as conn:
            generation = await (await conn.execute("SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
            if not generation or generation["id"] != data_generation:
                raise AppError("job_generation_expired", status=409)
            if not await self.sources.validate_persistence(conn, original.context, original.source_refs):
                raise AppError("source_unavailable", status=403)
            state = await (await conn.execute("SELECT active_batch_id,lease_owner,lease_until>now() AS leased FROM memory.consolidation_state "
                "WHERE partition_id=%s FOR UPDATE", (partition_id,))).fetchone()
            if owner is not None and (not state or state["lease_owner"] != owner or not state["leased"]):
                raise AppError("memory_consolidation_lease_changed", status=409)
            batch = await (await conn.execute("SELECT b.* FROM memory.consolidation_batches b "
                "JOIN memory.consolidation_attempts a ON a.batch_id=b.batch_id "
                "WHERE a.step_id=%s AND b.partition_id=%s FOR UPDATE OF b", (step_id, partition_id))).fetchone()
            attempt = await (await conn.execute("SELECT status FROM memory.consolidation_attempts WHERE step_id=%s FOR UPDATE",
                (step_id,))).fetchone()
            fresh = await (await conn.execute("SELECT state,request FROM memory.model_steps WHERE step_id=%s FOR UPDATE",
                (step_id,))).fetchone()
            if not fresh or fresh["state"] == "source_revoked":
                raise AppError("source_unavailable", status=403)
            if (not state or not batch or not attempt or batch["status"] not in {"prepared", "waiting_model", "ready", "quarantined"}
                    or batch["status"] != "quarantined" and state["active_batch_id"] != batch["batch_id"]
                    or not batch["step_ids"] or batch["step_ids"][-1] != step_id or batch.get("selected_step_id")
                    or batch["manifest"].get("data_generation") != data_generation
                    or step_id not in batch["manifest"].get("steps", {})
                    or batch["manifest"].get("recovery_pending") or fresh["request"] != recorded["request"]):
                raise AppError("memory_consolidation_attempt_changed", status=409)
            submit = (not observe_only and recorded["state"] in {"prepared", "dispatching"}
                      and fresh["state"] == recorded["state"] and existing is None
                      and attempt["status"] in {"prepared", "observing"} and admission_open(original))
            if submit:
                if validate_on is None or batch["dependency_refs"] != wire(original.source_refs):
                    raise AppError("memory_consolidation_attempt_changed", status=409)
                await validate_on(conn, batch, original.context, step_id)
            if submit and fresh["state"] == "prepared":
                await conn.execute("UPDATE memory.model_steps SET state='dispatching',updated_at=now() WHERE step_id=%s",
                    (step_id,))
            elif existing is not None and fresh["state"] in {"prepared", "dispatching"}:
                await conn.execute("UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() WHERE step_id=%s",
                    (existing.status, existing.error_code, step_id))
                await conn.execute("UPDATE memory.consolidation_attempts SET status=%s,error_code=%s,updated_at=now() "
                    "WHERE step_id=%s", (existing.status, existing.error_code, step_id))
        if submit:
            completion = JobSpec("memory.consolidate", "memory-model-completion:" + original.attempt_id,
                {"partition_id": partition_id}, ServiceClass.ELASTIC, subject_id=partition_id,
                data_generation=data_generation)
            try:
                receipt = await self.models.submit(original, completion=completion)
            except BaseException as exc:
                if isinstance(exc,RecoveryWait) or classify_database_failure(exc) is not None:
                    raise
                if not needs_rpc_observation(exc):
                    raise
                # A lost submit acknowledgement is not the Models outcome.
                # Preserve both the wrapper and the authoritative original
                # receipt before choosing recovery or consuming a result.
                outcome = await observe_original(self.models, original)
                async with self.database.transaction() as conn:
                    await conn.execute("UPDATE memory.consolidation_attempts SET first_failure=COALESCE(first_failure,%s),"
                        "updated_at=now() WHERE step_id=%s", (Jsonb(outcome.record(rpc_error=exc)), step_id))
                if outcome.result is not None and outcome.status == "succeeded":
                    await self._outcome(step_id, "succeeded", None)
                    return outcome.result, original
                if outcome.receipt is not None:
                    # Once any receipt is confirmed, a later missing lookup is
                    # observation-only and cannot become another admission.
                    await self._outcome(step_id, outcome.status, outcome.receipt.error_code)
                # With no receipt, keep the frozen dispatching request for
                # idempotent admission by the same attempt and completion.
                raise outcome.exception(original.attempt_id, rpc_error=exc) from exc
            if (receipt.attempt_id != original.attempt_id or receipt.intent_id != original.intent_id
                    or receipt.data_generation != original.data_generation):
                raise AppError("memory_model_result_identity_conflict", status=409)
            await self._outcome(step_id, receipt.status, receipt.error_code)
            async with self.database.transaction() as conn:
                await conn.execute("UPDATE memory.consolidation_attempts SET status=%s,error_code=%s,updated_at=now() "
                    "WHERE step_id=%s", (receipt.status, receipt.error_code, step_id))
            if receipt.status in {"queued", "running"}:
                return None
        outcome = await observe_original(self.models, original)
        if outcome.result is not None and outcome.status == "succeeded":
            await self._outcome(step_id, "succeeded", None)
            return outcome.result, original
        if outcome.receipt is not None:
            await self._outcome(step_id, outcome.status, outcome.receipt.error_code)
        if outcome.status in {"queued", "running"} or outcome.receipt is None:
            return None
        raise outcome.exception(original.attempt_id)

    async def _outcome(self, step_id, status, error_code):
        async with self.database.transaction() as conn:
            await conn.execute(
                "UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() WHERE step_id=%s AND state<>'source_revoked'",
                (status, error_code, step_id))
