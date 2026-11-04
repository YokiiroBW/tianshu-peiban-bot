"""Author controls observe or cancel the original chapter/diary attempt."""
from datetime import UTC, datetime
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from tianshu.conversation.codec import context_from_record, signature
from tianshu.models.timing import waiting_budget
from .validation import revision


async def submit_frozen(life, models, generations, material_key, request, *, guard, completion, kind, item_id):
    """Admit the saved intent and release the domain worker after acceptance."""
    async with life.database.transaction() as connection:
        request = await generations.prepare(material_key, request, connection=connection, guard=guard)
        admission = waiting_budget(request)
        if admission is not None and admission.deadline_at <= datetime.now(UTC):
            raise AppError("generation_request_expired", status=409)
        await connection.execute(
            "UPDATE conversation.generation_requests SET state='claimed',claimed_at=coalesce(claimed_at,now()) "
            "WHERE material_key=%s AND state='prepared'", (material_key,))
    receipt = await models.submit(request, completion=completion)
    if receipt is None or receipt.attempt_id != request.attempt_id:
        raise AppError("generation_receipt_identity_mismatch", status=502)
    table = "life.diaries" if kind == "diary" else "life.chapters"
    async with life.database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            f"SELECT generation_id,generation_cancel_requested FROM {table} WHERE id=%s", (item_id,))).fetchone()
    # Cancellation can race the gap between the domain guard and admission.
    if row and row["generation_id"] == request.attempt_id and row["generation_cancel_requested"]:
        await models.cancel(request.attempt_id, reason="explicit_cancel")
    return receipt


class GenerationControl:
    def __init__(self, life, writing, diaries):
        self.life, self.writing, self.diaries = life, writing, diaries

    @staticmethod
    def query(kind, *, lock=False):
        if kind == "chapter":
            sql = "SELECT c.* FROM life.chapters c JOIN life.works w ON w.id=c.work_id WHERE c.id=%s AND w.actor_id=%s AND w.scope_ids<@%s::text[]"
            return sql + (" FOR UPDATE OF c" if lock else "")
        if kind == "diary":
            return "SELECT * FROM life.diaries WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]" + (" FOR UPDATE" if lock else "")
        raise ValueError(kind)

    async def request(self, context, body, *, kind, operation):
        await self.life.identity.check(context)
        async def apply(connection):
            # Cancellation handles execution metadata even after source loss.
            await self.life.validate_persistence(connection, context)
            row = (await self.diaries._diary(context,body.get("id"),connection,lock=True)
                if kind=="diary" and operation=="reconcile" else await (await connection.execute(self.query(kind, lock=True),
                    (body.get("id"), context.actor_id, list(context.scope_ids)))).fetchone())
            if not row:
                raise AppError(kind+"_not_found", status=404)
            revision(row, body.get("expected_revision"))
            if not row["generation_id"]:
                raise AppError("generation_not_started", status=409)
            if operation=="reconcile" and row["state"]=="collecting":
                return CommandResult(body["client_request_id"],{"id":row["id"],"state":"collecting"},row["revision"],attempt_id=row["generation_id"])
            if kind=="diary" and operation=="reconcile":
                await self.diaries.reconcile_saved(context,row,connection)
            current = row["revision"]+1
            if row["state"] in {"collecting", "generating", "unknown", "cancelling"}:
                table = "life.chapters" if kind == "chapter" else "life.diaries"
                await connection.execute(f"UPDATE {table} SET state=%s,reason=%s,revision=%s,generation_cancel_requested=generation_cancel_requested OR %s WHERE id=%s",
                    ("cancelling" if operation=="cancel" else row["state"], "cancel_requested" if operation=="cancel" else row["reason"], current, operation=="cancel", row["id"]))
                await self.life.jobs.enqueue(connection, JobSpec("life.generation_control",
                    f"generation-{operation}:{kind}:{row['generation_id']}:{current}",
                    {"kind":kind,"item_id":row["id"],"generation_id":row["generation_id"],"context":wire(context),"operation":operation},
                    ServiceClass.INTERACTIVE, subject_id=context.actor_id))
            else:
                current = row["revision"]
            return CommandResult(body["client_request_id"], {"id":row["id"],"state":"cancelling" if operation=="cancel" and current!=row["revision"] else row["state"]},
                current, attempt_id=row["generation_id"])
        return await self.life.commands.execute(context, f"life.{'chapters' if kind=='chapter' else 'diaries'}.{operation}", body["client_request_id"], signature(body), apply)

    async def work(self, kind, item_id, generation_id, context, operation):
        context = context_from_record(context)
        if kind == "diary":
            return await self.diaries.control_generation(item_id,generation_id,context,operation)
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(self.query(kind), (item_id,context.actor_id,list(context.scope_ids)))).fetchone()
        if not row or row["generation_id"] != generation_id or row["state"] not in {"generating","unknown","cancelling","collecting"}:
            return
        if kind=="chapter":
            original=context_from_record(row["generation_context"])
            if original.principal_id!=context.principal_id:
                raise AppError("generation_observation_context_mismatch",status=403)
            if operation=="cancel":
                try:
                    await self.writing.models.cancel(generation_id)
                except AppError as error:
                    if error.status!=404:
                        raise
            return await self.writing.generate(item_id,generation_id)

    async def recover(self):
        async with self.life.database.transaction() as connection:
            for kind,table,context_column in (("chapter","life.chapters","generation_context"),("diary","life.diaries","context")):
                rows = await (await connection.execute(f"SELECT * FROM {table} WHERE state IN ('generating','unknown','cancelling','collecting')")).fetchall()
                for row in rows:
                    if row["state"]=="collecting":
                        task,payload="life.diary_collect",{"diary_id":row["id"],"generation_id":row["generation_id"],"cursor":row["generation_recipe"]["cursor"]}
                    elif row["state"]=="generating":
                        task="life.chapter_generate" if kind=="chapter" else "life.diary_generate"
                        payload={"chapter_id" if kind=="chapter" else "diary_id":row["id"],"generation_id":row["generation_id"]}
                    else:
                        task,payload="life.generation_control",{"kind":kind,"item_id":row["id"],"generation_id":row["generation_id"],"context":row[context_column],"operation":"cancel" if row["state"]=="cancelling" else "reconcile"}
                    await self.life.jobs.enqueue(connection,JobSpec(task,f"generation-recover:{kind}:{row['generation_id']}:{datetime.now(UTC).isoformat()}",payload,ServiceClass.INTERACTIVE,subject_id=row.get("actor_id")))
