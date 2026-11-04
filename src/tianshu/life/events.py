"""Wake formal Life waits from real domain events; reports do not execute commands."""
from tianshu.conversation.codec import context_from_record, document, source_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass


KINDS = frozenset({"user_reply", "activity_completed", "capability_available"})


def condition(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) - {"kind", "subject_id"}
            or value.get("kind") not in KINDS or not isinstance(value.get("subject_id"), str)
            or not value["subject_id"].strip()):
        raise AppError("invalid_wake_condition")
    return value


async def wake(life, context, *, kind, subject_id, event_id, connection):
    if kind not in KINDS:
        raise AppError("invalid_life_event")
    # Exact stored conditions and CAS revisions make replay harmless. Merely
    # reading a memory mentioning cancellation never reaches this consumer.
    for table in ("activities", "commitments"):
        rows = await (await connection.execute(
            f"SELECT * FROM life.{table} WHERE actor_id=%s AND scope_ids<@%s::text[] "
            "AND wake_condition=%s AND state IN ('waiting','dormant','paused','scheduled')",
            (context.actor_id, list(context.scope_ids), document({"kind": kind, "subject_id": subject_id})))).fetchall()
        for row in rows:
            if table == "activities" and (row["action_id"] or row["waiting_reason"] == "paused_by_user"):
                continue
            original = context_from_record(row["context"])
            current = await life.identity.refresh_scheduled_context(original, permission="life.manage", connection=connection)
            snapshot=row
            row=await life.lock_row(connection,current,f"SELECT * FROM life.{table} WHERE id=%s AND actor_id=%s FOR UPDATE",(row['id'],context.actor_id))
            if row['revision']!=snapshot['revision'] or row['wake_condition']!=snapshot['wake_condition'] or row['state'] not in {'waiting','dormant','paused','scheduled'}:
                continue
            version = row["revision"] + 1
            if table == "activities":
                await connection.execute(
                    "UPDATE life.activities SET state='planned',wake_condition=NULL,waiting_reason=NULL,resume_condition=NULL,"
                    "next_due_at=now(),revision=%s,context=%s WHERE id=%s", (version, document(current), row["id"]))
                task, payload = "life.activity_step", {"activity_id": row["id"], "expected_revision": version, "context": wire(current)}
            else:
                await connection.execute(
                    "UPDATE life.commitments SET state='scheduled',wake_condition=NULL,due_at=now(),reason=NULL,revision=%s,context=%s WHERE id=%s",
                    (version, document(current), row["id"]))
                task, payload = "life.commitment_due", {"commitment_id": row["id"], "expected_revision": version}
            await life.jobs.enqueue(connection, JobSpec(task, f"life-event:{table}:{row['id']}:{version}:{event_id}", payload,
                ServiceClass.DUE, subject_id=context.actor_id))


async def available(life,context,choices,*,data_generation):
    ready={item["definition"].id for item in choices if item["can_execute"]}
    async with life.database.transaction() as connection:
        await life.jobs.assert_generation_on(connection,data_generation)
        needed=set()
        for table in ("activities","commitments"):
            rows=await (await connection.execute(f"SELECT DISTINCT wake_condition->>'subject_id' AS capability_id FROM life.{table} WHERE actor_id=%s AND scope_ids<@%s::text[] AND wake_condition->>'kind'='capability_available'",(context.actor_id,list(context.scope_ids)))).fetchall()
            needed.update(row["capability_id"] for row in rows)
        for identifier in sorted(ready & needed):
            await wake(life,context,kind="capability_available",subject_id=identifier,event_id="available:"+identifier,connection=connection)
