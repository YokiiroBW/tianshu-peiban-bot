"""Local-day diaries from actual authorized experiences, draft before publication."""
from tianshu.sources.dependencies import unique_refs
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from .validation import revision, text
from .writing import can_edit, generated_text, generation_error
from . import publications
from tianshu.conversation.types import InputPart
from tianshu.conversation.generation import GenerationRequests
from .model_budget import generation_request
from .diary_automation import DiaryAutomation, automatic_view, diary_experience
from .diary_materials import assemble_diary_material, episode_versions, frozen_recipe
from .computation_retry import freeze_recipe
from .diary_retry import DiaryRetry
from .generation_control import GenerationRecovery, backfill_saved_statistics, frozen_data_generation, record_saved_on, submit_frozen


class DiaryService:
    def __init__(self, life, models, assembler):
        self.life, self.models = life, models
        # Each actor/day is an independent authored request. Reuse the common
        # tokenizer/authority rules without dialogue or cross-day retrieval.
        self.assembler = ContextAssembler(life.identity, None, None, settings=assembler.settings)
        self.generations = GenerationRequests(life.database, life.sources, models)
        self.automation = DiaryAutomation(self)
        self.retry = DiaryRetry(self)
        self.recovery = None
        self.statistics = None

    def bind_observability(self, *, recovery_policy, statistics):
        self.recovery = GenerationRecovery(recovery_policy)
        self.statistics = statistics

    async def backfill_statistics(self, *, limit=128, data_generation):
        return await backfill_saved_statistics(self.life,self.statistics,"diary",limit=limit,data_generation=data_generation)

    async def changed(self, context, watermark, *, episode=None, connection=None, data_generation=None):
        return await self.automation.changed(context, watermark, episode=episode, connection=connection,data_generation=data_generation)

    async def consume_episode_change(self, context, episode_id, revision, watermark=None, generation=None):
        """Consume a durable notification under its original authorized scene."""
        if not isinstance(episode_id, str) or not episode_id or type(revision) is not int or revision < 1:
            raise AppError("invalid_episode_refs")
        if not isinstance(generation,str) or not generation:
            raise AppError("job_generation_missing",status=409)
        reader = await self._reader(context_from_record(context))
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,generation)
            page = await self.life.memory.episodes_by_refs(reader, ((episode_id, revision),), connection=connection)
            if not page.items:
                return {"state": "unavailable_or_revised", "episode_id": episode_id, "revision": revision}
            await self.changed(reader, watermark, episode=page.items[0],connection=connection,data_generation=generation)
        return {"state": "observed", "episode_id": episode_id, "revision": revision}

    async def day_closed(self, context, day, *, connection=None):
        return await self.automation.day_closed(context, day, connection=connection)

    async def review_day(self, actor_id, scope_key, day, expected_revision, data_generation):
        return await self.automation.review(actor_id, scope_key, day, expected_revision, data_generation)

    async def recover(self):
        await self.automation.recover()

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
        page = await self.life._page(context, rows, limit)
        raw_by_id = {row["id"]:row for row in rows[:limit]}
        automatic = await self._automatic(context,tuple(raw_by_id[item["id"]] for item in page.items))
        return replace(page,items=tuple({**item,"automatic":automatic.get(item["id"])} for item in page.items))

    async def _automatic(self, context, diary_rows):
        """Join source-checked database dates before exposing the public diary DTO."""
        if not diary_rows:
            return {}
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT day,timezone,revision,reviewed_revision,generation_id,closed_at,last_reviewed_at,last_error,scope_key "
                "FROM life.diary_days WHERE actor_id=%s AND day=ANY(%s::date[]) AND scope_ids<@%s::text[] "
                "ORDER BY closed_at DESC",
                (context.actor_id,[row["day"] for row in diary_rows],list(context.scope_ids)))).fetchall()
        by_day = {}
        for row in rows:
            by_day[(row["day"], row["scope_key"])] = row
        return {diary["id"]:automatic_view(by_day[(diary["day"],diary["scene_key"])],diary)
                for diary in diary_rows if (diary["day"],diary["scene_key"]) in by_day}

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
        automatic = await self._automatic(context,(diary,))
        return {"diary": {**diary,"day":diary["day"].isoformat(),
                          "automatic":automatic.get(diary["id"])}, "version": row}

    async def request(self, context, body, *, automatic=None):
        await self.life.identity.check(context)
        state = await self.life.state(context)
        try:
            day = date.fromisoformat(body.get("day", ""))
        except (TypeError, ValueError):
            raise AppError("invalid_diary_day") from None
        recipe = {"day":day.isoformat(),"timezone":automatic["timezone"] if automatic else state["timezone"],"cursor":None,
                  "watermark":None,"scan_hash":"","scanned":0,"episodes":[],"material_revisions":[],
                  "coverage":{"complete":False,"missing":["collection_pending"]}, "automatic":automatic}
        async def apply(connection):
            data_generation = (automatic or {}).get("data_generation")
            if data_generation is None:
                data_generation = await self.life.jobs.current_generation_on(connection)
            await self.life.jobs.assert_generation_on(connection,data_generation)
            scene_key = self.life.identity.scene_id(context)
            diary_id = str(uuid5(NAMESPACE_URL,f"diary:{context.actor_id}:{day}:{scene_key}"))
            await self.life.validate_persistence(connection,context)
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("diary:"+diary_id,))
            # Starting a new draft does not consume the old draft's private text
            # or revoked evidence. Its own newly collected sources are checked.
            row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s FOR UPDATE",(diary_id,))).fetchone()
            if row and not set(row["scope_ids"]).issubset(context.scope_ids):
                raise AppError("diary_not_found",status=404)
            revision(row,body.get("expected_revision"))
            if row and row["state"] in {"collecting","generating","unknown","cancelling"}:
                raise AppError("diary_generation_unresolved",status=409,details={"attempt_id":row["generation_id"]})
            generation_id = str(uuid4())
            bound,generation_context = await self.automation.bind(connection, automatic, generation_id, context=context, day=day)
            selected_recipe = {**recipe,"timezone":bound["timezone"] if bound else recipe["timezone"],"automatic":bound,
                               "data_generation":data_generation}
            current = row["revision"]+1 if row else 1
            if row:
                await connection.execute("UPDATE life.diaries SET state='collecting',generation_cancel_requested=false,generation_id=%s,context=%s,generation_recipe=%s,revision=%s,reason=NULL,source_refs='[]',episode_ids='{}',generation_retry_count=0,generation_retry_due_at=NULL WHERE id=%s",(generation_id,document(generation_context),document(selected_recipe),current,diary_id))
            else:
                await connection.execute("INSERT INTO life.diaries(id,actor_id,day,scene_key,state,generation_id,context,generation_recipe,episode_ids,source_refs,scope_ids) VALUES (%s,%s,%s,%s,'collecting',%s,%s,%s,'{}','[]',%s)",(diary_id,context.actor_id,day,scene_key,generation_id,document(generation_context),document(selected_recipe),list(generation_context.scope_ids)))
            await self.life.jobs.enqueue(connection,JobSpec("life.diary_collect","diary-collect:"+generation_id+":start",{"diary_id":diary_id,"generation_id":generation_id,"cursor":None,"data_generation":data_generation},subject_id=context.actor_id,data_generation=data_generation))
            return CommandResult(body["client_request_id"],{"id":diary_id,"revision":current,"state":"collecting"},current,attempt_id=generation_id)
        return await self.life.commands.execute(context,"life.diaries.request",body["client_request_id"],signature(body),apply)

    async def collect(self, diary_id, generation_id, cursor, data_generation=None):
        if data_generation is None:
            async with self.life.database.transaction(read_only=True) as connection:
                row = await (await connection.execute(
                    "SELECT generation_recipe FROM life.diaries WHERE id=%s AND generation_id=%s",
                    (diary_id,generation_id))).fetchone()
            if not row:
                return
            data_generation = frozen_data_generation(row["generation_recipe"])
        try:
            return await self._collect(diary_id, generation_id, cursor, data_generation)
        except AppError as error:
            if error.code != "job_generation_expired":
                await self._generation_state(diary_id, generation_id, "failed", error.code, settle=True,data_generation=data_generation)
            raise

    async def _collect(self, diary_id, generation_id, cursor, data_generation):
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s",(diary_id,))).fetchone()
        if not row or row["generation_id"]!=generation_id or row["state"]!="collecting" or row["generation_recipe"]["cursor"]!=cursor:
            return
        data_generation = frozen_data_generation(row["generation_recipe"],data_generation)
        context,recipe = await self._reader(context_from_record(row["context"])),row["generation_recipe"]
        day = date.fromisoformat(recipe["day"])
        zone = ZoneInfo(recipe["timezone"])
        start,end = datetime.combine(day,time.min,zone),datetime.combine(day+timedelta(days=1),time.min,zone)
        page = await self.life.memory.episodes_between(context,start,end,limit=200,cursor=cursor)
        actual = tuple(item for item in page.items if diary_experience(item))
        episodes = tuple(item for item in actual if item.source_refs)
        selected = recipe["episodes"]+list(wire(episodes))
        scanned = recipe["scanned"]+len(episodes)
        material_versions = dict(recipe["material_revisions"])
        material_versions.update((item.episode_id,item.revision) for item in episodes)
        # Actor-wide sequence/coverage can change because of a diary, another day,
        # or an internal event. Only actual input versions define new material.
        digest = signature({"day":recipe["day"],"timezone":recipe["timezone"],
                            "revisions":sorted(material_versions.items())})
        missing = sorted(set(recipe["coverage"].get("missing",[]))-{"collection_pending"} | set(page.coverage.missing))
        if len(episodes) != len(actual):
            missing.append("original_source_missing")
        refs = unique_refs(tuple(ref for item in selected for ref in source_refs(item["source_refs"])))
        recipe = {**recipe,"cursor":page.next_cursor,"watermark":page.watermark,"scan_hash":digest,"scanned":scanned,
                  "episodes":selected,"material_revisions":sorted(material_versions.items()),
                  "coverage":{"complete":not page.next_cursor and not missing,"missing":sorted(set(missing)),
                              "scanned":scanned,"selected":len(selected),"watermark":page.watermark}}
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            fresh = await self.life.lock_row(connection,context,"SELECT * FROM life.diaries WHERE id=%s FOR UPDATE",(diary_id,),additional_refs=refs)
            if fresh["generation_id"]!=generation_id or fresh["state"]!="collecting" or fresh["generation_recipe"]["cursor"]!=cursor:
                return
            if not await self.life.sources.validate_persistence(connection,context,refs):
                await connection.execute("UPDATE life.diaries SET state='failed',generation_recipe=NULL,reason='source_unavailable',revision=revision+1 WHERE id=%s",(diary_id,))
                await self.automation.settle(connection,generation_id,error="source_unavailable",data_generation=data_generation)
                return
            if page.next_cursor:
                await connection.execute("UPDATE life.diaries SET generation_recipe=%s,source_refs=%s,context=%s WHERE id=%s",(document(recipe),document(refs),document(context),diary_id))
                await self.life.jobs.enqueue(connection,JobSpec("life.diary_collect","diary-collect:"+generation_id+":"+signature(page.next_cursor),{"diary_id":diary_id,"generation_id":generation_id,"cursor":page.next_cursor,"data_generation":data_generation},subject_id=context.actor_id,data_generation=data_generation))
                return
            if not selected or fresh["material_key"]==digest:
                state = "published" if fresh["published_revision"] == fresh["current_revision"] and fresh["current_revision"] else "draft" if fresh["current_revision"] else "empty"
                scopes = sorted({scope for item in selected for scope in item["scope_ids"]}) if selected else fresh["scope_ids"]
                saved = None
                if fresh["current_revision"]:
                    saved = await (await connection.execute(
                        "SELECT r.model_attempt_id,r.recipe,g.context FROM life.diary_revisions r "
                        "LEFT JOIN life.generation_recipes g ON g.generation_id=r.model_attempt_id "
                        "WHERE r.diary_id=%s AND r.revision<=%s AND r.model_attempt_id IS NOT NULL "
                        "ORDER BY r.revision DESC LIMIT 1", (diary_id,fresh["current_revision"]))).fetchone()
                receipt = await self.models.status(saved["model_attempt_id"]) if saved else None
                saved_receipt = (saved["recipe"].get("model_receipt") or {}) if saved else {}
                original_valid = (await self.life.sources.validate_persistence(
                    connection,context,source_refs(saved["recipe"].get("source_refs", ()))) if saved else False)
                proven = (saved and saved["context"] and receipt and receipt.status == "succeeded"
                    and receipt.attempt_id == saved["model_attempt_id"]
                    and saved_receipt.get("attempt_id") == saved["model_attempt_id"]
                    and saved_receipt.get("status") == "succeeded" and original_valid
                    and fresh["material_key"] == digest)
                original_generation = saved["model_attempt_id"] if saved else generation_id
                original_context = saved["context"] if saved and saved["context"] else wire(context)
                reason = ("diary_saved_material_changed" if saved and fresh["material_key"] != digest else
                    "diary_saved_receipt_unavailable" if saved and not proven else "diary_no_new_material")
                retained_refs = source_refs(saved["recipe"].get("source_refs", ())) if saved and not proven else refs
                retained_ids = ([item["episode_id"] for item in saved["recipe"].get("episodes", ())]
                    if saved and not proven else [item["episode_id"] for item in selected])
                retained_scopes = fresh["scope_ids"] if saved and not proven else scopes
                retained_key = fresh["material_key"] if saved and not proven else digest
                await connection.execute("UPDATE life.diaries SET state=%s,reason=%s,material_key=%s,generation_recipe=NULL,"
                    "source_refs=%s,episode_ids=%s,scope_ids=%s,context=%s,generation_id=%s,"
                    "last_reviewed_at=CASE WHEN %s THEN last_reviewed_at ELSE now() END,revision=revision+1 WHERE id=%s",
                    (state,reason,retained_key,document(retained_refs),retained_ids,retained_scopes,
                     document(original_context),original_generation,bool(saved and not proven),diary_id))
                if proven:
                    await connection.execute("UPDATE life.diary_days SET generation_id=%s WHERE generation_id=%s",
                        (original_generation,generation_id))
                    await self.automation.settle(connection,original_generation,recipe=saved["recipe"],data_generation=data_generation)
                else:
                    await self.automation.settle(connection,generation_id,recipe=recipe,
                        error=reason if saved else None)
                return
            scopes = sorted({scope for item in selected for scope in item["scope_ids"]})
            await connection.execute("UPDATE life.diaries SET state='generating',generation_recipe=%s,episode_ids=%s,source_refs=%s,scope_ids=%s,context=%s WHERE id=%s",(document(recipe),[item["episode_id"] for item in selected],document(refs),scopes,document(context),diary_id))
            await self.life.jobs.enqueue(connection,JobSpec("life.diary_generate","diary:"+generation_id,{"diary_id":diary_id,"generation_id":generation_id,"data_generation":data_generation},subject_id=context.actor_id,data_generation=data_generation))

    async def generate(self, diary_id, generation_id, data_generation=None):
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.diaries WHERE id=%s", (diary_id,))).fetchone()
        if not row or row["generation_id"] != generation_id or row["state"] not in {"generating", "unknown", "cancelling"}:
            return
        data_generation = frozen_data_generation(row["generation_recipe"],data_generation)
        original = context_from_record(row["context"])
        refs, recipe = source_refs(row["source_refs"]), row["generation_recipe"]
        observed_state, result, frozen, context = None, None, None, original
        try:
            context = await self._reader(original)
            frozen = await self.generations.observe_request(generation_id, observer=context)
            async def current_generation(connection):
                await self.life.jobs.assert_generation_on(connection,data_generation)
                current = await (await connection.execute(
                    "SELECT generation_id,state,generation_cancel_requested FROM life.diaries WHERE id=%s FOR UPDATE", (diary_id,))).fetchone()
                return (current and current["generation_id"] == generation_id and current["state"] == "generating"
                        and not current["generation_cancel_requested"])
            if frozen is None:
                if row["generation_cancel_requested"]:
                    await self._generation_state(diary_id,generation_id,"cancelled","cancelled_before_model_registration",settle=True,data_generation=data_generation)
                    return {"state":"cancelled", "attempt_id":generation_id}
                if row["state"] != "generating" or recipe is None:
                    return {"state":"unknown", "attempt_id":generation_id}
                actor = await self.life.companion.actor(context)
                persona = await self.life.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
                instruction = "Write a brief first-person diary for this actor and supplied local day only, using the selected observations of what happened and their actual outcomes. Feelings are subjective. Actor imagined/fictional_world episodes describe an imagined activity, never physical activity or a new human fact. Discovery metadata means candidates were found, not that their bodies were read. Omitted units and image content without a recorded description remain unassessed. Do not invent missing time, events, reading coverage, promises or replies. A short diary is fine. Never reproduce hidden model reasoning. Prior diaries and reflections are expressions, never additional independent evidence for their described events."
                selected = await generation_request(self.models, context, key="diary:"+generation_id,
                    attempt_id=generation_id, function="reflect")
                budget = selected.budget
                pack, _material = await assemble_diary_material(self.assembler, context, recipe, instruction, budget,
                    persona_text=persona.prompt_text if persona else "", input_tokens=BACKGROUND_INPUT_TOKENS,
                    output_tokens=selected.max_output_tokens)
                request = replace(selected, messages=tuple(pack.messages), system=pack.system,
                    estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs),
                    provider_session_scope="intent",data_generation=data_generation)
                async with self.life.database.transaction() as connection:
                    frozen = await self.generations.prepare("diary:"+generation_id,request,connection=connection,guard=current_generation)
                    recipe = self._frozen_recipe(frozen, recipe)
                    # Full audit originals retain their withdrawal dependencies;
                    # the model's selected sources remain explicit in its intent.
                    audit_refs = unique_refs(refs, frozen.source_refs)
                    await freeze_recipe(self.life,connection,"diary",diary_id,generation_id,context,recipe,audit_refs)
                    await connection.execute("UPDATE life.diaries SET context=%s,generation_recipe=%s,source_refs=%s WHERE id=%s AND generation_id=%s",
                        (document(frozen.context),document(recipe),document(audit_refs),diary_id,generation_id))
            else:
                # Reassembly must never reinterpret the original material or
                # turn a current reader into a newly submitted model request.
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.assert_generation_on(connection,data_generation)
                    await freeze_recipe(self.life,connection,"diary",diary_id,generation_id,context,recipe,refs)
            result = await self.generations.read(generation_id,frozen.context,observer=context)
            try:
                receipt = await self.models.status(generation_id)
            except Exception:
                await self._observe_later(diary_id,generation_id,context.actor_id,data_generation=data_generation)
                return {"state":row["state"],"attempt_id":generation_id,"reason":"model_observation_unavailable"}
            if result is None and receipt is None and row["state"] == "generating" and not row["generation_cancel_requested"]:
                if frozen.context.access_epoch != context.access_epoch:
                    await self._generation_state(diary_id,generation_id,"failed","diary_original_authorization_changed",settle=True,data_generation=data_generation)
                    return {"state":"failed","attempt_id":generation_id,"reason":"diary_original_authorization_changed"}
                completion = JobSpec("life.diary_generate", "diary-complete:"+generation_id,
                    {"diary_id":diary_id,"generation_id":generation_id,"data_generation":data_generation}, subject_id=context.actor_id,data_generation=data_generation)
                receipt = await submit_frozen(self.life,self.models,self.generations,"diary:"+generation_id,
                    frozen,guard=current_generation,completion=completion,kind="diary",item_id=diary_id,
                    recovery=self.recovery)
                if receipt.status == "succeeded":
                    result = await self.generations.read(generation_id,frozen.context,observer=context)
            if result is None and receipt is not None and receipt.status in {"queued", "running", "succeeded"}:
                await self._observe_later(diary_id,generation_id,context.actor_id,data_generation=data_generation)
                return {"state":"generating","attempt_id":generation_id}
            if result is None:
                return await self.retry.failed(row,context,frozen=frozen)
            observed_state = result.receipt.status
            refs = frozen.source_refs
            await self.life.identity.check(context, refs)
            if observed_state in {"queued", "running", "unknown", "failed", "cancelled"}:
                return await self.retry.failed(row,context,result=result,frozen=frozen)
            if observed_state == "succeeded" and self.recovery is not None:
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.assert_generation_on(connection,data_generation)
                    await self.recovery.observe_on(connection,generation_id,"succeeded")
            if row["generation_cancel_requested"]:
                await self._generation_state(diary_id,generation_id,"cancelled","model_completed_after_cancel",settle=True,data_generation=data_generation)
                return {"state":"cancelled","attempt_id":generation_id}
            if observed_state != "succeeded" or not generated_text(result).strip():
                raise AppError(generation_error(result,"diary_generation_failed"))
            latest=await self.models.status(generation_id)
            if latest and getattr(latest,"cancellation_reason",None)=="explicit_cancel":
                await self._generation_state(diary_id,generation_id,"cancelled","explicit_cancel",settle=True,data_generation=data_generation)
                return {"state":"cancelled","attempt_id":generation_id}
            recipe = self._frozen_recipe(frozen, recipe)
            saved = await self.save_generated(context,diary_id,generation_id,recipe,result,refs)
            return {"state":"draft" if saved else "stale","attempt_id":generation_id}
        except BaseException as error:
            code = getattr(error,"code",type(error).__name__)
            if code in {"job_generation_expired","job_generation_missing"}:
                raise
            if code == "generation_no_longer_current":
                return {"state":"changed_since_generation","attempt_id":generation_id}
            if code in {"recovery_not_due", "recovery_dependency_cooling", "recovery_dependency_probe_in_progress", "recovery_claim_lost"}:
                await self._observe_later(diary_id,generation_id,context.actor_id,data_generation=data_generation)
                return {"state":"generating","attempt_id":generation_id,"reason":code}
            if code == "recovery_attempt_not_dispatchable":
                return await self.retry.failed(row,context,error=error,frozen=frozen)
            from tianshu.models.retry import transient_model_failure
            if transient_model_failure(code) or not isinstance(error,Exception):
                from tianshu.runtime.cancellation import finish_cleanup
                await finish_cleanup(self.retry.failed(row,context,error=error,frozen=frozen))
                raise
            # This is the diary application's state. Models retains the actual
            # unknown receipt even when revoked material makes it inapplicable.
            await self._generation_state(diary_id,generation_id,"failed",code,settle=True,data_generation=data_generation)
            raise

    async def _reader(self, original):
        try:
            await self.life.identity.check(original)
            return original
        except AppError as error:
            if error.code != "access_changed":
                raise
        current = await self.life.identity.refresh_scheduled_context(original,permission="life.manage")
        self.generations.observer_context(original,current)
        return current

    @staticmethod
    def _frozen_recipe(request, collected=None):
        return frozen_recipe(request, collected)

    async def _generation_state(self, diary_id, generation_id, state, reason, *, settle=False, data_generation):
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            changed = await (await connection.execute(
                "UPDATE life.diaries SET state=CASE WHEN generation_cancel_requested AND %s IN ('generating','unknown') THEN 'cancelling' ELSE %s END,"
                "reason=%s,revision=revision+1,generation_retry_due_at=CASE WHEN %s OR generation_cancel_requested THEN NULL ELSE generation_retry_due_at END,"
                "last_reviewed_at=CASE WHEN %s THEN now() ELSE last_reviewed_at END "
                "WHERE id=%s AND generation_id=%s AND state IN ('collecting','generating','unknown','cancelling') RETURNING id,generation_recipe",
                (state,state,reason,settle,settle,diary_id,generation_id))).fetchone()
            if settle and changed:
                await self.automation.settle(connection,generation_id,error=reason,recipe=changed["generation_recipe"],data_generation=data_generation)

    async def _observe_later(self, diary_id, generation_id, actor_id, *, when=None, data_generation):
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            current=await (await connection.execute("SELECT generation_id FROM life.diaries WHERE id=%s FOR UPDATE",(diary_id,))).fetchone()
            if not current or current["generation_id"]!=generation_id:
                return
            await self.life.jobs.enqueue(connection,JobSpec("life.diary_generate",
                f"diary-observe:{generation_id}:{datetime.now(UTC).isoformat()}",
                {"diary_id":diary_id,"generation_id":generation_id,"data_generation":data_generation},
                scheduled_at=when or datetime.now(UTC)+timedelta(seconds=30),subject_id=actor_id,data_generation=data_generation),queueing_lock="diary-generate:"+generation_id)

    async def control_generation(self, diary_id, generation_id, context, operation, data_generation=None):
        """Author controls use the original scene, even after an epoch refresh."""
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM life.diaries WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]",
                (diary_id,context.actor_id,list(context.scope_ids)))).fetchone()
        if not row or row["generation_id"] != generation_id:
            return
        data_generation = frozen_data_generation(row["generation_recipe"],data_generation)
        original = context_from_record(row["context"])
        # The caller must still be authorized for this Identity scene; the
        # original authoring context remains the Models observer.
        reader = await self._reader(original)
        if self.life.identity.scene_id(context) != self.life.identity.scene_id(original):
            raise AppError("generation_observation_context_mismatch",status=403)
        if operation == "cancel":
            async with self.life.database.transaction() as connection:
                await self.life.jobs.assert_generation_on(connection,data_generation)
            cancelled = None
            try:
                cancelled = await self.models.cancel(generation_id, reason="explicit_cancel")
            except AppError as error:
                if error.status != 404:
                    raise
            frozen = await self.generations.observe_request(generation_id, observer=reader)
            if cancelled is None and frozen is None:
                await self._generation_state(diary_id,generation_id,"cancelled","cancelled_before_model_registration",settle=True,data_generation=data_generation)
                return {"state":"cancelled","attempt_id":generation_id}
        self.generations.observer_context(original,reader)
        return await self.generate(diary_id,generation_id,data_generation=data_generation)

    async def reconcile_saved(self, context, row, connection):
        """Repair only the closed-day projection of this immutable saved success."""
        if row["state"] not in {"draft","published"} or not row["current_revision"] or row["generation_cancel_requested"]:
            return
        original = context_from_record(row["context"])
        if self.life.identity.scene_id(context) != self.life.identity.scene_id(original):
            raise AppError("generation_observation_context_mismatch",status=403)
        reader = await self._reader(original)
        current = await (await connection.execute(
            "SELECT * FROM life.diary_revisions WHERE diary_id=%s AND revision=%s",
            (row["id"],row["current_revision"]))).fetchone()
        saved = await (await connection.execute(
            "SELECT * FROM life.diary_revisions WHERE diary_id=%s AND model_attempt_id=%s "
            "AND revision<=%s ORDER BY revision DESC LIMIT 1",
            (row["id"],row["generation_id"],row["current_revision"]))).fetchone()
        recipe = saved["recipe"] if saved else {}
        receipt = recipe.get("model_receipt") or {}
        if (not current or not current["content"].strip() or not saved or not saved["content"].strip()
                or saved["model_attempt_id"] != row["generation_id"]
                or receipt.get("attempt_id") != row["generation_id"] or receipt.get("status") != "succeeded"
                or recipe.get("day") != row["day"].isoformat() or not recipe.get("timezone")):
            return
        actual_receipt = await self.models.status(row["generation_id"])
        if (actual_receipt is None or actual_receipt.attempt_id != row["generation_id"]
                or actual_receipt.status != "succeeded"):
            return
        await self.life.validate_persistence(connection,reader,source_refs(recipe["source_refs"]))
        await self.life.validate_persistence(connection,reader,source_refs(current["recipe"]["source_refs"]))
        await self.life.validate_persistence(connection,context,source_refs(recipe["source_refs"]))
        await self.life.validate_persistence(connection,context,source_refs(current["recipe"]["source_refs"]))
        matched = await self.automation.matching_day(connection,context,row["day"],scope_ids=row["scope_ids"],timezone=recipe["timezone"])
        if matched is None:
            return
        day,day_reader = matched
        if day["generation_id"] is not None and day["generation_id"] != row["generation_id"]:
            return
        await self.life.validate_persistence(connection,day_reader,source_refs(recipe["source_refs"]))
        versions = dict(recipe.get("material_revisions",()))
        versions.update((item["episode_id"],item["revision"]) for item in recipe.get("episodes",()))
        expected = tuple(sorted(versions.items()))
        zone = ZoneInfo(day["timezone"])
        actual = set()
        for offset in range(0,len(expected),128):
            page = await self.life.memory.episodes_by_refs(day_reader,expected[offset:offset+128],connection=connection)
            actual.update((item.episode_id,item.revision) for item in page.items if diary_experience(item)
                and item.occurred_at.astimezone(zone).date() == day["day"])
        if not expected or set(expected) != actual or any(versions.get(key,0)<version for key,version in day["material_revisions"].items()):
            await self.automation.enqueue(connection,day,reason="saved-material-changed",data_generation=frozen_data_generation(recipe))
            return
        if day["generation_id"] is None:
            if day["reviewed_revision"]>=day["revision"] and not day["last_error"]:
                return
            await self.automation.bind(connection,None,row["generation_id"],context=day_reader,day=row["day"])
        await self.automation.settle(connection,row["generation_id"],recipe=recipe,reviewed_at=saved["created_at"])

    async def save_generated(self, context, diary_id, generation_id, recipe, result, refs):
        content = generated_text(result)
        if result.receipt.status != "succeeded" or not content.strip():
            raise AppError(generation_error(result, "diary_generation_failed"))
        refs = unique_refs(refs, *(source_refs(item["source_refs"]) for item in recipe["episodes"]))
        async with self.life.database.transaction() as connection:
            data_generation = frozen_data_generation(recipe)
            await self.life.jobs.assert_generation_on(connection,data_generation)
            await self.life.validate_persistence(connection,context,refs)
            versions = tuple((item["episode_id"],item["revision"]) for item in recipe["episodes"])
            actual = await episode_versions(self.life.memory,context,versions,connection=connection)
            if set(versions) != actual:
                current = await (await connection.execute(
                    "UPDATE life.diaries SET state='failed',reason='diary_episode_revised',revision=revision+1,last_reviewed_at=now() "
                    "WHERE id=%s AND generation_id=%s AND state IN ('generating','unknown') RETURNING id",
                    (diary_id,generation_id))).fetchone()
                if current:
                    await self.automation.settle(connection,generation_id,error="diary_episode_revised",recipe=recipe)
                return False
            current = await self.life.lock_row(connection,context,
                "SELECT * FROM life.diaries WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[] FOR UPDATE",
                (diary_id,context.actor_id,list(context.scope_ids)),additional_refs=refs)
            if not current:
                raise AppError("diary_not_found",status=404)
            if current["generation_id"] != generation_id:
                return False
            if current["generation_cancel_requested"]:
                await connection.execute(
                    "UPDATE life.diaries SET state='cancelled',reason='model_completed_after_cancel',revision=revision+1,last_reviewed_at=now() WHERE id=%s",
                    (diary_id,))
                await self.automation.settle(connection,generation_id,error="model_completed_after_cancel",recipe=recipe)
                return False
            if current["state"] not in {"generating", "unknown"}:
                return False
            if self.recovery is None:
                raise AppError("recovery_policy_unavailable", status=503)
            await self.recovery.assert_applicable_on(connection, generation_id)
            if not await self.life.sources.validate_persistence(connection,context,refs):
                await connection.execute("UPDATE life.diaries SET state='failed',generation_recipe=NULL,reason='result_source_unavailable',revision=revision+1 WHERE id=%s",(diary_id,))
                await self.automation.settle(connection,generation_id,error="result_source_unavailable",data_generation=data_generation)
                return False
            version = current["current_revision"]+1
            created_at = datetime.now(UTC)
            await connection.execute("INSERT INTO life.diary_revisions(diary_id,revision,content,model_attempt_id,recipe,created_at) VALUES (%s,%s,%s,%s,%s,%s)", (diary_id, version, content, result.receipt.attempt_id, document({**recipe, "source_refs": refs, "model_receipt": result.receipt}),created_at))
            await connection.execute("UPDATE life.diaries SET current_revision=%s,material_key=%s,state='draft',review_state=NULL,review_note=NULL,review_content_revision=NULL,generation_retry_count=0,generation_retry_due_at=NULL,revision=revision+1,last_reviewed_at=now(),last_changed_at=now() WHERE id=%s", (version,recipe['scan_hash'], diary_id))
            await record_saved_on(self.life,connection,self.statistics,kind="diary",item_id=diary_id,
                content_revision=version,actor_id=context.actor_id,created_at=created_at,route_id=result.receipt.route_id,data_generation=data_generation)
            await self.automation.settle(connection,generation_id,recipe=recipe)
        # This output is a work artifact, not independent evidence for its described events.
        return True

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
            created_at = datetime.now(UTC)
            await connection.execute("INSERT INTO life.diary_revisions(diary_id,revision,content,recipe,created_at) VALUES (%s,%s,%s,%s,%s)", (diary["id"], version, content, document({"kind": "author_edit", "source_refs": refs, "episode_ids": diary["episode_ids"]}),created_at))
            await connection.execute("UPDATE life.diaries SET current_revision=%s,state='draft',review_state=NULL,review_note=NULL,review_content_revision=NULL,revision=revision+1,last_changed_at=now() WHERE id=%s", (version, diary["id"]))
            await record_saved_on(self.life,connection,self.statistics,kind="diary",item_id=diary["id"],
                content_revision=version,actor_id=context.actor_id,created_at=created_at)
            return CommandResult(body["client_request_id"], {"id": diary["id"], "content_revision": version}, diary["revision"]+1)
        return await self.life.commands.execute(context, "life.diaries.revise", body["client_request_id"], signature(body), apply)

    async def review(self, context, body):
        """Review the current immutable version without making review a publish gate."""
        await self.life.identity.check(context)
        state = body.get("state")
        if state not in {"approved", "changes_requested"}:
            raise AppError("invalid_review_state")
        note = text(body.get("note", ""), "审阅意见", 20000, empty=True)

        async def apply(connection):
            diary = await self._diary(context, body.get("id"), connection, lock=True)
            revision(diary, body.get("expected_revision"))
            if not diary["current_revision"]:
                raise AppError("diary_not_ready", status=409)
            await self.life.validate_persistence(connection, context, await self._version_refs(diary, connection))
            await connection.execute(
                "UPDATE life.diaries SET review_state=%s,review_note=%s,review_content_revision=current_revision,"
                "revision=revision+1 WHERE id=%s", (state, note, diary["id"]))
            return CommandResult(body["client_request_id"], {"id":diary["id"], "review_state":state,
                "content_revision":diary["current_revision"]}, diary["revision"]+1)
        return await self.life.commands.execute(context, "life.diaries.review", body["client_request_id"], signature(body), apply)

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
            await connection.execute("UPDATE life.diary_revisions SET disclosure_ref=%s,publication_metadata=%s WHERE diary_id=%s AND revision=%s",(document(disclosure),document({"day":diary["day"].isoformat()}),diary["id"],diary["current_revision"]))
            await connection.execute("UPDATE life.diaries SET published_revision=current_revision,state='published',revision=revision+1 WHERE id=%s", (diary["id"],))
            return CommandResult(body["client_request_id"], {"id": diary["id"], "published_revision": diary["current_revision"]}, diary["revision"]+1)
        return await self.life.commands.execute(context, "life.diaries.publish", body["client_request_id"], signature(body), apply)
