"""Recover collection pages without replacing a diary or a model attempt."""

from uuid import uuid4

from tianshu.conversation.codec import context_from_record, document, signature
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from .generation_control import frozen_data_generation


class DiaryCollection:
    def __init__(self, diaries):
        self.diaries, self.life = diaries, diaries.life
        self.after_id = ""

    async def enqueue_on(self, connection, row, *, recovery=False):
        recipe = row["generation_recipe"]
        generation = frozen_data_generation(recipe)
        await self.life.jobs.assert_generation_on(connection, generation)
        payload = {"diary_id": row["id"], "generation_id": row["generation_id"],
                   "cursor": recipe["cursor"], "data_generation": generation}
        cursor_key = signature(recipe["cursor"]) if recipe["cursor"] else "start"
        key = f"diary-collect:{row['generation_id']}:{cursor_key}"
        if recovery:
            key += ":recover:" + str(uuid4())
        job = await self.life.jobs.enqueue(connection, JobSpec("life.diary_collect", key, payload,
            subject_id=row["actor_id"], data_generation=generation), queueing_lock=key.split(":recover:")[0])
        return job

    async def record_wait(self, diary_id, generation_id, cursor, failure, generation):
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection, generation)
            await connection.execute("UPDATE life.diaries SET reason=%s WHERE id=%s AND generation_id=%s "
                "AND state='collecting' AND generation_recipe->'cursor'=%s::jsonb",
                (failure.reason, diary_id, generation_id, document(cursor)))

    async def reconcile_one(self, snapshot, *, context=None):
        if (snapshot["state"] != "collecting" or not snapshot["generation_recipe"]
                or snapshot["generation_cancel_requested"]):
            return False
        generation = frozen_data_generation(snapshot["generation_recipe"])
        context = context or await self.diaries._reader(context_from_record(snapshot["context"]))
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection, generation)
            row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s "
                "FOR UPDATE SKIP LOCKED", (snapshot["id"],))).fetchone()
            if (not row or row["state"] != "collecting" or row["generation_cancel_requested"]
                    or row["generation_id"] != snapshot["generation_id"] or row["context"] != snapshot["context"]
                    or row["generation_recipe"] != snapshot["generation_recipe"]):
                return False
            if not set(row["scope_ids"]).issubset(context.scope_ids):
                return False
            # Prepared/submitted work belongs to original-request observation,
            # even if a stale diary row still says collecting.
            physical = await (await connection.execute(
                "SELECT EXISTS (SELECT 1 FROM conversation.generation_requests WHERE attempt_id=%s) "
                "OR EXISTS (SELECT 1 FROM models.attempts WHERE attempt_id=%s) AS present",
                (row["generation_id"], row["generation_id"]))).fetchone()
            if physical["present"]:
                return False
            payload = {"diary_id": row["id"], "generation_id": row["generation_id"],
                       "cursor": row["generation_recipe"]["cursor"], "data_generation": generation}
            pending = await (await connection.execute(
                "SELECT j.id FROM procrastinate_jobs j WHERE j.task_name='life.diary_collect' "
                "AND j.status IN ('todo','doing') AND j.args=%s::jsonb AND EXISTS ("
                "SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s "
                "AND k.cancelled_at IS NULL) LIMIT 1", (document(payload), generation))).fetchone()
            if pending:
                return False
            await self.enqueue_on(connection, row, recovery=True)
            return True

    async def reconcile(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("invalid_diary_collection_limit")
        async with self.life.database.transaction(read_only=True) as connection:
            generation = await self.life.jobs.current_generation_on(connection)
            rows = await (await connection.execute("SELECT * FROM life.diaries WHERE id>%s "
                "AND state='collecting' AND NOT generation_cancel_requested "
                "AND generation_recipe->>'data_generation'=%s ORDER BY id LIMIT %s",
                (self.after_id, generation, limit + 1))).fetchall()
        page, more = rows[:limit], len(rows) > limit
        woken = 0
        for row in page:
            self.after_id = row["id"]
            try:
                woken += int(await self.reconcile_one(row))
            except AppError as error:
                if error.code == "job_generation_expired" or error.status not in {401,403,404,409,410}:
                    raise
        if not more:
            self.after_id = ""
        return {"scanned": len(page), "woken": woken, "complete": not more}
