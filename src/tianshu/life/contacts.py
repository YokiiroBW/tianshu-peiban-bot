from datetime import UTC, datetime
from tianshu.conversation.codec import document
from .salience import fingerprint


DEFAULT_EXPIRY_SECONDS = 4 * 60 * 60


class Contacts:
    def __init__(self, database):
        self.database = database
        self.on_received = None
        self.identity = None

    def bind_authority(self,identity):
        self.identity=identity

    async def resolve_target(self, context, refs=(), *, identity):
        """A global default is not a recipient list. Only explicit people or this scene."""
        from tianshu.runtime.errors import AppError
        if context.conversation_id and context.delivery_allowed:
            if (await self.preferences(context))["enabled"]:
                await identity.check(context, refs)
                return context
        async with self.database.transaction(read_only=True) as connection:
            configured = await (await connection.execute(
                "SELECT v.conversation_id,p.person_id FROM life.contact_preferences p "
                "JOIN identity.conversations v ON v.actor_id=p.actor_id AND v.person_id=p.person_id "
                "WHERE p.actor_id=%s AND p.person_id<>'' AND p.enabled AND v.kind='private' "
                "ORDER BY p.person_id,v.conversation_id",
                (context.actor_id,))).fetchall()
        # The saved actor routine deliberately has no private recipient scope.
        # Selecting an explicitly opted-in destination is a separate Identity
        # authorization, not permission to read its scene in the planner.
        await identity.check(context, refs)
        for row in configured:
            try:
                selected = await identity.authorize_conversation(context, row["conversation_id"])
                if not selected.delivery_allowed or selected.person_id != row["person_id"]:
                    continue
                await identity.check(selected, refs)
                if (await self.preferences(selected))["enabled"]:
                    return selected
            except AppError as error:
                if error.status not in {401, 403, 404, 409, 410}:
                    raise
        return None

    async def preferences(self, context, *, group=False, connection=None):
        person_id = "" if group else context.person_id or ""
        async with self.database.transaction(read_only=True, connection=connection) as connection:
            row = await (await connection.execute(
                "SELECT * FROM life.contact_preferences WHERE actor_id=%s AND person_id IN (%s,'') "
                "AND scope_ids<@%s::text[] ORDER BY (person_id=%s) DESC LIMIT 1",
                (context.actor_id, person_id, list(context.scope_ids), person_id))).fetchone()
        if row:
            exact=row["person_id"]==person_id
            from tianshu.conversation.codec import source_refs
            refs=source_refs(row["availability_source_refs"])
            readable=not refs or self.identity is not None and (await self.identity.check_many(context,(refs,)))[0]
            if not readable or row["available_until"] and row["available_until"]<=datetime.now(UTC):
                row={**row,"availability":"unknown"}
            return {**row,"enabled":row["enabled"] if exact or group else False,
                    "edit_revision":row["revision"] if exact else None,"inherited":not exact}
        return {"revision": 0,"edit_revision":None,"inherited":False, "timezone": "Asia/Shanghai", "quiet_start": "23:00", "quiet_end": "08:00",
                       "cooldown_seconds": 1800, "daily_quota": 3, "unanswered_limit": 3,
                       "expiry_seconds": DEFAULT_EXPIRY_SECONDS,
                       # Group participation has its own explicit per-group switch.
                       # Its existing shared timing budget must survive private opt-out.
                       "responsiveness": .5, "enabled": group, "availability": "unknown", "available_until": None}

    async def history(self, context, *, conversation_only=False):
        async with self.database.transaction(read_only=True) as connection:
            return await (await connection.execute(
                "SELECT * FROM life.contacts WHERE actor_id=%s AND "
                + ("conversation_id=%s " if conversation_only else "person_id IS NOT DISTINCT FROM %s ")
                + "ORDER BY delivered_at DESC NULLS LAST LIMIT 64",
                (context.actor_id, context.conversation_id if conversation_only else context.person_id))).fetchall()

    async def record_delivery(self, context, plan, result, *, connection=None):
        if result.delivery != "delivered":
            return
        if connection is None:
            async with self.database.transaction() as transaction:
                return await self.record_delivery(context,plan,result,connection=transaction)
        await connection.execute(
            "INSERT INTO life.contacts(expression_id,actor_id,person_id,conversation_id,candidate_id,delivered_at,signature,receipt) "
            "VALUES (%s,%s,%s,%s,%s,now(),%s,%s) ON CONFLICT DO NOTHING",
            (plan.expression_id, context.actor_id, context.person_id, context.conversation_id,
             getattr(plan, "candidate_id", None), document(fingerprint(" ".join(part.get("text", "") for part in plan.public_parts), [ref.source_id for ref in plan.source_refs])), document(result)))

    async def received(self, context, occurred_at=None, *, input_id, connection=None):
        at = occurred_at or datetime.now(UTC)
        if connection is None:
            async with self.database.transaction() as transaction:
                return await self.received(context, at, input_id=input_id, connection=transaction)
        inserted = await (await connection.execute("INSERT INTO life.contact_replies(input_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING input_id", (input_id,))).fetchone()
        if not inserted:
            return
        await connection.execute(
            "UPDATE life.contacts SET replied_at=%s WHERE expression_id=(SELECT expression_id FROM life.contacts "
            "WHERE actor_id=%s AND person_id IS NOT DISTINCT FROM %s AND conversation_id=%s "
            "AND delivered_at<=%s AND replied_at IS NULL ORDER BY delivered_at DESC LIMIT 1)",
            (at, context.actor_id, context.person_id, context.conversation_id, at))
        if self.on_received:
            await self.on_received(context, input_id=input_id, occurred_at=at, connection=connection)
