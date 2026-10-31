"""Stable model-step identity; recovery asks the same Broker attempt, never creates a retry."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from tianshu.models.codec import request_from_wire
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import RequestBudget


class ModelSteps:
    def __init__(self,database,models):
        self.database,self.models=database,models

    async def run(self,step_id,request):
        async with self.database.transaction() as conn:
            await conn.execute("INSERT INTO memory.model_steps(step_id,request) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                               (step_id,Jsonb(wire(request))))
            row=await (await conn.execute("SELECT * FROM memory.model_steps WHERE step_id=%s",(step_id,))).fetchone()
        if row["state"]=="source_revoked":
            raise AppError("source_unavailable",status=403)
        stored=request_from_wire(row["request"])
        # Only transport time changes; identity, input, scope, route and attempt are retained.
        stored=replace(stored,budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=120)))
        try:
            result=await self.models.generate(stored)
        except BaseException:
            async with self.database.transaction() as conn:
                await conn.execute("UPDATE memory.model_steps SET state='unknown',error_code='model_transport_unknown',updated_at=now() WHERE step_id=%s AND state<>'source_revoked'",
                                   (step_id,))
            raise
        async with self.database.transaction() as conn:
            await conn.execute("UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() WHERE step_id=%s AND state<>'source_revoked'",
                (result.receipt.status,result.receipt.error_code,step_id))
        if result.receipt.status!="succeeded":
            raise AppError(result.receipt.error_code or "model_outcome_unknown",status=503)
        return result
