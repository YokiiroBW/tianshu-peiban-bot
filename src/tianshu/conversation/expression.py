"""The only owner of public part ordering and delivery identity."""
from __future__ import annotations
from tianshu.sources.dependencies import unique_refs

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import Receipt, ServiceClass
from .codec import document, receipt, signature, source_refs, artifacts, context_from_record
from .types import ExpressionPlan
from .recovery import can_retry_resource_wait, is_resource_wait, raise_resource_wait


def delivery_state(result: Receipt) -> str:
    """An accepted channel action has not made a delivery decision yet."""
    if result.delivery == "delivered":
        return "delivered"
    if "unknown" in {result.execution, result.effect, result.delivery}:
        return "unknown"
    if result.execution in {"pending", "running"} or result.delivery == "pending":
        return "waiting_delivery"
    return "failed"


def expression_turn_state(state: str) -> str:
    if state == "delivered":
        return "completed"
    if state == "cancelled":
        return "cancelled"
    if state in {"waiting_render", "ready", "waiting_order", "delivering", "waiting_delivery", "sending"}:
        return "waiting_expression"
    if state in {"render_unknown", "delivery_unknown", "unknown"}:
        return "unknown"
    return "failed"


def receipt_advances(previous: Receipt | None, result: Receipt) -> bool:
    if previous is None:
        return True
    for key in ("action_id", "attempt_id"):
        old, new = getattr(previous, key), getattr(result, key)
        if old is not None and new is not None and old != new:
            raise AppError("expression_receipt_identity_conflict", status=409)
        if old is not None and new is None:
            return False
    prior_state, next_state = delivery_state(previous), delivery_state(result)
    if prior_state == "delivered":
        return False
    if prior_state == "unknown" and next_state == "waiting_delivery":
        return False
    if prior_state == "failed" and next_state in {"waiting_delivery", "unknown"}:
        return False
    return previous != result


