from datetime import UTC, datetime
from tianshu.conversation.codec import document, signature


class Contacts:
    def __init__(self, database):
        self.database = database

    async def preferences(self, context):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM life.contact_preferences WHERE actor_id=%s AND person_id IN (%s,'') "
                "AND scope_ids<@%s::text[] ORDER BY (person_id=%s) DESC LIMIT 1",
                (context.actor_id, context.person_id or "", list(context.scope_ids), context.person_id or ""))).fetchone()
        return row or {"revision": 0, "timezone": "Asia/Shanghai", "quiet_start": "23:00", "quiet_end": "08:00",
                       "cooldown_seconds": 1800, "daily_quota": 3, "unanswered_limit": 3,
                       "responsiveness": .5, "enabled": True, "availability": "unknown", "available_until": None}

    async def history(self, context):
        async with self.database.transaction(read_only=True) as connection:
            return await (await connection.execute(
                "SELECT * FROM life.contacts WHERE actor_id=%s AND person_id IS NOT DISTINCT FROM %s "
                "ORDER BY delivered_at DESC NULLS LAST LIMIT 64", (context.actor_id, context.person_id))).fetchall()

    async def record_delivery(self, context, plan, result):
        if result.delivery != "delivered":
            return
        async with self.database.transaction() as connection:
            await connection.execute(
                "INSERT INTO life.contacts(expression_id,actor_id,person_id,conversation_id,candidate_id,delivered_at,signature,receipt) "
                "VALUES (%s,%s,%s,%s,%s,now(),%s,%s) ON CONFLICT DO NOTHING",
                (plan.expression_id, context.actor_id, context.person_id, context.conversation_id,
                 getattr(plan, "candidate_id", None), document({"content": signature(plan.public_parts)}), document(result)))

    async def received(self, context, occurred_at=None):
        at = occurred_at or datetime.now(UTC)
        async with self.database.transaction() as connection:
            await connection.execute(
                "UPDATE life.contacts SET replied_at=%s WHERE expression_id=(SELECT expression_id FROM life.contacts "
                "WHERE actor_id=%s AND person_id IS NOT DISTINCT FROM %s AND conversation_id=%s "
                "AND delivered_at<=%s AND replied_at IS NULL ORDER BY delivered_at DESC LIMIT 1)",
                (at, context.actor_id, context.person_id, context.conversation_id, at))
