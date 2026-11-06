"""Durable, receipt-bound derived wakes from a completed activity."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.conversation.codec import document, signature, source_refs
from tianshu.conversation.persistence import retry_material_snapshot
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.recovery_policy import RecoveryWait
from .rbi_activity_reconciliation import generation_on
from .recovery import scheduled_context


async def enqueue_on(life, connection, *, context, activity_id, result, result_revision, refs, data_generation=None):
    """Freeze the waiters visible at the event, then enqueue in the result transaction."""
    generation = await generation_on(life,connection,data_generation)
    targets = []
    for table in ("activities", "commitments"):
        rows = await (await connection.execute(
            f"SELECT id,revision FROM life.{table} WHERE actor_id=%s AND scope_ids<@%s::text[] "
            "AND wake_condition=%s AND state IN ('waiting','dormant','paused','scheduled') ORDER BY id",
            (context.actor_id,list(context.scope_ids),document({"kind":"activity_completed","subject_id":activity_id})))).fetchall()
        targets.extend({"table":table,"id":row["id"],"revision":row["revision"]} for row in rows)
    if not targets:
        return None
    identity = signature((generation,activity_id,result.action_id,result.receipt_id,result_revision))
    due = datetime.now(UTC)
    inserted = await (await connection.execute(
        "INSERT INTO life.rbi_activity_wake_intents(id,data_generation,actor_id,activity_id,action_id,receipt_id,"
        "result_revision,context,source_refs,targets,due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(id) DO NOTHING RETURNING id",
        (identity,generation,context.actor_id,activity_id,result.action_id,result.receipt_id,result_revision,
         document(context),document(refs),document(targets),due))).fetchone()
    if inserted:
        await ensure_on(life,connection,identity,generation,due_at=due)
    return identity


async def ensure_on(life, connection, intent_id, generation, *, due_at):
    await generation_on(life,connection,generation)
    row = await (await connection.execute(
        "SELECT * FROM life.rbi_activity_wake_intents WHERE id=%s FOR UPDATE",(intent_id,))).fetchone()
    if not row or row["state"] != "pending" or row["data_generation"] != generation:
        return None
    job_id = await life.jobs.enqueue(connection,JobSpec("life.activity_completed_wake",
        "life-completion-wake:"+str(uuid4()),{"intent_id":intent_id,"data_generation":generation},
        scheduled_at=due_at,subject_id=row["actor_id"],data_generation=generation),
        queueing_lock="life-completion-wake:"+intent_id)
    await connection.execute("UPDATE life.rbi_activity_wake_intents SET job_id=%s,updated_at=now() WHERE id=%s",
        (job_id,intent_id))
    return job_id


async def _advance(life, connection, row, generation, *, skipped=None):
    next_index = row["next_index"]+1
    remaining = next_index < len(row["targets"])
    entries = [*(row["skipped"] or ()),skipped] if skipped else (row["skipped"] or [])
    await connection.execute("UPDATE life.rbi_activity_wake_intents SET next_index=%s,skipped=%s,state=%s,"
        "reason=NULL,updated_at=now() WHERE id=%s",
        (next_index,document(entries),"pending" if remaining else "done",row["id"]))
    if remaining:
        await ensure_on(life,connection,row["id"],generation,due_at=datetime.now(UTC))


async def execute(worker, intent_id, data_generation):
    """Advance one captured target; each commit also advances its durable cursor."""
    life = worker.life
    async def apply():
        async with life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM life.rbi_activity_wake_intents WHERE id=%s",(intent_id,))).fetchone()
            if not row or row["state"] != "pending" or row["data_generation"] != data_generation:
                return
            target = row["targets"][row["next_index"]]
            if target["table"] not in {"activities", "commitments"}:
                raise AppError("invalid_life_wake_target",status=409)
            snapshot = await (await connection.execute(
                f"SELECT * FROM life.{target['table']} WHERE id=%s AND actor_id=%s",
                (target["id"],row["actor_id"]))).fetchone()
        context = await scheduled_context(life,row["context"])
        await life.identity.check(context,source_refs(row["source_refs"]))
        current_context, skipped = None, None
        if not snapshot or snapshot["revision"] != target["revision"]:
            skipped = {**target,"reason":"target_changed"}
        else:
            try:
                current_context = await scheduled_context(life,snapshot["context"])
                await life.identity.check(current_context,source_refs(snapshot["source_refs"]))
            except AppError as error:
                if isinstance(error,RecoveryWait):
                    raise
                if error.status not in {401,403,404,410} and error.code not in {"source_unavailable","access_changed"}:
                    raise
                skipped = {**target,"reason":error.code}
        async with life.database.transaction() as connection:
            await generation_on(life,connection,data_generation)
            refs = source_refs(row["source_refs"])
            if current_context is not None:
                await life.sources.prepare_event_writes(connection,context,(),additional_refs=refs,
                    scoped_refs=((current_context,source_refs(snapshot["source_refs"])),))
                await life.validate_persistence(connection,current_context,source_refs(snapshot["source_refs"]))
            else:
                await life.validate_persistence(connection,context,refs)
            fresh = await (await connection.execute(
                "SELECT * FROM life.rbi_activity_wake_intents WHERE id=%s FOR UPDATE",(intent_id,))).fetchone()
            if (not fresh or fresh["state"] != "pending" or fresh["data_generation"] != data_generation
                    or fresh["next_index"] != row["next_index"]):
                return
            pinned = ("actor_id","activity_id","action_id","receipt_id","result_revision",
                "context","source_refs","targets","due_at")
            if signature({key:row[key] for key in pinned}) != signature({key:fresh[key] for key in pinned}):
                from tianshu.conversation.persistence import MaterialSnapshotChanged
                raise MaterialSnapshotChanged()
            if current_context is not None:
                from .events import wake_target
                changed = await wake_target(life,context,table=target["table"],snapshot=snapshot,
                    current_context=current_context,kind="activity_completed",subject_id=row["activity_id"],
                    event_id=row["receipt_id"],connection=connection,due_at=row["due_at"],source_prelocked=True)
                if not changed:
                    skipped = {**target,"reason":"target_changed"}
            await _advance(life,connection,fresh,data_generation,skipped=skipped)
    try:
        return await retry_material_snapshot(apply)
    except AppError as error:
        if isinstance(error,RecoveryWait):
            raise
        access_codes = {"source_unavailable","source_support_unavailable","source_withdrawn",
            "source_revoked","source_suppressed","access_changed"}
        if error.code == "job_generation_expired" or (error.status not in {401,403,404,410} and error.code not in access_codes):
            raise
        async with life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM life.rbi_activity_wake_intents WHERE id=%s",(intent_id,))).fetchone()
        if not row or row["state"] != "pending" or row["data_generation"] != data_generation:
            return
        try:
            context = await scheduled_context(life,row["context"])
            await life.identity.check(context,source_refs(row["source_refs"]))
            event_authorized, event_reason = True, None
        except AppError as event_error:
            if isinstance(event_error,RecoveryWait):
                raise
            if event_error.status not in {401,403,404,410} and event_error.code not in access_codes:
                raise
            event_authorized, event_reason = False, event_error.code
        async with life.database.transaction() as connection:
            await generation_on(life,connection,data_generation)
            fresh = await (await connection.execute(
                "SELECT * FROM life.rbi_activity_wake_intents WHERE id=%s FOR UPDATE",(intent_id,))).fetchone()
            if not fresh or fresh["state"] != "pending" or fresh["next_index"] != row["next_index"]:
                return
            if not event_authorized:
                await connection.execute("UPDATE life.rbi_activity_wake_intents SET state='blocked',reason=%s,"
                    "updated_at=now() WHERE id=%s",(event_reason,intent_id))
            else:
                target = row["targets"][row["next_index"]]
                await _advance(life,connection,fresh,data_generation,skipped={**target,"reason":error.code})


async def reconcile_due(worker, *, limit=32):
    life, cursor = worker.life, worker._activity_wake_cursor
    async with life.database.transaction(read_only=True) as connection:
        generation = await life.jobs.current_generation_on(connection)
        rows = await (await connection.execute(
            "SELECT id,job_id,due_at FROM life.rbi_activity_wake_intents "
            "WHERE data_generation=%s AND state='pending' AND id>%s ORDER BY id LIMIT %s",
            (generation,cursor,limit+1))).fetchall()
    page, more = rows[:limit], len(rows)>limit
    result = {"scanned":0,"woken":0,"complete":not more}
    for row in page:
        worker._activity_wake_cursor = row["id"]
        result["scanned"] += 1
        async with life.database.transaction() as connection:
            await generation_on(life,connection,generation)
            current = await (await connection.execute(
                "SELECT * FROM life.rbi_activity_wake_intents WHERE id=%s FOR UPDATE",(row["id"],))).fetchone()
            if not current or current["state"] != "pending" or current["data_generation"] != generation:
                continue
            job = await life.jobs.observe_on(connection,current["job_id"]) if current["job_id"] else None
            if job and job["job_state"] == "todo":
                continue
            due = max(current["due_at"],datetime.now(UTC)+timedelta(seconds=15)) if job and job["job_state"] == "doing" else current["due_at"]
            result["woken"] += int(await ensure_on(life,connection,current["id"],generation,due_at=due) is not None)
    if not more:
        worker._activity_wake_cursor = ""
    return result