class ExpressionService:
    def __init__(self, database, jobs, identity, channels, contacts, *, sources):
        self.database, self.jobs, self.identity = database, jobs, identity
        self.channels, self.contacts = channels, contacts
        self.sources = sources
        self.statistics = None

    def bind_statistics(self, statistics):
        self.statistics = statistics

    async def send(self, context, plan: ExpressionPlan):
        if not context.delivery_allowed or context.actor_id != plan.actor_id or context.conversation_id != plan.target_ref:
            raise AppError("expression_target_mismatch", status=403)
        await self.identity.check(context, plan.source_refs)
        public = self._parts(plan.public_parts)
        if not public:
            raise AppError("empty_expression")
        plan = replace(plan, public_parts=public)
        fingerprint = signature({"target": plan.target_ref, "parts": public,
                                 "artifacts": plan.artifacts, "source_refs": plan.source_refs,
                                 "behavior_refs": plan.behavior_refs})
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,plan.source_refs):
                raise AppError("source_unavailable",status=409)
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (plan.expression_id,))
            old = await (await connection.execute("SELECT * FROM conversation.expressions WHERE expression_id=%s FOR UPDATE", (plan.expression_id,))).fetchone()
            if old:
                if old["actor_id"] != context.actor_id or old["conversation_id"] != context.conversation_id:
                    raise AppError("expression_not_found", status=404)
                if old["fingerprint"] != fingerprint:
                    raise AppError("expression_identity_conflict", status=409)
                if old["state"] != "prepared":
                    # Sending may have crossed the process boundary before a crash.
                    existing = receipt(old["receipt"]) if old["receipt"] else self._unknown(plan.expression_id)
                    return existing
            else:
                await connection.execute(
                    "INSERT INTO conversation.expressions(expression_id,actor_id,conversation_id,turn_id,target_ref,context,"
                    "public_parts,source_refs,artifacts,state,fingerprint,candidate_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'prepared',%s,%s)",
                    (plan.expression_id, context.actor_id, context.conversation_id, plan.turn_id, plan.target_ref,
                     document(context), document(public), document(plan.source_refs), document(plan.artifacts), fingerprint, plan.candidate_id))
            lease = datetime.now(UTC)+timedelta(minutes=20)
            await connection.execute("UPDATE conversation.expressions SET state='sending',lease_until=%s WHERE expression_id=%s", (lease, plan.expression_id))
            await self.jobs.enqueue(connection, JobSpec("conversation.expression_expire", f"expression-lease:{plan.expression_id}",
                {"expression_id": plan.expression_id}, ServiceClass.INTERACTIVE, lease, context.actor_id))
        try:
            await self.identity.check(context, plan.source_refs)
        except Exception as error:
            if is_resource_wait(error):
                if can_retry_resource_wait(error):
                    # No call to Channels has begun. Release only this preflight
                    # claim so the same expression can resume after the wait.
                    async with self.database.transaction() as connection:
                        await connection.execute("UPDATE conversation.expressions SET state='prepared',lease_until=NULL "
                            "WHERE expression_id=%s AND state='sending' AND receipt IS NULL", (plan.expression_id,))
                raise
            result = self._unknown(plan.expression_id, getattr(error, "code", "delivery_outcome_unknown"))
            return await self._finish(context, plan, result)
        try:
            result = receipt(await self.channels.deliver(context, plan))
        except (asyncio.CancelledError, Exception) as error:
            raise_resource_wait(error)
            result = self._unknown(plan.expression_id, getattr(error, "code", "delivery_outcome_unknown"))
            result = await self._finish(context, plan, result)
            if isinstance(error, asyncio.CancelledError):
                raise
            return result
        return await self._finish(context, plan, result)

    async def _finish(self, context, plan, result):
        async with self.database.transaction() as connection:
            material_valid = await self.sources.validate_persistence(connection,context,plan.source_refs)
            current = await (await connection.execute(
                "SELECT state,receipt,revision,created_at FROM conversation.expressions WHERE expression_id=%s "
                "AND actor_id=%s AND conversation_id=%s FOR UPDATE",
                (plan.expression_id, context.actor_id, context.conversation_id))).fetchone()
            if not current:
                raise AppError("expression_not_found", status=404)
            previous = receipt(current["receipt"])
            if previous and result.action_id in {None, previous.action_id}:
                result = replace(
                    result, action_id=result.action_id or previous.action_id,
                    attempt_id=result.attempt_id or previous.attempt_id,
                )
            if not receipt_advances(previous, result):
                return previous
            state = delivery_state(result)
            observed_at = datetime.now(UTC)
            await connection.execute(
                "UPDATE conversation.expressions SET state=%s,receipt=%s,revision=revision+1,"
                "result_observed_at=%s,completed_at=CASE WHEN %s='waiting_delivery' THEN NULL ELSE %s END WHERE expression_id=%s",
                (state, document(result), observed_at, state, observed_at, plan.expression_id))
            from .observability import delivery_on
            recorded = await delivery_on(self.statistics, connection, expression_id=plan.expression_id,
                                         actor_id=context.actor_id, revision=current["revision"]+1,
                                         occurred_at=observed_at, value=result)
            if self.statistics is not None and not recorded:
                from .observability import schedule_repair_on
                await schedule_repair_on(self.jobs, connection)
            if state == "delivered" and material_valid:
                await connection.execute(
                    "INSERT INTO conversation.messages(actor_id,conversation_id,speaker,parts,source_refs,scope_ids,turn_id,expression_id) "
                    "VALUES (%s,%s,'assistant',%s,%s,%s,%s,%s) ON CONFLICT(expression_id) DO NOTHING",
                    (context.actor_id, context.conversation_id, document(plan.public_parts), document(plan.source_refs),
                     list(context.scope_ids), plan.turn_id, plan.expression_id))
        if material_valid:
            await self.contacts.record_delivery(context, plan, result)
        return result

    async def present(self, context, expression_id, artifacts, public_parts):
        """An explicit capability presentation request uses the same durable expression."""
        refs = unique_refs(tuple(ref for item in artifacts for ref in item.source_refs))
        return await self.send(context, ExpressionPlan(expression_id, context.actor_id, context.conversation_id,
            tuple(public_parts), refs, context.origin_kind, artifacts=tuple(artifacts)))

    async def get(self, context, expression_id):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.expressions WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s", (expression_id, context.actor_id, context.conversation_id))).fetchone()
        if not row:
            raise AppError("expression_not_found", status=404)
        await self.identity.check(context, source_refs(row["source_refs"]))
        return {"expression_id": expression_id, "state": row["state"], "public_parts": row["public_parts"],
                "artifacts": row["artifacts"], "receipt": row["receipt"], "revision": row["revision"]}

    async def reconcile(self, context, expression_id, expected_revision):
        current = await self.get(context, expression_id)
        if expected_revision != current["revision"]:
            raise AppError("revision_conflict", status=409, details={"current_revision": current["revision"]})
        pending_failure = current["state"] == "failed" and current["receipt"] and delivery_state(receipt(current["receipt"])) in {"waiting_delivery", "unknown"}
        if current["state"] not in {"unknown","waiting_delivery","sending"} and not pending_failure:
            return current
        actual = await self.channels.reconcile(context, expression_id)
        if actual is None:
            return current
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.expressions WHERE expression_id=%s", (expression_id,))).fetchone()
        plan = ExpressionPlan(expression_id, context.actor_id, context.conversation_id, tuple(row["public_parts"]),
            source_refs(row["source_refs"]), context.origin_kind, row["turn_id"], artifacts(row["artifacts"]), row["candidate_id"])
        await self.identity.check(context, plan.source_refs)
        await self._finish(context, plan, receipt(actual))
        return await self.get(context, expression_id)

    async def channel_result(self, context, expression_id, result) -> Receipt | None:
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expressions WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s",(expression_id,context.actor_id,context.conversation_id))).fetchone()
        if not row:
            return None
        plan=ExpressionPlan(expression_id,context.actor_id,context.conversation_id,tuple(row["public_parts"]),source_refs(row["source_refs"]),context.origin_kind,row["turn_id"],artifacts(row["artifacts"]),row["candidate_id"])
        result = await self._finish(context,plan,result)
        async with self.database.transaction() as connection:
            state = expression_turn_state(delivery_state(result))
            await connection.execute(
                "UPDATE conversation.turns SET state=%s,error_code=%s,revision=revision+1,"
                "completed_at=CASE WHEN %s='waiting_expression' THEN NULL ELSE now() END "
                "WHERE actor_id=%s AND conversation_id=%s AND expression_id=%s "
                "AND (state IN ('running','waiting_expression','unknown') "
                "OR (state='failed' AND error_code IS NULL AND %s='completed')) "
                "AND (state IS DISTINCT FROM %s OR error_code IS DISTINCT FROM %s)",
                (state, result.error_code, state, context.actor_id, context.conversation_id,
                 expression_id, state, state, result.error_code))
        return result

    async def expire(self, expression_id):
        # A channel Action may still be waiting for its execution owner. This
        # lease observes the original send ledger; it never resubmits a plan.
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM conversation.expressions WHERE expression_id=%s "
                "AND state IN ('sending','waiting_delivery') AND lease_until<=now()",
                (expression_id,))).fetchone()
        if row is None:
            return None
        context = context_from_record(row["context"])
        result = None
        try:
            observed = receipt(await self.channels.reconcile(context, expression_id))
        except Exception as error:
            raise_resource_wait(error)
            pending = True
        else:
            if observed is None:
                previous = receipt(row["receipt"])
                observation = self._unknown(expression_id, "delivery_lease_expired")
                observed = replace(
                    previous, receipt_id=observation.receipt_id, execution="unknown",
                    effect="unknown", delivery="unknown", error_code=observation.error_code,
                ) if previous else observation
            result = await self.channel_result(context, expression_id, observed)
            pending = result is not None and delivery_state(result) == "waiting_delivery"
        if not pending:
            return result
        async with self.database.transaction() as connection:
            renewed = await (await connection.execute(
                "UPDATE conversation.expressions SET lease_until=now()+interval '1 minute' WHERE expression_id=%s "
                "AND state IN ('sending','waiting_delivery') AND lease_until<=now() RETURNING actor_id,lease_until",
                (expression_id,))).fetchone()
            if renewed:
                await self.jobs.enqueue(connection, JobSpec("conversation.expression_expire",
                    f"expression-observe:{expression_id}:{uuid4()}", {"expression_id": expression_id},
                    ServiceClass.INTERACTIVE, renewed["lease_until"], renewed["actor_id"]))
        return result

    async def recover(self):
        async with self.database.transaction() as connection:
            rows = await (await connection.execute("SELECT expression_id,actor_id,lease_until FROM conversation.expressions WHERE state IN ('sending','waiting_delivery')")).fetchall()
            for row in rows:
                await self.jobs.enqueue(connection, JobSpec("conversation.expression_expire", f"expression-recover:{row['expression_id']}:{uuid4()}",
                    {"expression_id": row["expression_id"]}, ServiceClass.INTERACTIVE, row["lease_until"], row["actor_id"]))

    @staticmethod
    def _parts(parts):
        allowed = {"text", "image", "audio", "video", "file", "reaction", "reference", "forward", "reply",
                   "mention", "mention_all", "channel_segment"}
        output = []
        for part in parts:
            if not isinstance(part, dict) or part.get("kind") not in allowed:
                raise AppError("invalid_public_part")
            if part.get("native", {}).get("thought") or part.get("visibility") == "private":
                raise AppError("private_part_in_expression")
            if part["kind"] == "text" and not isinstance(part.get("text"), str):
                raise AppError("invalid_public_text")
            output.append(part)
        return tuple(output)

    @staticmethod
    def _unknown(expression_id, reason="delivery_outcome_unknown"):
        return Receipt(str(uuid5(NAMESPACE_URL, "expression-receipt:" + expression_id)), None, None,
                       "unknown", "unknown", "unknown", error_code=reason)
