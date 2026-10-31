"""Incremental subjective reflections and revocable expression habits."""
from tianshu.sources.dependencies import unique_refs
from datetime import UTC, datetime, timedelta
import json

from tianshu.models.types import ModelRequest
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ContextContribution, RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from tianshu.life.validation import revision
from tianshu.conversation.generation import GenerationRequests, attempt_for
from tianshu.conversation.context.assembly import ContextAssembler
from .reflection_output import (
    CONTENT_MAX_LENGTH, INPUT_TOKEN_BUDGET, MAX_NOTES, OUTPUT_TOKEN_BUDGET,
    REVIEW_ERRORS, TITLE_MAX_LENGTH, frozen_episodes, output_example, output_schema,
    prepare_reflections,
)


class GrowthService:
    def __init__(self, companion, jobs, models, assembler, *, on_projection_changed):
        self.companion, self.jobs, self.models = companion, jobs, models
        # This closed batch must not acquire unrelated optional memory as reflection evidence.
        self.assembler = ContextAssembler(companion.identity, companion.memory, None, settings=assembler.settings)
        self.on_projection_changed = on_projection_changed
        self.generations = GenerationRequests(companion.database, companion.sources, models)

    async def changed(self, context, watermark, *, connection=None):
        if connection is None:
            await self.companion.identity.check(context)
            async with self.companion.database.transaction() as transaction:
                return await self.changed(context, watermark, connection=transaction)
        await self.companion.validate_persistence(connection, context)
        scope_key = signature(sorted(context.scope_ids))
        row = await (await connection.execute(
            "INSERT INTO companion.organize_state(actor_id,scope_key,watermark,context) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT(actor_id,scope_key) DO UPDATE SET watermark=excluded.watermark,context=excluded.context,"
            "revision=companion.organize_state.revision+1 WHERE companion.organize_state.watermark<>excluded.watermark RETURNING revision",
            (context.actor_id, scope_key, watermark, document(context)))).fetchone()
        if row:
            await self.jobs.enqueue(connection, JobSpec("companion.organize", f"organize:{context.actor_id}:{scope_key}:{row['revision']}",
                {"actor_id": context.actor_id, "scope_key": scope_key, "expected_revision": row["revision"]},
                scheduled_at=datetime.now(UTC)+timedelta(seconds=30), subject_id=context.actor_id))

    async def organize(self, actor_id, scope_key, expected_revision):
        async with self.companion.database.transaction(read_only=True) as connection:
            state = await (await connection.execute("SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if not state or state["revision"] != expected_revision or state["reviewed_watermark"] == state["watermark"] or state["last_error"] in REVIEW_ERRORS:
            return
        context = context_from_record(state["context"])
        await self.companion.identity.check(context)
        page = await self.companion.memory.episodes_since(context, state["cursor"], 32)
        if not page.items:
            await self._reviewed(actor_id, scope_key, expected_revision, page.watermark, False)
            return
        actor = await self.companion.actor(context)
        persona = await self.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
        refs = unique_refs(tuple(ref for episode in page.items for ref in episode.source_refs))
        material = {"episodes": wire(page.items), "instruction":
            "Reflect on actual new experiences. Return only JSON matching output_schema; every declared note field is required. "
            f"Select at most {MAX_NOTES} useful, distinct notes for this whole batch, not one note per episode. "
            f"Keep each title within {TITLE_MAX_LENGTH} characters and content within {CONTENT_MAX_LENGTH} characters. "
            "Do not repeat the supplied episodes, instructions, or example. Respect every schema list/string bound; cite only the most relevant episode IDs. "
            "conditions is an object with when and unless arrays of strings, never a list. counterexamples is a separate array of actual counterexamples, empty when none are known. "
            "affect is null or the complete bounded numeric object in the schema. Do not invent affect to fill the field. "
            "Use at most one note per kind and episode_ids set. "
            "The example demonstrates format only; its prose is not experience evidence. "
            "No change is a valid result. Separate interpretation from observed fact. Cite only supplied episode IDs. "
            "Interests are the actor's experience, not copied user preferences. Habits are contextual and revisable; never rewrite authored identity or infer permissions. "
            "Do not treat prior reflections/diaries as independent evidence for the described event.",
            "output_schema": output_schema(item.episode_id for item in page.items),
            "example": output_example(page.items[0].episode_id), "no_change_example": {"notes": []}}
        budget = RequestBudget(datetime.now(UTC)+timedelta(minutes=3))
        pack = await self.assembler.assemble(context, "reflect", "Review new experiences without forcing a change.", budget,
            persona_text=persona.prompt_text if persona else "",
            max_input_tokens=INPUT_TOKEN_BUDGET + OUTPUT_TOKEN_BUDGET,
            reserved_output_tokens=OUTPUT_TOKEN_BUDGET,
            contributions=(ContextContribution("new_experiences", json.dumps(material, ensure_ascii=False), refs, context.scope_ids, 80, True, str(expected_revision)),))
        material_key = "organize:" + signature({"context": self.generations.context_key(context),
            "episodes": [(item.episode_id,item.revision) for item in page.items],
            "persona": (actor["persona_id"],actor["persona_revision"]),"generation_revision":state["generation_revision"]})
        async def current_material(connection):
            current = await (await connection.execute("SELECT revision,cursor,reviewed_watermark,watermark FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",(actor_id,scope_key))).fetchone()
            return current and current["revision"]==expected_revision and current["cursor"]==state["cursor"] and current["reviewed_watermark"]!=current["watermark"]
        try:
            result = await self.generations.run(material_key, ModelRequest(material_key, attempt_for(material_key), "reflect", ServiceClass.ELASTIC,
                context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=OUTPUT_TOKEN_BUDGET,
                estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs)),guard=current_material)
        except AppError as error:
            if error.code!="generation_request_expired":
                raise
            async with self.companion.database.transaction() as connection:
                await connection.execute("UPDATE companion.organize_state SET last_error=%s WHERE actor_id=%s AND scope_key=%s AND revision=%s",(error.code,actor_id,scope_key,expected_revision))
            return
        if result is None or result.receipt.status != "succeeded":
            if result is not None and result.receipt.status in {"queued", "running"}:
                async with self.companion.database.transaction() as connection:
                    await self.jobs.enqueue(connection, JobSpec("companion.organize",
                        f"organize-observe:{material_key}:{datetime.now(UTC).isoformat()}",
                        {"actor_id":actor_id,"scope_key":scope_key,"expected_revision":expected_revision},
                        scheduled_at=datetime.now(UTC)+timedelta(seconds=30),subject_id=actor_id))
            raise AppError(result.receipt.error_code or "reflection_generation_"+result.receipt.status if result else "reflection_outcome_unknown",
                details={"attempt_id":attempt_for(material_key)})
        frozen = await self.generations.request(attempt_for(material_key), context)
        generated_refs = frozen.source_refs
        await self.companion.identity.check(context, generated_refs)
        try:
            episodes = frozen_episodes(frozen)
            prepared = prepare_reflections(result, context, episodes, generated_refs)
        except AppError as error:
            if error.code not in REVIEW_ERRORS:
                raise
            await self._rejected(actor_id, scope_key, expected_revision, error.code)
            return {"state": "rejected", "error_code": error.code, "attempt_id": result.receipt.attempt_id}
        permitted = await self.companion.identity.check_many(context, tuple(item.proposal.source_refs for item in prepared))
        if not all(permitted):
            raise AppError("source_unavailable", status=409)
        relationship = (await self.companion.memory.relationship(context, context.person_id)
                        if context.person_id and any(item.proposal.kind == "style" for item in prepared) else None)
        changed = False
        for item in prepared:
            proposal = item.proposal
            try:
                await self.companion.memory.record_reflection(context, proposal)
            except AppError as error:
                if error.code not in {"reflection_not_original_evidence", "reflection_original_evidence_required"}:
                    raise
                await self._rejected(actor_id, scope_key, expected_revision, "reflection_material_contract_invalid")
                return {"state": "rejected", "error_code": "reflection_material_contract_invalid",
                        "attempt_id": result.receipt.attempt_id}
            # This is a current expression projection, never a second fact ledger.
            if proposal.kind in {"style", "interest"} and not (proposal.kind == "style" and relationship and relationship["frozen"]):
                async with self.companion.database.transaction() as connection:
                    await self.companion.validate_persistence(connection, context, proposal.source_refs)
                    inserted = await (await connection.execute(
                        "INSERT INTO companion.style_rules(id,actor_id,person_id,kind,title,content,conditions,counterexamples,episode_ids,source_refs,scope_ids,state) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active') ON CONFLICT(id) DO UPDATE SET "
                        "title=excluded.title,content=excluded.content,conditions=excluded.conditions,counterexamples=excluded.counterexamples,"
                        "source_refs=excluded.source_refs,scope_ids=excluded.scope_ids,revision=companion.style_rules.revision+1,updated_at=now() "
                        "WHERE companion.style_rules.source_refs<>excluded.source_refs RETURNING id",
                        (proposal.proposal_id, actor_id, context.person_id, proposal.kind, item.title, proposal.content,
                         document(proposal.conditions), document(proposal.counterexamples), list(proposal.episode_ids),
                         document(proposal.source_refs), list(context.scope_ids)))).fetchone()
                    changed = changed or bool(inserted)
            if item.affect is not None:
                await self.companion.apply_affect(context, item.affect)
        await self._reviewed(actor_id, scope_key, expected_revision, page.watermark, changed)
        if changed:
            await self.on_projection_changed(context, f"growth:{scope_key}:{page.watermark}")
        if page.next_cursor:
            await self.changed(context, f"continuation:{state['watermark']}:{page.next_cursor}")

    async def request_review(self,context,body):
        await self.companion.identity.check(context)
        scope_key=signature(sorted(context.scope_ids))
        async def apply(connection):
            await self.companion.validate_persistence(connection,context)
            row=await (await connection.execute("SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",(context.actor_id,scope_key))).fetchone()
            if not row:
                raise AppError("reflection_material_not_found",status=404)
            revision(row,body.get("expected_revision"))
            if row["last_error"] not in REVIEW_ERRORS:
                raise AppError("reflection_original_attempt_unresolved",status=409)
            current=row["revision"]+1
            await connection.execute("UPDATE companion.organize_state SET generation_revision=generation_revision+1,revision=%s,watermark=%s,last_error=NULL,context=%s WHERE actor_id=%s AND scope_key=%s",(current,"manual:"+body["client_request_id"],document(context),context.actor_id,scope_key))
            await self.jobs.enqueue(connection,JobSpec("companion.organize",f"organize-explicit:{body['client_request_id']}",{"actor_id":context.actor_id,"scope_key":scope_key,"expected_revision":current},subject_id=context.actor_id))
            return CommandResult(body["client_request_id"],{"state":"queued"},current)
        return await self.companion.commands.execute(context,"companion.growth.review",body["client_request_id"],signature(body),apply)

    async def recover(self):
        async with self.companion.database.transaction() as connection:
            rows = await (await connection.execute("SELECT actor_id,scope_key,revision FROM companion.organize_state WHERE reviewed_watermark<>watermark "
                "AND (last_error IS NULL OR last_error<>ALL(%s))", (sorted(REVIEW_ERRORS),))).fetchall()
            for row in rows:
                await self.jobs.enqueue(connection,JobSpec("companion.organize",f"organize-recover:{row['actor_id']}:{row['scope_key']}:{row['revision']}:{datetime.now(UTC).isoformat()}",
                    {"actor_id":row["actor_id"],"scope_key":row["scope_key"],"expected_revision":row["revision"]},subject_id=row["actor_id"]))

    async def _rejected(self, actor_id, scope_key, expected_revision, code):
        async with self.companion.database.transaction() as connection:
            await connection.execute(
                "UPDATE companion.organize_state SET last_error=%s,reviewed_watermark=watermark,last_reviewed_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND revision=%s",
                (code, actor_id, scope_key, expected_revision))

    async def _reviewed(self, actor_id, scope_key, expected_revision, cursor, changed):
        async with self.companion.database.transaction() as connection:
            await connection.execute("UPDATE companion.organize_state SET cursor=%s,reviewed_watermark=watermark,last_error=NULL,last_reviewed_at=now(),last_changed_at=CASE WHEN %s THEN now() ELSE last_changed_at END WHERE actor_id=%s AND scope_key=%s AND revision=%s", (cursor, changed, actor_id, scope_key, expected_revision))

    async def view(self, context, *, cursor=None, limit=40):
        await self.companion.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.style_rules WHERE actor_id=%s AND (person_id IS NULL OR person_id=%s) AND scope_ids<@%s::text[] "
                "AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, context.person_id, list(context.scope_ids), cursor, cursor, limit+1))).fetchall()
        permitted = await self.companion.identity.check_many(context, tuple(source_refs(row["source_refs"]) for row in rows[:limit]))
        items = [{**row, "occurred_at": row["created_at"], "evidence": row["source_refs"], "can_modify": bool(context.permissions & {"*","companion.manage"})} for row, valid in zip(rows[:limit], permitted, strict=True) if valid]
        projection = await self.companion.memory.projection(context, context.person_id)
        notes = [{"id": item.proposal_id, "revision": item.revision, "kind": "reflection", "title": item.content[:60],
                  "content": item.content, "state": "active", "occurred_at": item.occurred_at,
                  "evidence": item.source_refs, "source_refs": item.source_refs, "episode_ids": item.episode_ids,
                  "conditions": item.conditions, "counterexamples": item.counterexamples, "can_modify": False}
                 for item in projection["reflections"] if item.kind == "reflection"]
        async with self.companion.database.transaction(read_only=True) as connection:
            review=await (await connection.execute("SELECT revision,last_error,last_reviewed_at FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s",(context.actor_id,signature(sorted(context.scope_ids))))).fetchone()
        return {"can_review":bool(context.permissions & {"*","companion.manage"}),"review":({**review,"can_request_review":review["last_error"] in REVIEW_ERRORS} if review else None),"items": items, "next_cursor": rows[limit-1]["id"] if len(rows)>limit else None,
                "reflections": notes, "missing": [] if all(permitted) else ["source_unavailable"],
                "evidence": await self.companion.memory.style_view(context, context.person_id)}

    async def material(self, context):
        view = await self.view(context, limit=40)
        rules = [row for row in view["items"] if row["state"] == "active"]
        if not rules:
            return ()
        refs = unique_refs(tuple(ref for row in rules for ref in source_refs(row["source_refs"])))
        return (ContextContribution("learned_habits", json.dumps(wire(rules), ensure_ascii=False), refs, context.scope_ids, 55),)

    async def set_state(self, context, body, state):
        if state not in {"active", "revoked"}:
            raise AppError("invalid_growth_state")
        await self.companion.identity.check(context)
        async with self.companion.database.transaction(read_only=True) as connection:
            original = await (await connection.execute("SELECT * FROM companion.style_rules WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]", (body.get("id"), context.actor_id, list(context.scope_ids)))).fetchone()
        if not original:
            raise AppError("growth_item_not_found", status=404)
        await self.companion.identity.check(context, source_refs(original["source_refs"]))
        async def apply(connection):
            await self.companion.validate_persistence(connection,context,source_refs(original["source_refs"]))
            row = await (await connection.execute("SELECT * FROM companion.style_rules WHERE id=%s FOR UPDATE", (original["id"],))).fetchone()
            revision(row, body.get("expected_revision"))
            await connection.execute("UPDATE companion.style_rules SET state=%s,reason=%s,revision=revision+1,updated_at=now() WHERE id=%s", (state, "explicit_user_change", row["id"]))
            return CommandResult(body["client_request_id"], {"id": row["id"], "state": state}, row["revision"]+1)
        return await self.companion.commands.execute(context, "companion.growth."+state, body["client_request_id"], signature(body), apply)
