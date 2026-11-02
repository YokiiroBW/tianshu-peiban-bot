"""Durable model attempts; an absent live task never proves absence of an external effect."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from .types import ModelReceipt, ModelUsage
from .timing import waiting_budget


def digest(value):
    return hashlib.sha256(json.dumps(wire(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def context_fingerprint(context):
    return digest({key: getattr(context, key) for key in
        ("actor_id", "person_id", "conversation_id", "scope_ids", "audience", "conversation_kind", "delivery_allowed")})


def receipt(row):
    usage = row.get("usage") or {}
    return ModelReceipt(
        row["attempt_id"], row["intent_id"], row["status"], row["dispatch"], row["route_id"],
        row["route_revision"], row["provider_id"], row["model"], row["protocol"],
        row["quota_group"], ModelUsage(**usage), row["provider_handle"], row["queued_at"],
        row["started_at"], row["finished_at"], row["first_output_ms"], row["error_code"], row.get("hold_until"), row.get("deadline_at"), row.get("execution_started_at"), row.get("cancellation_reason"))


class AttemptLedger:
    def __init__(self, database, instance_id):
        self.database, self.instance_id = database, instance_id

    async def create(self, request, plan, units):
        semantic = wire(request)
        semantic.pop("budget", None)
        if semantic.get("provider_session_scope") == "conversation":
            semantic.pop("provider_session_scope")
        if semantic.get("execution_start") == "admitted":
            semantic.pop("execution_start")
        if getattr(request, "execution_timeout_seconds", None) is None:
            semantic.pop("max_queue_wait", None)
        else:
            # Only the new contract canonicalizes unordered inputs. Existing
            # attempt digests retain their exact legacy representation.
            semantic["context"]["permissions"] = sorted(semantic["context"]["permissions"])
            if "required_capabilities" in semantic:
                semantic["required_capabilities"] = sorted(semantic["required_capabilities"])
        # New timing fields participate in a new attempt's identity. Omitting
        # absent fields preserves the digest of previously frozen attempts.
        for name in ("execution_timeout_seconds", "business_deadline_at"):
            if semantic.get(name) is None:
                semantic.pop(name, None)
        request_hash = digest(semantic)
        queued_budget = waiting_budget(request)
        async with self.database.transaction(queued_budget) as conn:
            cursor = await conn.execute(
                "INSERT INTO models.attempts(attempt_id,intent_id,principal_id,actor_id,"
                "conversation_id,function,service_class,route_id,route_revision,provider_id,"
                "model,protocol,quota_group,request_digest,status,dispatch,deadline_at,"
                "estimated_tokens,estimated_units,charged_units,broker_instance,billing_account,source_refs,context_fingerprint,persona_revision,"
                "execution_timeout_seconds,business_deadline_at,max_queue_wait,execution_start) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued','not_started',"
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",
                (request.attempt_id, request.intent_id, request.context.principal_id,
                 request.context.actor_id, request.context.conversation_id, request.function,
                 request.service_class.value, plan.route_id, plan.revision, plan.provider_id,
                 plan.model, plan.protocol, plan.quota_group, request_hash,
                 request.budget.deadline_at if getattr(request, "execution_timeout_seconds", None) is None else None,
                 units.get("tokens"), Jsonb(dict(units)),
                 Jsonb(dict(units)), self.instance_id, getattr(request, "billing_account", None),
                 Jsonb(wire(request.source_refs)), context_fingerprint(request.context), getattr(request, "persona_revision", None),
                 getattr(request, "execution_timeout_seconds", None), getattr(request, "business_deadline_at", None),
                 request.max_queue_wait, getattr(request, "execution_start", "admitted")))
            row = await cursor.fetchone()
            if row:
                cancelled = await (await conn.execute("SELECT reason FROM models.cancellations "
                    "WHERE attempt_id=%s", (request.attempt_id,))).fetchone()
                if cancelled:
                    row = await (await conn.execute("UPDATE models.attempts SET status='cancelled',"
                        "cancel_requested=true,finished_at=now(),error_code=%s "
                        "WHERE attempt_id=%s RETURNING *", (
                            "consumer_interrupted" if cancelled["reason"] == "consumer_interrupted" else "cancelled_before_start",
                            request.attempt_id))).fetchone()
                return await self._cancellation(conn, row), True
            cursor = await conn.execute("SELECT * FROM models.attempts WHERE attempt_id=%s",
                                        (request.attempt_id,))
            row = await cursor.fetchone()
            if row["request_digest"] != request_hash:
                raise AppError("attempt_identity_conflict", status=409)
            return await self._cancellation(conn, row), False

    async def get(self, attempt_id):
        async with self.database.transaction(control=True, read_only=True) as conn:
            cursor = await conn.execute("SELECT a.*,c.reason AS cancellation_reason FROM models.attempts a "
                                        "LEFT JOIN models.cancellations c USING(attempt_id) WHERE a.attempt_id=%s",
                                        (attempt_id,))
            return await cursor.fetchone()

    @staticmethod
    async def _cancellation(connection, row):
        if row is None:
            return None
        cancelled = await (await connection.execute("SELECT reason FROM models.cancellations WHERE attempt_id=%s",
            (row["attempt_id"],))).fetchone()
        return {**row, "cancellation_reason": cancelled["reason"] if cancelled else None}

    async def admitted(self, attempt_id, deadline_at):
        """External admission starts its execution window, without claiming I/O."""
        async with self.database.transaction(control=True) as conn:
            row = await (await conn.execute("UPDATE models.attempts SET deadline_at=%s,execution_started_at=now(),revision=revision+1 "
                "WHERE attempt_id=%s AND status='queued' AND NOT cancel_requested "
                "AND deadline_at IS NULL AND execution_timeout_seconds IS NOT NULL AND %s>now() "
                "AND NOT EXISTS(SELECT 1 FROM models.cancellations c "
                "WHERE c.attempt_id=models.attempts.attempt_id) RETURNING *",
                (deadline_at, attempt_id, deadline_at))).fetchone()
        if not row:
            raise AppError("attempt_cancelled_before_start", status=409)
        return row

    async def started(self, attempt_id, hold_seconds, *, deadline_at=None):
        async with self.database.transaction(control=True) as conn:
            cursor = await conn.execute(
                "UPDATE models.attempts SET status='running',dispatch='submitted',started_at=now(),"
                "execution_started_at=CASE WHEN execution_start='admitted' THEN COALESCE(execution_started_at,now()) ELSE execution_started_at END,"
                "deadline_at=COALESCE(%s,deadline_at),"
                "hold_until=GREATEST(COALESCE(%s,deadline_at),now()+(%s*interval '1 second')),"
                "charged_tokens=estimated_tokens,revision=revision+1 "
                "WHERE attempt_id=%s AND status='queued' AND NOT cancel_requested "
                "AND (COALESCE(%s,deadline_at)>now() OR (execution_start='provider_running' "
                "AND deadline_at IS NULL AND (business_deadline_at IS NULL OR business_deadline_at>now()))) "
                "AND NOT EXISTS(SELECT 1 FROM models.cancellations c "
                "WHERE c.attempt_id=models.attempts.attempt_id) RETURNING *",
                (deadline_at, deadline_at, hold_seconds, attempt_id, deadline_at))
            row = await cursor.fetchone()
            if not row:
                raise AppError("attempt_cancelled_before_start", status=409)
            return row

    async def observe_external(self, attempt_id, provider_handle, state, *, hold_seconds=None, validate_observation):
        async with self.database.transaction(control=True) as conn:
            # Preserve the Sources -> Models lock order used by withdrawal.
            if not await validate_observation(conn):
                raise AppError("source_unavailable", status=403)
            row = await (await conn.execute("SELECT * FROM models.attempts "
                "WHERE attempt_id=%s FOR UPDATE", (attempt_id,))).fetchone()
            if not row or row["protocol"] != "external":
                raise AppError("external_attempt_not_found", status=404)
            if row["provider_handle"] != provider_handle or not row["started_at"] or row["dispatch"] == "not_started":
                raise AppError("external_provider_handle_changed", status=409)
            if row["execution_start"] != "provider_running":
                return await self._cancellation(conn, row)
            # Use the execution owner's clock, after lock waiting has finished.
            # PostgreSQL now() is its transaction-start clock and can also live
            # on another host from the Broker/Client timeout machinery.
            observed_at = datetime.now(UTC)
            started, deadline, hold = row["execution_started_at"], row["deadline_at"], row["hold_until"]
            if started is None and state in {"running", "completed"}:
                started = observed_at
                deadline = started + timedelta(seconds=row["execution_timeout_seconds"])
                if row["business_deadline_at"] is not None:
                    deadline = min(deadline, row["business_deadline_at"])
                if hold_seconds is not None and row["status"] == "running":
                    hold = max(value for value in (hold, deadline, started + timedelta(seconds=hold_seconds))
                        if value is not None)
            updated = await (await conn.execute("UPDATE models.attempts SET provider_state=%s,provider_observed_at=%s,"
                "execution_started_at=%s,deadline_at=%s,hold_until=%s,revision=revision+1 WHERE attempt_id=%s RETURNING *",
                (state, observed_at, started, deadline, hold, attempt_id))).fetchone()
            return await self._cancellation(conn, updated)

    async def checkpoint(self, attempt_id, *, provider_handle=None, first_output_ms=None):
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "UPDATE models.attempts SET output_observed=true,provider_handle=COALESCE(%s,"
                "provider_handle),first_output_ms=COALESCE(first_output_ms,%s),revision=revision+1 "
                "WHERE attempt_id=%s", (provider_handle, first_output_ms, attempt_id))

    async def finish(self, attempt_id, status, *, usage=None, result=None, error_code=None,
                     charged_units=None, provider_handle=None, validate_result=None):
        async with self.database.transaction(control=True) as conn:
            if result is not None and validate_result is not None and not await validate_result(conn):
                result = None
                error_code = error_code or "result_source_unavailable"
            cursor = await conn.execute(
                "UPDATE models.attempts SET status=%s,dispatch=CASE WHEN %s='unknown' "
                "THEN 'unknown' ELSE dispatch END,finished_at=now(),usage=%s,result=%s,error_code=%s,"
                "provider_handle=COALESCE(%s,provider_handle),charged_units=COALESCE(%s,charged_units),"
                "charged_tokens=COALESCE(%s,charged_tokens),revision=revision+1 "
                "WHERE attempt_id=%s AND status NOT IN ('succeeded','failed','cancelled') RETURNING *",
                (status, status, Jsonb(wire(usage)) if usage is not None else None,
                 Jsonb(wire(result)) if result is not None else None, error_code, provider_handle,
                 Jsonb(dict(charged_units)) if charged_units is not None else None,
                 charged_units.get("tokens") if charged_units else None, attempt_id))
            row = await cursor.fetchone()
            if row is None:
                row = await (await conn.execute("SELECT * FROM models.attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
            return await self._cancellation(conn, row)

    async def request_cancel(self, attempt_id, *, reason="explicit_cancel"):
        if reason not in {"explicit_cancel", "consumer_interrupted"}:
            raise AppError("invalid_model_cancellation_reason")
        async with self.database.transaction(control=True) as conn:
            await conn.execute("INSERT INTO models.cancellations AS c(attempt_id,reason) VALUES(%s,%s) "
                "ON CONFLICT(attempt_id) DO UPDATE SET reason=CASE WHEN c.reason='explicit_cancel' "
                "THEN c.reason ELSE excluded.reason END", (attempt_id, reason))
            cursor = await conn.execute(
                "UPDATE models.attempts SET cancel_requested=true,revision=revision+1 "
                "WHERE attempt_id=%s RETURNING *", (attempt_id,))
            return await self._cancellation(conn, await cursor.fetchone())

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
    await purge_source_results_many(connection, (source_ref,))


async def purge_source_results_many(connection, source_refs):
    if not source_refs:
        return
    rows = await (await connection.execute("SELECT a.attempt_id FROM models.attempts a WHERE a.attempt_id IN ("
        "SELECT c.attempt_id FROM unnest(%s::text[],%s::bigint[]) s(source_id,revision) JOIN models.attempts c "
        "ON c.source_refs @> jsonb_build_array(jsonb_build_object('source_id',s.source_id,'revision',s.revision))) "
        "ORDER BY a.attempt_id FOR UPDATE", ([ref.source_id for ref in source_refs],
                                            [ref.revision for ref in source_refs]))).fetchall()
    if rows:
        await connection.execute("UPDATE models.attempts SET result=NULL,revision=revision+1 WHERE attempt_id=ANY(%s)",
                                  ([row["attempt_id"] for row in rows],))
