"""Author controls observe or cancel the original chapter/diary attempt."""
from datetime import UTC, datetime
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from tianshu.conversation.codec import context_from_record, signature
from .validation import revision
from .writing import generated_text, generation_error


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
            row = await (await connection.execute(self.query(kind, lock=True),
                (body.get("id"), context.actor_id, list(context.scope_ids)))).fetchone()
            if not row:
                raise AppError(kind+"_not_found", status=404)
            revision(row, body.get("expected_revision"))
            if not row["generation_id"]:
                raise AppError("generation_not_started", status=409)
            if operation=="reconcile" and row["state"]=="collecting":
                return CommandResult(body["client_request_id"],{"id":row["id"],"state":"collecting"},row["revision"],attempt_id=row["generation_id"])
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
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(self.query(kind), (item_id,context.actor_id,list(context.scope_ids)))).fetchone()
        if not row or row["generation_id"] != generation_id or row["state"] not in {"generating","unknown","cancelling","collecting"}:
            return
        service = self.writing if kind=="chapter" else self.diaries
        if operation=="cancel":
            try:
                await service.models.cancel(generation_id)
            except AppError as error:
                if error.status!=404:
                    raise
        result = await service.generations.read(generation_id,context)
        frozen = await service.generations.request(generation_id,context)
        if result is None and frozen is None and operation=="reconcile" and row["state"] in {"collecting","generating"}:
            return
        if result is not None and result.receipt.status=="succeeded" and generated_text(result).strip() and frozen is not None and not row["generation_cancel_requested"]:
            await service.save_generated(context,item_id,generation_id,row["generation_recipe"],result,frozen.source_refs)
            return
        state = ("cancelled" if row["generation_cancel_requested"] and result.receipt.status=="succeeded" else "failed" if result.receipt.status=="succeeded" else result.receipt.status) if result is not None else "cancelled" if frozen is None and operation=="cancel" else "unknown"
        if state in {"queued","running"}:
            state = "cancelling" if row["generation_cancel_requested"] else "generating"
        elif state not in {"failed","cancelled"}:
            state = "unknown"
        if result is None and frozen is not None and frozen.budget.deadline_at<=datetime.now(UTC):
            state="cancelled" if row["generation_cancel_requested"] else "failed"
        table = "life.chapters" if kind=="chapter" else "life.diaries"
        if result is not None:
            reason = ("model_completed_after_cancel" if row["generation_cancel_requested"] and result.receipt.status == "succeeded"
                      else generation_error(result, "generation_output_empty") if result.receipt.status == "succeeded"
                      else result.receipt.error_code or "model_" + result.receipt.status)
        else:
            reason = "cancelled_before_model_registration" if state == "cancelled" else "generation_request_expired" if state == "failed" else "model_outcome_unknown"
        async with self.life.database.transaction() as connection:
            await self.life.validate_persistence(connection,context)
            await connection.execute(f"UPDATE {table} SET state=%s,reason=%s,revision=revision+1 WHERE id=%s AND generation_id=%s AND state IN ('generating','unknown','cancelling','collecting')",
                (state,reason,item_id,generation_id))

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
