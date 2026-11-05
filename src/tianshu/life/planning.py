"""Durable notification versions and coalesced planning wakes."""
from tianshu.runtime.jobs import JobSpec, current_execution_generation
from uuid import uuid4


NOTIFICATION_ONLY = frozenset({"growth", "rhythm", "capability-observation"})


def wake_payload(actor_id, generation):
    # The actor row, not a stale queue payload, owns the latest revision and
    # authorisation context. Equal payloads allow the native queue to coalesce.
    return {"actor_id": actor_id, "revision": None, "context": None, "data_generation": generation}


def wake_lock(actor_id):
    from tianshu.conversation.codec import signature
    return "life-plan-wake:" + signature(actor_id)


async def signal(connection,jobs,context,watermark,*,change=None):
    from tianshu.conversation.codec import document,signature
    generation = current_execution_generation()
    if generation is None:
        generation = await jobs.current_generation_on(connection)
    await jobs.assert_generation_on(connection, generation)
    kind=watermark.split(':',1)[0]
    # Growth and clock versions are notifications, not independent reasons to
    # replace a decision. Their actual interests/routine/date are fingerprinted.
    event={} if kind in NOTIFICATION_ONLY else {kind+':'+signature(sorted(context.scope_ids)):
        {'kind':kind,'watermark':watermark,'scope_ids':list(context.scope_ids),**(change or {})}}
    row=await (await connection.execute(
        "INSERT INTO life.plan_watermarks(actor_id,change_watermark,change,wake_context) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT(actor_id) DO UPDATE SET change_watermark=excluded.change_watermark,"
        "change=life.plan_watermarks.change||excluded.change,wake_context=excluded.wake_context,"
        "revision=life.plan_watermarks.revision+1 WHERE life.plan_watermarks.change_watermark<>excluded.change_watermark "
        "RETURNING revision",(context.actor_id,watermark,document(event),document(context)))).fetchone()
    if row:
        await jobs.enqueue(connection,JobSpec("life.plan",f"life-plan:{generation}:{context.actor_id}:{row['revision']}",
            wake_payload(context.actor_id,generation),subject_id=context.actor_id,data_generation=generation),
            queueing_lock=wake_lock(context.actor_id))
    return bool(row)


async def planning_ready(connection,jobs,context,capability_ids):
    """Accelerate only an existing capability refresh wait, without new material."""
    if not capability_ids:
        return False
    generation = current_execution_generation()
    if generation is None:
        generation = await jobs.current_generation_on(connection)
    await jobs.assert_generation_on(connection,generation)
    row = await (await connection.execute(
        "SELECT reason FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",(context.actor_id,))).fetchone()
    if not row or row["reason"] != "planner_capabilities_refresh":
        return False
    await jobs.enqueue(connection,JobSpec("life.plan",f"life-plan-ready:{generation}:{context.actor_id}:{uuid4()}",
        wake_payload(context.actor_id,generation),subject_id=context.actor_id,data_generation=generation),
        queueing_lock=wake_lock(context.actor_id))
    return True
