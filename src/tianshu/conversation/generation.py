"""Frozen requests for authored output and autonomous decisions.

This owns the decision to submit once. Models remains the sole execution/result
ledger. A claimed request is only observed after interruption, never resubmitted.
"""
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from tianshu.models.codec import request_from_wire
from tianshu.runtime.errors import AppError
from .codec import document, signature, source_refs


def attempt_for(material_key):
    return str(uuid5(NAMESPACE_URL, "tianshu-generation:" + material_key))


class GenerationRequests:
    def __init__(self, database, sources, models):
        self.database, self.sources, self.models = database, sources, models

    async def run(self, material_key, request, *, guard=None):
        """Persist before submission; repeated calls only observe the original ID."""
        context = request.context
        async with self.database.transaction() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("generation:"+material_key,))
            original = await (await connection.execute(
                "SELECT * FROM conversation.generation_requests WHERE material_key=%s", (material_key,))).fetchone()
            refs = source_refs(original["source_refs"]) if original else request.source_refs
            if not await self.sources.validate_persistence(connection, context, refs):
                raise AppError("source_unavailable", status=409)
            if guard is not None and not await guard(connection):
                raise AppError("generation_no_longer_current", status=409)
            await connection.execute(
                "INSERT INTO conversation.generation_requests(material_key,attempt_id,context_key,request,source_refs) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(material_key) DO NOTHING",
                (material_key, request.attempt_id, self.context_key(context), document(request), document(request.source_refs)))
            row = await (await connection.execute(
                "SELECT * FROM conversation.generation_requests WHERE material_key=%s FOR UPDATE", (material_key,))).fetchone()
            if row["context_key"] != self.context_key(context):
                raise AppError("generation_context_changed", status=409)
            if row["state"] == "invalidated":
                raise AppError("source_unavailable", status=409)
            submit = row["state"] == "prepared"
            if submit:
                await connection.execute(
                    "UPDATE conversation.generation_requests SET state='claimed',claimed_at=now() WHERE material_key=%s", (material_key,))
        if not submit:
            return await self.models.result(row["attempt_id"], context=context)
        frozen = request_from_wire(row["request"])
        if frozen.budget.deadline_at <= datetime.now(UTC):
            # No provider submission occurred. An explicit new intent may start a
            # new generation; automatic recovery cannot silently renew a budget.
            raise AppError("generation_request_expired", status=409)
        return await self.models.generate(frozen)

    async def read(self, attempt_id, context):
        return await self.models.result(attempt_id, context=context)

    async def request(self, attempt_id, context):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM conversation.generation_requests WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not row:
            return None
        if row["context_key"] != self.context_key(context):
            raise AppError("generation_context_changed", status=409)
        if row["state"] == "invalidated":
            raise AppError("source_unavailable", status=409)
        return request_from_wire(row["request"])

    @staticmethod
    def context_key(context):
        return signature({"actor": context.actor_id, "person": context.person_id,
            "conversation": context.conversation_id, "principal": context.principal_id,
            "scopes": sorted(context.scope_ids), "audience": sorted(context.audience),
            "epoch": context.access_epoch})
