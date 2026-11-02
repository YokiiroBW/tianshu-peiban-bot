"""Works, pinned generation recipes and immutable chapter revisions."""
from tianshu.sources.dependencies import unique_refs
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from tianshu.models.types import ModelRequest
from tianshu.models.completion import content_completion
from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ContextContribution, Coverage, OriginKind, RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from .validation import revision, text
from . import publications
from .computation_retry import freeze_recipe, outcome
from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from tianshu.conversation.types import InputPart
from tianshu.conversation.generation import GenerationRequests


def can_edit(context):
    return "*" in context.permissions or "life.manage" in context.permissions


def generated_text(result):
    if content_completion(result).state == "incomplete":
        return ""
    return "".join(part.text or "" for part in result.parts
                   if part.kind == "text" and not part.native.get("thought"))


def generation_error(result, fallback):
    return "generation_content_incomplete" if content_completion(result).state == "incomplete" else result.receipt.error_code or fallback


class WritingService:
    def __init__(self, life, models, assembler):
        self.life, self.models, self.assembler = life, models, assembler
        self.generations = GenerationRequests(life.database, life.sources, models)
        self.on_completed = None

    def bind_completion(self, callback):
        self.on_completed = callback

    async def list(self, context, *, cursor=None, limit=30):
        await self.life.identity.check(context)
        limit = min(100, max(1, int(limit)))
        if not can_edit(context):
            async with self.life.database.transaction(read_only=True) as connection:
                rows = await (await connection.execute(
                    "SELECT w.id,w.actor_id,w.title,count(c.id) AS chapter_count FROM life.works w "
                    "JOIN life.chapters c ON c.work_id=w.id JOIN life.chapter_revisions r ON r.chapter_id=c.id AND r.revision=c.published_revision "
                    "WHERE w.actor_id=%s AND r.disclosure_ref IS NOT NULL AND (%s::text IS NULL OR w.id>%s) "
                    "GROUP BY w.id ORDER BY w.id LIMIT %s",(context.actor_id,cursor,cursor,limit+1))).fetchall()
                refs = await (await connection.execute(
                    "SELECT c.work_id,r.disclosure_ref,r.publication_metadata FROM life.chapters c JOIN life.chapter_revisions r ON r.chapter_id=c.id AND r.revision=c.published_revision WHERE c.work_id=ANY(%s) AND r.disclosure_ref IS NOT NULL",([row["id"] for row in rows[:limit]],))).fetchall()
            allowed = await self.life.identity.check_many(context,tuple(publications.reference(row) for row in refs))
            visible = {row["work_id"] for row,valid in zip(refs,allowed,strict=True) if valid}
            counts = {work_id:sum(1 for row,valid in zip(refs,allowed,strict=True) if valid and row["work_id"]==work_id) for work_id in visible}
            more = len(rows)>limit
            return Page(tuple({"id":row["id"],"actor_id":row["actor_id"],"title":next(item["publication_metadata"]["work_title"] for item,valid in zip(refs,allowed,strict=True) if valid and item["work_id"]==row["id"]),"chapter_count":counts[row["id"]]} for row in rows[:limit] if row["id"] in visible),rows[limit-1]["id"] if more else None,Coverage(not more and len(visible)==len(rows[:limit])))
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT w.*, (SELECT count(*) FROM life.chapters c WHERE c.work_id=w.id) AS chapter_count "
                "FROM life.works w WHERE actor_id=%s AND scope_ids<@%s::text[] "
                "AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, list(context.scope_ids), cursor, cursor, limit+1))).fetchall()
        return await self.life._page(context, rows, limit)

    async def _work(self, context, work_id, connection, *, lock=False):
        query = "SELECT * FROM life.works WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]"
        parameters = (work_id, context.actor_id, list(context.scope_ids))
        row = (await self.life.lock_row(connection, context, query+" FOR UPDATE", parameters)
               if lock else await (await connection.execute(query, parameters)).fetchone())
        if not row:
            raise AppError("work_not_found", status=404)
        return row

    async def _chapter(self, context, chapter_id, connection, *, lock=False, additional_refs=()):
        query = ("SELECT c.*,w.actor_id,w.scope_ids,w.source_refs FROM life.chapters c JOIN life.works w ON w.id=c.work_id "
                 "WHERE c.id=%s AND w.actor_id=%s AND w.scope_ids<@%s::text[]")
        parameters = (chapter_id, context.actor_id, list(context.scope_ids))
        row = (await self.life.lock_row(connection, context, query+" FOR UPDATE OF c", parameters,
                                       additional_refs=additional_refs, related_refs=self._version_refs)
               if lock else await (await connection.execute(query, parameters)).fetchone())
        if not row:
            raise AppError("chapter_not_found", status=404)
        return row

    async def _version_refs(self, chapter, connection):
        current = await (await connection.execute("SELECT recipe FROM life.chapter_revisions WHERE chapter_id=%s AND revision=%s", (chapter["id"], chapter["current_revision"]))).fetchone()
        return unique_refs(tuple(ref for ref in (
            *source_refs(chapter["source_refs"]), *(source_refs(current["recipe"]["source_refs"]) if current else ()))))

    async def save_work(self, context, body):
        await self.life.identity.check(context)
        title = text(body.get("title"), "作品名称", 1000)
        fields = {key: text(body.get(key, ""), key, 100000, empty=True) for key in ("synopsis", "outline", "characters")}
        async def apply(connection):
            work_id = body.get("id") or str(uuid4())
            row = await self._work(context, work_id, connection, lock=True) if body.get("id") else None
            revision(row, body.get("expected_revision"))
            current = row["revision"] + 1 if row else 1
            if row:
                await connection.execute("UPDATE life.works SET title=%s,synopsis=%s,outline=%s,characters=%s,revision=%s,updated_at=now() WHERE id=%s", (title, fields["synopsis"], fields["outline"], fields["characters"], current, work_id))
            else:
                await connection.execute("INSERT INTO life.works(id,actor_id,title,synopsis,outline,characters,scope_ids) VALUES (%s,%s,%s,%s,%s,%s,%s)", (work_id, context.actor_id, title, fields["synopsis"], fields["outline"], fields["characters"], list(context.scope_ids)))
            return CommandResult(body["client_request_id"], {"id": work_id, "revision": current}, current)
        return await self.life.commands.execute(context, "life.works.save", body["client_request_id"], signature(body), apply)

    async def chapters(self, context, body):
        await self.life.identity.check(context)
        limit = min(100, max(1, int(body.get("limit", 40))))
        if not can_edit(context):
            async with self.life.database.transaction(read_only=True) as connection:
                work = await (await connection.execute("SELECT id,actor_id,title FROM life.works WHERE id=%s AND actor_id=%s",(body.get("work_id"),context.actor_id))).fetchone()
                if not work:
                    raise AppError("work_not_found",status=404)
                rows = await (await connection.execute(
                    "SELECT c.id,c.work_id,c.position,c.published_revision,r.disclosure_ref,r.publication_metadata FROM life.chapters c "
                    "JOIN life.chapter_revisions r ON r.chapter_id=c.id AND r.revision=c.published_revision "
                    "WHERE c.work_id=%s AND r.disclosure_ref IS NOT NULL AND (%s::int IS NULL OR c.position>%s) ORDER BY c.position LIMIT %s",
                    (work["id"],body.get("cursor"),body.get("cursor"),limit+1))).fetchall()
            page = await publications.page(self.life.identity,context,rows,limit,publications.chapter,cursor_key="position")
            public_work={"id":work["id"],"actor_id":work["actor_id"]}
            return {"work":public_work,"chapters":page}
        async with self.life.database.transaction(read_only=True) as connection:
            work = await self._work(context, body.get("work_id"), connection)
            rows = await (await connection.execute(
                "SELECT * FROM life.chapters WHERE work_id=%s AND (%s OR published_revision IS NOT NULL) "
                "AND (%s::int IS NULL OR position>%s) ORDER BY position LIMIT %s",
                (work["id"], can_edit(context), body.get("cursor"), body.get("cursor"), limit+1))).fetchall()
        await self.life.identity.check(context, source_refs(work["source_refs"]))
        more = len(rows)>limit
        return {"work": work, "chapters": Page(tuple(rows[:limit]), str(rows[limit-1]["position"]) if more else None, Coverage(not more))}

    async def read(self, context, body):
        await self.life.identity.check(context)
        if not can_edit(context):
            async with self.life.database.transaction(read_only=True) as connection:
                row = await (await connection.execute(
                    "SELECT c.id,c.work_id,c.published_revision,r.content,r.created_at AS published_at,r.disclosure_ref,r.publication_metadata "
                    "FROM life.chapters c JOIN life.works w ON w.id=c.work_id JOIN life.chapter_revisions r ON r.chapter_id=c.id AND r.revision=c.published_revision "
                    "WHERE c.id=%s AND w.actor_id=%s",(body.get("id"),context.actor_id))).fetchone()
            await publications.readable(self.life.identity,context,row)
            return {"chapter":publications.chapter(row),"version":publications.version(row)}
        async with self.life.database.transaction(read_only=True) as connection:
            chapter = await self._chapter(context, body.get("id"), connection)
            version = body.get("revision") if can_edit(context) else chapter["published_revision"]
            version = version or chapter["current_revision"] if can_edit(context) else version
            row = await (await connection.execute("SELECT * FROM life.chapter_revisions WHERE chapter_id=%s AND revision=%s", (chapter["id"], version))).fetchone()
        if not row:
            raise AppError("chapter_revision_not_found", status=404)
        await self.life.identity.check(context, source_refs(row["recipe"]["source_refs"]))
        return {"chapter": chapter, "version": row}

    async def add_chapter(self, context, body):
        await self.life.identity.check(context)
        title, plan = text(body.get("title"), "章节标题", 1000), text(body.get("plan", ""), "章节计划", 20000, empty=True)
        async def apply(connection):
            work = await self._work(context, body.get("work_id"), connection, lock=True)
            if work:
                await self.life.validate_persistence(connection, context, source_refs(work["source_refs"]))
            revision(work, body.get("expected_revision"))
            position = (await (await connection.execute("SELECT COALESCE(max(position),0)+1 AS position FROM life.chapters WHERE work_id=%s", (work["id"],))).fetchone())["position"]
            chapter_id = str(uuid4())
            await connection.execute("INSERT INTO life.chapters(id,work_id,position,title,plan) VALUES (%s,%s,%s,%s,%s)", (chapter_id, work["id"], position, title, plan))
            await connection.execute("UPDATE life.works SET revision=revision+1 WHERE id=%s", (work["id"],))
            return CommandResult(body["client_request_id"], {"id": chapter_id, "revision": 1, "work_revision": work["revision"]+1}, 1)
        return await self.life.commands.execute(context, "life.chapters.add", body["client_request_id"], signature(body), apply)

    async def request_generation(self, context, body):
        await self.life.identity.check(context)
        instruction = text(body.get("instruction", ""), "创作要求", 20000, empty=True)
        async def apply(connection):
            chapter = await self._chapter(context, body.get("id"), connection)
            revision(chapter, body.get("expected_revision"))
            if chapter["state"] in {"generating", "unknown", "cancelling"}:
                raise AppError("chapter_generation_unresolved", status=409, details={"attempt_id": chapter["generation_id"]})
            work = await self._work(context, chapter["work_id"], connection)
            previous = await (await connection.execute(
                "SELECT c.id,c.title,c.current_revision,r.content,r.recipe->'source_refs' AS source_refs FROM life.chapters c JOIN life.chapter_revisions r "
                "ON r.chapter_id=c.id AND r.revision=c.current_revision WHERE c.work_id=%s AND c.position<%s ORDER BY c.position DESC LIMIT 3",
                (work["id"], chapter["position"]))).fetchall()
            attempt_id = str(uuid4())
            refs = unique_refs(tuple(ref for ref in (
                *source_refs(work["source_refs"]), *(ref for chapter in previous for ref in source_refs(chapter["source_refs"] or ())))))
            chapter = await self._chapter(context, body.get("id"), connection, lock=True, additional_refs=refs)
            revision(chapter, body.get("expected_revision"))
            recipe = {"work_revision": work["revision"], "title": work["title"], "synopsis": work["synopsis"],
                "outline": work["outline"], "characters": work["characters"], "chapter_title": chapter["title"],
                "plan": chapter["plan"], "instruction": instruction, "previous": previous, "source_refs": refs}
            next_revision = chapter["revision"]+1
            await connection.execute("UPDATE life.chapters SET state='generating',generation_cancel_requested=false,generation_id=%s,generation_context=%s,generation_recipe=%s,reason=NULL,revision=%s,generation_retry_count=0,generation_retry_due_at=NULL WHERE id=%s", (attempt_id, document(context), document(recipe), next_revision, chapter["id"]))
            await self.life.jobs.enqueue(connection, JobSpec("life.chapter_generate", "chapter:"+attempt_id,
                {"chapter_id": chapter["id"], "generation_id": attempt_id}, subject_id=context.actor_id))
            return CommandResult(body["client_request_id"], {"id": chapter["id"], "state": "generating", "revision": next_revision}, next_revision, attempt_id=attempt_id)
        return await self.life.commands.execute(context, "life.chapters.generate", body["client_request_id"], signature(body), apply)

    async def generate(self, chapter_id, generation_id):
        async with self.life.database.transaction(read_only=True) as connection:
            chapter = await (await connection.execute("SELECT * FROM life.chapters WHERE id=%s", (chapter_id,))).fetchone()
        if not chapter or chapter["generation_id"] != generation_id or chapter["state"] not in {"generating","unknown","cancelling"}:
            return
        context, recipe = context_from_record(chapter["generation_context"]), chapter["generation_recipe"]
        refs = source_refs(recipe["source_refs"])
        frozen, result = None, None
        try:
            context=await self.life.identity.refresh_scheduled_context(context,permission="life.manage")
            self.generations.observer_context(context_from_record(chapter["generation_context"]),context)
            await self.life.identity.check(context, refs)
            frozen=await self.generations.observe_request(generation_id,observer=context)
            if frozen is not None:
                async with self.life.database.transaction() as connection:
                    await freeze_recipe(self.life,connection,"chapter",chapter_id,generation_id,context,recipe,refs)
                result=await self.generations.read(generation_id,frozen.context,observer=context)
                receipt=await self.models.status(generation_id)
                if result is not None or receipt is not None or chapter["state"]!="generating":
                    if result is not None and result.receipt.status=="succeeded":
                        if receipt and getattr(receipt,"cancellation_reason",None)=="explicit_cancel":
                            return await self._failed_generation(chapter,context,result=result,error="explicit_cancel",frozen=frozen)
                        return await self.save_generated(context,chapter_id,generation_id,recipe,result,frozen.source_refs)
                    return await self._failed_generation(chapter,context,result=result,frozen=frozen)
            if chapter["generation_cancel_requested"]:
                return await self._failed_generation(chapter,context,error="explicit_cancel",frozen=frozen)
            actor = await self.life.companion.actor(context)
            persona = await self.life.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
            budget = RequestBudget(datetime.now(UTC)+timedelta(minutes=10))
            pack = await self.assembler.assemble(context, "writing", recipe["instruction"] or recipe["chapter_title"], budget,
                persona_text=persona.prompt_text if persona else "", max_input_tokens=32000, reserved_output_tokens=6000,
                contributions=(ContextContribution("writing_recipe", json.dumps(recipe, ensure_ascii=False, default=str), refs, context.scope_ids, 90, True, str(recipe["work_revision"])),))
            async def current_generation(connection):
                current = await (await connection.execute("SELECT generation_id,state,generation_cancel_requested FROM life.chapters WHERE id=%s FOR UPDATE", (chapter_id,))).fetchone()
                return current and current["generation_id"] == generation_id and current["state"] == "generating" and not current["generation_cancel_requested"]
            request=ModelRequest("chapter:"+generation_id, generation_id, "writing", ServiceClass.ELASTIC,
                context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=6000,
                execution_timeout_seconds=600,
                estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs))
            async with self.life.database.transaction() as connection:
                frozen=await self.generations.prepare("chapter:"+generation_id,request,connection=connection,guard=current_generation)
                await freeze_recipe(self.life,connection,"chapter",chapter_id,generation_id,context,recipe,refs)
            result = await self.generations.run("chapter:"+generation_id,frozen,guard=current_generation)
            if result is None:
                return await self._failed_generation(chapter,context,frozen=frozen)
            frozen = await self.generations.request(generation_id, context)
            refs = frozen.source_refs
            await self.life.identity.check(context, refs)
            content = generated_text(result)
            if result.receipt.status != "succeeded" or not content.strip():
                if result.receipt.status!="succeeded":
                    return await self._failed_generation(chapter,context,result=result,frozen=frozen)
                raise AppError(generation_error(result, "chapter_generation_failed"), details={"dispatch": result.receipt.dispatch})
        except BaseException as error:
            code=getattr(error,"code",type(error).__name__)
            if transient_model_failure(code) or not isinstance(error,Exception):
                from tianshu.runtime.cancellation import finish_cleanup
                await finish_cleanup(self._failed_generation(chapter,context,error=error,frozen=frozen))
                raise
            async with self.life.database.transaction() as connection:
                await connection.execute("UPDATE life.chapters SET state='failed',reason=%s,generation_retry_due_at=NULL,revision=revision+1 "
                    "WHERE id=%s AND generation_id=%s AND state IN ('generating','unknown','cancelling')",
                    (code,chapter_id,generation_id))
            raise
        latest=await self.models.status(generation_id)
        if latest and getattr(latest,"cancellation_reason",None)=="explicit_cancel":
            return await self._failed_generation(chapter,context,result=result,error="explicit_cancel",frozen=frozen)
        await self.save_generated(context, chapter_id, generation_id, recipe, result, refs)

    async def _failed_generation(self,chapter,context,*,result=None,error=None,frozen=None):
        try:
            status,code,retry=await outcome(self.models,chapter["generation_id"],result=result,error=error)
        except Exception:
            status,code,retry="running","model_observation_unavailable",False
        if error=="explicit_cancel":
            code,retry="explicit_cancel",False
        if status in {"queued","running","succeeded"}:
            retry=False
        async with self.life.database.transaction() as connection:
            current=await (await connection.execute("SELECT * FROM life.chapters WHERE id=%s FOR UPDATE",(chapter["id"],))).fetchone()
            if (not current or current["generation_id"]!=chapter["generation_id"]
                    or current["state"] not in {"generating","unknown","cancelling"}):
                return
            cancel=current["generation_cancel_requested"] or code=="explicit_cancel"
            if (not cancel and retry and status not in {"queued","running","succeeded"}
                    and current["generation_retry_due_at"] is not None and current["generation_retry_due_at"]<=datetime.now(UTC)):
                pass
            else:
                live=status in {"queued","running","succeeded"} and code!="explicit_cancel"
                count=current["generation_retry_count"]+(1 if retry and current["generation_retry_due_at"] is None else 0)
                due=(current["generation_retry_due_at"] or datetime.now(UTC)+timedelta(seconds=retry_delay_seconds(count))) if retry and not cancel else datetime.now(UTC)+timedelta(seconds=30) if live else None
                state="cancelling" if cancel and live else "cancelled" if cancel else "generating" if live else "unknown" if retry else "cancelled" if status=="cancelled" else "failed"
                await connection.execute("UPDATE life.chapters SET state=%s,reason=%s,generation_retry_count=%s,generation_retry_due_at=%s,revision=revision+1 WHERE id=%s",
                    (state,code,count,due,current["id"]))
                if due:
                    await self.life.jobs.enqueue(connection,JobSpec("life.chapter_generate","chapter-observe:"+str(uuid4()),
                        {"chapter_id":current["id"],"generation_id":current["generation_id"]},scheduled_at=due,subject_id=context.actor_id),
                        queueing_lock="chapter-generate:"+current["generation_id"])
                return
        refs=source_refs(current["generation_recipe"]["source_refs"])
        async with self.life.database.transaction() as connection:
            await self.life.validate_persistence(connection,context,refs)
            fresh=await self._chapter(context,current["id"],connection,lock=True,additional_refs=refs)
            if (fresh["generation_id"]!=current["generation_id"] or fresh["generation_cancel_requested"]
                    or fresh["state"] not in {"generating","unknown"}
                    or fresh["generation_retry_due_at"] is None or fresh["generation_retry_due_at"]>datetime.now(UTC)):
                return
            work=await self._work(context,fresh["work_id"],connection)
            if work["revision"]!=current["generation_recipe"]["work_revision"]:
                await connection.execute("UPDATE life.chapters SET state='failed',generation_retry_due_at=NULL,reason='writing_material_changed',revision=revision+1 WHERE id=%s",(fresh["id"],))
                return
            generation_id=str(uuid4())
            await freeze_recipe(self.life,connection,"chapter",fresh["id"],fresh["generation_id"],context,fresh["generation_recipe"],refs)
            await connection.execute("UPDATE life.chapters SET state='generating',generation_id=%s,generation_context=%s,reason=NULL,generation_retry_due_at=NULL,revision=revision+1 WHERE id=%s",
                (generation_id,document(context),fresh["id"]))
            if frozen is not None:
                request=ModelRequest("chapter:"+generation_id,generation_id,"writing",ServiceClass.ELASTIC,context,
                    RequestBudget(datetime.now(UTC)+timedelta(seconds=600)),frozen.messages,system=frozen.system,
                    max_output_tokens=frozen.max_output_tokens,estimated_input_tokens=frozen.estimated_input_tokens,
                    source_refs=frozen.source_refs,execution_timeout_seconds=600)
                await self.generations.prepare("chapter:"+generation_id,request,connection=connection)
            await freeze_recipe(self.life,connection,"chapter",fresh["id"],generation_id,context,fresh["generation_recipe"],refs,previous=fresh["generation_id"])
            await self.life.jobs.enqueue(connection,JobSpec("life.chapter_generate","chapter:"+generation_id,
                {"chapter_id":fresh["id"],"generation_id":generation_id},subject_id=context.actor_id))

    async def save_generated(self, context, chapter_id, generation_id, recipe, result, refs):
        content = generated_text(result)
        if result.receipt.status != "succeeded" or not content.strip():
            raise AppError(generation_error(result, "chapter_generation_failed"))
        async with self.life.database.transaction() as connection:
            fresh = await self._chapter(context, chapter_id, connection, lock=True, additional_refs=refs)
            if fresh["generation_id"] != generation_id or fresh["generation_cancel_requested"] or fresh["state"] not in {"generating", "unknown"}:
                return
            if not await self.life.sources.validate_persistence(connection,context,refs):
                await connection.execute("UPDATE life.chapters SET state='failed',generation_recipe=NULL,reason='result_source_unavailable',revision=revision+1 WHERE id=%s",(chapter_id,))
                return
            work=await self._work(context,fresh["work_id"],connection,lock=True)
            if work["revision"]!=recipe["work_revision"]:
                await connection.execute("UPDATE life.chapters SET state='failed',generation_retry_due_at=NULL,reason='writing_material_changed',revision=revision+1 WHERE id=%s",(chapter_id,))
                return
            version = fresh["current_revision"]+1
            saved = {"chapter_id": chapter_id, "work_id": fresh["work_id"],
                "content_revision": version, "generation_id": generation_id,
                "model_attempt_id": result.receipt.attempt_id, "content_signature": signature(content),
                "character_count": len(content), "representation": "saved_chapter_revision"}
            originals = await self.life.sources.record_event(context, producer="life.chapter.generated",
                event_id=chapter_id, revision=version, occurred_at=datetime.now(UTC),
                # The immutable chapter remains Writing's authority. Its save receipt
                # proves progress without disclosing an unpublished draft via Sources.
                parts=(InputPart("text", json.dumps(saved, ensure_ascii=False)),), origin_kind=OriginKind.ACTOR,
                support_refs=refs, connection=connection)
            refs = unique_refs(refs, originals)
            await connection.execute("INSERT INTO life.chapter_revisions(chapter_id,revision,content,work_revision,model_attempt_id,recipe) VALUES (%s,%s,%s,%s,%s,%s)", (chapter_id, version, content, recipe["work_revision"], result.receipt.attempt_id, document({**recipe, "source_refs": refs, "completion_source_refs": originals, "model_receipt": result.receipt})))
            await connection.execute("UPDATE life.chapters SET current_revision=%s,state='draft',review_state=NULL,review_note=NULL,generation_retry_count=0,generation_retry_due_at=NULL,revision=revision+1 WHERE id=%s", (version, chapter_id))
            if self.on_completed is not None:
                await self.on_completed(context, {**fresh, "current_revision": version,
                    "revision": fresh["revision"]+1, "state": "draft", "generation_id": generation_id,
                    "model_attempt_id": result.receipt.attempt_id, "completion_source_refs": originals}, refs, connection)

    async def revise(self, context, body):
        await self.life.identity.check(context)
        content = text(body.get("content"), "章节正文", 500000)
        async def apply(connection):
            chapter = await self._chapter(context, body.get("id"), connection, lock=True)
            if chapter:
                await self.life.validate_persistence(connection, context, source_refs(chapter["source_refs"]))
            revision(chapter, body.get("expected_revision"))
            if chapter["state"] == "generating":
                raise AppError("chapter_generation_running", status=409)
            work = await self._work(context, chapter["work_id"], connection)
            version = chapter["current_revision"]+1
            refs = await self._version_refs(chapter, connection)
            await self.life.validate_persistence(connection, context, refs)
            await connection.execute("INSERT INTO life.chapter_revisions(chapter_id,revision,content,work_revision,recipe) VALUES (%s,%s,%s,%s,%s)", (chapter["id"], version, content, work["revision"], document({"kind": "author_edit", "source_refs": refs})))
            await connection.execute("UPDATE life.chapters SET current_revision=%s,state='draft',review_state=NULL,review_note=NULL,revision=revision+1 WHERE id=%s", (version, chapter["id"]))
            return CommandResult(body["client_request_id"], {"id": chapter["id"], "content_revision": version}, chapter["revision"]+1)
        return await self.life.commands.execute(context, "life.chapters.revise", body["client_request_id"], signature(body), apply)

    async def review(self, context, body):
        await self.life.identity.check(context)
        state = body.get("state")
        if state not in {"approved", "changes_requested"}:
            raise AppError("invalid_review_state")
        note = text(body.get("note", ""), "审校意见", 20000, empty=True)
        async def apply(connection):
            chapter = await self._chapter(context, body.get("id"), connection, lock=True)
            if chapter:
                await self.life.validate_persistence(connection, context, source_refs(chapter["source_refs"]))
            revision(chapter, body.get("expected_revision"))
            if not chapter["current_revision"] or chapter["state"] == "generating":
                raise AppError("chapter_not_ready", status=409)
            await self.life.validate_persistence(connection, context, await self._version_refs(chapter, connection))
            await connection.execute("UPDATE life.chapters SET review_state=%s,review_note=%s,revision=revision+1 WHERE id=%s", (state, note, chapter["id"]))
            return CommandResult(body["client_request_id"], {"id": chapter["id"], "review_state": state}, chapter["revision"]+1)
        return await self.life.commands.execute(context, "life.chapters.review", body["client_request_id"], signature(body), apply)

    async def publish(self, context, body):
        await self.life.identity.check(context)
        async def apply(connection):
            chapter = await self._chapter(context, body.get("id"), connection)
            revision(chapter, body.get("expected_revision"))
            if chapter["state"] == "generating" or chapter["review_state"] != "approved":
                raise AppError("chapter_review_required", status=409)
            refs = await self._version_refs(chapter,connection)
            work = await self._work(context,chapter["work_id"],connection)
            current = await (await connection.execute("SELECT content FROM life.chapter_revisions WHERE chapter_id=%s AND revision=%s",(chapter["id"],chapter["current_revision"]))).fetchone()
            disclosure = await self.life.sources.disclose(context,producer="life.chapter",event_id=chapter["id"]+":"+str(chapter["current_revision"]),revision=chapter["revision"]+1,
                parts=(InputPart("text",current["content"]),),source_refs=refs,reader_ids=tuple(body.get("reader_ids",())),conversation_ids=tuple(body.get("conversation_ids",())),connection=connection)
            chapter = await self._chapter(context, body.get("id"), connection, lock=True, additional_refs=refs)
            revision(chapter, body.get("expected_revision"))
            await connection.execute("UPDATE life.chapter_revisions SET disclosure_ref=%s,publication_metadata=%s WHERE chapter_id=%s AND revision=%s",(document(disclosure),document({"title":chapter["title"],"work_title":work["title"],"position":chapter["position"]}),chapter["id"],chapter["current_revision"]))
            await connection.execute("UPDATE life.chapters SET published_revision=current_revision,state='published',revision=revision+1 WHERE id=%s", (chapter["id"],))
            return CommandResult(body["client_request_id"], {"id": chapter["id"], "published_revision": chapter["current_revision"]}, chapter["revision"]+1)
        return await self.life.commands.execute(context, "life.chapters.publish", body["client_request_id"], signature(body), apply)
