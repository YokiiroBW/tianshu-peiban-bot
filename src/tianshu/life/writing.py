"""Works, pinned generation recipes and immutable chapter revisions."""
from tianshu.sources.dependencies import unique_refs
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from tianshu.models.types import ModelRequest
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.models.completion import content_completion
from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ContextContribution, Coverage, OriginKind, RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from .validation import revision, text
from . import publications
from .computation_retry import finished_unknown, freeze_recipe, outcome
from tianshu.models.retry import transient_model_failure
from tianshu.conversation.types import InputPart
from tianshu.conversation.generation import GenerationRequests
from .generation_control import (GenerationRecovery, backfill_saved_statistics, defer_generation,
    frozen_data_generation, observe_original, record_saved_on, submit_frozen)


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
        self.prepare_completion = None
        self.recovery = None
        self.statistics = None

    def bind_observability(self, *, recovery_policy, statistics):
        self.recovery = GenerationRecovery(recovery_policy,life=self.life)
        self.statistics = statistics

    async def backfill_statistics(self, *, limit=128, data_generation):
        return await backfill_saved_statistics(self.life,self.statistics,"chapter",limit=limit,data_generation=data_generation)

    def bind_completion(self, callback, *, prepare=None):
        self.on_completed = callback
        self.prepare_completion = prepare

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

    async def _previous_current(self, connection, work_id, recipe):
        previous = recipe.get("previous") or ()
        if not previous:
            return True
        expected = {(item["id"],item["current_revision"]) for item in previous}
        rows = await (await connection.execute(
            "SELECT id,current_revision FROM life.chapters WHERE work_id=%s AND id=ANY(%s) ORDER BY id FOR SHARE",
            (work_id,[item["id"] for item in previous]))).fetchall()
        return expected == {(item["id"],item["current_revision"]) for item in rows}

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
            data_generation=await self.life.jobs.current_generation_on(connection)
            await self.life.jobs.assert_generation_on(connection,data_generation)
            recipe["data_generation"]=data_generation
            next_revision = chapter["revision"]+1
            await connection.execute("UPDATE life.chapters SET state='generating',generation_cancel_requested=false,generation_id=%s,generation_context=%s,generation_recipe=%s,reason=NULL,revision=%s,generation_retry_count=0,generation_retry_due_at=NULL WHERE id=%s", (attempt_id, document(context), document(recipe), next_revision, chapter["id"]))
            await self.life.jobs.enqueue(connection, JobSpec("life.chapter_generate", "chapter:"+attempt_id,
                {"chapter_id": chapter["id"], "generation_id": attempt_id,"data_generation":data_generation}, subject_id=context.actor_id,data_generation=data_generation))
            return CommandResult(body["client_request_id"], {"id": chapter["id"], "state": "generating", "revision": next_revision}, next_revision, attempt_id=attempt_id)
        return await self.life.commands.execute(context, "life.chapters.generate", body["client_request_id"], signature(body), apply)

    async def _observe_later(self, chapter_id, generation_id, actor_id, data_generation):
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            await self.life.jobs.enqueue(connection,JobSpec("life.chapter_generate","chapter-observe:"+str(uuid4()),
                {"chapter_id":chapter_id,"generation_id":generation_id,"data_generation":data_generation},
                scheduled_at=datetime.now(UTC)+timedelta(seconds=30),subject_id=actor_id,data_generation=data_generation),
                queueing_lock="chapter-generate:"+generation_id)

    async def generate(self, chapter_id, generation_id, data_generation=None):
        async with self.life.database.transaction(read_only=True) as connection:
            chapter = await (await connection.execute("SELECT * FROM life.chapters WHERE id=%s", (chapter_id,))).fetchone()
        if not chapter or chapter["generation_id"] != generation_id or chapter["state"] not in {"generating","unknown","cancelling"}:
            return
        data_generation=frozen_data_generation(chapter["generation_recipe"],data_generation)
        context, recipe = context_from_record(chapter["generation_context"]), chapter["generation_recipe"]
        refs = source_refs(recipe["source_refs"])
        frozen, result = None, None
        try:
            context=await self.life.identity.refresh_scheduled_context(context,permission="life.manage")
            self.generations.observer_context(context_from_record(chapter["generation_context"]),context)
            await self.life.identity.check(context, refs)
            frozen=await self.generations.observe_request(generation_id,observer=context)
            async def current_generation(connection):
                await self.life.jobs.assert_generation_on(connection,data_generation)
                current = await (await connection.execute("SELECT generation_id,state,generation_cancel_requested,work_id FROM life.chapters WHERE id=%s FOR UPDATE", (chapter_id,))).fetchone()
                if not current or current["generation_id"] != generation_id or current["state"] != "generating" or current["generation_cancel_requested"]:
                    return False
                work = await self._work(context,current["work_id"],connection)
                return work["revision"] == recipe["work_revision"] and await self._previous_current(connection,current["work_id"],recipe)
            if frozen is None:
                if chapter["generation_cancel_requested"]:
                    return await self._failed_generation(chapter,context,error="explicit_cancel")
                if chapter["state"] != "generating":
                    raise AppError("generation_request_not_found",status=409)
                actor = await self.life.companion.actor(context)
                persona = await self.life.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
                budget = RequestBudget(datetime.now(UTC)+timedelta(minutes=10))
                pack = await self.assembler.assemble(context, "writing", recipe["instruction"] or recipe["chapter_title"], budget,
                    persona_text=persona.prompt_text if persona else "", max_input_tokens=BACKGROUND_INPUT_TOKENS + 6000, reserved_output_tokens=6000,
                    contributions=(ContextContribution("writing_recipe", json.dumps(recipe, ensure_ascii=False, default=str), refs, context.scope_ids, 90, True, str(recipe["work_revision"])),))
                request=ModelRequest("chapter:"+generation_id, generation_id, "writing", ServiceClass.ELASTIC,
                    context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=6000,
                    execution_timeout_seconds=600,
                    estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs),
                    data_generation=data_generation)
                profile=await self.models.generation_profile(replace(request,budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=30))))
                request=replace(request,route_id=profile["route_id"],route_revision=profile["route_revision"],
                    protocol=profile["protocol"])
                async with self.life.database.transaction() as connection:
                    frozen=await self.generations.prepare("chapter:"+generation_id,request,connection=connection,guard=current_generation)
                    await freeze_recipe(self.life,connection,"chapter",chapter_id,generation_id,context,recipe,refs,known_prepared=True)
            else:
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.assert_generation_on(connection,data_generation)
                    await freeze_recipe(self.life,connection,"chapter",chapter_id,generation_id,context,recipe,refs)
            try:
                receipt,pre_submit=await observe_original(self.life,self.models,generation_id,data_generation)
            except Exception as error:
                from tianshu.runtime.recovery_policy import RecoveryWait
                if isinstance(error,RecoveryWait) or getattr(error,"database_failure",None) is not None or getattr(error,"code",None)=="recovery_claim_lost":
                    raise
                await self._observe_later(chapter_id,generation_id,context.actor_id,data_generation)
                return {"state":chapter["state"],"attempt_id":generation_id,"reason":"model_observation_unavailable"}
            if receipt is None and pre_submit and chapter["state"] == "generating" and not chapter["generation_cancel_requested"]:
                if frozen.context.access_epoch != context.access_epoch:
                    raise AppError("writing_original_authorization_changed",status=409)
                completion = JobSpec("life.chapter_generate", "chapter-complete:"+generation_id,
                    {"chapter_id":chapter_id,"generation_id":generation_id,"data_generation":data_generation}, subject_id=context.actor_id,data_generation=data_generation)
                receipt = await submit_frozen(self.life,self.models,self.generations,"chapter:"+generation_id,
                    frozen,guard=current_generation,completion=completion,kind="chapter",item_id=chapter_id,
                    recovery=self.recovery)
            if receipt is None or receipt.status in {"queued", "running"}:
                await self._observe_later(chapter_id,generation_id,context.actor_id,data_generation)
                return {"state":"generating","attempt_id":generation_id}
            if receipt.cancellation_reason == "explicit_cancel":
                return await self._failed_generation(chapter,context,error="explicit_cancel",frozen=frozen)
            if receipt.status == "succeeded":
                reader=self.generations.observer_context(frozen.context,context)
                result=await self.models.reusable_result(generation_id,context=reader,
                    expected_request_digest=receipt.request_digest,expected_data_generation=data_generation)
            if result is None:
                return await self._failed_generation(chapter,context,frozen=frozen)
            refs = frozen.source_refs
            await self.life.identity.check(context, refs)
            if result.receipt.status == "succeeded" and self.recovery is not None:
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.assert_generation_on(connection,data_generation)
                    await self.recovery.observe_on(connection,generation_id,"succeeded")
            content = generated_text(result)
            if result.receipt.status != "succeeded" or not content.strip():
                if result.receipt.status!="succeeded":
                    return await self._failed_generation(chapter,context,result=result,frozen=frozen)
                raise AppError(generation_error(result, "chapter_generation_failed"), details={"dispatch": result.receipt.dispatch})
        except BaseException as error:
            from tianshu.runtime.recovery_policy import RecoveryWait
            if isinstance(error, RecoveryWait):
                return await defer_generation(self.life,"chapter",chapter_id,generation_id,context.actor_id,data_generation,error)
            if getattr(error,"database_failure",None) is not None:
                raise
            code=getattr(error,"code",type(error).__name__)
            if code == "recovery_claim_lost":
                raise
            if code in {"job_generation_expired","job_generation_missing"}:
                raise
            if code == "recovery_attempt_not_dispatchable":
                return await self._failed_generation(chapter,context,error=error,frozen=frozen)
            if transient_model_failure(code) or not isinstance(error,Exception):
                from tianshu.runtime.cancellation import finish_cleanup
                await finish_cleanup(self._failed_generation(chapter,context,error=error,frozen=frozen))
                raise
            async with self.life.database.transaction() as connection:
                await self.life.jobs.assert_generation_on(connection,data_generation)
                await connection.execute("UPDATE life.chapters SET state='failed',reason=%s,generation_retry_due_at=NULL,revision=revision+1 "
                    "WHERE id=%s AND generation_id=%s AND state IN ('generating','unknown','cancelling')",
                    (code,chapter_id,generation_id))
            raise
        latest=await self.models.status(generation_id)
        if latest is None or latest.attempt_id != generation_id or latest.status != "succeeded":
            if latest and getattr(latest,"cancellation_reason",None)=="explicit_cancel":
                return await self._failed_generation(chapter,context,result=result,error="explicit_cancel",frozen=frozen)
            await self._observe_later(chapter_id,generation_id,context.actor_id,data_generation)
            return {"state":chapter["state"],"attempt_id":generation_id,"reason":"model_observation_unavailable"}
        if latest and getattr(latest,"cancellation_reason",None)=="explicit_cancel":
            return await self._failed_generation(chapter,context,result=result,error="explicit_cancel",frozen=frozen)
        try:
            await self.save_generated(context, chapter_id, generation_id, recipe, result, refs)
        except AppError as error:
            if error.code != "writing_preparation_changed":
                raise
            await self._observe_later(chapter_id,generation_id,context.actor_id,data_generation)
            return {"state":"generating","attempt_id":generation_id,"reason":error.code}

    async def _failed_generation(self,chapter,context,*,result=None,error=None,frozen=None):
        data_generation=frozen_data_generation(chapter["generation_recipe"])
        try:
            status,code,_retry,receipt=await outcome(self.models,chapter["generation_id"],error=error)
        except Exception:
            status,code,receipt="unavailable","model_observation_unavailable",None
        if error=="explicit_cancel":
            code="explicit_cancel"
        next_attempt = False
        exhausted = False
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            observation = (await self.recovery.prepare_observation_on(connection,chapter["generation_id"],status,receipt,
                allow_unknown_retry=code!="explicit_cancel",kind="chapter",item_id=chapter["id"])
                if self.recovery is not None else None)
            current=await (await connection.execute("SELECT * FROM life.chapters WHERE id=%s FOR UPDATE",(chapter["id"],))).fetchone()
            if (not current or current["generation_id"]!=chapter["generation_id"]
                    or current["state"] not in {"generating","unknown","cancelling"}):
                return
            cancel=current["generation_cancel_requested"] or code=="explicit_cancel"
            ledger,due = (await self.recovery.observe_on(connection,chapter["generation_id"],status,code,
                retry_after=getattr(receipt,"hold_until",None),model_receipt=receipt,
                allow_unknown_retry=not cancel,observation=observation)
                if self.recovery is not None else ("unregistered",None))
            if status in {"queued","running"} or (status=="succeeded" and result is None):
                state,when=("cancelling" if cancel else "generating"),None
            elif status in {"unknown","absent","unavailable"}:
                state=("cancelling" if cancel else "unknown" if current["state"] == "unknown"
                    or (status == "unknown" and ledger == "unknown") else "generating")
                when=due if not cancel and status=="unknown" else None
                next_attempt=when is not None and when<=datetime.now(UTC)
                exhausted=(not cancel and ledger=="unknown" and due is None
                    and finished_unknown(receipt,chapter["generation_id"],data_generation))
                if exhausted:
                    code="computation_attempts_exhausted"
            elif cancel:
                state,when="cancelled",None
            elif result is not None and result.receipt.status=="succeeded":
                state,when="failed",None
                code=getattr(error,"code",None) or generation_error(result,"chapter_generation_failed")
            else:
                when = (due or current["generation_retry_due_at"]) if ledger=="failed" else None
                from .rbi_model_recovery import interrupted
                state = ("unknown" if when is not None else "cancelled" if status=="cancelled"
                    and not interrupted(receipt) else "failed")
                next_attempt = when is not None and when<=datetime.now(UTC)
                if ledger=="unregistered":
                    code="recovery_original_lineage_missing"
            count=current["generation_retry_count"]+(1 if when is not None and current["generation_retry_due_at"] is None else 0)
            await connection.execute("UPDATE life.chapters SET state=%s,reason=%s,generation_retry_count=%s,generation_retry_due_at=%s,revision=revision+1 WHERE id=%s",
                (state,code,count,when,current["id"]))
            if not next_attempt:
                if state in {"generating","unknown","cancelling"} and not exhausted:
                    await self.life.jobs.enqueue(connection,JobSpec("life.chapter_generate","chapter-observe:"+str(uuid4()),
                        {"chapter_id":current["id"],"generation_id":current["generation_id"],"data_generation":data_generation},
                        scheduled_at=when or datetime.now(UTC)+timedelta(seconds=30),subject_id=context.actor_id,data_generation=data_generation),
                        queueing_lock="chapter-generate:"+current["generation_id"])
                return {"state":state,"attempt_id":current["generation_id"],"retry_due_at":when}
        refs=source_refs(current["generation_recipe"]["source_refs"])
        generation_id=str(uuid4())
        request = None
        if frozen is not None:
            draft=ModelRequest("chapter:"+generation_id,generation_id,"writing",ServiceClass.ELASTIC,context,
                RequestBudget(datetime.now(UTC)+timedelta(seconds=600)),frozen.messages,system=frozen.system,
                max_output_tokens=frozen.max_output_tokens,estimated_input_tokens=frozen.estimated_input_tokens,
                source_refs=frozen.source_refs,execution_timeout_seconds=600,parameters=frozen.parameters,
                required_capabilities=frozen.required_capabilities,provider_session_scope=frozen.provider_session_scope,
                data_generation=data_generation)
            profile=await self.models.generation_profile(draft)
            request=replace(draft,route_id=profile["route_id"],route_revision=profile["route_revision"],
                protocol=profile["protocol"])
        original_receipt = None
        if status == "unknown":
            if request is None:
                return {"state":"unknown","attempt_id":current["generation_id"]}
            try:
                original_receipt=await self.models.status(current["generation_id"])
            except Exception:
                pass
            if not finished_unknown(original_receipt,current["generation_id"],data_generation):
                await self._observe_later(current["id"],current["generation_id"],context.actor_id,data_generation)
                return {"state":"unknown","attempt_id":current["generation_id"]}
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            await self.life.validate_persistence(connection,context,refs)
            fresh=await self._chapter(context,current["id"],connection,lock=True,additional_refs=refs)
            if (fresh["generation_id"]!=current["generation_id"] or fresh["generation_cancel_requested"]
                    or fresh["state"] not in {"generating","unknown"}
                    or fresh["generation_retry_due_at"] is None or fresh["generation_retry_due_at"]>datetime.now(UTC)):
                return
            work=await self._work(context,fresh["work_id"],connection)
            if (work["revision"]!=current["generation_recipe"]["work_revision"]
                    or not await self._previous_current(connection,fresh["work_id"],current["generation_recipe"])):
                await connection.execute("UPDATE life.chapters SET state='failed',generation_retry_due_at=NULL,reason='writing_material_changed',revision=revision+1 WHERE id=%s",(fresh["id"],))
                return
            await freeze_recipe(self.life,connection,"chapter",fresh["id"],fresh["generation_id"],context,fresh["generation_recipe"],refs)
            await connection.execute("UPDATE life.chapters SET state='generating',generation_id=%s,generation_context=%s,reason=NULL,generation_retry_due_at=NULL,revision=revision+1 WHERE id=%s",
                (generation_id,document(context),fresh["id"]))
            if request is not None:
                await self.generations.prepare("chapter:"+generation_id,request,connection=connection)
            await freeze_recipe(self.life,connection,"chapter",fresh["id"],generation_id,context,fresh["generation_recipe"],refs,previous=fresh["generation_id"])
            if self.recovery is None:
                raise AppError("recovery_policy_unavailable",status=503)
            await self.recovery.inherit_on(connection,generation_id,fresh["generation_id"])
            if status == "unknown":
                await self.recovery.reserve_on(connection,"chapter",fresh["id"],request,
                    quota_group=profile.get("quota_group"),model_receipt=original_receipt)
            await self.life.jobs.enqueue(connection,JobSpec("life.chapter_generate","chapter:"+generation_id,
                {"chapter_id":fresh["id"],"generation_id":generation_id,"data_generation":data_generation},subject_id=context.actor_id,data_generation=data_generation))

    async def save_generated(self, context, chapter_id, generation_id, recipe, result, refs):
        content = generated_text(result)
        if result.receipt.status != "succeeded" or not content.strip():
            raise AppError(generation_error(result, "chapter_generation_failed"))
        prepared = await self.prepare_completion(context,chapter_id) if self.prepare_completion is not None else None
        if prepared is None:
            async with self.life.database.transaction(read_only=True) as connection:
                snapshot = await (await connection.execute("SELECT * FROM life.chapters WHERE id=%s",(chapter_id,))).fetchone()
            if not snapshot:
                raise AppError("chapter_not_found",status=404)
        else:
            snapshot = prepared["chapter"]
        version = snapshot["current_revision"]+1
        saved = {"chapter_id":chapter_id,"work_id":snapshot["work_id"],"content_revision":version,
            "generation_id":generation_id,"model_attempt_id":result.receipt.attempt_id,
            "content_signature":signature(content),"character_count":len(content),"representation":"saved_chapter_revision"}
        event = {"producer":"life.chapter.generated","event_id":chapter_id,"revision":version,
            "parts":(InputPart("text",json.dumps(saved,ensure_ascii=False)),),"support_refs":refs}
        async with self.life.database.transaction() as connection:
            data_generation=frozen_data_generation(recipe)
            await self.life.jobs.assert_generation_on(connection,data_generation)
            dependencies=unique_refs(refs,prepared["refs"] if prepared else ())
            await self.life.sources.prepare_event_writes(connection,context,(event,),additional_refs=dependencies)
            await self.life.validate_persistence(connection,context,dependencies)
            target=await (await connection.execute("SELECT work_id FROM life.chapters WHERE id=%s",(chapter_id,))).fetchone()
            if not target:
                raise AppError("chapter_not_found",status=404)
            if prepared is not None:
                locked_work=await (await connection.execute("SELECT * FROM life.works WHERE id=%s FOR UPDATE",(target["work_id"],))).fetchone()
                locked_chapter=await (await connection.execute("SELECT * FROM life.chapters WHERE id=%s FOR UPDATE",(chapter_id,))).fetchone()
                if (not locked_work or not locked_chapter or locked_work["revision"] != snapshot["work_snapshot_revision"]
                        or locked_work["source_refs"] != snapshot["work_source_refs"]
                        or locked_chapter["revision"] != snapshot["revision"]
                        or locked_chapter["source_refs"] != snapshot["source_refs"]):
                    raise AppError("writing_preparation_changed",status=409)
            work=await self._work(context,target["work_id"],connection,lock=True)
            fresh = await self._chapter(context, chapter_id, connection, lock=True, additional_refs=refs)
            if not fresh or fresh["current_revision"] != snapshot["current_revision"] or fresh["work_id"] != snapshot["work_id"]:
                raise AppError("writing_preparation_changed",status=409)
            if fresh["generation_id"] != generation_id or fresh["generation_cancel_requested"] or fresh["state"] not in {"generating", "unknown"}:
                return
            if self.recovery is None:
                raise AppError("recovery_policy_unavailable", status=503)
            await self.recovery.assert_applicable_on(connection, generation_id)
            if not await self.life.sources.validate_persistence(connection,context,refs):
                await connection.execute("UPDATE life.chapters SET state='failed',generation_recipe=NULL,reason='result_source_unavailable',revision=revision+1 WHERE id=%s",(chapter_id,))
                return
            if (fresh["work_id"] != target["work_id"] or work["revision"]!=recipe["work_revision"]
                    or not await self._previous_current(connection,fresh["work_id"],recipe)):
                await connection.execute("UPDATE life.chapters SET state='failed',generation_retry_due_at=NULL,reason='writing_material_changed',revision=revision+1 WHERE id=%s",(chapter_id,))
                return
            originals = await self.life.sources.record_event(context, producer="life.chapter.generated",
                event_id=chapter_id, revision=version, occurred_at=datetime.now(UTC),
                # The immutable chapter remains Writing's authority. Its save receipt
                # proves progress without disclosing an unpublished draft via Sources.
                parts=event["parts"], origin_kind=OriginKind.ACTOR,
                support_refs=refs, connection=connection)
            refs = unique_refs(refs, originals)
            created_at = datetime.now(UTC)
            await connection.execute("INSERT INTO life.chapter_revisions(chapter_id,revision,content,work_revision,model_attempt_id,recipe,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", (chapter_id, version, content, recipe["work_revision"], result.receipt.attempt_id, document({**recipe, "source_refs": refs, "completion_source_refs": originals, "model_receipt": result.receipt}),created_at))
            await connection.execute("UPDATE life.chapters SET current_revision=%s,state='draft',review_state=NULL,review_note=NULL,generation_retry_count=0,generation_retry_due_at=NULL,revision=revision+1 WHERE id=%s", (version, chapter_id))
            await record_saved_on(self.life,connection,self.statistics,kind="chapter",item_id=chapter_id,
                content_revision=version,actor_id=context.actor_id,created_at=created_at,route_id=result.receipt.route_id,data_generation=data_generation)
            if self.on_completed is not None:
                completed={**fresh, "current_revision": version,
                    "revision": fresh["revision"]+1, "state": "draft", "generation_id": generation_id,
                    "model_attempt_id": result.receipt.attempt_id, "completion_source_refs": originals}
                if prepared is None:
                    await self.on_completed(context,completed,refs,connection)
                else:
                    await self.on_completed(context,completed,refs,connection,prepared=prepared)

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
            created_at = datetime.now(UTC)
            await connection.execute("INSERT INTO life.chapter_revisions(chapter_id,revision,content,work_revision,recipe,created_at) VALUES (%s,%s,%s,%s,%s,%s)", (chapter["id"], version, content, work["revision"], document({"kind": "author_edit", "source_refs": refs}),created_at))
            await connection.execute("UPDATE life.chapters SET current_revision=%s,state='draft',review_state=NULL,review_note=NULL,revision=revision+1 WHERE id=%s", (version, chapter["id"]))
            await record_saved_on(self.life,connection,self.statistics,kind="chapter",item_id=chapter["id"],
                content_revision=version,actor_id=context.actor_id,created_at=created_at)
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
