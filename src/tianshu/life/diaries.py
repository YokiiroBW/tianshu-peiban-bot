"""Local-day diaries from actual authorized experiences, draft before publication."""
from datetime import UTC, date, datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo
import json

from tianshu.models.types import ModelRequest
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ContextContribution, RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from .validation import revision, text
from .writing import can_edit, generated_text


class DiaryService:
    def __init__(self, life, models, assembler):
        self.life, self.models, self.assembler = life, models, assembler

    async def list(self, context, *, cursor=None, limit=30):
        await self.life.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM life.diaries WHERE actor_id=%s AND scope_ids<@%s::text[] AND (%s OR published_revision IS NOT NULL) "
                "AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, list(context.scope_ids), can_edit(context), cursor, cursor, limit+1))).fetchall()
        return await self.life._page(context, rows, limit)

    async def _diary(self, context, diary_id, connection, *, lock=False):
        row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]" + (" FOR UPDATE" if lock else ""), (diary_id, context.actor_id, list(context.scope_ids)))).fetchone()
        if not row or (not can_edit(context) and row["published_revision"] is None):
            raise AppError("diary_not_found", status=404)
        return row

    async def _version_refs(self, diary, connection):
        current = await (await connection.execute("SELECT recipe FROM life.diary_revisions WHERE diary_id=%s AND revision=%s", (diary["id"], diary["current_revision"]))).fetchone()
        return tuple({(ref.source_id, ref.revision): ref for ref in (
            *source_refs(diary["source_refs"]), *(source_refs(current["recipe"]["source_refs"]) if current else ()))}.values())

    async def read(self, context, body):
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            diary = await self._diary(context, body.get("id"), connection)
            version = (body.get("revision") or diary["current_revision"]) if can_edit(context) else diary["published_revision"]
            row = await (await connection.execute("SELECT * FROM life.diary_revisions WHERE diary_id=%s AND revision=%s", (diary["id"], version))).fetchone()
        if not row:
            raise AppError("diary_revision_not_found", status=404)
        await self.life.identity.check(context, source_refs(row["recipe"]["source_refs"]))
        return {"diary": diary, "version": row}

    async def request(self, context, body):
        await self.life.identity.check(context)
        state = await self.life.state(context)
        try:
            day = date.fromisoformat(body.get("day", ""))
        except (TypeError, ValueError):
            raise AppError("invalid_diary_day") from None
        zone = ZoneInfo(state["timezone"])
        start = datetime.combine(day, time.min, zone)
        end = datetime.combine(day+timedelta(days=1), time.min, zone)
        page = await self.life.memory.episodes_between(context, start, end, limit=200)
        # Reflections and earlier diary text are not another observation of their subject.
        episodes = tuple(item for item in page.items if item.origin_kind in {"human", "observation", "tool_result", "actor"}
                         and (item.actual_result or {}).get("derived_kind") not in {"reflection", "diary"})
        if not episodes:
            raise AppError("diary_no_new_material", "这一天还没有可写入日记的实际经历", status=409)
        refs = tuple({(ref.source_id, ref.revision): ref for item in episodes for ref in item.source_refs}.values())
        inherited_scopes = {scope for item in episodes for scope in item.scope_ids}
        await self.life.identity.check(context, refs)
        recipe = {"day": day.isoformat(), "timezone": state["timezone"], "episodes": wire(episodes),
                  "material_revisions": [(item.episode_id, item.revision) for item in episodes], "coverage": wire(page.coverage)}
        material_key = signature(recipe["material_revisions"])
        async def apply(connection):
            diary_id = str(uuid5(NAMESPACE_URL, f"diary:{context.actor_id}:{day}"))
            row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s FOR UPDATE", (diary_id,))).fetchone()
            if row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("diary_not_found", status=404)
            if row:
                await self.life.identity.check(context, source_refs(row["source_refs"]))
            scopes = sorted(inherited_scopes | (set(row["scope_ids"]) if row else set()))
            revision(row, body.get("expected_revision"))
            if row and row["state"] in {"generating", "unknown"}:
                raise AppError("diary_generation_unresolved", status=409, details={"attempt_id": row["generation_id"]})
            if row and row["material_key"] == material_key:
                raise AppError("diary_no_new_material", status=409)
            generation_id = str(uuid4())
            current = row["revision"]+1 if row else 1
            if row:
                await connection.execute("UPDATE life.diaries SET state='generating',generation_id=%s,context=%s,material_key=%s,generation_recipe=%s,episode_ids=%s,source_refs=%s,scope_ids=%s,revision=%s,reason=NULL WHERE id=%s", (generation_id, document(context), material_key, document(recipe), [item.episode_id for item in episodes], document(refs), scopes, current, diary_id))
            else:
                await connection.execute("INSERT INTO life.diaries(id,actor_id,day,state,generation_id,context,material_key,generation_recipe,episode_ids,source_refs,scope_ids) VALUES (%s,%s,%s,'generating',%s,%s,%s,%s,%s,%s,%s)", (diary_id, context.actor_id, day, generation_id, document(context), material_key, document(recipe), [item.episode_id for item in episodes], document(refs), scopes))
            await self.life.jobs.enqueue(connection, JobSpec("life.diary_generate", "diary:"+generation_id, {"diary_id": diary_id, "generation_id": generation_id}, subject_id=context.actor_id))
            return CommandResult(body["client_request_id"], {"id": diary_id, "revision": current, "state": "generating", "coverage": page.coverage}, current, attempt_id=generation_id)
        return await self.life.commands.execute(context, "life.diaries.request", body["client_request_id"], signature(body), apply)

    async def generate(self, diary_id, generation_id):
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s", (diary_id,))).fetchone()
        if not row or row["generation_id"] != generation_id or row["state"] != "generating":
            return
        context = context_from_record(row["context"])
        refs, recipe = source_refs(row["source_refs"]), row["generation_recipe"]
        dispatched = False
        try:
            await self.life.identity.check(context, refs)
            actor = await self.life.companion.actor(context)
            persona = await self.life.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
            instruction = "Write a brief first-person diary for the supplied local day. Facts must come from actual episodes. Feelings are subjective. Do not invent missing time, events, reading coverage, promises or replies. A short diary is fine. Never reproduce hidden model reasoning."
            budget = RequestBudget(datetime.now(UTC)+timedelta(minutes=4))
            pack = await self.assembler.assemble(context, "diary", instruction, budget,
                persona_text=persona.prompt_text if persona else "", reserved_output_tokens=3000,
                contributions=(ContextContribution("diary_material", json.dumps(recipe, ensure_ascii=False), refs, context.scope_ids, 90, True),))
            dispatched = True
            result = await self.models.generate(ModelRequest("diary:"+generation_id, generation_id, "reflect", ServiceClass.ELASTIC,
                context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=3000,
                estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs)))
            dispatched = result.receipt.dispatch != "not_started"
            await self.life.identity.check(context, tuple(pack.source_refs))
            content = generated_text(result)
            if result.receipt.status != "succeeded" or not content.strip():
                raise AppError(result.receipt.error_code or "diary_generation_failed")
        except BaseException as error:
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.diaries SET state=%s,reason=%s,revision=revision+1 WHERE id=%s AND generation_id=%s AND state='generating'", ("unknown" if dispatched else "failed", getattr(error, "code", "generation_outcome_unknown"), diary_id, generation_id))
            raise
        async with self.life.database.transaction() as connection:
            current = await self._diary(context, diary_id, connection, lock=True)
            if current["generation_id"] != generation_id or current["state"] != "generating":
                return
            version = current["current_revision"]+1
            await connection.execute("INSERT INTO life.diary_revisions(diary_id,revision,content,model_attempt_id,recipe) VALUES (%s,%s,%s,%s,%s)", (diary_id, version, content, result.receipt.attempt_id, document({**recipe, "source_refs": pack.source_refs, "model_receipt": result.receipt})))
            await connection.execute("UPDATE life.diaries SET current_revision=%s,state='draft',revision=revision+1,last_reviewed_at=now(),last_changed_at=now() WHERE id=%s", (version, diary_id))
        # This output is a work artifact, not independent evidence for its described events.

    async def revise(self, context, body):
        await self.life.identity.check(context)
        content = text(body.get("content"), "日记正文", 100000)
        async def apply(connection):
            diary = await self._diary(context, body.get("id"), connection, lock=True)
            if diary:
                await self.life.identity.check(context, source_refs(diary["source_refs"]))
            revision(diary, body.get("expected_revision"))
            if diary["state"] == "generating":
                raise AppError("diary_generation_running", status=409)
            version = diary["current_revision"]+1
            refs = await self._version_refs(diary, connection)
            await self.life.identity.check(context, refs)
            await connection.execute("INSERT INTO life.diary_revisions(diary_id,revision,content,recipe) VALUES (%s,%s,%s,%s)", (diary["id"], version, content, document({"kind": "author_edit", "source_refs": refs, "episode_ids": diary["episode_ids"]})))
            await connection.execute("UPDATE life.diaries SET current_revision=%s,state='draft',revision=revision+1,last_changed_at=now() WHERE id=%s", (version, diary["id"]))
            return CommandResult(body["client_request_id"], {"id": diary["id"], "content_revision": version}, diary["revision"]+1)
        return await self.life.commands.execute(context, "life.diaries.revise", body["client_request_id"], signature(body), apply)

    async def publish(self, context, body):
        await self.life.identity.check(context)
        async def apply(connection):
            diary = await self._diary(context, body.get("id"), connection, lock=True)
            if diary:
                await self.life.identity.check(context, source_refs(diary["source_refs"]))
            revision(diary, body.get("expected_revision"))
            if not diary["current_revision"] or diary["state"] == "generating":
                raise AppError("diary_not_ready", status=409)
            await self.life.identity.check(context, await self._version_refs(diary, connection))
            await connection.execute("UPDATE life.diaries SET published_revision=current_revision,state='published',revision=revision+1 WHERE id=%s", (diary["id"],))
            return CommandResult(body["client_request_id"], {"id": diary["id"], "published_revision": diary["current_revision"]}, diary["revision"]+1)
        return await self.life.commands.execute(context, "life.diaries.publish", body["client_request_id"], signature(body), apply)
