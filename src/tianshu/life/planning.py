"""One durable change watermark for all life decision triggers."""
from tianshu.runtime.jobs import JobSpec, current_execution_generation
from tianshu.runtime.serialization import wire


async def signal(connection,jobs,context,watermark,*,change=None):
    from tianshu.conversation.codec import document,signature
    generation = current_execution_generation()
    if generation is None:
        generation = await jobs.current_generation_on(connection)
    await jobs.assert_generation_on(connection, generation)
    kind=watermark.split(':',1)[0]
    # Growth and clock versions are notifications, not independent reasons to
    # replace a decision. Their actual interests/routine/date are fingerprinted.
    event={} if kind in {'growth','rhythm'} else {kind+':'+signature(sorted(context.scope_ids)):
        {'kind':kind,'watermark':watermark,'scope_ids':list(context.scope_ids),**(change or {})}}
    row=await (await connection.execute(
        "INSERT INTO life.plan_watermarks(actor_id,change_watermark,change) VALUES (%s,%s,%s) ON CONFLICT(actor_id) DO UPDATE SET change_watermark=excluded.change_watermark,change=life.plan_watermarks.change||excluded.change,revision=life.plan_watermarks.revision+1 WHERE life.plan_watermarks.change_watermark<>excluded.change_watermark RETURNING revision",(context.actor_id,watermark,document(event)))).fetchone()
    if row:
        await jobs.enqueue(connection,JobSpec("life.plan",f"life-plan:{generation}:{context.actor_id}:{row['revision']}",
            {"actor_id":context.actor_id,"revision":row["revision"],"context":wire(context),"data_generation":generation},
            subject_id=context.actor_id,data_generation=generation))
    return bool(row)
