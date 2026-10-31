"""Actor lifecycle callback can be wired before either runtime service is constructed."""


async def synchronize_actor(context, actor_id, enabled, *, connection):
    await connection.execute("INSERT INTO life.actor_state(actor_id) VALUES (%s) ON CONFLICT DO NOTHING", (actor_id,))
    await connection.execute("INSERT INTO life.plan_watermarks(actor_id) VALUES (%s) ON CONFLICT DO NOTHING", (actor_id,))
    if not enabled:
        await connection.execute("UPDATE life.activities SET state='paused',waiting_reason='actor_disabled',next_due_at=NULL,revision=revision+1 WHERE actor_id=%s AND state='running'", (actor_id,))
        await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE actor_id=%s", (actor_id,))
