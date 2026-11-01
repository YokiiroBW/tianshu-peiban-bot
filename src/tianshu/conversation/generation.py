"""Frozen requests for authored output and autonomous decisions.

This owns the frozen intent. Models remains the sole execution/result ledger.
A registered attempt is only observed; authoritative absence permits original-ID
first admission before the original deadline.
"""
from dataclasses import replace
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

    async def prepare(self, material_key, request, *, connection=None, guard=None):
        """Freeze an intent with its domain pointer, without claiming or submitting it."""
        if connection is None:
            async with self.database.transaction() as transaction:
                return await self.prepare(material_key, request, connection=transaction, guard=guard)
        context = request.context
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
        if row["attempt_id"] != request.attempt_id:
            raise AppError("generation_identity_conflict", status=409)
        if row["state"] == "expired":
            raise AppError("generation_request_expired", status=409)
        if row["state"] == "invalidated" or row["request"] is None:
            raise AppError("source_unavailable", status=409)
        return request_from_wire(row["request"])

    async def run(self, material_key, request, *, guard=None, observer=None):
        """Persist before submission; repeated calls preserve the original ID and budget."""
        if observer is not None:
            # A current observer can never claim, extend or submit an old intent.
            frozen = await self.observe_request(request.attempt_id, observer=observer)
            if frozen is None or frozen.intent_id != request.intent_id:
                raise AppError("generation_request_not_found", status=404)
            async with self.database.transaction(read_only=True) as connection:
                row = await (await connection.execute(
                    "SELECT material_key FROM conversation.generation_requests WHERE attempt_id=%s",
                    (request.attempt_id,))).fetchone()
            if row["material_key"] != material_key:
                raise AppError("generation_identity_conflict", status=409)
            return await self.read(request.attempt_id, request.context, observer=observer)
        context = request.context
        async with self.database.transaction() as connection:
            await self.prepare(material_key, request, connection=connection, guard=guard)
            row = await (await connection.execute(
                "SELECT * FROM conversation.generation_requests WHERE material_key=%s FOR UPDATE", (material_key,))).fetchone()
            submit = row["state"] == "prepared"
            if submit:
                await connection.execute(
                    "UPDATE conversation.generation_requests SET state='claimed',claimed_at=now() WHERE material_key=%s", (material_key,))
        frozen = request_from_wire(row["request"])
        if not submit:
            result = await self.models.result(row["attempt_id"], context=context)
            if result is not None:
                return result
            # A successful None is the Broker's authoritative absence result.
            # Same frozen payload/attempt admission is atomic even if an original
            # RPC is still preparing or arrives late. Observation errors propagate.
        if frozen.budget.deadline_at <= datetime.now(UTC):
            actual = await self.models.result(row["attempt_id"], context=context)
            if actual is not None:
                return actual
            async with self.database.transaction() as connection:
                await connection.execute("UPDATE conversation.generation_requests SET state='expired' WHERE material_key=%s AND state='claimed'",(material_key,))
            raise AppError("generation_request_expired", status=409)
        return await self.models.generate(frozen)

    async def read(self, attempt_id, context, *, observer=None):
        if observer is None:
            return await self.models.result(attempt_id, context=context)
        frozen = await self.request(attempt_id, context)
        if frozen is None:
            return None
        reader = self._observer(frozen.context, observer)
        # Models rechecks current Identity and the original source closure. Its
        # fingerprint remains the original semantic scene; epoch is not identity.
        return await self.models.result(attempt_id, context=reader)

    async def observe_request(self, attempt_id, *, observer):
        """Read an immutable request under current authorization of its same scene."""
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM conversation.generation_requests WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if row is None:
            return None
        if row["state"] == "invalidated" or row["request"] is None:
            raise AppError("source_unavailable", status=409)
        frozen = request_from_wire(row["request"])
        reader = self._observer(frozen.context, observer)
        # Request text is as sensitive as the result. Validate before returning it,
        # including when no Models attempt was ever admitted.
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection, reader, frozen.source_refs):
                raise AppError("source_unavailable", status=409)
        return frozen

    @staticmethod
    def _observer(original, current):
        fields = ("principal_id", "actor_id", "person_id", "conversation_id",
                  "conversation_kind", "delivery_allowed")
        if (any(getattr(original, key) != getattr(current, key) for key in fields)
                or set(original.scope_ids) != set(current.scope_ids)
                or set(original.audience) != set(current.audience)):
            raise AppError("generation_observation_context_mismatch", status=403)
        permissions = (current.permissions if "*" in original.permissions else original.permissions
                       if "*" in current.permissions else original.permissions & current.permissions)
        return replace(original, access_epoch=current.access_epoch, permissions=permissions,
                       origin_kind=current.origin_kind)

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

    async def pending_materials(self, context, *, prefix, after=None, limit=128):
        """Bounded original-intent inventory for a domain's one-time recovery.

        A claimed intent does not imply any execution result. Consumers observe
        Models with each original request's context and original attempt ID.
        Actor, principal and the entire authorized scope set must still match;
        person/conversation may differ from the latest domain checkpoint.
        """
        limit = min(128, max(1, int(limit)))
        scopes = document(sorted(context.scope_ids))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT material_key,attempt_id,request,state,created_at FROM conversation.generation_requests "
                "WHERE left(material_key,length(%s))=%s AND (%s::text IS NULL OR material_key>%s) "
                "AND (((request->'context'->>'principal_id')=%s AND (request->'context'->>'actor_id')=%s "
                "AND (request->'context'->'scope_ids') @> %s::jsonb "
                "AND %s::jsonb @> (request->'context'->'scope_ids')) "
                "OR (request IS NULL AND context_key=%s)) ORDER BY material_key LIMIT %s",
                (prefix, prefix, after, after, context.principal_id, context.actor_id,
                 scopes, scopes, self.context_key(context), limit))).fetchall()
        return tuple({**row, "request": request_from_wire(row["request"]) if row["request"] is not None else None}
                     for row in rows)

    @staticmethod
    def context_key(context):
        return signature({"actor": context.actor_id, "person": context.person_id,
            "conversation": context.conversation_id, "principal": context.principal_id,
            "scopes": sorted(context.scope_ids), "audience": sorted(context.audience),
            "epoch": context.access_epoch})
