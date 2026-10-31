"""Local-day diaries from actual authorized experiences, draft before publication."""
from tianshu.sources.dependencies import unique_refs
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
from . import publications
from tianshu.conversation.types import InputPart
from tianshu.conversation.generation import GenerationRequests


class DiaryService:
    def __init__(self, life, models, assembler):
        self.life, self.models, self.assembler = life, models, assembler
        self.generations = GenerationRequests(life.database, life.sources, models)

    async def list(self, context, *, cursor=None, limit=30):
        await self.life.identity.check(context)
        limit = min(100, max(1, int(limit)))
        if not can_edit(context):
            async with self.life.database.transaction(read_only=True) as connection:
                rows = await (await connection.execute(
                    "SELECT d.id,d.actor_id,d.day,d.published_revision,r.disclosure_ref FROM life.diaries d "
                    "JOIN life.diary_revisions r ON r.diary_id=d.id AND r.revision=d.published_revision "
                    "WHERE d.actor_id=%s AND r.disclosure_ref IS NOT NULL AND (%s::text IS NULL OR d.id>%s) ORDER BY d.id LIMIT %s",
                    (context.actor_id,cursor,cursor,limit+1))).fetchall()
            return await publications.page(self.life.identity,context,rows,limit,publications.diary)
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM life.diaries WHERE actor_id=%s AND scope_ids<@%s::text[] AND (%s OR published_revision IS NOT NULL) "
                "AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, list(context.scope_ids), can_edit(context), cursor, cursor, limit+1))).fetchall()
        return await self.life._page(context, rows, limit)

    async def _diary(self, context, diary_id, connection, *, lock=False, additional_refs=()):
        query = "SELECT * FROM life.diaries WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]"
        parameters = (diary_id, context.actor_id, list(context.scope_ids))
        row = (await self.life.lock_row(connection, context, query+" FOR UPDATE", parameters,
                                       additional_refs=additional_refs, related_refs=self._version_refs)
               if lock else await (await connection.execute(query, parameters)).fetchone())
        if not row or (not can_edit(context) and row["published_revision"] is None):
            raise AppError("diary_not_found", status=404)
        return row

    async def _version_refs(self, diary, connection):
        current = await (await connection.execute("SELECT recipe FROM life.diary_revisions WHERE diary_id=%s AND revision=%s", (diary["id"], diary["current_revision"]))).fetchone()
        return unique_refs(tuple(ref for ref in (
            *source_refs(diary["source_refs"]), *(source_refs(current["recipe"]["source_refs"]) if current else ()))))

    async def read(self, context, body):
        await self.life.identity.check(context)
        if not can_edit(context):
            async with self.life.database.transaction(read_only=True) as connection:
                row = await (await connection.execute(
                    "SELECT d.id,d.actor_id,d.day,d.published_revision,r.content,r.created_at AS published_at,r.disclosure_ref "
                    "FROM life.diaries d JOIN life.diary_revisions r ON r.diary_id=d.id AND r.revision=d.published_revision "
                    "WHERE d.id=%s AND d.actor_id=%s",(body.get("id"),context.actor_id))).fetchone()
            await publications.readable(self.life.identity,context,row)
            return {"diary":publications.diary(row),"version":publications.version(row)}
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
        recipe = {"day":day.isoformat(),"timezone":state["timezone"],"cursor":None,
                  "watermark":None,"scan_hash":"","scanned":0,"episodes":[],"material_revisions":[],
                  "coverage":{"complete":False,"missing":["collection_pending"]}}
        async def apply(connection):
            diary_id = str(uuid5(NAMESPACE_URL,f"diary:{context.actor_id}:{day}"))
            row = await self.life.lock_row(connection,context,"SELECT * FROM life.diaries WHERE id=%s FOR UPDATE",(diary_id,),related_refs=self._version_refs)
            if row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("diary_not_found",status=404)
            await self.life.validate_persistence(connection,context)
            revision(row,body.get("expected_revision"))
            if row and row["state"] in {"collecting","generating","unknown","cancelling"}:
                raise AppError("diary_generation_unresolved",status=409,details={"attempt_id":row["generation_id"]})
            generation_id = str(uuid4())
            current = row["revision"]+1 if row else 1
            if row:
                await connection.execute("UPDATE life.diaries SET state='collecting',generation_cancel_requested=false,generation_id=%s,context=%s,generation_recipe=%s,revision=%s,reason=NULL WHERE id=%s",(generation_id,document(context),document(recipe),current,diary_id))
            else:
                await connection.execute("INSERT INTO life.diaries(id,actor_id,day,state,generation_id,context,generation_recipe,episode_ids,source_refs,scope_ids) VALUES (%s,%s,%s,'collecting',%s,%s,%s,'{}','[]',%s)",(diary_id,context.actor_id,day,generation_id,document(context),document(recipe),list(context.scope_ids)))
            await self.life.jobs.enqueue(connection,JobSpec("life.diary_collect","diary-collect:"+generation_id+":start",{"diary_id":diary_id,"generation_id":generation_id,"cursor":None},subject_id=context.actor_id))
            return CommandResult(body["client_request_id"],{"id":diary_id,"revision":current,"state":"collecting"},current,attempt_id=generation_id)
        return await self.life.commands.execute(context,"life.diaries.request",body["client_request_id"],signature(body),apply)

    async def collect(self, diary_id, generation_id, cursor):
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s",(diary_id,))).fetchone()
        if not row or row["generation_id"]!=generation_id or row["state"]!="collecting" or row["generation_recipe"]["cursor"]!=cursor:
            return
        context,recipe = context_from_record(row["context"]),row["generation_recipe"]
        await self.life.identity.check(context)
        day = date.fromisoformat(recipe["day"])
        zone = ZoneInfo(recipe["timezone"])
        start,end = datetime.combine(day,time.min,zone),datetime.combine(day+timedelta(days=1),time.min,zone)
        page = await self.life.memory.episodes_between(context,start,end,limit=200,cursor=cursor)
        episodes = tuple(item for item in page.items if item.origin_kind in {"human","observation","tool_result","actor"}
                         and (item.actual_result or {}).get("derived_kind") not in {"reflection","diary"})
        selected = (recipe["episodes"]+list(wire(episodes)))[-64:]
        scanned = recipe["scanned"]+len(episodes)
        digest = signature({"previous":recipe["scan_hash"],"revisions":[(item.episode_id,item.revision) for item in episodes],"coverage":page.coverage})
        missing = sorted(set(recipe["coverage"].get("missing",[]))-{"collection_pending"} | set(page.coverage.missing))
        if scanned>len(selected):
            missing.append("older_experiences_omitted_from_generation")
        refs = unique_refs(tuple(ref for item in selected for ref in source_refs(item["source_refs"])))
        recipe = {**recipe,"cursor":page.next_cursor,"watermark":page.watermark,"scan_hash":digest,"scanned":scanned,
                  "episodes":selected,"material_revisions":[(item["episode_id"],item["revision"]) for item in selected],
                  "coverage":{"complete":not page.next_cursor and not missing,"missing":sorted(set(missing)),
                              "scanned":scanned,"selected":len(selected),"watermark":page.watermark}}
        async with self.life.database.transaction() as connection:
            fresh = await self.life.lock_row(connection,context,"SELECT * FROM life.diaries WHERE id=%s FOR UPDATE",(diary_id,),additional_refs=refs,related_refs=self._version_refs)
            if fresh["generation_id"]!=generation_id or fresh["state"]!="collecting" or fresh["generation_recipe"]["cursor"]!=cursor:
                return
            if not await self.life.sources.validate_persistence(connection,context,refs):
                await connection.execute("UPDATE life.diaries SET state='failed',generation_recipe=NULL,reason='source_unavailable',revision=revision+1 WHERE id=%s",(diary_id,))
                return
            if page.next_cursor:
                await connection.execute("UPDATE life.diaries SET generation_recipe=%s,source_refs=%s WHERE id=%s",(document(recipe),document(refs),diary_id))
                await self.life.jobs.enqueue(connection,JobSpec("life.diary_collect","diary-collect:"+generation_id+":"+signature(page.next_cursor),{"diary_id":diary_id,"generation_id":generation_id,"cursor":page.next_cursor},subject_id=context.actor_id))
                return
            if not selected or fresh["material_key"]==digest:
                await connection.execute("UPDATE life.diaries SET state=%s,reason='diary_no_new_material',generation_recipe=NULL,last_reviewed_at=now(),revision=revision+1 WHERE id=%s",("draft" if fresh["current_revision"] else "empty",diary_id))
                return
            scopes = sorted({scope for item in selected for scope in item["scope_ids"]})
            await connection.execute("UPDATE life.diaries SET state='generating',generation_recipe=%s,episode_ids=%s,source_refs=%s,scope_ids=%s WHERE id=%s",(document(recipe),[item["episode_id"] for item in selected],document(refs),scopes,diary_id))
            await self.life.jobs.enqueue(connection,JobSpec("life.diary_generate","diary:"+generation_id,{"diary_id":diary_id,"generation_id":generation_id},subject_id=context.actor_id))

    async def generate(self, diary_id, generation_id):
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s", (diary_id,))).fetchone()
        if not row or row["generation_id"] != generation_id or row["state"] != "generating":
            return
        context = context_from_record(row["context"])
        refs, recipe = source_refs(row["source_refs"]), row["generation_recipe"]
        dispatched = False
        observed_state = None
        try:
            await self.life.identity.check(context, refs)
            actor = await self.life.companion.actor(context)
            persona = await self.life.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
            instruction = "Write a brief first-person diary for the supplied local day. Facts must come from actual episodes. Feelings are subjective. Do not invent missing time, events, reading coverage, promises or replies. A short diary is fine. Never reproduce hidden model reasoning."
            budget = RequestBudget(datetime.now(UTC)+timedelta(minutes=4))
            pack = await self.assembler.assemble(context, "diary", instruction, budget,
                persona_text=persona.prompt_text if persona else "", reserved_output_tokens=3000,
                contributions=(ContextContribution("diary_material", json.dumps(recipe, ensure_ascii=False), refs, context.scope_ids, 90, True),))
            async def current_generation(connection):
                current = await (await connection.execute("SELECT generation_id,state FROM life.diaries WHERE id=%s FOR UPDATE", (diary_id,))).fetchone()
                return current and current["generation_id"] == generation_id and current["state"] == "generating"
            dispatched = True
            result = await self.generations.run("diary:"+generation_id, ModelRequest("diary:"+generation_id, generation_id, "reflect", ServiceClass.ELASTIC,
                context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=3000,
                estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs)), guard=current_generation)
            if result is None:
                raise AppError("generation_outcome_unknown")
            dispatched = result.receipt.dispatch != "not_started"
            observed_state = result.receipt.status
            frozen = await self.generations.request(generation_id, context)
            refs = frozen.source_refs
            await self.life.identity.check(context, refs)
            content = generated_text(result)
            if result.receipt.status != "succeeded" or not content.strip():
                raise AppError(result.receipt.error_code or "diary_generation_failed")
        except BaseException as error:
            if getattr(error,"code",None)=="generation_request_expired":
                observed_state="failed"
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.diaries SET state=%s,reason=%s,revision=revision+1 WHERE id=%s AND generation_id=%s AND state='generating'", ((observed_state if observed_state in {"failed","cancelled"} else "failed" if observed_state=="succeeded" else "unknown" if dispatched else "failed"), getattr(error, "code", "generation_outcome_unknown"), diary_id, generation_id))
            raise
        await self.save_generated(context, diary_id, generation_id, recipe, result, refs)

    async def save_generated(self, context, diary_id, generation_id, recipe, result, refs):
        content = generated_text(result)
        if result.receipt.status != "succeeded" or not content.strip():
            raise AppError(result.receipt.error_code or "diary_generation_failed")
        async with self.life.database.transaction() as connection:
            current = await self._diary(context, diary_id, connection, lock=True, additional_refs=refs)
            if current["generation_id"] != generation_id or current["generation_cancel_requested"] or current["state"] not in {"generating", "unknown"}:
                return
            if not await self.life.sources.validate_persistence(connection,context,refs):
                await connection.execute("UPDATE life.diaries SET state='failed',generation_recipe=NULL,reason='result_source_unavailable',revision=revision+1 WHERE id=%s",(diary_id,))
                return
            version = current["current_revision"]+1
            await connection.execute("INSERT INTO life.diary_revisions(diary_id,revision,content,model_attempt_id,recipe) VALUES (%s,%s,%s,%s,%s)", (diary_id, version, content, result.receipt.attempt_id, document({**recipe, "source_refs": refs, "model_receipt": result.receipt})))
            await connection.execute("UPDATE life.diaries SET current_revision=%s,material_key=%s,state='draft',revision=revision+1,last_reviewed_at=now(),last_changed_at=now() WHERE id=%s", (version,recipe['scan_hash'], diary_id))
        # This output is a work artifact, not independent evidence for its described events.

    async def revise(self, context, body):
        await self.life.identity.check(context)
        content = text(body.get("content"), "日记正文", 100000)
        async def apply(connection):
            diary = await self._diary(context, body.get("id"), connection, lock=True)
            if diary:
                await self.life.validate_persistence(connection, context, source_refs(diary["source_refs"]))
            revision(diary, body.get("expected_revision"))
            if diary["state"] in {"collecting","generating"}:
                raise AppError("diary_generation_running", status=409)
            version = diary["current_revision"]+1
            refs = await self._version_refs(diary, connection)
            await self.life.validate_persistence(connection, context, refs)
            await connection.execute("INSERT INTO life.diary_revisions(diary_id,revision,content,recipe) VALUES (%s,%s,%s,%s)", (diary["id"], version, content, document({"kind": "author_edit", "source_refs": refs, "episode_ids": diary["episode_ids"]})))
            await connection.execute("UPDATE life.diaries SET current_revision=%s,state='draft',revision=revision+1,last_changed_at=now() WHERE id=%s", (version, diary["id"]))
            return CommandResult(body["client_request_id"], {"id": diary["id"], "content_revision": version}, diary["revision"]+1)
        return await self.life.commands.execute(context, "life.diaries.revise", body["client_request_id"], signature(body), apply)

    async def publish(self, context, body):
        await self.life.identity.check(context)
        async def apply(connection):
            diary = await self._diary(context, body.get("id"), connection)
            revision(diary, body.get("expected_revision"))
            if not diary["current_revision"] or diary["state"] in {"collecting","generating"}:
                raise AppError("diary_not_ready", status=409)
            refs = await self._version_refs(diary, connection)
            current = await (await connection.execute("SELECT content FROM life.diary_revisions WHERE diary_id=%s AND revision=%s",(diary["id"],diary["current_revision"]))).fetchone()
            disclosure = await self.life.sources.disclose(context,producer="life.diary",event_id=diary["id"]+":"+str(diary["current_revision"]),revision=diary["revision"]+1,
                parts=(InputPart("text",current["content"]),),source_refs=refs,reader_ids=tuple(body.get("reader_ids",())),conversation_ids=tuple(body.get("conversation_ids",())),connection=connection)
            diary = await self._diary(context, body.get("id"), connection, lock=True, additional_refs=refs)
            revision(diary, body.get("expected_revision"))
            await connection.execute("UPDATE life.diary_revisions SET disclosure_ref=%s,publication_metadata=%s WHERE diary_id=%s AND revision=%s",(document(disclosure),document({"day":diary["day"]}),diary["id"],diary["current_revision"]))
            await connection.execute("UPDATE life.diaries SET published_revision=current_revision,state='published',revision=revision+1 WHERE id=%s", (diary["id"],))
            return CommandResult(body["client_request_id"], {"id": diary["id"], "published_revision": diary["current_revision"]}, diary["revision"]+1)
        return await self.life.commands.execute(context, "life.diaries.publish", body["client_request_id"], signature(body), apply)
