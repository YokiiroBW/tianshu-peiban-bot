"""One durable change watermark for all life decision triggers."""
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire


async def signal(connection,jobs,context,watermark):
    row=await (await connection.execute(
        "INSERT INTO life.plan_watermarks(actor_id,change_watermark) VALUES (%s,%s) ON CONFLICT(actor_id) DO UPDATE SET change_watermark=excluded.change_watermark,revision=life.plan_watermarks.revision+1 WHERE life.plan_watermarks.change_watermark<>excluded.change_watermark RETURNING revision",(context.actor_id,watermark))).fetchone()
    if row:
        await jobs.enqueue(connection,JobSpec("life.plan",f"life-plan:{context.actor_id}:{row['revision']}",{"actor_id":context.actor_id,"revision":row["revision"],"context":wire(context)},subject_id=context.actor_id))
    return bool(row)
