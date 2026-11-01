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
from tianshu.models.completion import completion_from_native
from .growth_materials import select_batch
from .reflection_output import (OUTPUT_TOKEN_BUDGET, REVIEW_ERRORS, frozen_episodes, prepare_reflections)


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
            "revision=companion.organize_state.revision+1,updated_at=now() WHERE companion.organize_state.watermark<>excluded.watermark RETURNING revision,last_error",
            (context.actor_id, scope_key, watermark, document(context)))).fetchone()
        if row and row["last_error"] not in REVIEW_ERRORS:
            await self.jobs.enqueue(connection, JobSpec("companion.organize", f"organize:{context.actor_id}:{scope_key}:{row['revision']}",
                {"actor_id": context.actor_id, "scope_key": scope_key, "expected_revision": row["revision"]},
                scheduled_at=datetime.now(UTC)+timedelta(seconds=30), subject_id=context.actor_id))

    async def organize(self, actor_id, scope_key, expected_revision):
        async with self.companion.database.transaction(read_only=True) as connection:
            state = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if not state or state["last_error"] in REVIEW_ERRORS:
            return
        batch = state["active_batch"]
        if not batch and (state["revision"] != expected_revision or state["reviewed_watermark"] == state["watermark"]):
            return
        context = context_from_record(batch["context"] if batch else state["context"])
        await self.companion.identity.check(context)
        if batch:
            material_key = batch["material_key"]
            request = await self.generations.request(attempt_for(material_key), context)
            if request is None:
                await self._rejected(actor_id, scope_key, material_key, "reflection_material_contract_invalid")
                return
        else:
            page = await self.companion.memory.episodes_since(context, state["cursor"], 32)
            if not page.items:
                async with self.companion.database.transaction() as connection:
                    current = await (await connection.execute(
                        "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE", (actor_id, scope_key))).fetchone()
                    if current and current["revision"] == expected_revision and not current["active_batch"]:
                        await self._finish(connection, current, {"end_cursor": page.watermark,
                            "watermark": state["watermark"], "has_more": bool(page.next_cursor)}, False)
                return
            actor = await self.companion.actor(context)
            persona = await self.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
            budget = RequestBudget(datetime.now(UTC) + timedelta(minutes=3))
            try:
                episodes, pack, end_cursor, has_more = await select_batch(
                    self.assembler, context, page, persona.prompt_text if persona else "", budget, expected_revision)
            except AppError as error:
                if error.code not in {"reflection_material_budget_exceeded", "reflection_material_source_required"}:
                    raise
                async with self.companion.database.transaction() as connection:
                    await connection.execute(
                        "UPDATE companion.organize_state SET last_error=%s,blocked_material=%s,updated_at=now() "
                        "WHERE actor_id=%s AND scope_key=%s AND revision=%s AND active_batch IS NULL",
                        (error.code, document(error.details or {}), actor_id, scope_key, expected_revision))
                return {"state": "blocked", "error_code": error.code, "material": error.details}
            material_key = "organize:" + signature({"context": self.generations.context_key(context),
                "episodes": [(item.episode_id, item.revision) for item in episodes],
                "persona": (actor["persona_id"], actor["persona_revision"]), "generation_revision": state["generation_revision"]})
            batch = {"material_key": material_key, "context": wire(context), "start_cursor": state["cursor"],
                     "end_cursor": end_cursor, "watermark": state["watermark"], "has_more": has_more}
            request = ModelRequest(material_key, attempt_for(material_key), "reflect", ServiceClass.ELASTIC,
                context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=OUTPUT_TOKEN_BUDGET,
                estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs))

        async def current_material(connection):
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE", (actor_id, scope_key))).fetchone()
            if not current or current["cursor"] != batch["start_cursor"]:
                return False
            if current["active_batch"]:
                return current["active_batch"]["material_key"] == material_key and current["last_error"] not in REVIEW_ERRORS
            if current["revision"] != expected_revision or current["last_error"] in REVIEW_ERRORS:
                return False
            # GenerationRequests stores the original request in this same transaction.
            # Later events may update the pending watermark, never this frozen batch.
            await connection.execute(
                "UPDATE companion.organize_state SET active_batch=%s,blocked_material=NULL,last_error=NULL,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s", (document(batch), actor_id, scope_key))
            return True
        try:
            result = await self.generations.run(material_key, request, guard=current_material)
        except AppError as error:
            if error.code == "generation_no_longer_current":
                return
            if error.code != "generation_request_expired":
                raise
            await self._rejected(actor_id, scope_key, material_key, error.code)
            return {"state": "rejected", "error_code": error.code, "attempt_id": request.attempt_id}
        if result is None or result.receipt.status != "succeeded":
            status = result.receipt.status if result is not None else "unknown"
            code = result.receipt.error_code or "reflection_generation_" + status if result else "reflection_outcome_unknown"
            if status in {"failed", "cancelled"}:
                await self._rejected(actor_id, scope_key, material_key, "reflection_generation_" + status)
                return {"state": "rejected", "error_code": code, "attempt_id": request.attempt_id}
            async with self.companion.database.transaction() as connection:
                await connection.execute(
                    "UPDATE companion.organize_state SET last_error=%s WHERE actor_id=%s AND scope_key=%s AND active_batch->>'material_key'=%s",
                    (code, actor_id, scope_key, material_key))
                if status in {"queued", "running"}:
                    await self._enqueue(connection, actor_id, scope_key, state["revision"], "observe:" + material_key, delay=30)
            return {"state": "waiting", "error_code": code, "attempt_id": request.attempt_id,
                    "recovery": "observe_original_attempt"}
        frozen = await self.generations.request(request.attempt_id, context)
        generated_refs = frozen.source_refs
        await self.companion.identity.check(context, generated_refs)
        try:
            completion = result.completion
            if completion.state == "unknown":
                completion = completion_from_native(result.receipt.protocol, result.native_response)
            if completion.state == "incomplete":
                raise AppError("reflection_content_incomplete", details={"stop_reasons": completion.stop_reasons})
            episodes = frozen_episodes(frozen)
            prepared = prepare_reflections(result, context, episodes, generated_refs)
            relationship = (await self.companion.memory.relationship(context, context.person_id)
                            if context.person_id and any(item.proposal.kind == "style" for item in prepared) else None)
            changed = bool(prepared)
            async with self.companion.database.transaction() as connection:
                await self.companion.validate_persistence(connection, context, generated_refs)
                current = await (await connection.execute(
                    "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE", (actor_id, scope_key))).fetchone()
                if not current or not current["active_batch"] or current["active_batch"]["material_key"] != material_key:
                    return
                for item in prepared:
                    proposal = item.proposal
                    await self.companion.memory.record_reflection(context, proposal, connection=connection,
                        episode_revisions={key: episode.revision for key, episode in episodes.items()})
                    if proposal.kind in {"style", "interest"} and not (proposal.kind == "style" and relationship and relationship["frozen"]):
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
                        await self.companion.apply_affect(context, item.affect, connection=connection)
                await self._finish(connection, current, batch, changed)
        except AppError as error:
            if error.code not in REVIEW_ERRORS | {"reflection_not_original_evidence", "reflection_original_evidence_required", "reflection_episode_unavailable", "reflection_episode_revision_conflict"}:
                raise
            code = error.code if error.code in REVIEW_ERRORS else "reflection_material_contract_invalid"
            await self._rejected(actor_id, scope_key, material_key, code)
            return {"state": "rejected", "error_code": code, "attempt_id": result.receipt.attempt_id}
        if changed:
            await self.on_projection_changed(context, f"growth:{scope_key}:{batch['end_cursor']}")
        return {"state": "reviewed", "cursor": batch["end_cursor"], "has_more": batch["has_more"]}

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
            await connection.execute("UPDATE companion.organize_state SET generation_revision=generation_revision+1,revision=%s,watermark=%s,last_error=NULL,active_batch=NULL,blocked_material=NULL,context=%s WHERE actor_id=%s AND scope_key=%s",(current,"manual:"+body["client_request_id"],document(context),context.actor_id,scope_key))
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

    async def _rejected(self, actor_id, scope_key, material_key, code):
        async with self.companion.database.transaction() as connection:
            await connection.execute(
                "UPDATE companion.organize_state SET last_error=%s,last_reviewed_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND active_batch->>'material_key'=%s",
                (code, actor_id, scope_key, material_key))

    async def _enqueue(self, connection, actor_id, scope_key, revision, reason, *, delay=0):
        await self.jobs.enqueue(connection, JobSpec("companion.organize",
            f"organize:{actor_id}:{scope_key}:{revision}:{reason}:{datetime.now(UTC).isoformat()}",
            {"actor_id": actor_id, "scope_key": scope_key, "expected_revision": revision},
            scheduled_at=datetime.now(UTC) + timedelta(seconds=delay), subject_id=actor_id))

    async def _finish(self, connection, current, batch, changed):
        next_revision = current["revision"] + 1
        await connection.execute(
            "UPDATE companion.organize_state SET cursor=%s,reviewed_watermark=%s,active_batch=NULL,blocked_material=NULL,"
            "last_error=NULL,last_reviewed_at=now(),revision=%s,updated_at=now(),"
            "last_changed_at=CASE WHEN %s THEN now() ELSE last_changed_at END WHERE actor_id=%s AND scope_key=%s",
            (batch["end_cursor"], current["reviewed_watermark"] if batch["has_more"] else batch["watermark"],
             next_revision, changed, current["actor_id"], current["scope_key"]))
        if batch["has_more"] or current["watermark"] != batch["watermark"]:
            await self._enqueue(connection, current["actor_id"], current["scope_key"], next_revision, "continuation")

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
            review=await (await connection.execute("SELECT revision,last_error,last_reviewed_at,blocked_material,active_batch->>'material_key' AS material_key,cursor FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s",(context.actor_id,signature(sorted(context.scope_ids))))).fetchone()
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
