"""Frozen requests for authored output and autonomous decisions.

This owns the frozen intent. Models remains the sole execution/result ledger.
A known receipt is only observed. An unconfirmed claim may retry the same
frozen admission identity; Models owns its atomic attempt and dispatch ledger.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from tianshu.models.codec import request_from_wire
from tianshu.models.ledger import submission_digest
from tianshu.models.timing import waiting_budget
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from .codec import document, signature, source_refs
from .recovery import can_retry_resource_wait, is_resource_wait


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
        original = await (await connection.execute(
            "SELECT * FROM conversation.generation_requests WHERE material_key=%s", (material_key,))).fetchone()
        refs = source_refs(original["source_refs"]) if original else request.source_refs
        if not await self.sources.validate_persistence(connection, context, refs):
            raise AppError("source_unavailable", status=409)
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("generation:"+material_key,))
        current = await (await connection.execute(
            "SELECT * FROM conversation.generation_requests WHERE material_key=%s", (material_key,))).fetchone()
        if (current is None) != (original is None) or (current and current["source_refs"] != original["source_refs"]):
            raise RecoveryWait("source_lock_set_changed", datetime.now(UTC) + timedelta(seconds=5),
                               dependency="conversation:generation_sources")
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
        if row["state"] == "expired":
            raise AppError("generation_request_expired", status=409)
        if row["state"] == "invalidated" or row["request"] is None:
            raise AppError("source_unavailable", status=409)
        return request_from_wire(row["request"])

    async def run(self, material_key, request, *, guard=None, observer=None, stream=False):
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
        try:
            return await self._run_owned(material_key, request, guard=guard, stream=stream)
        except Exception as error:
            if is_resource_wait(error) and not can_retry_resource_wait(error):
                try:
                    async with self.database.transaction() as connection:
                        await connection.execute("UPDATE conversation.generation_requests SET recovery_unconfirmed=true "
                            "WHERE material_key=%s AND context_key=%s AND state IN ('prepared','claimed')",
                            (material_key, self.context_key(request.context)))
                except BaseException as observation_error:
                    raise error from observation_error
            raise

    async def _run_owned(self, material_key, request, *, guard=None, stream=False):
        context = request.context
        frozen, submit = await self.claim(material_key, request, guard=guard)
        if not submit:
            return await self.observe_result(frozen, context)
        if not stream:
            result = await self.models.generate(frozen)
            await self.confirm(frozen.attempt_id)
            return result
        # Partial text/native events are transport observations, never authored
        # output. Only the result persisted under this original attempt can be
        # returned to the domain for validation and its single atomic commit.
        received = False
        events = self.models.stream(frozen)
        try:
            async for event in events:
                received |= event.kind == "receipt"
        finally:
            await events.aclose()
        if not received:
            raise AppError("generation_stream_interrupted", status=502)
        await self.confirm(frozen.attempt_id)
        return await self.models.result(frozen.attempt_id, context=context)

    async def claim(self, material_key, request, *, guard=None):
        """Claim first admission once, after a pre-submit lookup outside the transaction."""
        async with self.database.transaction() as connection:
            frozen = await self.prepare(material_key, request, connection=connection, guard=guard)
            row = await (await connection.execute(
                "SELECT state,submission_confirmed,recovery_unconfirmed FROM conversation.generation_requests WHERE material_key=%s", (material_key,))).fetchone()
        if row["recovery_unconfirmed"]:
            await self.models.require_receipt(frozen.attempt_id)
            raise AppError("generation_recovery_unconfirmed", status=409,
                           details={"attempt_id": frozen.attempt_id, "action": "observe_original_attempt"})
        if row["state"] == "claimed":
            if row["submission_confirmed"]:
                original = await self.models.require_receipt(frozen.attempt_id)
                self.validate_observed(frozen, original)
                return frozen, False
            original = await self.models.status(frozen.attempt_id, phase="pre_submit")
            if original is not None:
                self.validate_observed(frozen, original)
                await self.confirm(frozen.attempt_id)
                return frozen, False
            admission = waiting_budget(frozen)
            if admission is not None and admission.deadline_at <= datetime.now(UTC):
                await self.models.require_receipt(frozen.attempt_id)
                return frozen, False
            # The claim can precede a crashed or delayed RPC. This is the same
            # frozen admission, not evidence that the earlier RPC was unsent.
            async with self.database.transaction() as connection:
                frozen = await self.prepare(material_key, frozen, connection=connection, guard=guard)
                current = await (await connection.execute(
                    "SELECT state,submission_confirmed,recovery_unconfirmed FROM conversation.generation_requests "
                    "WHERE material_key=%s FOR UPDATE", (material_key,))).fetchone()
                if current["recovery_unconfirmed"]:
                    raise AppError("generation_recovery_unconfirmed", status=409,
                                   details={"attempt_id": frozen.attempt_id, "action": "observe_original_attempt"})
                return frozen, (current["state"] == "claimed" and not current["submission_confirmed"]
                                and not current["recovery_unconfirmed"])
        if row["state"] != "prepared":
            return frozen, False
        original = await self.models.status(frozen.attempt_id, phase="pre_submit")
        if original is not None:
            self.validate_observed(frozen, original)
        admission = waiting_budget(frozen)
        expired = original is None and admission is not None and admission.deadline_at <= datetime.now(UTC)
        async with self.database.transaction() as connection:
            frozen = await self.prepare(material_key, frozen, connection=connection, guard=guard)
            row = await (await connection.execute(
                "SELECT state,submission_confirmed,recovery_unconfirmed FROM conversation.generation_requests WHERE material_key=%s FOR UPDATE", (material_key,))).fetchone()
            if row["recovery_unconfirmed"]:
                raise AppError("generation_recovery_unconfirmed", status=409,
                               details={"attempt_id": frozen.attempt_id, "action": "observe_original_attempt"})
            if row["state"] != "prepared":
                # Another owner may have committed the claim but not reached
                # Models yet. The same frozen admission is deduplicated there.
                return frozen, (row["state"] == "claimed" and not row["submission_confirmed"] and not row["recovery_unconfirmed"]
                                and original is None and not expired)
            if expired:
                await connection.execute("UPDATE conversation.generation_requests SET state='expired' WHERE material_key=%s", (material_key,))
            else:
                await connection.execute("UPDATE conversation.generation_requests SET state='claimed',claimed_at=now(),submission_confirmed=%s WHERE material_key=%s", (original is not None, material_key))
        if expired:
            raise AppError("generation_request_expired", status=409)
        return frozen, original is None

    async def observe_result(self, frozen, context):
        result = await self.models.result(frozen.attempt_id, context=context)
        if result is None:
            await self.models.require_receipt(frozen.attempt_id)
            result = await self.models.result(frozen.attempt_id, context=context)
            if result is None:
                raise AppError("model_result_unavailable", status=409, details={"attempt_id": frozen.attempt_id})
        self.validate_observed(frozen, result.receipt)
        await self.confirm(frozen.attempt_id)
        if result.receipt.status == "succeeded":
            return await self.models.reusable_result(frozen.attempt_id, context=context,
                                                     expected_request_digest=submission_digest(frozen),
                                                     expected_data_generation=frozen.data_generation)
        return result

    @staticmethod
    def validate_observed(frozen, original):
        if (original.attempt_id != frozen.attempt_id
                or (original.request_digest is not None and original.request_digest != submission_digest(frozen))
                or (frozen.data_generation is not None and original.data_generation is not None
                    and original.data_generation != frozen.data_generation)):
            raise AppError("attempt_identity_conflict", status=409, details={"attempt_id": frozen.attempt_id})

    async def confirm(self, attempt_id, *, connection=None):
        if connection is None:
            async with self.database.transaction() as transaction:
                await self.confirm(attempt_id, connection=transaction)
            return
        await connection.execute("UPDATE conversation.generation_requests SET submission_confirmed=true "
                                 "WHERE attempt_id=%s AND state='claimed'", (attempt_id,))

    async def read(self, attempt_id, context, *, observer=None):
        if observer is None:
            return await self.models.result(attempt_id, context=context)
        frozen = await self.request(attempt_id, context)
        if frozen is None:
            return None
        reader = self.observer_context(frozen.context, observer)
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
        reader = self.observer_context(frozen.context, observer)
        # Request text is as sensitive as the result. Validate before returning it,
        # including when no Models attempt was ever admitted.
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection, reader, frozen.source_refs):
                raise AppError("source_unavailable", status=409)
        return frozen

    @staticmethod
    def observer_context(original, current):
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


    @staticmethod
    def context_key(context):
        return signature({"actor": context.actor_id, "person": context.person_id,
            "conversation": context.conversation_id, "principal": context.principal_id,
            "scopes": sorted(context.scope_ids), "audience": sorted(context.audience),
            "epoch": context.access_epoch})
