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
from .codec import document, receipt, signature, source_refs, artifacts
from .types import ExpressionPlan


class ExpressionService:
    def __init__(self, database, jobs, identity, channels, contacts, *, sources):
        self.database, self.jobs, self.identity = database, jobs, identity
        self.channels, self.contacts = channels, contacts
        self.sources = sources

    async def send(self, context, plan: ExpressionPlan):
        if not context.delivery_allowed or context.actor_id != plan.actor_id or context.conversation_id != plan.target_ref:
            raise AppError("expression_target_mismatch", status=403)
        await self.identity.check(context, plan.source_refs)
        public = self._parts(plan.public_parts)
        if not public:
            raise AppError("empty_expression")
        plan = replace(plan, public_parts=public)
        fingerprint = signature({"target": plan.target_ref, "parts": public,
                                 "artifacts": plan.artifacts, "source_refs": plan.source_refs})
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
            result = receipt(await self.channels.deliver(context, plan))
        except (asyncio.CancelledError, Exception) as error:
            result = self._unknown(plan.expression_id, getattr(error, "code", "delivery_outcome_unknown"))
            await self._finish(context, plan, result)
            if isinstance(error, asyncio.CancelledError):
                raise
            return result
        await self._finish(context, plan, result)
        return result

    async def _finish(self, context, plan, result):
        state = "delivered" if result.delivery == "delivered" else "waiting_delivery" if result.delivery=="pending" else "unknown" if result.delivery=="unknown" else "failed"
        async with self.database.transaction() as connection:
            material_valid = await self.sources.validate_persistence(connection,context,plan.source_refs)
            await connection.execute("UPDATE conversation.expressions SET state=%s,receipt=%s,revision=revision+1,completed_at=now() WHERE expression_id=%s AND state IN ('sending','waiting_delivery','unknown') AND (state<>%s OR receipt IS DISTINCT FROM %s)", (state, document(result), plan.expression_id,state,document(result)))
            if state == "delivered" and material_valid:
                await connection.execute(
                    "INSERT INTO conversation.messages(actor_id,conversation_id,speaker,parts,source_refs,scope_ids,turn_id,expression_id) "
                    "VALUES (%s,%s,'assistant',%s,%s,%s,%s,%s) ON CONFLICT(expression_id) DO NOTHING",
                    (context.actor_id, context.conversation_id, document(plan.public_parts), document(plan.source_refs),
                     list(context.scope_ids), plan.turn_id, plan.expression_id))
        if material_valid:
            await self.contacts.record_delivery(context, plan, result)

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
        if current["state"] not in {"unknown","waiting_delivery","sending"}:
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

    async def channel_result(self, context, expression_id, result):
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute("SELECT * FROM conversation.expressions WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s",(expression_id,context.actor_id,context.conversation_id))).fetchone()
        if not row:
            return False
        plan=ExpressionPlan(expression_id,context.actor_id,context.conversation_id,tuple(row["public_parts"]),source_refs(row["source_refs"]),context.origin_kind,row["turn_id"],artifacts(row["artifacts"]),row["candidate_id"])
        await self._finish(context,plan,result)
        async with self.database.transaction() as connection:
            state="completed" if result.delivery=="delivered" else "waiting_expression" if result.delivery=="pending" else "unknown" if result.delivery=="unknown" else "failed"
            await connection.execute("UPDATE conversation.turns SET state=%s,error_code=%s,completed_at=now() WHERE expression_id=%s AND state IN ('running','waiting_expression','unknown')",(state,result.error_code,expression_id))
        return True

    async def expire(self, expression_id):
        async with self.database.transaction() as connection:
            await connection.execute("UPDATE conversation.expressions SET state='unknown',revision=revision+1,receipt=%s WHERE expression_id=%s AND state IN ('sending','waiting_delivery') AND lease_until<=now()", (document(self._unknown(expression_id, "delivery_lease_expired")), expression_id))

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
