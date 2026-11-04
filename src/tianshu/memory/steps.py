"""Fixed model-step requests; unknown outcomes are observed by original attempt only."""

from math import ceil

from psycopg.types.json import Jsonb

from tianshu.models.codec import request_from_wire
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from .authority import request_identity
from .model_outcome import needs_rpc_observation, observe_original


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

    async def submit_or_observe(self, step_id, partition_id, *, data_generation, observe_only=False):
        """Submit the frozen attempt once, then release the worker while Models runs."""
        _, original = await self.recorded(step_id)
        if original.data_generation != data_generation:
            raise AppError("job_generation_expired", status=409)
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn, original.context, original.source_refs):
                raise AppError("source_unavailable", status=403)
            fresh = await (await conn.execute(
                "SELECT state FROM memory.model_steps WHERE step_id=%s FOR UPDATE", (step_id,))).fetchone()
            if not fresh or fresh["state"] == "source_revoked":
                raise AppError("source_unavailable", status=403)
            # submit is idempotent for the same attempt and request digest. A
            # crash between the local marker and submit can safely adopt that
            # exact request without manufacturing another physical attempt.
            submit = not observe_only and fresh["state"] in {"prepared", "dispatching"}
            if submit:
                await conn.execute("UPDATE memory.model_steps SET state='dispatching',updated_at=now() WHERE step_id=%s",
                    (step_id,))
        if submit:
            completion = JobSpec("memory.consolidate", "memory-model-completion:" + original.attempt_id,
                {"partition_id": partition_id}, ServiceClass.ELASTIC, subject_id=partition_id,
                data_generation=data_generation)
            try:
                receipt = await self.models.submit(original, completion=completion)
            except BaseException as exc:
                if needs_rpc_observation(exc):
                    # No new physical attempt is authorized by a transport
                    # error. Persist uncertainty and observe the original.
                    raise AppError("model_outcome_unknown",status=503,details={
                        "status":"unknown","attempt_id":original.attempt_id,
                        "action":"observe_original_attempt"}) from exc
                raise
            if receipt.attempt_id != original.attempt_id or receipt.intent_id != original.intent_id:
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
