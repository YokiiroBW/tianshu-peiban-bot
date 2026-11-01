"""Incremental subjective reflections and revocable expression habits."""
from tianshu.sources.dependencies import unique_refs
from datetime import UTC, datetime, timedelta
import json

from tianshu.models.types import ModelRequest
from tianshu.memory.types import EpisodePage
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ContextContribution, Coverage, RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from tianshu.life.validation import revision
from tianshu.conversation.generation import GenerationRequests, attempt_for
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.models.completion import completion_from_native
from .growth_materials import select_batch, reflectable
from .growth_recovery import AUTHORIZATION_BLOCKED, GrowthRecovery, authorization_error
from .reflection_output import (OUTPUT_TOKEN_BUDGET, REVIEW_ERRORS, frozen_episodes, prepare_reflections)


class GrowthService:
    def __init__(self, companion, jobs, models, assembler, *, on_projection_changed):
        self.companion, self.jobs, self.models = companion, jobs, models
        # This closed batch must not acquire unrelated optional memory as reflection evidence.
        self.assembler = ContextAssembler(companion.identity, companion.memory, None, settings=assembler.settings)
        self.on_projection_changed = on_projection_changed
        self.generations = GenerationRequests(companion.database, companion.sources, models)
        self.recovery = GrowthRecovery(self)

    async def changed(self, context, watermark, *, episode=None, connection=None):
        if episode is None or not reflectable(episode):
            return
        if connection is None:
            await self.companion.identity.check(context)
            async with self.companion.database.transaction() as transaction:
                return await self.changed(context, watermark, episode=episode, connection=transaction)
        await self.companion.validate_persistence(connection, context)
        scope_key = signature(sorted(context.scope_ids))
        created = await (await connection.execute(
            "INSERT INTO companion.organize_state(actor_id,scope_key,watermark,context,legacy_state) VALUES (%s,%s,%s,%s,'complete') "
            "ON CONFLICT(actor_id,scope_key) DO NOTHING RETURNING revision",
            (context.actor_id, scope_key, watermark, document(context)))).fetchone()
        # Queue mutations consistently lock the consumer state before its inputs.
        await connection.execute(
            "SELECT revision FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
            (context.actor_id, scope_key))
        if not await self._register_input(connection, context, scope_key, episode):
            return
        await connection.execute(
            "UPDATE companion.organize_state SET last_error=NULL,blocked_material=NULL WHERE actor_id=%s AND scope_key=%s "
            "AND blocked_material->>'episode_id'=%s AND (blocked_material->>'revision')::bigint<%s",
            (context.actor_id, scope_key, episode.episode_id, episode.revision))
        row = await (await connection.execute(
            "UPDATE companion.organize_state SET watermark=%s,context=CASE WHEN last_error=%s THEN context ELSE %s END,revision=revision+%s,updated_at=now() "
            "WHERE actor_id=%s AND scope_key=%s RETURNING revision,last_error",
            (watermark, AUTHORIZATION_BLOCKED, document(context), 0 if created else 1, context.actor_id, scope_key))).fetchone()
        if row["last_error"] not in REVIEW_ERRORS | {AUTHORIZATION_BLOCKED}:
            await self.jobs.enqueue(connection, JobSpec("companion.organize", f"organize:{context.actor_id}:{scope_key}:{row['revision']}",
                {"actor_id": context.actor_id, "scope_key": scope_key, "expected_revision": row["revision"]},
                scheduled_at=datetime.now(UTC)+timedelta(seconds=30), subject_id=context.actor_id))

    async def _register_input(self, connection, context, scope_key, episode):
        registered = await (await connection.execute(
            "INSERT INTO companion.growth_inputs(actor_id,scope_key,episode_id,revision,sequence,context) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(actor_id,scope_key,episode_id) DO UPDATE SET revision=excluded.revision,sequence=excluded.sequence,"
            "context=excluded.context,state='pending',missing='{}',updated_at=now() "
            "WHERE companion.growth_inputs.revision<excluded.revision RETURNING episode_id",
            (context.actor_id, scope_key, episode.episode_id, episode.revision, episode.sequence, document(context)))).fetchone()
        return bool(registered)

    async def organize(self, actor_id, scope_key, expected_revision):
        async with self.companion.database.transaction(read_only=True) as connection:
            state = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if not state:
            return
        if state["last_error"] == AUTHORIZATION_BLOCKED:
            return self.recovery.blocked(state)
        if state["legacy_state"] != "complete":
            return await self.recovery.step(state)
        if not state["active_batch"] and state["last_error"] not in REVIEW_ERRORS:
            state = await self.recovery.activate(state)
        if state["last_error"] in REVIEW_ERRORS:
            return
        batch = state["active_batch"]
        if not batch and state["revision"] != expected_revision:
            return
        original_context = context_from_record(batch["context"] if batch else state["context"])
        context = original_context
        observe_only = self.recovery.observes_original(batch, state["blocked_material"])
        try:
            await self.companion.identity.check(context)
        except AppError as error:
            if not authorization_error(error):
                raise
            if not batch:
                return await self.recovery.block(state, error)
            try:
                context = await self.recovery.reader(original_context)
            except AppError as denied:
                if not authorization_error(denied):
                    raise
                return await self.recovery.block(state, denied)
            observe_only = True
        if batch:
            material_key = batch["material_key"]
            try:
                # The current reader does not replace the original request key,
                # epoch, source closure or frozen Episode revisions.
                request = await self.generations.request(attempt_for(material_key), original_context)
            except AppError as error:
                if not authorization_error(error):
                    raise
                return await self.recovery.block(state, error)
            if request is None:
                await self._rejected(actor_id, scope_key, material_key, "reflection_material_contract_invalid")
                return
        else:
            async with self.companion.database.transaction(read_only=True) as connection:
                pending = await (await connection.execute(
                    "SELECT episode_id,revision FROM companion.growth_inputs WHERE actor_id=%s AND scope_key=%s "
                    "AND reviewed_revision<revision ORDER BY sequence,episode_id LIMIT 33", (actor_id, scope_key))).fetchall()
            refs = tuple((item["episode_id"], item["revision"]) for item in pending[:32])
            available = await self.companion.memory.episodes_by_refs(context, refs)
            found = {(item.episode_id, item.revision) for item in available.items}
            missing = tuple(ref for ref in refs if ref not in found)
            source_less = tuple(item for item in available.items if reflectable(item) and not item.source_refs)
            unavailable = [(identifier, version, available.coverage.missing or ("episode_unavailable_or_revised",))
                           for identifier, version in missing]
            unavailable.extend((item.episode_id, item.revision, ("original_source_missing",)) for item in source_less)
            if unavailable:
                try:
                    if not await self._unavailable(context, state, unavailable):
                        return {"state": "changed_since_material_read"}
                except AppError as error:
                    if not authorization_error(error):
                        raise
                    return await self.recovery.block(state, error)
            without_sources = {(item.episode_id, item.revision) for item in source_less}
            items = tuple(item for item in available.items if (item.episode_id, item.revision) not in without_sources)
            coverage = available.coverage
            if source_less:
                coverage = Coverage(False, missing=tuple(sorted(set(coverage.missing) | {"original_source_missing"})))
            page = EpisodePage(items, "pending" if len(pending)>32 else None, coverage,
                items[-1].cursor if items else state["cursor"] or "e:0")
            if not any(reflectable(item) for item in page.items):
                async with self.companion.database.transaction() as connection:
                    current = await (await connection.execute(
                        "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE", (actor_id, scope_key))).fetchone()
                    if current and current["revision"] == expected_revision and not current["active_batch"]:
                        await self._finish(connection, current, {"end_cursor": page.watermark,
                            "watermark": state["watermark"], "has_more": bool(page.next_cursor),
                            "inputs": [(item.episode_id, item.revision) for item in items]}, False)
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
                     "end_cursor": end_cursor, "watermark": state["watermark"], "has_more": has_more,
                     "inputs": [(item.episode_id, item.revision) for item in episodes]}
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
            if observe_only:
                result = await self.generations.read(request.attempt_id, context)
            else:
                result = await self.generations.run(material_key, request, guard=current_material)
        except AppError as error:
            if error.code == "generation_no_longer_current":
                return
            if authorization_error(error):
                return await self.recovery.block(state, error)
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
        state = {**state, "active_batch": batch}
        try:
            frozen = await self.generations.request(request.attempt_id, original_context)
            if frozen is None:
                raise AppError("reflection_material_contract_invalid")
            generated_refs = frozen.source_refs
            await self.companion.identity.check(context, generated_refs)
            completion = result.completion
            if completion.state == "unknown":
                completion = completion_from_native(result.receipt.protocol, result.native_response)
            if completion.state == "incomplete":
                raise AppError("reflection_content_incomplete", details={"stop_reasons": completion.stop_reasons})
            episodes = frozen_episodes(frozen)
            if any(not item.source_refs for item in episodes.values()):
                raise AppError("reflection_original_evidence_required")
            prepared = prepare_reflections(result, context, episodes, generated_refs)
            relationship = (await self.companion.memory.relationship(context, context.person_id)
                            if context.person_id and any(item.proposal.kind == "style" for item in prepared) else None)
            changed = bool(prepared)
            async with self.companion.database.transaction() as connection:
                await self.companion.validate_persistence(connection, context, generated_refs)
                versions = tuple((item.episode_id, item.revision) for item in episodes.values())
                readable = await self.companion.memory.episodes_by_refs(context, versions, connection=connection)
                if set(versions) != {(item.episode_id, item.revision) for item in readable.items}:
                    if "unavailable_sources" in readable.coverage.missing:
                        raise AppError("source_unavailable", status=403)
                    raise AppError("reflection_episode_unavailable", status=409)
                # Match Memory's source -> Episode -> projection lock order.
                # Any later batch guard failure rolls back these writes too.
                for item in prepared:
                    await self.companion.memory.record_reflection(context, item.proposal, connection=connection,
                        episode_revisions={key: episode.revision for key, episode in episodes.items()})
                current = await (await connection.execute(
                    "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE", (actor_id, scope_key))).fetchone()
                if not current or not current["active_batch"] or current["active_batch"]["material_key"] != material_key:
                    raise AppError("generation_no_longer_current", status=409)
                for item in prepared:
                    proposal = item.proposal
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
                if changed:
                    await self.on_projection_changed(context, f"growth:{scope_key}:{batch['end_cursor']}", connection=connection)
        except AppError as error:
            if error.code == "generation_no_longer_current":
                return
            if authorization_error(error):
                return await self.recovery.block(state, error)
            if error.code not in REVIEW_ERRORS | {"reflection_not_original_evidence", "reflection_original_evidence_required", "reflection_episode_unavailable", "reflection_episode_revision_conflict"}:
                raise
            code = error.code if error.code in REVIEW_ERRORS else "reflection_material_contract_invalid"
            await self._rejected(actor_id, scope_key, material_key, code)
            return {"state": "rejected", "error_code": code, "attempt_id": result.receipt.attempt_id}
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
            if row["last_error"] == AUTHORIZATION_BLOCKED or (
                row["last_error"] not in REVIEW_ERRORS and
                self.recovery.observes_original(row["active_batch"], row["blocked_material"])
            ):
                current = await self.recovery.resume(connection, row, context)
                return CommandResult(body["client_request_id"], {"state": "queued", "recovery": (
                    "observe_original_attempt" if row["active_batch"] else "reauthorize_original_scope")}, current)
            if row["last_error"] not in REVIEW_ERRORS:
                raise AppError("reflection_original_attempt_unresolved",status=409)
            if row["last_error"] == "reflection_material_source_required" and not row["active_batch"]:
                current = row["revision"] + 1
                await connection.execute(
                    "UPDATE companion.organize_state SET revision=%s,last_error=NULL,blocked_material=NULL,context=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s", (current, document(context), context.actor_id, scope_key))
                await self._enqueue(connection, context.actor_id, scope_key, current, "available-material-resume")
                return CommandResult(body["client_request_id"],
                    {"state": "queued", "recovery": "continue_available_material"}, current)
            if row["active_batch"] and row["active_batch"].get("legacy_material"):
                await connection.execute(
                    "UPDATE companion.growth_legacy_requests SET state='abandoned',error_code=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND material_key=%s AND state='pending'",
                    (row["last_error"], context.actor_id, scope_key, row["active_batch"]["material_key"]))
            current=row["revision"]+1
            await connection.execute("UPDATE companion.organize_state SET generation_revision=generation_revision+1,revision=%s,watermark=%s,last_error=NULL,active_batch=NULL,blocked_material=NULL,context=%s WHERE actor_id=%s AND scope_key=%s",(current,"manual:"+body["client_request_id"],document(context),context.actor_id,scope_key))
            await self.jobs.enqueue(connection,JobSpec("companion.organize",f"organize-explicit:{body['client_request_id']}",{"actor_id":context.actor_id,"scope_key":scope_key,"expected_revision":current},subject_id=context.actor_id))
            return CommandResult(body["client_request_id"],{"state":"queued"},current)
        return await self.companion.commands.execute(context,"companion.growth.review",body["client_request_id"],signature(body),apply)

    async def _unavailable(self, context, state, versions):
        """Close unavailable versions as coverage gaps, never successful reviews."""
        async with self.companion.database.transaction() as connection:
            await self.companion.validate_persistence(connection, context)
            current = await (await connection.execute(
                "SELECT revision,active_batch FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"]))).fetchone()
            if not current or current["revision"] != state["revision"] or current["active_batch"]:
                return False
            for identifier, version, missing in versions:
                await connection.execute(
                    "UPDATE companion.growth_inputs SET reviewed_revision=revision,state='unavailable',missing=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s AND reviewed_revision<revision",
                    (list(missing), state["actor_id"], state["scope_key"], identifier, version))
        return True

    async def recover(self):
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.organize_state s WHERE (last_error IS NULL OR last_error<>%s) AND (legacy_state<>'complete' OR "
                "((last_error IS NULL OR last_error<>ALL(%s)) AND (active_batch IS NOT NULL OR reviewed_watermark<>watermark OR "
                "EXISTS(SELECT 1 FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND reviewed_revision<i.revision) OR "
                "EXISTS(SELECT 1 FROM companion.growth_legacy_requests r WHERE r.actor_id=s.actor_id AND r.scope_key=s.scope_key AND r.state='pending'))))",
                (AUTHORIZATION_BLOCKED, sorted(REVIEW_ERRORS)))).fetchall()
        for row in rows:
            # Startup only registers work. Each old context/material is handled
            # by its own nonretrying job; it cannot abort another domain's start.
            async with self.companion.database.transaction() as connection:
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
        if batch.get("legacy_material"):
            await connection.execute(
                "UPDATE companion.growth_legacy_requests SET state='reviewed',error_code=NULL,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND material_key=%s AND state='pending'",
                (current["actor_id"], current["scope_key"], batch["material_key"]))
        for identifier, version in batch.get("inputs", ()):
            await connection.execute(
                "UPDATE companion.growth_inputs SET reviewed_revision=%s,state='reviewed',missing='{}',updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s",
                (version, current["actor_id"], current["scope_key"], identifier, version))
        remaining = await (await connection.execute(
            "SELECT EXISTS(SELECT 1 FROM companion.growth_inputs WHERE actor_id=%s AND scope_key=%s AND reviewed_revision<revision) OR "
            "EXISTS(SELECT 1 FROM companion.growth_legacy_requests WHERE actor_id=%s AND scope_key=%s AND state='pending') AS pending",
            (current["actor_id"], current["scope_key"], current["actor_id"], current["scope_key"]))).fetchone()
        next_revision = current["revision"] + 1
        await connection.execute(
            "UPDATE companion.organize_state SET cursor=%s,reviewed_watermark=%s,active_batch=NULL,blocked_material=NULL,"
            "last_error=NULL,last_reviewed_at=now(),revision=%s,updated_at=now(),"
            "last_changed_at=CASE WHEN %s THEN now() ELSE last_changed_at END WHERE actor_id=%s AND scope_key=%s",
            (batch["end_cursor"], current["reviewed_watermark"] if remaining["pending"] else batch["watermark"],
             next_revision, changed, current["actor_id"], current["scope_key"]))
        if remaining["pending"] or current["watermark"] != batch["watermark"]:
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
            review=await (await connection.execute(
                "SELECT revision,last_error,last_reviewed_at,blocked_material,active_batch->>'material_key' AS material_key,"
                "active_batch-'context' AS batch,coalesce(active_batch->'context',context) AS recovery_context,"
                "cursor,legacy_state,legacy_request_cursor,legacy_episode_cursor,legacy_missing,"
                "(SELECT count(*) FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND reviewed_revision<i.revision) AS pending_inputs,"
                "(SELECT count(*) FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='unavailable') AS unavailable_inputs,"
                "ARRAY(SELECT DISTINCT gap.reason FROM companion.growth_inputs i CROSS JOIN LATERAL unnest(i.missing) gap(reason) "
                "WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='unavailable' ORDER BY gap.reason) AS input_missing,"
                "(SELECT count(*) FROM companion.growth_legacy_requests r WHERE r.actor_id=s.actor_id AND r.scope_key=s.scope_key AND r.state='pending') AS pending_original_requests "
                "FROM companion.organize_state s WHERE actor_id=%s AND scope_key=%s",
                (context.actor_id,signature(sorted(context.scope_ids))))).fetchone()
        if review:
            original = context_from_record(review.pop("recovery_context"))
            same_scene = self.recovery.same_scene(context, original)
            review = {**review, "attempt_id": attempt_for(review["material_key"]) if review["material_key"] else None,
                "can_request_review": review["legacy_state"] == "complete" and review["last_error"] in REVIEW_ERRORS,
                "recovery": "observe_original_attempt" if review["material_key"] and review["last_error"] not in REVIEW_ERRORS else None}
            review["legacy_missing"] = sorted(set(review["legacy_missing"]) | set(review.pop("input_missing")))
            if (review["last_error"] not in REVIEW_ERRORS and
                    self.recovery.observes_original(review["batch"], review["blocked_material"])):
                review["can_request_review"] = same_scene
                review["recovery"] = "observe_original_attempt"
                review["reason"] = "只读取原请求结果并复核原版本；不会提交新请求或替换未知结果。"
            if review["last_error"] == "reflection_material_source_required" and not review["material_key"]:
                review["recovery"] = "continue_available_material"
                review["reason"] = "逐项记录原件缺项，继续有来源的后续经历；不补造原件。"
            if review["last_error"] == AUTHORIZATION_BLOCKED:
                review["recovery"] = "observe_original_attempt" if review["material_key"] else "reauthorize_original_scope"
                review["can_request_review"] = same_scene
                review["reason"] = ("在原权限范围重新授权后，只读取原请求结果；不会提交新请求。"
                    if review["material_key"] else "在原权限范围重新授权后，从原扫描位置继续。")
                # Existing UI must show the saved blocker rather than falsely
                # claiming an unauthorized conversion is still running.
                review.pop("legacy_state")  # Its exact phase remains in blocked_material.
        return {"can_review":bool(context.permissions & {"*","companion.manage"}),"review":review,"items": items, "next_cursor": rows[limit-1]["id"] if len(rows)>limit else None,
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
