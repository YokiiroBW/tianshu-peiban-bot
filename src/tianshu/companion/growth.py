"""Incremental subjective reflections and revocable expression habits."""
from tianshu.sources.dependencies import unique_refs
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import aiohttp

from tianshu.models.types import ModelRequest
from tianshu.models.retry import transient_model_failure
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
from .growth_observations import MATERIAL_PROTOCOL, material_coverages
from .growth_output_repair import review_available
from .growth_recovery import AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED, GrowthRecovery, authorization_error
from .growth_retry import GrowthRetries, error_code, later_cursor, manual_retry_allowed, material_time
from .reflection_output import (CORRECTABLE_ERRORS, IDENTITY_PROTOCOL, OUTPUT_PROTOCOL, OUTPUT_TOKEN_BUDGET,
                                REVIEW_ERRORS, frozen_episodes, prepare_reflections, validate_batch_request)


class GrowthService:
    def __init__(self, companion, jobs, models, assembler, *, on_projection_changed):
        self.companion, self.jobs, self.models = companion, jobs, models
        # This closed batch must not acquire unrelated optional memory as reflection evidence.
        self.assembler = ContextAssembler(companion.identity, companion.memory, None, settings=assembler.settings)
        self.on_projection_changed = on_projection_changed
        self.generations = GenerationRequests(companion.database, companion.sources, models)
        self.recovery = GrowthRecovery(self)
        self.retries = GrowthRetries(self)

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
        if row["last_error"] not in REVIEW_ERRORS | {AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED}:
            await self.jobs.enqueue(connection, JobSpec("companion.organize", f"organize:{context.actor_id}:{scope_key}:{row['revision']}",
                {"actor_id": context.actor_id, "scope_key": scope_key, "expected_revision": row["revision"]},
                scheduled_at=datetime.now(UTC)+timedelta(seconds=30), subject_id=context.actor_id))

    async def _register_input(self, connection, context, scope_key, episode):
        registered = await (await connection.execute(
            "INSERT INTO companion.growth_inputs(actor_id,scope_key,episode_id,revision,sequence,context) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(actor_id,scope_key,episode_id) DO UPDATE SET revision=excluded.revision,sequence=excluded.sequence,"
            "context=excluded.context,state='pending',missing='{}',retry_key=NULL,updated_at=now() "
            "WHERE companion.growth_inputs.revision<excluded.revision RETURNING episode_id",
            (context.actor_id, scope_key, episode.episode_id, episode.revision, episode.sequence, document(context)))).fetchone()
        return bool(registered)

    async def organize(self, actor_id, scope_key, expected_revision):
        owner = {}
        try:
            return await self._organize(actor_id, scope_key, expected_revision, owner)
        except (AppError, TimeoutError, aiohttp.ClientError) as error:
            code = error_code(error)
            if not transient_model_failure(code):
                raise
            async with self.companion.database.transaction(read_only=True) as connection:
                state = await (await connection.execute(
                    "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s",
                    (actor_id, scope_key))).fetchone()
            if (not state or not state["active_batch"]
                    or state["active_batch"]["material_key"] != owner.get("material_key")):
                raise
            # A failed source/result read or commit is not a failed provider
            # response. The due task observes this exact attempt first.
            return await self.retries.defer(state, state["active_batch"], code)

    async def _organize(self, actor_id, scope_key, expected_revision, owner):
        async with self.companion.database.transaction(read_only=True) as connection:
            state = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if not state:
            return
        if state["last_error"] == AUTHORIZATION_BLOCKED:
            return self.recovery.blocked(state)
        if state["last_error"] == IDENTITY_BLOCKED:
            return {"state": "blocked", "error_code": IDENTITY_BLOCKED, "recovery": "observe_original_attempt"}
        if state["legacy_state"] != "complete":
            return await self.recovery.step(state)
        if not state["active_batch"] and state["last_error"] not in REVIEW_ERRORS:
            state = await self.recovery.activate(state)
        if state["last_error"] in REVIEW_ERRORS and not state["active_batch"]:
            return
        batch = state["active_batch"]
        if batch:
            owner["material_key"] = batch["material_key"]
        if batch and batch.get("reflection_identity_protocol") != IDENTITY_PROTOCOL and not batch.get("identity_conversion_key"):
            async with self.companion.database.transaction() as connection:
                state = await self.recovery.register_active_on(connection, state)
            batch = state["active_batch"]
        if not batch and state["revision"] != expected_revision:
            return
        original_context = context_from_record(batch["context"] if batch else state["context"])
        context = original_context
        observe_only = self.recovery.observes_original(batch, state["blocked_material"]) or state["last_error"] in REVIEW_ERRORS
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
                return await self.retries.defer(state, batch, denied.code)
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
                return await self.retries.defer(state, batch, error.code)
            if request is None:
                await self._rejected(actor_id, scope_key, material_key, "reflection_material_contract_invalid")
                return
        else:
            async with self.companion.database.transaction(read_only=True) as connection:
                pending = await (await connection.execute(
                    "SELECT episode_id,revision FROM companion.growth_inputs WHERE actor_id=%s AND scope_key=%s "
                    "AND reviewed_revision<revision AND retry_key IS NULL ORDER BY sequence,episode_id LIMIT 33", (actor_id, scope_key))).fetchall()
            if not pending:
                return {"state": "idle"}
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
                "persona": (actor["persona_id"], actor["persona_revision"]), "generation_revision": state["generation_revision"],
                "material_protocol": MATERIAL_PROTOCOL, "output_protocol": OUTPUT_PROTOCOL})
            owner["material_key"] = material_key
            batch = {"material_key": material_key, "context": wire(context), "start_cursor": state["cursor"],
                     "end_cursor": end_cursor, "watermark": state["watermark"], "has_more": has_more,
                     "inputs": [(item.episode_id, item.revision) for item in episodes],
                     "material_coverage": material_coverages(episodes),
                     "material_at": max(item.occurred_at for item in episodes).isoformat(),
                     "reflection_identity_protocol": IDENTITY_PROTOCOL, "reflection_output_protocol": OUTPUT_PROTOCOL,
                     "episode_refs": {"e"+str(index+1): item.episode_id for index, item in enumerate(episodes)}}
            request = ModelRequest(material_key, attempt_for(material_key), "reflect", ServiceClass.ELASTIC,
                context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=OUTPUT_TOKEN_BUDGET,
                estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs),
                execution_timeout_seconds=180.0)

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
        except (AppError, TimeoutError, aiohttp.ClientError) as error:
            if isinstance(error, AppError) and error.code == "generation_no_longer_current":
                return
            if authorization_error(error):
                return await self.retries.defer(state, batch, error.code)
            if isinstance(error, AppError) and error.code == "generation_request_expired":
                await self._rejected(actor_id, scope_key, material_key, error.code)
                return {"state": "rejected", "error_code": error.code, "attempt_id": request.attempt_id}
            # Only a successfully frozen intent permits original-result
            # observation. An RPC error says nothing about provider execution.
            frozen = await self.generations.request(request.attempt_id, original_context)
            if frozen is None:
                raise
            request = frozen
            rpc_error = error.code if isinstance(error, AppError) else type(error).__name__
            result, read_error = None, rpc_error if observe_only else None
            if not observe_only:
                try:
                    result = await self.generations.read(request.attempt_id, context)
                except (AppError, TimeoutError, aiohttp.ClientError) as failure:
                    if authorization_error(failure):
                        return await self.retries.defer({**state, "active_batch": batch}, batch, failure.code)
                    read_error = failure.code if isinstance(failure, AppError) else type(failure).__name__
            async with self.companion.database.transaction() as connection:
                current = await (await connection.execute(
                    "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                    (actor_id, scope_key))).fetchone()
                if (not current or not current["active_batch"] or current["active_batch"]["material_key"] != material_key
                        or current["last_error"] in REVIEW_ERRORS | {AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED}):
                    return {"state": "changed_since_original_result_read"}
                previous = current["blocked_material"] or {}
                details = {**previous, "recovery": "observe_original_attempt",
                    "attempt_id": request.attempt_id, "rpc_error": previous.get("rpc_error", rpc_error), "result_read_error": read_error,
                    "model_status": result.receipt.status if result else None,
                    "model_error": result.receipt.error_code if result else None,
                    "reason": "请求响应中断后仅观察原执行；不提交新请求或替换未知结果。"}
                await connection.execute(
                    "UPDATE companion.organize_state SET blocked_material=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s", (document(details), actor_id, scope_key))
                state, batch = {**current, "blocked_material": details}, current["active_batch"]
        if result and getattr(result.receipt, "cancellation_reason", None) == "explicit_cancel":
            return await self.retries.defer(state, batch, "explicit_cancel", result)
        if result is None or result.receipt.status != "succeeded":
            status = result.receipt.status if result is not None else "unknown"
            details = state["blocked_material"] or {}
            code = (result.receipt.error_code or ("reflection_outcome_unknown" if status == "unknown" else
                    "reflection_generation_" + status)) if result else (
                    details.get("result_read_error") or details.get("rpc_error") or "reflection_outcome_unknown")
            if status not in {"queued", "running"}:
                return await self.retries.defer(state, batch, code, result)
            async with self.companion.database.transaction() as connection:
                await connection.execute(
                    "UPDATE companion.organize_state SET last_error=%s WHERE actor_id=%s AND scope_key=%s AND active_batch->>'material_key'=%s",
                    (code, actor_id, scope_key, material_key))
                if status in {"queued", "running"}:
                    await self._enqueue(connection, actor_id, scope_key, state["revision"], "observe:" + material_key, delay=30)
            return {"state": "waiting", "error_code": code, "attempt_id": request.attempt_id,
                    "recovery": "observe_original_attempt"}
        state = {**state, "active_batch": batch}
        async with self.companion.database.transaction(read_only=True) as connection:
            current_owner = await (await connection.execute(
                "SELECT active_batch->>'material_key' AS material_key FROM companion.organize_state "
                "WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if not current_owner or current_owner["material_key"] != material_key:
            return {"state": "superseded_attempt", "attempt_id": request.attempt_id}
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
            batch = validate_batch_request(batch, frozen, episodes)
            batch = {**batch, "material_at": material_time(episodes).isoformat()}
            state = {**state, "active_batch": batch}
            if any(not item.source_refs for item in episodes.values()):
                raise AppError("reflection_original_evidence_required", status=403)
            prepared = prepare_reflections(result, context, episodes, generated_refs, request=frozen)
            if batch.get("identity_conversion_key"):
                prepared, batch = await self.recovery.reflection_identities(state, context, batch, frozen, episodes, prepared)
                state = {**state, "active_batch": batch}
            history_only = bool(batch.get("retry_key") and state["reviewed_material_at"]
                                and state["reviewed_material_at"] > material_time(episodes))
            if history_only:
                # A delayed judgement is historical evidence, not the newest
                # habit/interest or mood. Keep the full model result in Models.
                prepared = tuple(replace(item, proposal=replace(item.proposal,
                    occurred_at=material_time(episodes)), affect=None)
                    for item in prepared if item.proposal.kind == "reflection")
                batch = {**batch, "historical_only": True, "material_coverage": {
                    identifier: {**batch.get("material_coverage", {}).get(identifier, {}), "complete": False,
                        "missing": sorted(set(batch.get("material_coverage", {}).get(identifier, {}).get("missing", ()))
                                          | {"projection_superseded_by_newer_material"})}
                    for identifier, _version in batch["inputs"]}}
            relationship = (await self.companion.memory.relationship(context, context.person_id)
                            if context.person_id and any(item.proposal.kind == "style" for item in prepared) else None)
            changed = bool(prepared)
            async with self.companion.database.transaction() as connection:
                await self._validate_material(connection, context, episodes, generated_refs)
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
                return await self.retries.defer(state, batch, error.code)
            if error.code == IDENTITY_BLOCKED:
                async with self.companion.database.transaction() as connection:
                    await connection.execute(
                        "UPDATE companion.organize_state SET last_error=%s,blocked_material=%s,updated_at=now() "
                        "WHERE actor_id=%s AND scope_key=%s AND active_batch->>'material_key'=%s",
                        (IDENTITY_BLOCKED, document({"recovery": "observe_original_attempt",
                            "reason": "原反思身份冲突，需核对既有记录；仅可复核原结果，不会新建模型请求。"}),
                         actor_id, scope_key, material_key))
                return {"state": "blocked", "error_code": IDENTITY_BLOCKED,
                    "attempt_id": result.receipt.attempt_id, "recovery": "observe_original_attempt"}
            if error.code not in REVIEW_ERRORS | {"reflection_not_original_evidence", "reflection_original_evidence_required", "reflection_episode_unavailable", "reflection_episode_revision_conflict"}:
                raise
            code = error.code if error.code in REVIEW_ERRORS else "reflection_material_contract_invalid"
            if code in CORRECTABLE_ERRORS:
                outcome = await self.retries.defer(state, batch, code, result, diagnostic=error.details)
                return {**outcome, "state": "rejected", "recovery": outcome["state"]}
            await self._rejected(actor_id, scope_key, material_key, code)
            return {"state": "rejected", "error_code": code, "attempt_id": result.receipt.attempt_id}
        return {"state": "reviewed", "cursor": batch["end_cursor"], "has_more": batch["has_more"]}

    async def request_review(self,context,body):
        await self.companion.identity.check(context)
        if body.get("retry_key") is not None and (not isinstance(body["retry_key"], str) or not body["retry_key"]):
            raise AppError("invalid_input")
        scope_key=signature(sorted(context.scope_ids))
        async def apply(connection):
            await self.companion.validate_persistence(connection,context)
            row=await (await connection.execute("SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",(context.actor_id,scope_key))).fetchone()
            if not row:
                raise AppError("reflection_material_not_found",status=404)
            revision(row,body.get("expected_revision"))
            if body.get("retry_key"):
                current = await self.retries.resume_on(connection, row, context, body["retry_key"])
                return CommandResult(body["client_request_id"], {"state": "queued", "recovery": "retry_computation"}, current)
            if not row["active_batch"] and row["last_error"] not in REVIEW_ERRORS:
                blocked = await self.retries.blocked_for_scene(connection, row, context)
                if blocked:
                    current = await self.retries.resume_on(connection, row, context, blocked)
                    return CommandResult(body["client_request_id"], {"state": "queued", "recovery": "retry_computation"}, current)
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

    async def _validate_material(self, connection, context, episodes, generated_refs):
        closure = unique_refs(generated_refs, *(item.source_refs for item in episodes.values()))
        await self.companion.validate_persistence(connection, context, closure)
        versions = tuple((item.episode_id, item.revision) for item in episodes.values())
        readable = await self.companion.memory.episodes_by_refs(context, versions, connection=connection)
        if set(versions) != {(item.episode_id, item.revision) for item in readable.items}:
            if "unavailable_sources" in readable.coverage.missing:
                raise AppError("source_unavailable", status=403)
            raise AppError("reflection_episode_unavailable", status=409)

    async def recover(self):
        await self.retries.recover()
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.organize_state s WHERE (last_error IS NULL OR last_error<>ALL(%s)) AND (legacy_state<>'complete' OR "
                "active_batch IS NOT NULL OR ((last_error IS NULL OR last_error<>ALL(%s)) AND ("
                "EXISTS(SELECT 1 FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key "
                "AND reviewed_revision<i.revision AND retry_key IS NULL) OR "
                "EXISTS(SELECT 1 FROM companion.growth_legacy_requests r WHERE r.actor_id=s.actor_id AND r.scope_key=s.scope_key AND r.state='pending'))))",
                (sorted({AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED}), sorted(REVIEW_ERRORS)))).fetchall()
        for row in rows:
            # Startup only registers work. Each old context/material is handled
            # by its own nonretrying job; it cannot abort another domain's start.
            async with self.companion.database.transaction() as connection:
                row = await self.recovery.register_active_on(connection, row)
                await self.jobs.enqueue(connection,JobSpec("companion.organize",f"organize-recover:{row['actor_id']}:{row['scope_key']}:{row['revision']}:{datetime.now(UTC).isoformat()}",
                    {"actor_id":row["actor_id"],"scope_key":row["scope_key"],"expected_revision":row["revision"]},subject_id=row["actor_id"]))

    async def _rejected(self, actor_id, scope_key, material_key, code):
        async with self.companion.database.transaction(read_only=True) as connection:
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if current and current["active_batch"] and current["active_batch"]["material_key"] == material_key:
            return await self.retries.defer(current, current["active_batch"], code)

    async def _enqueue(self, connection, actor_id, scope_key, revision, reason, *, delay=0):
        await self.jobs.enqueue(connection, JobSpec("companion.organize",
            f"organize:{actor_id}:{scope_key}:{revision}:{reason}:{datetime.now(UTC).isoformat()}",
            {"actor_id": actor_id, "scope_key": scope_key, "expected_revision": revision},
            scheduled_at=datetime.now(UTC) + timedelta(seconds=delay), subject_id=actor_id))

    async def _finish(self, connection, current, batch, changed):
        await self.retries.complete_on(connection, current, batch, history_only=batch.get("historical_only", False))
        if batch.get("legacy_material"):
            await connection.execute(
                "UPDATE companion.growth_legacy_requests SET state='reviewed',error_code=NULL,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND material_key=%s AND (state='pending' OR (%s AND state='abandoned'))",
                (current["actor_id"], current["scope_key"], batch["material_key"], bool(batch.get("retry_key"))))
        for identifier, version in batch.get("inputs", ()):
            coverage = batch.get("material_coverage", {}).get(identifier, {})
            await connection.execute(
                "UPDATE companion.growth_inputs SET reviewed_revision=%s,state='reviewed',missing=%s,retry_key=NULL,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s",
                (version, list(coverage.get("missing", ())), current["actor_id"], current["scope_key"], identifier, version))
        remaining = await (await connection.execute(
            "SELECT EXISTS(SELECT 1 FROM companion.growth_inputs WHERE actor_id=%s AND scope_key=%s AND reviewed_revision<revision) OR "
            "EXISTS(SELECT 1 FROM companion.growth_legacy_requests WHERE actor_id=%s AND scope_key=%s AND state='pending') AS pending",
            (current["actor_id"], current["scope_key"], current["actor_id"], current["scope_key"]))).fetchone()
        next_revision = current["revision"] + 1
        await connection.execute(
            "UPDATE companion.organize_state SET cursor=%s,reviewed_watermark=%s,active_batch=NULL,blocked_material=NULL,"
            "last_error=NULL,last_reviewed_at=now(),revision=%s,updated_at=now(),reviewed_material_at=GREATEST(reviewed_material_at,%s),"
            "last_changed_at=CASE WHEN %s THEN now() ELSE last_changed_at END WHERE actor_id=%s AND scope_key=%s",
            (later_cursor(current["cursor"], batch["end_cursor"]), current["reviewed_watermark"] if remaining["pending"] else batch["watermark"],
             next_revision, datetime.fromisoformat(batch["material_at"]) if batch.get("material_at") else None,
             changed, current["actor_id"], current["scope_key"]))
        ready = await (await connection.execute(
            "SELECT EXISTS(SELECT 1 FROM companion.growth_inputs WHERE actor_id=%s AND scope_key=%s "
            "AND reviewed_revision<revision AND retry_key IS NULL) OR EXISTS(SELECT 1 FROM companion.growth_legacy_requests "
            "WHERE actor_id=%s AND scope_key=%s AND state='pending') AS pending",
            (current["actor_id"], current["scope_key"], current["actor_id"], current["scope_key"]))).fetchone()
        if ready["pending"]:
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
                "(SELECT count(*) FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='reviewed' AND cardinality(i.missing)>0) AS partial_inputs,"
                "ARRAY(SELECT DISTINCT gap.reason FROM companion.growth_inputs i CROSS JOIN LATERAL unnest(i.missing) gap(reason) "
                "WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='reviewed' ORDER BY gap.reason) AS material_missing,"
                "ARRAY(SELECT DISTINCT gap.reason FROM companion.growth_inputs i CROSS JOIN LATERAL unnest(i.missing) gap(reason) "
                "WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='unavailable' ORDER BY gap.reason) AS input_missing,"
                "(SELECT count(*) FROM companion.growth_legacy_requests r WHERE r.actor_id=s.actor_id AND r.scope_key=s.scope_key AND r.state='pending') AS pending_original_requests "
                "FROM companion.organize_state s WHERE actor_id=%s AND scope_key=%s",
                (context.actor_id,signature(sorted(context.scope_ids))))).fetchone()
            retries = await (await connection.execute(
                "SELECT retry_key,batch->'context' AS context,batch->'output_repair_count' AS output_repair_count,"
                "current_material_key,previous_material_key,attempt_number,"
                "state,due_at,last_error,waiting_on_original,outcome,revision FROM companion.growth_retry_batches "
                "WHERE actor_id=%s AND scope_key=%s ORDER BY CASE state WHEN 'active' THEN 0 WHEN 'waiting' THEN 1 "
                "WHEN 'blocked' THEN 2 ELSE 3 END,due_at NULLS LAST,updated_at DESC LIMIT 20",
                (context.actor_id, signature(sorted(context.scope_ids))))).fetchall()
            retry_summary = await (await connection.execute(
                "SELECT count(*) FILTER(WHERE state='waiting') AS waiting,count(*) FILTER(WHERE state='active') AS active,"
                "count(*) FILTER(WHERE state='blocked') AS blocked,min(due_at) FILTER(WHERE state='waiting') AS next_retry_at "
                "FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s",
                (context.actor_id, signature(sorted(context.scope_ids))))).fetchone()
        if review:
            review["retry_summary"] = retry_summary
            review["retry_batches"] = [{**{key: value for key, value in retry.items() if key != "context"},
                "attempt_id": attempt_for(retry["current_material_key"]),
                "can_request_review": retry["state"] == "blocked" and manual_retry_allowed(retry["last_error"])
                    and review_available({"output_repair_count": retry.get("output_repair_count") or 0}, retry["last_error"])
                    and self.recovery.same_scene(context, context_from_record(retry["context"]))}
                for retry in retries]
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
            if review["last_error"] == IDENTITY_BLOCKED:
                review["reason"] = "原反思身份冲突，需核对既有记录；此操作仅复核原结果，不会新建模型请求。"
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
