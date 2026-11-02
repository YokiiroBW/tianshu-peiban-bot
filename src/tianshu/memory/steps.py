"""Fixed model-step requests; unknown outcomes are observed by original attempt only."""

from math import ceil

from psycopg.types.json import Jsonb

from tianshu.models.codec import request_from_wire
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from .authority import request_identity


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
                result = await self.models.result(original.attempt_id, context=original.context)
                if result is None:
                    receipt = await self.models.status(original.attempt_id)
                    if receipt is None:
                        raise AppError("model_outcome_unknown", status=503,
                            details={"attempt_id": original.attempt_id, "action": "observe_original_attempt"})
                    await self._outcome(step_id, receipt.status, receipt.error_code)
                    raise AppError(receipt.error_code or "model_outcome_"+receipt.status, status=503,
                        details={"attempt_id": original.attempt_id, "status": receipt.status,
                                 "action": "observe_original_attempt"})
        except BaseException as exc:
            if not isinstance(exc, AppError) or exc.code not in {"source_unavailable"}:
                known_cancel = isinstance(exc, AppError) and (exc.details or {}).get("dispatch") == "not_started" and (exc.details or {}).get("status") == "cancelled"
                async with self.database.transaction() as conn:
                    await conn.execute(
                        "UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() "
                        "WHERE step_id=%s AND state NOT IN ('source_revoked','succeeded','failed','cancelled')",
                        ("cancelled" if known_cancel else "unknown",getattr(exc, "code", "model_transport_unknown"), step_id))
            raise
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn, original.context, original.source_refs):
                raise AppError("source_unavailable", status=403)
            await conn.execute(
                "UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() WHERE step_id=%s AND state<>'source_revoked'",
                (result.receipt.status, result.receipt.error_code, step_id))
        if result.receipt.status != "succeeded":
            raise AppError(result.receipt.error_code or "model_outcome_"+result.receipt.status,
                status=503, details={"attempt_id": original.attempt_id, "status": result.receipt.status})
        return result, original

    async def _outcome(self, step_id, status, error_code):
        async with self.database.transaction() as conn:
            await conn.execute(
                "UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() WHERE step_id=%s AND state<>'source_revoked'",
                (status, error_code, step_id))
