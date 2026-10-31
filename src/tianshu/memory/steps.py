"""Stable model-step identity; recovery asks the same Broker attempt, never creates a retry."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from tianshu.models.codec import request_from_wire
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import RequestBudget


class ModelSteps:
    def __init__(self, database, models, sources):
        self.database, self.models, self.sources = database, models, sources

    async def run(self, step_id, request):
        result, _ = await self.run_recorded(step_id, request)
        return result

    async def run_recorded(self, step_id, request):
        """Return the original request as well, so a recovered result keeps its exact dependencies."""
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(
                conn, request.context, request.source_refs
            ):
                raise AppError("source_unavailable", status=403)
            await conn.execute(
                "INSERT INTO memory.model_steps(step_id,request) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (step_id, Jsonb(wire(request))),
            )
            row = await (
                await conn.execute("SELECT * FROM memory.model_steps WHERE step_id=%s", (step_id,))
            ).fetchone()
        if row["state"] == "source_revoked":
            raise AppError("source_unavailable", status=403)
        stored = request_from_wire(row["request"])
        # Only transport time changes; identity, input, scope, route and attempt are retained.
        deadline = min(request.budget.deadline_at, datetime.now(UTC) + timedelta(seconds=120))
        stored = replace(
            stored,
            budget=RequestBudget(
                deadline,
                trace_id=request.budget.trace_id,
                cancellation_id=request.budget.cancellation_id,
            ),
        )
        try:
            result = await self.models.generate(stored)
        except BaseException:
            async with self.database.transaction() as conn:
                await conn.execute(
                    "UPDATE memory.model_steps SET state='unknown',error_code='model_transport_unknown',updated_at=now() WHERE step_id=%s AND state<>'source_revoked'",
                    (step_id,),
                )
            raise
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(
                conn, stored.context, stored.source_refs
            ):
                raise AppError("source_unavailable", status=403)
            await conn.execute(
                "UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() WHERE step_id=%s AND state<>'source_revoked'",
                (result.receipt.status, result.receipt.error_code, step_id),
            )
        if result.receipt.status != "succeeded":
            raise AppError(result.receipt.error_code or "model_outcome_unknown", status=503)
        return result, stored
