"""Durable model attempts; an absent live task never proves absence of an external effect."""

from __future__ import annotations

import hashlib
import json

from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from .types import ModelReceipt, ModelUsage


def digest(value):
    return hashlib.sha256(json.dumps(wire(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def receipt(row):
    usage = row.get("usage") or {}
    return ModelReceipt(
        row["attempt_id"], row["intent_id"], row["status"], row["dispatch"], row["route_id"],
        row["route_revision"], row["provider_id"], row["model"], row["protocol"],
        row["quota_group"], ModelUsage(**usage), row["provider_handle"], row["queued_at"],
        row["started_at"], row["finished_at"], row["first_output_ms"], row["error_code"])


class AttemptLedger:
    def __init__(self, database, instance_id):
        self.database, self.instance_id = database, instance_id

    async def create(self, request, plan, units):
        semantic = wire(request)
        semantic.pop("budget", None)
        semantic.pop("max_queue_wait", None)
        request_hash = digest(semantic)
        async with self.database.transaction(request.budget) as conn:
            cursor = await conn.execute(
                "INSERT INTO models.attempts(attempt_id,intent_id,principal_id,actor_id,"
                "conversation_id,function,service_class,route_id,route_revision,provider_id,"
                "model,protocol,quota_group,request_digest,status,dispatch,deadline_at,"
                "estimated_tokens,estimated_units,charged_units,broker_instance,billing_account,source_refs) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued','not_started',"
                "%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",
                (request.attempt_id, request.intent_id, request.context.principal_id,
                 request.context.actor_id, request.context.conversation_id, request.function,
                 request.service_class.value, plan.route_id, plan.revision, plan.provider_id,
                 plan.model, plan.protocol, plan.quota_group, request_hash,
                 request.budget.deadline_at, units.get("tokens"), Jsonb(dict(units)),
                 Jsonb(dict(units)), self.instance_id, getattr(request, "billing_account", None),
                 Jsonb(wire(request.source_refs))))
            row = await cursor.fetchone()
            if row:
                cancelled = await (await conn.execute("SELECT 1 FROM models.cancellations "
                    "WHERE attempt_id=%s", (request.attempt_id,))).fetchone()
                if cancelled:
                    row = await (await conn.execute("UPDATE models.attempts SET status='cancelled',"
                        "cancel_requested=true,finished_at=now(),error_code='cancelled_before_start' "
                        "WHERE attempt_id=%s RETURNING *", (request.attempt_id,))).fetchone()
                return row, True
            cursor = await conn.execute("SELECT * FROM models.attempts WHERE attempt_id=%s",
                                        (request.attempt_id,))
            row = await cursor.fetchone()
            if row["request_digest"] != request_hash:
                raise AppError("attempt_identity_conflict", status=409)
            return row, False

    async def get(self, attempt_id):
        async with self.database.transaction(control=True, read_only=True) as conn:
            cursor = await conn.execute("SELECT * FROM models.attempts WHERE attempt_id=%s",
                                        (attempt_id,))
            return await cursor.fetchone()

    async def started(self, attempt_id, hold_seconds):
        async with self.database.transaction(control=True) as conn:
            cursor = await conn.execute(
                "UPDATE models.attempts SET status='running',dispatch='submitted',started_at=now(),"
                "hold_until=GREATEST(deadline_at,now()+(%s*interval '1 second')),"
                "charged_tokens=estimated_tokens,revision=revision+1 "
                "WHERE attempt_id=%s AND status='queued' AND NOT cancel_requested "
                "AND NOT EXISTS(SELECT 1 FROM models.cancellations c "
                "WHERE c.attempt_id=models.attempts.attempt_id) RETURNING *",
                (hold_seconds, attempt_id))
            row = await cursor.fetchone()
            if not row:
                raise AppError("attempt_cancelled_before_start", status=409)
            return row

    async def checkpoint(self, attempt_id, *, provider_handle=None, first_output_ms=None):
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "UPDATE models.attempts SET output_observed=true,provider_handle=COALESCE(%s,"
                "provider_handle),first_output_ms=COALESCE(first_output_ms,%s),revision=revision+1 "
                "WHERE attempt_id=%s", (provider_handle, first_output_ms, attempt_id))

    async def finish(self, attempt_id, status, *, usage=None, result=None, error_code=None,
                     charged_units=None, provider_handle=None):
        async with self.database.transaction(control=True) as conn:
            cursor = await conn.execute(
                "UPDATE models.attempts SET status=%s,dispatch=CASE WHEN %s='unknown' "
                "THEN 'unknown' ELSE dispatch END,finished_at=now(),usage=%s,result=%s,error_code=%s,"
                "provider_handle=COALESCE(%s,provider_handle),charged_units=COALESCE(%s,charged_units),"
                "charged_tokens=COALESCE(%s,charged_tokens),revision=revision+1 "
                "WHERE attempt_id=%s RETURNING *",
                (status, status, Jsonb(wire(usage)) if usage is not None else None,
                 Jsonb(wire(result)) if result is not None else None, error_code, provider_handle,
                 Jsonb(dict(charged_units)) if charged_units is not None else None,
                 charged_units.get("tokens") if charged_units else None, attempt_id))
            return await cursor.fetchone()

    async def request_cancel(self, attempt_id):
        async with self.database.transaction(control=True) as conn:
            await conn.execute("INSERT INTO models.cancellations(attempt_id) VALUES(%s) "
                               "ON CONFLICT DO NOTHING", (attempt_id,))
            cursor = await conn.execute(
                "UPDATE models.attempts SET cancel_requested=true,revision=revision+1 "
                "WHERE attempt_id=%s RETURNING *", (attempt_id,))
            return await cursor.fetchone()

    async def recover(self):
        async with self.database.transaction(control=True) as conn:
            await conn.execute("UPDATE models.attempts SET status='cancelled',finished_at=now(),"
                "error_code='broker_restarted_before_start' WHERE status='queued'")
            await conn.execute("UPDATE models.attempts SET status='unknown',dispatch='unknown',"
                "finished_at=now(),error_code='broker_restarted_after_submit' WHERE status='running'")
            cursor = await conn.execute(
                "SELECT * FROM models.attempts WHERE started_at>now()-interval '60 seconds' "
                "OR (status='unknown' AND hold_until>now())")
            return await cursor.fetchall()


async def purge_source_results(connection, source_ref):
    """Source owner calls this in its forgetting transaction, through the models port."""
    await connection.execute("UPDATE models.attempts SET result=NULL,revision=revision+1 "
        "WHERE source_refs @> %s", (Jsonb([{"source_id": source_ref.source_id,
                                            "revision": source_ref.revision}]),))
