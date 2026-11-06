"""Incremental subjective reflections and revocable expression habits."""
from tianshu.sources.dependencies import unique_refs
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

import aiohttp

from tianshu.models.types import ModelRequest
from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from tianshu.memory.types import EpisodePage
from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.database import classify_database_failure
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from tianshu.life.validation import revision, timestamp
from tianshu.conversation.generation import GenerationRequests, attempt_for
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.models.completion import completion_from_native
from .growth_materials import select_batch, reflectable
from .growth_observations import MATERIAL_PROTOCOL, material_coverages
from .growth_output_repair import review_available
from .growth_recovery import AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED, GrowthRecovery, authorization_error
from .growth_retry import GrowthRetries, error_code, later_cursor, manual_retry_allowed, material_time
from .habits import HabitLedger, management_scope
from .expression_examples import ExpressionExamples
from .growth_statistics import GrowthStatistics
from .growth_operations import GrowthOperations
from .growth_input_status import GrowthInputStatus
from .growth_policy import OWNER, MAX_ATTEMPTS, database_now_on, reserve_initial, succeed as succeed_recovery
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
        self.habits = HabitLedger(companion, on_changed=self._index_learning)
        self.examples = ExpressionExamples(companion, self.habits)
        self.life = None
        self.observations = GrowthStatistics(companion.database, jobs)
        self.recovery_policy = None
        self.operations = GrowthOperations(self)
        self.input_status = GrowthInputStatus(companion, jobs)

    def set_observability(self, *, statistics, recovery_policy):
        self.observations.bind(statistics)
        self.recovery_policy = recovery_policy

    def set_conversation(self, conversation):
        self.examples.conversation = conversation

    def set_life(self, life):
        self.life = life

    def set_operations_guard(self, guard_on):
        self.operations.guard_on = guard_on

    async def changed(self, context, watermark, *, episode=None, connection=None, generation=None):
        if episode is None:
            return
        actual = episode.actual_result or {}
        low_rest = (actual.get('derived_kind') == 'routine_rest' and actual.get('learning_value') == 'low'
                    and actual.get('experience_eligible') is False)
        if not reflectable(episode) and not low_rest:
            return
        if connection is None:
            if not generation:
                raise AppError('growth_generation_required')
            await self.companion.identity.check(context)
            async with self.companion.database.transaction() as transaction:
                return await self.changed(context, watermark, episode=episode, connection=transaction, generation=generation)
        if generation is None:
            generation = await self.jobs.current_generation_on(connection)
        await self.jobs.assert_generation_on(connection, generation)
        await self.companion.validate_persistence(connection, context)
        scope_key = signature(sorted(context.scope_ids))
        created = await (await connection.execute(
            "INSERT INTO companion.organize_state(actor_id,scope_key,watermark,context,runtime_generation) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT(actor_id,scope_key) DO NOTHING RETURNING revision",
            (context.actor_id, scope_key, watermark, document(context), generation))).fetchone()
        # Queue mutations consistently lock the consumer state before its inputs.
        saved = await (await connection.execute(
            "SELECT revision,runtime_generation FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
            (context.actor_id, scope_key))).fetchone()
        if saved['runtime_generation'] != generation:
            raise AppError('growth_generation_changed', status=409)
        if not await self._register_input(connection, context, scope_key, episode, generation):
            return
        if low_rest:
            await connection.execute(
                "UPDATE companion.growth_inputs SET reviewed_revision=revision,state='reviewed',skip_reason='low_value_rest',"
                "missing='{}',updated_at=now() WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s",
                (context.actor_id, scope_key, episode.episode_id, episode.revision))
            await connection.execute(
                'UPDATE companion.organize_state SET watermark=%s,revision=revision+%s,updated_at=now(),'
                'reviewed_watermark=CASE WHEN NOT EXISTS(SELECT 1 FROM companion.growth_inputs i '
                'WHERE i.actor_id=%s AND i.scope_key=%s AND i.reviewed_revision<i.revision) THEN %s ELSE reviewed_watermark END '
                'WHERE actor_id=%s AND scope_key=%s',
                (watermark, 0 if created else 1, context.actor_id, scope_key, watermark, context.actor_id, scope_key))
            return {'state': 'skipped', 'reason': 'low_value_rest'}
        await connection.execute(
            "UPDATE companion.organize_state SET last_error=NULL,blocked_material=NULL WHERE actor_id=%s AND scope_key=%s "
            "AND blocked_material->>'episode_id'=%s AND (blocked_material->>'revision')::bigint<%s",
            (context.actor_id, scope_key, episode.episode_id, episode.revision))
        row = await (await connection.execute(
            "UPDATE companion.organize_state SET watermark=%s,context=CASE WHEN last_error=%s THEN context ELSE %s END,revision=revision+%s,updated_at=now() "
            "WHERE actor_id=%s AND scope_key=%s RETURNING revision,last_error,blocked_material",
            (watermark, AUTHORIZATION_BLOCKED, document(context), 0 if created else 1, context.actor_id, scope_key))).fetchone()
        if row["last_error"] not in REVIEW_ERRORS | {AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED}:
            due = await database_now_on(connection) + timedelta(seconds=30)
            blocked = row['blocked_material'] or {}
            if blocked.get('recovery') == 'dependency_wait':
                due = max(due, timestamp(blocked['retry_at']))
            await self._enqueue(connection, context.actor_id, scope_key, row['revision'], 'episode-changed',
                                generation=generation, scheduled_at=due)

    async def _register_input(self, connection, context, scope_key, episode, generation):
        registered = await (await connection.execute(
            "INSERT INTO companion.growth_inputs(actor_id,scope_key,episode_id,revision,sequence,context) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(actor_id,scope_key,episode_id) DO UPDATE SET revision=excluded.revision,sequence=excluded.sequence,"
            "context=excluded.context,state='pending',missing='{}',skip_reason=NULL,retry_key=NULL,updated_at=now() "
            "WHERE companion.growth_inputs.revision<excluded.revision RETURNING episode_id",
            (context.actor_id, scope_key, episode.episode_id, episode.revision, episode.sequence, document(context)))).fetchone()
        if registered:
            superseded = await (await connection.execute(
                "UPDATE companion.growth_material_failures SET state='superseded',observation_revision=observation_revision+1,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND episode_revision<%s AND runtime_generation=%s AND state='blocked' RETURNING *",
                (context.actor_id, scope_key, episode.episode_id, episode.revision, generation))).fetchall()
            for failure in superseded:
                await self.observations.emit_on(connection, self.observations.material_event(failure), generation=failure['runtime_generation'])
        return bool(registered)

    async def consume_episode_change(self, context, episode_id, revision, generation, watermark=None):
        if isinstance(context, dict):
            context = context_from_record(context)
        async with self.companion.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
        await self.companion.identity.check(context)
        page = await self.companion.memory.episodes_by_refs(context, ((episode_id, revision),))
        if not page.items:
            return {'state': 'unavailable', 'missing': page.coverage.missing}
        return await self.changed(context, watermark or f'episode:{episode_id}:{revision}', episode=page.items[0], generation=generation)

    async def organize(self, actor_id, scope_key, expected_revision, generation):
        owner = {}
        try:
            return await self._organize(actor_id, scope_key, expected_revision, owner, generation)
        except RecoveryWait as wait:
            if not recovery_wait_retryable(wait) and owner.get('material_key'):
                attempt_id = attempt_for(owner['material_key'])
                try:
                    await self.retries.isolate_control_wait(actor_id, scope_key, owner['material_key'],
                                                            generation, wait)
                except Exception as isolation_error:
                    # A failed isolation commit can itself be uncertain. Read
                    # the actual owner before propagating either uncertainty.
                    try:
                        await self.retries.observe_control_owner(actor_id, scope_key, generation)
                    except Exception:
                        pass
                    try:
                        await self.retries.current_status(attempt_id)
                    except Exception:
                        pass
                    raise wait from isolation_error
                await self.retries.propagate_unconfirmed_wait(wait, attempt_id)
            raise
        except Exception as error:
            failure = classify_database_failure(error)
            if failure is not None:
                if failure.receipt_required:
                    try:
                        reconciled = await self.recovery.reconcile_commit(
                            actor_id, scope_key, owner.get('material_key'), owner.get('inputs', ()), generation)
                    except RecoveryWait:
                        # Keep the remote rollback evidence from the original-ID read.
                        raise
                    except Exception:
                        raise error
                    if reconciled is not None:
                        return reconciled
                raise
            if not isinstance(error, (AppError, TimeoutError, aiohttp.ClientError)):
                raise
            if owner.get('result_succeeded') and isinstance(error, (TimeoutError, aiohttp.ClientError)):
                # A later local or dependency error cannot turn a known model
                # success into an unknown physical attempt.
                raise
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

    async def _organize(self, actor_id, scope_key, expected_revision, owner, generation):
        async with self.companion.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            state = await (await connection.execute(
                "SELECT *,clock_timestamp() AS db_now FROM companion.organize_state "
                "WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if not state:
            return
        if state['runtime_generation'] != generation:
            raise AppError('growth_generation_changed', status=409)
        if state["last_error"] == AUTHORIZATION_BLOCKED:
            return self.recovery.blocked(state)
        if state["last_error"] == IDENTITY_BLOCKED:
            return {"state": "blocked", "error_code": IDENTITY_BLOCKED, "recovery": "observe_original_attempt"}
        if state["last_error"] in REVIEW_ERRORS and not state["active_batch"]:
            return
        batch = state["active_batch"]
        prepared_without_active_batch = batch is None
        reloading_authoritative_batch = False
        if batch:
            owner["material_key"] = batch["material_key"]
            owner['inputs'] = batch.get('inputs', ())
        if not batch and state["revision"] != expected_revision:
            return
        blocked = state['blocked_material'] or {}
        if not batch and blocked.get('recovery') == 'dependency_wait':
            due = timestamp(blocked['retry_at'])
            if due > state['db_now']:
                async with self.companion.database.transaction() as connection:
                    await self.jobs.assert_generation_on(connection, generation)
                    current = await (await connection.execute(
                        'SELECT revision,active_batch,blocked_material FROM companion.organize_state '
                        'WHERE actor_id=%s AND scope_key=%s FOR UPDATE', (actor_id, scope_key))).fetchone()
                    if (current and current['revision'] == state['revision'] and not current['active_batch']
                            and current['blocked_material'] == blocked):
                        await self._enqueue(connection, actor_id, scope_key, current['revision'], 'dependency-wait',
                                            generation=generation, scheduled_at=due)
                return {'state': 'waiting_dependency', 'reason': blocked['reason'], 'retry_at': due}
        original_context = context_from_record(batch["context"] if batch else state["context"])
        context = original_context
        observe_only = (self.recovery.observes_original(batch, state["blocked_material"])
            or bool(batch and batch.get('recovery_observe_only')) or state["last_error"] in REVIEW_ERRORS)
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
                    await self.jobs.assert_generation_on(connection, generation)
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
                rules = await self.habits.candidates(context, include_inactive=True)
                examples = await self.examples.candidates(context, page.items)
                episodes, pack, end_cursor, has_more = await select_batch(
                    self.assembler, context, page, persona.prompt_text if persona else "", budget, expected_revision,
                    rules=rules, examples=examples)
            except RecoveryWait:
                raise
            except AppError as error:
                if error.code not in {"reflection_material_budget_exceeded", "reflection_material_source_required"}:
                    raise
                return await self._block_input(context, state, expected_revision, error)
            material_key = "organize:" + signature({"generation": generation, "context": self.generations.context_key(context),
                "episodes": [(item.episode_id, item.revision) for item in episodes],
                "persona": (actor["persona_id"], actor["persona_revision"]), "generation_revision": state["generation_revision"],
                "material_protocol": MATERIAL_PROTOCOL, "output_protocol": OUTPUT_PROTOCOL})
            owner["material_key"] = material_key
            batch = {"material_key": material_key, "runtime_generation": generation, "context": wire(context), "start_cursor": state["cursor"],
                     "end_cursor": end_cursor, "watermark": state["watermark"], "has_more": has_more,
                     "inputs": [(item.episode_id, item.revision) for item in episodes],
                     "material_coverage": material_coverages(episodes),
                     "material_at": max(item.occurred_at for item in episodes).isoformat(),
                     "reflection_identity_protocol": IDENTITY_PROTOCOL, "reflection_output_protocol": OUTPUT_PROTOCOL,
                     "episode_refs": {"e"+str(index+1): item.episode_id for index, item in enumerate(episodes)}}
            owner['inputs'] = batch['inputs']
            request = ModelRequest(material_key, attempt_for(material_key), "reflect", ServiceClass.ELASTIC,
                context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=OUTPUT_TOKEN_BUDGET,
                estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs),
                execution_timeout_seconds=180.0, data_generation=generation)
            try:
                profile = await self.models.generation_profile(request)
                if not profile.get('quota_group') or not profile.get('route_id') or not profile.get('route_revision'):
                    raise AppError('growth_model_quota_unavailable', status=503)
            except RecoveryWait as wait:
                if wait.reason == 'recovery_claim_lost' or not recovery_wait_retryable(wait):
                    raise
                return await self.recovery.wait_dependency(state, batch, wait)
            except (AppError, TimeoutError, aiohttp.ClientError) as error:
                if classify_database_failure(error) is not None:
                    raise
                if isinstance(error, AppError) and authorization_error(error):
                    return await self.recovery.block(state, error)
                failed = AppError('growth_model_profile_unavailable', status=503, details={
                    'episode_id': episodes[0].episode_id, 'revision': episodes[0].revision,
                    'underlying_error': error_code(error)})
                return await self._block_input(context, state, expected_revision, failed)
            request = replace(request, route_id=profile['route_id'], route_revision=profile['route_revision'])
            batch.update(recovery_dependency='models:quota:'+profile['quota_group'], recovery_route=profile['route_id'])

        async def current_material(connection):
            nonlocal batch, state, request, reloading_authoritative_batch
            await self.jobs.assert_generation_on(connection, generation)
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE", (actor_id, scope_key))).fetchone()
            if not current or current["cursor"] != batch["start_cursor"]:
                return False
            if current["active_batch"]:
                active = current['active_batch']
                if (active.get('material_key') != material_key or active.get('start_cursor') != current['cursor']
                        or current['runtime_generation'] != generation or active.get('runtime_generation') != generation
                        or current['last_error'] in REVIEW_ERRORS | {AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED}
                        or prepared_without_active_batch and current['revision'] != expected_revision):
                    return False
                # Another preparer can have the same key with only a provisional
                # batch. Adopt the persisted owner and its frozen request together.
                reloading_authoritative_batch = True
                candidate_batch = batch
                try:
                    candidate_inputs = tuple(tuple(item) for item in candidate_batch['inputs'])
                    active_inputs = tuple(tuple(item) for item in active['inputs'])
                except (KeyError, TypeError):
                    raise AppError('reflection_material_contract_invalid') from None
                if candidate_inputs != active_inputs:
                    return False
                batch, state = active, current
                if not isinstance(batch.get('lineage_id'), str) or not batch['lineage_id']:
                    raise AppError('growth_recovery_lineage_missing', status=503)
                if not isinstance(batch.get('recovery_version'), str) or not batch['recovery_version']:
                    raise AppError('growth_recovery_version_missing', status=503)
                for key in ('lineage_id', 'recovery_version'):
                    if candidate_batch.get(key) is not None and candidate_batch.get(key) != batch[key]:
                        return False
                try:
                    frozen_context = context_from_record(batch['context'])
                except (KeyError, TypeError, ValueError):
                    raise AppError('reflection_material_contract_invalid') from None
                if frozen_context != request.context:
                    return False
                frozen = await self.generations.request(
                    attempt_for(material_key), frozen_context, connection=connection, material_key=material_key)
                if frozen is None:
                    raise AppError('generation_request_not_found', status=404,
                                   details={'attempt_id': attempt_for(material_key)})
                if (frozen.attempt_id != attempt_for(material_key) or frozen.intent_id != material_key
                        or frozen.data_generation != generation):
                    raise AppError('generation_identity_conflict', status=409,
                                   details={'attempt_id': attempt_for(material_key)})
                if frozen.context != frozen_context:
                    raise AppError('generation_context_changed', status=409)
                validate_batch_request(batch, frozen, frozen_episodes(frozen))
                if prepared_without_active_batch:
                    changed = await self.retries.receipt_inputs_on(
                        connection, actor_id, scope_key, batch, batch.get('retry_key'))
                    if changed:
                        return False
                request = frozen
                return True
            if current["revision"] != expected_revision or current["last_error"] in REVIEW_ERRORS:
                return False
            recovery = await reserve_initial(self.recovery_policy, connection, context, batch, request)
            batch = {**batch, **recovery}
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
                request = await self.generations.prepare(material_key, request, guard=current_material)
                if request.data_generation != generation:
                    raise AppError('growth_generation_changed', status=409)
                result = await self.generations.read(request.attempt_id, context)
                if batch.get('recovery_observe_only') and result is None:
                    async with self.companion.database.transaction() as connection:
                        await self._enqueue(connection, actor_id, scope_key, expected_revision,
                                            'observe:'+material_key, generation=generation, delay=30)
                    return {'state': 'waiting', 'attempt_id': request.attempt_id,
                            'recovery': 'observe_original_attempt'}
                if result is None:
                    # A durable domain pointer is committed before the idempotent
                    # Models admission. Completion only wakes this owner; Models
                    # remains the authority for the result.
                    completion = JobSpec('companion.organize',
                        f'growth-completion:{generation}:{actor_id}:{scope_key}:{material_key}',
                        {'actor_id': actor_id, 'scope_key': scope_key, 'expected_revision': expected_revision, 'generation': generation},
                        subject_id=actor_id, data_generation=generation)
                    receipt = await self.models.submit(request, completion=completion)
                    if receipt.status in {'queued', 'running', 'succeeded'}:
                        result = await self.generations.read(request.attempt_id, context)
                        if result is None or result.receipt.status in {'queued', 'running'}:
                            async with self.companion.database.transaction() as connection:
                                await self._enqueue(connection, actor_id, scope_key, expected_revision,
                                                    'observe:'+material_key, generation=generation, delay=30)
                            return {'state': 'waiting', 'attempt_id': request.attempt_id,
                                    'recovery': 'observe_original_attempt'}
                    else:
                        result = await self.generations.read(request.attempt_id, context)
        except RecoveryWait as wait:
            if (wait.reason == 'recovery_claim_lost' or not recovery_wait_retryable(wait)
                    or batch.get('lineage_id')):
                # A frozen batch is resumed by the original Runtime job. It is
                # no longer eligible for the pre-admission domain wait path.
                raise
            return await self.recovery.wait_dependency(state, batch, wait)
        except (AppError, TimeoutError, aiohttp.ClientError) as error:
            if classify_database_failure(error) is not None:
                raise
            # A broken frozen batch is not an RPC failure that may be observed
            # or repaired by another prepare of the provisional request.
            if (reloading_authoritative_batch and isinstance(error, AppError) and error.code in {
                    'growth_recovery_lineage_missing', 'growth_recovery_version_missing',
                    'generation_request_not_found', 'generation_identity_conflict', 'generation_context_changed',
                    'reflection_material_contract_invalid', 'invalid_model_request'}):
                raise
            if isinstance(error, AppError) and error.code == "generation_no_longer_current":
                return
            if authorization_error(error):
                outcome = await self.retries.defer(state, batch, error.code)
                if outcome.get('state') == 'changed_since_original_result_read' and not state['active_batch']:
                    return await self.recovery.block(state, error)
                return outcome
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
                except RecoveryWait:
                    raise
                except (AppError, TimeoutError, aiohttp.ClientError) as failure:
                    if classify_database_failure(failure) is not None:
                        raise
                    if authorization_error(failure):
                        return await self.retries.defer({**state, "active_batch": batch}, batch, failure.code)
                    read_error = failure.code if isinstance(failure, AppError) else type(failure).__name__
            async with self.companion.database.transaction() as connection:
                await self.jobs.assert_generation_on(connection, generation)
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
            if status not in {"queued", "running"} or batch.get('control_observation'):
                return await self.retries.defer(state, batch, code, result, frozen_request=request)
            async with self.companion.database.transaction() as connection:
                await self.jobs.assert_generation_on(connection, generation)
                await connection.execute(
                    "UPDATE companion.organize_state SET last_error=%s WHERE actor_id=%s AND scope_key=%s AND active_batch->>'material_key'=%s",
                    (code, actor_id, scope_key, material_key))
                if status in {"queued", "running"}:
                    await self._enqueue(connection, actor_id, scope_key, state["revision"], "observe:" + material_key, generation=generation, delay=30)
            return {"state": "waiting", "error_code": code, "attempt_id": request.attempt_id,
                    "recovery": "observe_original_attempt"}
        owner['result_succeeded'] = True
        state = {**state, "active_batch": batch}
        async with self.companion.database.transaction(read_only=True) as connection:
            current_owner = await (await connection.execute(
                "SELECT active_batch->>'material_key' AS material_key FROM companion.organize_state "
                "WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if not current_owner or current_owner["material_key"] != material_key:
            await self.retries.settle_superseded(state, batch, request.attempt_id, generation)
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
            examples = {}
            for item in prepared:
                if item.action == 'example':
                    examples[item.proposal.proposal_id] = await self.examples.prepare(context, item, episodes)
            timezone = 'Asia/Shanghai'
            if any(item.validity == 'today' and item.proposal.kind in {'style', 'interest'} for item in prepared):
                if self.life is None:
                    raise AppError('growth_timezone_unavailable', status=503)
                timezone = (await self.life.state(context))['timezone']
            relationship = (await self.companion.memory.relationship(context, context.person_id)
                            if context.person_id and any(item.proposal.kind == "style" for item in prepared) else None)
            changed = bool(prepared)
            additional_refs = unique_refs(
                *(item.proposal.source_refs for item in prepared),
                *(item.affect.source_refs for item in prepared if item.affect is not None),
                *(relation['source_refs'] for relations in examples.values() for relation in relations))
            async with self.companion.database.transaction() as connection:
                await self._validate_material(connection, context, episodes, generated_refs,
                                              generation=generation, additional_refs=additional_refs)
                current = await (await connection.execute(
                    "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE", (actor_id, scope_key))).fetchone()
                if not current or not current["active_batch"] or current["active_batch"]["material_key"] != material_key:
                    raise AppError("generation_no_longer_current", status=409)
                await succeed_recovery(self.recovery_policy, connection, batch, request.attempt_id)
                await self.recovery_policy.assert_attempt_applicable_on(connection, request.attempt_id,
                    owner=OWNER, expected_version=batch['recovery_version'])
                for item in prepared:
                    proposal = item.proposal
                    if item.action == 'example':
                        relations = examples.get(proposal.proposal_id, ())
                        if not relations:
                            await self.habits.unresolved(connection, context, item, 'example_original_unavailable')
                        for relation in relations:
                            await self.examples.record(connection, context, item, relation)
                    elif proposal.kind in {"style", "interest"}:
                        changed_rule = await self.habits.apply(connection, context, item, episodes,
                            attempt_id=request.attempt_id,
                            frozen=bool(proposal.kind == 'style' and relationship and relationship['frozen']),
                            timezone=timezone)
                        if changed_rule and changed_rule.get('_unresolved'):
                            coverage = dict(batch.get('material_coverage', {}))
                            for identifier in item.support_ids:
                                existing = coverage.get(identifier, {})
                                coverage[identifier] = {**existing, 'complete': False,
                                    'missing': sorted(set(existing.get('missing', ())) | {'learning_identity_unresolved'})}
                            batch = {**batch, 'material_coverage': coverage}
                        elif changed_rule:
                            await self._index_learning(connection, context, changed_rule, changed_rule['kind'])
                    elif proposal.kind == 'reflection':
                        reflection = await self._record_reflection(connection, context, item, request.attempt_id)
                        await self._index_learning(connection, context, reflection, 'reflection')
                    if item.affect is not None and item.action != 'example' and any(
                            (episodes[identifier].actual_result or {}).get('derived_kind')
                            not in {'expression', 'reflection', 'diary'} for identifier in item.support_ids):
                        await self.companion.apply_affect(context, item.affect, connection=connection)
                await self._finish(connection, current, batch, changed)
                if changed:
                    await self.on_projection_changed(context, f"growth:{scope_key}:{batch['end_cursor']}", connection=connection)
        except RecoveryWait:
            raise
        except AppError as error:
            if error.code == "generation_no_longer_current":
                await self.retries.settle_superseded(state, batch, request.attempt_id, generation)
                return {"state": "superseded_attempt", "attempt_id": request.attempt_id}
            if authorization_error(error):
                return await self.retries.defer(state, batch, error.code, result)
            if error.code in {'growth_timezone_unavailable', 'growth_conversation_port_unavailable'}:
                return await self.retries.defer(state, batch, error.code, result)
            if error.code == IDENTITY_BLOCKED:
                async with self.companion.database.transaction() as connection:
                    await self.jobs.assert_generation_on(connection, generation)
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
                outcome = await self.retries.defer(state, batch, code, result, diagnostic=error.details or {})
                return {**outcome, "state": "rejected", "recovery": outcome["state"]}
            await self._rejected(actor_id, scope_key, material_key, code, result=result)
            return {"state": "rejected", "error_code": code, "attempt_id": result.receipt.attempt_id}
        return {"state": "reviewed", "cursor": batch["end_cursor"], "has_more": batch["has_more"]}

    async def request_review(self,context,body):
        await self.companion.identity.check(context)
        if body.get("retry_key") is not None and (not isinstance(body["retry_key"], str) or not body["retry_key"]):
            raise AppError("invalid_input")
        repair_ref = body.get('repair_ref')
        if repair_ref is not None and (not isinstance(repair_ref, str) or not repair_ref
                                       or repair_ref != repair_ref.strip() or len(repair_ref) > 256
                                       or any(ord(character) < 32 or ord(character) == 127 for character in repair_ref)):
            raise AppError('invalid_input')
        scope_key=signature(sorted(context.scope_ids))
        async def apply(connection):
            await self.companion.validate_persistence(connection,context)
            row=await (await connection.execute("SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",(context.actor_id,scope_key))).fetchone()
            if not row:
                raise AppError("reflection_material_not_found",status=404)
            await self.jobs.assert_generation_on(connection, row['runtime_generation'])
            revision(row,body.get("expected_revision"))
            if body.get('input_failure_key'):
                failure = await (await connection.execute(
                    "SELECT * FROM companion.growth_material_failures WHERE actor_id=%s AND scope_key=%s "
                    "AND failure_key=%s AND state='blocked' FOR UPDATE",
                    (context.actor_id, scope_key, body['input_failure_key']))).fetchone()
                if not failure or not self.recovery.same_scene(context, context_from_record(failure['context'])):
                    raise AppError('reflection_material_not_found', status=404)
                if not manual_retry_allowed(failure['error_code']):
                    raise AppError('reflection_retry_not_available', status=409)
                released = await (await connection.execute(
                    "UPDATE companion.growth_inputs SET retry_key=NULL,missing='{}',updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s "
                    "AND retry_key=%s AND reviewed_revision<revision RETURNING episode_id",
                    (context.actor_id, scope_key, failure['episode_id'], failure['episode_revision'],
                     failure['failure_key']))).fetchone()
                if not released:
                    raise AppError('reflection_episode_revision_conflict', status=409)
                failure = await (await connection.execute("UPDATE companion.growth_material_failures SET state='released',"
                    "observation_revision=observation_revision+1,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND failure_key=%s RETURNING *",
                    (context.actor_id, scope_key, failure['failure_key']))).fetchone()
                await self.observations.emit_on(connection, self.observations.material_event(failure), generation=row['runtime_generation'])
                updated = await (await connection.execute(
                    'UPDATE companion.organize_state SET revision=revision+1,updated_at=now() '
                    'WHERE actor_id=%s AND scope_key=%s RETURNING revision', (context.actor_id, scope_key))).fetchone()
                await self._enqueue(connection, context.actor_id, scope_key, updated['revision'], 'input-released', generation=row['runtime_generation'])
                return CommandResult(body['client_request_id'], {'state': 'queued', 'recovery': 'retry_input'}, updated['revision'])
            if body.get("retry_key"):
                current = await self.retries.resume_on(connection, row, context, body["retry_key"], repair_ref=repair_ref)
                return CommandResult(body["client_request_id"], {"state": "queued", "recovery": "retry_computation"}, current)
            if not row["active_batch"] and row["last_error"] not in REVIEW_ERRORS:
                blocked = await self.retries.blocked_for_scene(connection, row, context, repair_ref=repair_ref)
                if blocked:
                    current = await self.retries.resume_on(connection, row, context, blocked, repair_ref=repair_ref)
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
            if row['active_batch']:
                # A review signal cannot replace the frozen active request or
                # create a fresh lineage for the same work.
                current = await self.recovery.resume(connection, row, context)
                return CommandResult(body['client_request_id'],
                    {'state': 'queued', 'recovery': 'observe_original_attempt'}, current)
            if row["last_error"] == "reflection_material_source_required" and not row["active_batch"]:
                current = row["revision"] + 1
                await connection.execute(
                    "UPDATE companion.organize_state SET revision=%s,last_error=NULL,blocked_material=NULL,context=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s", (current, document(context), context.actor_id, scope_key))
                await self._enqueue(connection, context.actor_id, scope_key, current, "available-material-resume", generation=row['runtime_generation'])
                return CommandResult(body["client_request_id"],
                    {"state": "queued", "recovery": "continue_available_material"}, current)
            current=row["revision"]+1
            await connection.execute("UPDATE companion.organize_state SET generation_revision=generation_revision+1,revision=%s,watermark=%s,last_error=NULL,active_batch=NULL,blocked_material=NULL,context=%s WHERE actor_id=%s AND scope_key=%s",(current,"manual:"+body["client_request_id"],document(context),context.actor_id,scope_key))
            await self.jobs.enqueue(connection,JobSpec("companion.organize",f"organize-explicit:{row['runtime_generation']}:{body['client_request_id']}",{"actor_id":context.actor_id,"scope_key":scope_key,"expected_revision":current,'generation':row['runtime_generation']},subject_id=context.actor_id,data_generation=row['runtime_generation']))
            return CommandResult(body["client_request_id"],{"state":"queued"},current)
        return await self.companion.commands.execute(context,"companion.growth.review",body["client_request_id"],signature(body),apply)

    async def _block_input(self, context, state, expected_revision, error):
        details = error.details or {}
        episode_id, version = details.get('episode_id'), details.get('revision')
        if not episode_id or type(version) is not int:
            raise error
        scope_key = state['scope_key']
        key = 'input:' + signature({'actor': context.actor_id, 'scope': scope_key,
                                    'episode': episode_id, 'revision': version, 'generation': state['runtime_generation']})
        async with self.companion.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, state['runtime_generation'])
            await self.companion.validate_persistence(connection, context)
            current = await (await connection.execute(
                'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE',
                (context.actor_id, scope_key))).fetchone()
            if not current or current['revision'] != expected_revision or current['active_batch']:
                return {'state': 'changed_since_material_read'}
            reserved = await (await connection.execute(
                'UPDATE companion.growth_inputs SET retry_key=%s,missing=%s,updated_at=now() '
                'WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s '
                'AND reviewed_revision<revision AND retry_key IS NULL RETURNING episode_id',
                (key, [error.code], context.actor_id, scope_key, episode_id, version))).fetchone()
            if not reserved:
                return {'state': 'changed_since_material_read'}
            failure = await (await connection.execute(
                'INSERT INTO companion.growth_material_failures(actor_id,scope_key,episode_id,episode_revision,failure_key,error_code,details,context,runtime_generation) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(runtime_generation,actor_id,scope_key,episode_id,episode_revision) '
                "DO UPDATE SET error_code=excluded.error_code,details=excluded.details,state='blocked',"
                'retry_count=companion.growth_material_failures.retry_count+1,due_at=NULL,'
                'observation_revision=companion.growth_material_failures.observation_revision+1,updated_at=now() RETURNING *',
                (context.actor_id, scope_key, episode_id, version, key, error.code, document(details), document(context), state['runtime_generation']))).fetchone()
            if (error.code == 'growth_model_profile_unavailable' and
                    transient_model_failure(details.get('underlying_error')) and failure['retry_count'] < MAX_ATTEMPTS):
                due = await database_now_on(connection) + timedelta(seconds=retry_delay_seconds(failure['retry_count']))
                failure = await (await connection.execute(
                    'UPDATE companion.growth_material_failures SET due_at=%s WHERE actor_id=%s AND scope_key=%s '
                    'AND failure_key=%s RETURNING *', (due, context.actor_id, scope_key, key))).fetchone()
                await self._schedule_input_retry_on(connection, failure)
            await self.observations.emit_on(connection, self.observations.material_event(failure), generation=state['runtime_generation'])
            next_revision = current['revision'] + 1
            await connection.execute('UPDATE companion.organize_state SET revision=%s,updated_at=now() '
                'WHERE actor_id=%s AND scope_key=%s', (next_revision, context.actor_id, scope_key))
            await self._enqueue(connection, context.actor_id, scope_key, next_revision, 'input-blocked-yield', generation=state['runtime_generation'])
        return {'state': 'input_blocked', 'error_code': error.code, 'input_failure_key': key}

    async def retry_input(self, actor_id, scope_key, failure_key, expected_revision, generation):
        async with self.companion.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            failure = await (await connection.execute(
                "SELECT *,clock_timestamp() AS db_now FROM companion.growth_material_failures WHERE actor_id=%s AND scope_key=%s "
                "AND failure_key=%s AND state='blocked'", (actor_id, scope_key, failure_key))).fetchone()
        if not failure or failure['runtime_generation'] != generation or failure['observation_revision'] != expected_revision or not failure['due_at']:
            return {'state': 'superseded'}
        if failure['due_at'] > failure['db_now']:
            async with self.companion.database.transaction() as connection:
                await self.jobs.assert_generation_on(connection, generation)
                current = await (await connection.execute(
                    "SELECT * FROM companion.growth_material_failures WHERE actor_id=%s AND scope_key=%s "
                    "AND failure_key=%s AND runtime_generation=%s AND state='blocked' FOR UPDATE",
                    (actor_id, scope_key, failure_key, generation))).fetchone()
                if current and current['observation_revision'] == expected_revision and current['due_at']:
                    await self._schedule_input_retry_on(connection, current)
            return {'state': 'waiting'}
        original = context_from_record(failure['context'])
        try:
            await self.companion.identity.check(original)
            context = original
        except AppError as error:
            if not authorization_error(error):
                raise
            try:
                context = await self.recovery.reader(original)
            except AppError as denied:
                if not authorization_error(denied):
                    raise
                return await self._stop_input_retry(failure, generation, denied.code)
        page = await self.companion.memory.episodes_by_refs(context,
            ((failure['episode_id'], failure['episode_revision']),))
        if not page.items:
            if 'unavailable_sources' in page.coverage.missing:
                return await self._stop_input_retry(failure, generation, 'source_unavailable')
            if 'episodes_unavailable_or_revised' in page.coverage.missing:
                return await self._stop_input_retry(failure, generation, 'episode_unavailable_or_revised')
            return await self._postpone_input_retry(failure, generation)
        async with self.companion.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            try:
                await self.companion.validate_persistence(connection, context, page.items[0].source_refs)
            except RecoveryWait:
                raise
            except AppError as error:
                if not authorization_error(error):
                    raise
                return await self._stop_input_retry_on(connection, failure, generation, error.code)
            state = await (await connection.execute(
                'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE',
                (actor_id, scope_key))).fetchone()
            current = await (await connection.execute(
                "SELECT *,clock_timestamp() AS db_now FROM companion.growth_material_failures WHERE actor_id=%s AND scope_key=%s "
                "AND failure_key=%s AND state='blocked' FOR UPDATE", (actor_id, scope_key, failure_key))).fetchone()
            if not state or state['runtime_generation'] != generation or not current or current['runtime_generation'] != generation or current['observation_revision'] != expected_revision:
                return {'state': 'superseded'}
            if not current['due_at']:
                return {'state': 'superseded'}
            if current['due_at'] > current['db_now']:
                await self._schedule_input_retry_on(connection, current)
                return {'state': 'waiting'}
            released = await (await connection.execute(
                "UPDATE companion.growth_inputs SET retry_key=NULL,missing='{}',updated_at=now() "
                'WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s AND retry_key=%s '
                'AND reviewed_revision<revision RETURNING episode_id',
                (actor_id, scope_key, current['episode_id'], current['episode_revision'], failure_key))).fetchone()
            if not released:
                return {'state': 'superseded'}
            current = await (await connection.execute(
                "UPDATE companion.growth_material_failures SET state='released',due_at=NULL,"
                'observation_revision=observation_revision+1,updated_at=now() '
                'WHERE actor_id=%s AND scope_key=%s AND failure_key=%s RETURNING *',
                (actor_id, scope_key, failure_key))).fetchone()
            await self.observations.emit_on(connection, self.observations.material_event(current), generation=generation)
            next_revision = state['revision'] + 1
            await connection.execute('UPDATE companion.organize_state SET revision=%s,updated_at=now() '
                'WHERE actor_id=%s AND scope_key=%s', (next_revision, actor_id, scope_key))
            await self._enqueue(connection, actor_id, scope_key, next_revision, 'input-retry', generation=generation)
        return {'state': 'queued'}

    async def _stop_input_retry(self, failure, generation, reason):
        async with self.companion.database.transaction() as connection:
            return await self._stop_input_retry_on(connection, failure, generation, reason)

    async def _postpone_input_retry(self, failure, generation):
        async with self.companion.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            current = await (await connection.execute(
                "SELECT *,clock_timestamp() AS db_now FROM companion.growth_material_failures WHERE actor_id=%s AND scope_key=%s "
                "AND failure_key=%s AND runtime_generation=%s AND state='blocked' FOR UPDATE",
                (failure['actor_id'], failure['scope_key'], failure['failure_key'], generation))).fetchone()
            if not current or current['observation_revision'] != failure['observation_revision'] or not current['due_at']:
                return {'state': 'superseded'}
            due = current['due_at']
            if due <= current['db_now']:
                due = current['db_now'] + timedelta(minutes=5)
                current = await (await connection.execute(
                    'UPDATE companion.growth_material_failures SET due_at=%s,updated_at=now() '
                    'WHERE actor_id=%s AND scope_key=%s AND failure_key=%s AND runtime_generation=%s RETURNING *',
                    (due, current['actor_id'], current['scope_key'], current['failure_key'], generation))).fetchone()
            await self._schedule_input_retry_on(connection, current)
        return {'state': 'waiting', 'reason': 'episode_observation_incomplete', 'due_at': due}

    async def _stop_input_retry_on(self, connection, failure, generation, reason):
        await self.jobs.assert_generation_on(connection, generation)
        current = await (await connection.execute(
            "SELECT * FROM companion.growth_material_failures WHERE actor_id=%s AND scope_key=%s "
            "AND failure_key=%s AND runtime_generation=%s AND state='blocked' FOR UPDATE",
            (failure['actor_id'], failure['scope_key'], failure['failure_key'], generation))).fetchone()
        if not current or current['observation_revision'] != failure['observation_revision'] or not current['due_at']:
            return {'state': 'superseded'}
        details = {**current['details'], 'retry_stop_reason': reason}
        updated = await (await connection.execute(
            'UPDATE companion.growth_material_failures SET details=%s,due_at=NULL,'
            'observation_revision=observation_revision+1,updated_at=now() WHERE actor_id=%s '
            'AND scope_key=%s AND failure_key=%s AND runtime_generation=%s RETURNING *',
            (document(details), current['actor_id'], current['scope_key'], current['failure_key'], generation))).fetchone()
        await self.observations.emit_on(connection, self.observations.material_event(updated), generation=generation)
        return {'state': 'blocked', 'reason': reason}

    async def _unavailable(self, context, state, versions):
        """Close unavailable versions as coverage gaps, never successful reviews."""
        async with self.companion.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, state['runtime_generation'])
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

    async def _validate_material(self, connection, context, episodes, generated_refs, *, generation, additional_refs=()):
        await self.jobs.assert_generation_on(connection, generation)
        versions = tuple((item.episode_id, item.revision) for item in episodes.values())
        # Memory adds the current episode and supplement refs to this complete
        # caller set before taking any source locks or locking episode heads.
        closure = unique_refs(generated_refs, additional_refs,
                              *(item.source_refs for item in episodes.values()))
        readable = await self.companion.memory.lock_episodes(
            context, versions, connection=connection, extra_source_refs=closure)
        if set(versions) != {(item.episode_id, item.revision) for item in readable.items}:
            if "unavailable_sources" in readable.coverage.missing:
                raise AppError("source_unavailable", status=403)
            raise AppError("reflection_episode_unavailable", status=409)

    async def _record_reflection(self, connection, context, item, attempt_id):
        proposal = item.proposal
        row = await (await connection.execute(
            'INSERT INTO companion.learning_reflections(proposal_id,actor_id,principal_id,person_id,conversation_id,'
            'content,episode_ids,support_ids,source_refs,scope_ids,conditions,counterexamples,occurred_at,attempt_id) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT(proposal_id) DO NOTHING RETURNING *',
            (proposal.proposal_id, context.actor_id, context.principal_id, context.person_id,
             context.conversation_id, proposal.content, list(proposal.episode_ids), list(item.support_ids),
             document(proposal.source_refs), list(proposal.scope_ids), document(proposal.conditions),
             document(proposal.counterexamples), proposal.occurred_at, attempt_id))).fetchone()
        if not row:
            row = await (await connection.execute(
                'SELECT * FROM companion.learning_reflections WHERE proposal_id=%s AND actor_id=%s FOR UPDATE',
                (proposal.proposal_id, context.actor_id))).fetchone()
            if not row or row['content'] != proposal.content or row['source_refs'] != wire(proposal.source_refs):
                raise AppError('reflection_identity_conflict', status=409)
        return row

    async def _index_learning(self, connection, context, row, kind):
        await self.companion.memory.index_learning(context,
            rule_id=row['proposal_id'] if kind == 'reflection' else row['id'],
            revision=row['revision'], semantic_revision=row.get('semantic_revision', row['revision']),
            kind=kind, source_refs=source_refs(row['source_refs']), scope_ids=tuple(row['scope_ids']),
            search_text=json.dumps({'text': row['content'], 'conditions': row['conditions'],
                                    'counterexamples': row['counterexamples']}, ensure_ascii=False, default=str),
            connection=connection)

    async def learning_records(self, context, refs, *, purpose='history'):
        """Resolve typed Memory references through the owning ledger."""
        await self.companion.identity.check(context)
        result = []
        for ref in refs:
            kind, identifier = ref.get('kind'), ref.get('id')
            if kind in {'style', 'interest', 'habit'}:
                rows = [row for row in await self.habits.candidates(context, include_inactive=True)
                        if row['id'] == identifier]
                if not rows:
                    continue
                row = rows[0]
                if purpose != 'history':
                    resolved = await self.resolve_behaviors(context,
                        ({'rule_id': identifier, 'semantic_revision': row['semantic_revision']},), purpose=purpose)
                    if not resolved[0]['eligible']:
                        continue
                result.append({'kind': row['kind'], 'id': row['id'], 'revision': row['revision'],
                    'semantic_revision': row['semantic_revision'], 'state': row['state'], 'reason': row['reason'],
                    'content': row['content'], 'conditions': row['conditions'], 'counterexamples': row['counterexamples'],
                    'source_refs': row['source_refs'], 'scope_ids': row['scope_ids']})
            elif kind == 'reflection':
                async with self.companion.database.transaction(read_only=True) as connection:
                    row = await (await connection.execute(
                        'SELECT * FROM companion.learning_reflections WHERE proposal_id=%s AND actor_id=%s '
                        'AND scope_ids<@%s::text[] AND (person_id IS NULL OR person_id=%s) '
                        'AND (conversation_id IS NULL OR conversation_id=%s)',
                        (identifier, context.actor_id, list(context.scope_ids), context.person_id,
                         context.conversation_id))).fetchone()
                if not row or row['state'] != 'active':
                    continue
                allowed = await self.companion.identity.check_many(context, (source_refs(row['source_refs']),))
                if allowed[0]:
                    result.append({'kind': 'reflection', 'id': identifier, 'revision': row['revision'],
                        'semantic_revision': row['revision'], 'requested_revision': ref.get('revision'),
                        'compatible': ref.get('revision') in (None, row['revision']),
                        'state': row['state'], 'content': row['content'], 'conditions': row['conditions'],
                        'counterexamples': row['counterexamples'], 'episode_ids': row['episode_ids'],
                        'support_ids': row['support_ids'], 'attempt_id': row['attempt_id'],
                        'source_refs': row['source_refs'], 'scope_ids': row['scope_ids'],
                        'occurred_at': row['occurred_at'], 'recorded_at': row['created_at']})
        return tuple(result)

    async def input_learning_status(self, context, *, inputs):
        return await self.input_status.read(context, inputs=inputs)

    async def reflection_page(self, context, *, cursor=None, limit=40, time_mode='current',
                              valid_at=None, known_at=None):
        await self.companion.identity.check(context)
        if time_mode not in {'current', 'valid_at', 'known_at'}:
            raise AppError('invalid_memory_time_mode')
        valid_at, known_at = timestamp(valid_at, optional=True), timestamp(known_at, optional=True)
        if time_mode == 'valid_at' and valid_at is None:
            raise AppError('memory_valid_at_required')
        if time_mode == 'known_at' and known_at is None:
            raise AppError('memory_known_at_required')
        limit = min(100, max(1, int(limit)))
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.learning_reflections WHERE actor_id=%s AND state='active' "
                "AND scope_ids<@%s::text[] AND (%s OR person_id IS NULL OR person_id=%s) "
                "AND (%s OR conversation_id IS NULL OR conversation_id=%s) "
                "AND (%s::timestamptz IS NULL OR occurred_at<=%s) AND (%s::timestamptz IS NULL OR created_at<=%s) "
                "AND (%s::text IS NULL OR proposal_id>%s) ORDER BY proposal_id LIMIT %s",
                (context.actor_id, list(context.scope_ids), management_scope(context), context.person_id,
                 management_scope(context), context.conversation_id,
                 valid_at, valid_at, known_at, known_at, cursor, cursor, limit+1))).fetchall()
        visible = rows[:limit]
        allowed = await self.companion.identity.check_many(context,
            tuple(source_refs(row['source_refs']) for row in visible))
        items = tuple({'proposal_id': row['proposal_id'], 'revision': row['revision'], 'actor_id': row['actor_id'],
            'person_id': row['person_id'], 'conversation_id': row['conversation_id'], 'episode_ids': row['episode_ids'],
            'support_ids': row['support_ids'], 'content': row['content'], 'kind': 'reflection',
            'occurred_at': row['occurred_at'], 'recorded_at': row['created_at'], 'source_refs': row['source_refs'],
            'scope_ids': row['scope_ids'], 'conditions': row['conditions'], 'counterexamples': row['counterexamples'],
            'interpretation': True, 'state': row['state']} for row, valid in zip(visible, allowed, strict=True) if valid)
        missing = [] if all(allowed) else ['unavailable_sources']
        if time_mode == 'valid_at' and items:
            missing.append('reflection_valid_time_unknown')
        return Page(items, visible[-1]['proposal_id'] if len(rows)>limit else None,
                    Coverage(len(rows)<=limit and not missing, missing=tuple(missing)))

    async def recover(self):
        await self.retries.recover()
        async with self.companion.database.transaction(read_only=True) as connection:
            generation = await self.jobs.current_generation_on(connection)
            failures = await (await connection.execute(
                "SELECT actor_id,scope_key,failure_key,observation_revision FROM companion.growth_material_failures "
                "WHERE state='blocked' AND due_at IS NOT NULL AND runtime_generation=%s",
                (generation,))).fetchall()
        for failure in failures:
            async with self.companion.database.transaction() as connection:
                await self.jobs.assert_generation_on(connection, generation)
                current = await (await connection.execute(
                    "SELECT * FROM companion.growth_material_failures WHERE actor_id=%s AND scope_key=%s "
                    "AND failure_key=%s AND runtime_generation=%s AND state='blocked' FOR UPDATE",
                    (failure['actor_id'], failure['scope_key'], failure['failure_key'], generation))).fetchone()
                if current and current['observation_revision'] == failure['observation_revision'] and current['due_at']:
                    await self._schedule_input_retry_on(connection, current)
        if self.observations.statistics is not None:
            async with self.companion.database.transaction() as connection:
                generation = await self.jobs.current_generation_on(connection)
                await self.observations.schedule_on(connection, generation=generation)
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT actor_id,scope_key,revision FROM companion.organize_state s WHERE runtime_generation=%s AND (last_error IS NULL OR last_error<>ALL(%s)) AND ("
                "active_batch IS NOT NULL OR ((last_error IS NULL OR last_error<>ALL(%s)) AND "
                "EXISTS(SELECT 1 FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key "
                "AND reviewed_revision<i.revision AND retry_key IS NULL)))",
                (generation, sorted({AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED}), sorted(REVIEW_ERRORS)))).fetchall()
        for row in rows:
            async with self.companion.database.transaction() as connection:
                await self.jobs.assert_generation_on(connection, generation)
                current = await (await connection.execute(
                    'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE',
                    (row['actor_id'], row['scope_key']))).fetchone()
                if not current or current['runtime_generation'] != generation or current['revision'] != row['revision']:
                    continue
                blocked = current['blocked_material'] or {}
                due = timestamp(blocked['retry_at']) if blocked.get('recovery') == 'dependency_wait' else None
                await self._enqueue(connection, row['actor_id'], row['scope_key'], row['revision'], 'recover',
                                    generation=generation, scheduled_at=due)

    async def reconcile_due(self, *, limit=32):
        from .rbi_growth_maintenance import reconcile_due
        return await reconcile_due(self, limit=limit)

    async def _schedule_input_retry_on(self, connection, failure):
        generation = failure['runtime_generation']
        await self.jobs.assert_generation_on(connection, generation)
        identity = f"{generation}:{failure['actor_id']}:{failure['scope_key']}:{failure['failure_key']}:{failure['observation_revision']}"
        await self.jobs.enqueue(connection, JobSpec('companion.growth.input_retry',
            f"growth-input-retry:{identity}:{uuid4()}",
            {'actor_id': failure['actor_id'], 'scope_key': failure['scope_key'],
             'failure_key': failure['failure_key'], 'expected_revision': failure['observation_revision'],
             'generation': generation}, scheduled_at=failure['due_at'],
            subject_id=failure['actor_id'], data_generation=generation),
            queueing_lock='growth-input:'+identity)

    async def _rejected(self, actor_id, scope_key, material_key, code, *, result=None):
        async with self.companion.database.transaction(read_only=True) as connection:
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if current and current["active_batch"] and current["active_batch"]["material_key"] == material_key:
            return await self.retries.defer(current, current["active_batch"], code, result)

    async def _enqueue(self, connection, actor_id, scope_key, revision, reason, *, generation, delay=0, scheduled_at=None):
        await self.jobs.assert_generation_on(connection, generation)
        state = await (await connection.execute(
            'SELECT runtime_generation FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s',
            (actor_id, scope_key))).fetchone()
        if not state or state['runtime_generation'] != generation:
            raise AppError('growth_generation_required')
        if scheduled_at is None:
            scheduled_at = await database_now_on(connection) + timedelta(seconds=delay)
        await self.jobs.enqueue(connection, JobSpec("companion.organize",
            f"organize:{generation}:{actor_id}:{scope_key}:{revision}:{reason}:{datetime.now(UTC).isoformat()}",
            {"actor_id": actor_id, "scope_key": scope_key, "expected_revision": revision, 'generation':generation},
            scheduled_at=scheduled_at, subject_id=actor_id,
            data_generation=generation),
            queueing_lock=f"growth-organize:{actor_id}:{scope_key}:{revision}")

    async def _finish(self, connection, current, batch, changed):
        await self.jobs.assert_generation_on(connection, current['runtime_generation'])
        await self.retries.complete_on(connection, current, batch, history_only=batch.get("historical_only", False))
        for identifier, version in batch.get("inputs", ()):
            coverage = batch.get("material_coverage", {}).get(identifier, {})
            await connection.execute(
                "UPDATE companion.growth_inputs SET reviewed_revision=%s,state='reviewed',missing=%s,retry_key=NULL,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s",
                (version, list(coverage.get("missing", ())), current["actor_id"], current["scope_key"], identifier, version))
        remaining = await (await connection.execute(
            "SELECT EXISTS(SELECT 1 FROM companion.growth_inputs WHERE actor_id=%s AND scope_key=%s AND reviewed_revision<revision) AS pending",
            (current["actor_id"], current["scope_key"]))).fetchone()
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
            "AND reviewed_revision<revision AND retry_key IS NULL) AS pending",
            (current["actor_id"], current["scope_key"]))).fetchone()
        if ready["pending"]:
            await self._enqueue(connection, current["actor_id"], current["scope_key"], next_revision, "continuation", generation=current['runtime_generation'])

    async def view(self, context, *, cursor=None, limit=40):
        await self.companion.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.style_rules WHERE actor_id=%s AND (%s OR person_id IS NULL OR person_id=%s) AND scope_ids<@%s::text[] "
                "AND (%s OR conversation_id IS NULL OR conversation_id=%s) AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, management_scope(context), context.person_id, list(context.scope_ids),
                 management_scope(context), context.conversation_id, cursor, cursor, limit+1))).fetchall()
        permitted = await self.companion.identity.check_many(context, tuple(source_refs(row["source_refs"]) for row in rows[:limit]))
        items = [{**row, "occurred_at": row["created_at"], "evidence": row["source_refs"], "can_modify": bool(context.permissions & {"*","companion.manage"})} for row, valid in zip(rows[:limit], permitted, strict=True) if valid]
        reflections = await self.reflection_page(context, limit=40)
        notes = [{'id': row['proposal_id'], 'revision': row['revision'], 'kind': 'reflection',
                  'title': row['content'][:60], 'content': row['content'], 'state': row['state'],
                  'occurred_at': row['occurred_at'], 'evidence': row['source_refs'],
                  'source_refs': row['source_refs'], 'episode_ids': row['episode_ids'],
                  'conditions': row['conditions'], 'counterexamples': row['counterexamples'], 'can_modify': False}
                 for row in reflections.items]
        async with self.companion.database.transaction(read_only=True) as connection:
            review=await (await connection.execute(
                "SELECT revision,last_error,last_reviewed_at,blocked_material,active_batch->>'material_key' AS material_key,"
                "active_batch-'context' AS batch,coalesce(active_batch->'context',context) AS recovery_context,"
                "cursor,"
                "(SELECT count(*) FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND reviewed_revision<i.revision) AS pending_inputs,"
                "(SELECT count(*) FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='unavailable') AS unavailable_inputs,"
                "(SELECT count(*) FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='reviewed' AND cardinality(i.missing)>0) AS partial_inputs,"
                "(SELECT count(*) FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.skip_reason='low_value_rest') AS low_value_skipped_inputs,"
                "ARRAY(SELECT DISTINCT gap.reason FROM companion.growth_inputs i CROSS JOIN LATERAL unnest(i.missing) gap(reason) "
                "WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='reviewed' ORDER BY gap.reason) AS material_missing,"
                "ARRAY(SELECT DISTINCT gap.reason FROM companion.growth_inputs i CROSS JOIN LATERAL unnest(i.missing) gap(reason) "
                "WHERE i.actor_id=s.actor_id AND i.scope_key=s.scope_key AND i.state='unavailable' ORDER BY gap.reason) AS input_missing,"
                "(CASE WHEN active_batch IS NOT NULL THEN 1 ELSE 0 END + "
                "(SELECT count(*) FROM companion.growth_retry_batches r WHERE r.actor_id=s.actor_id AND r.scope_key=s.scope_key "
                "AND r.waiting_on_original AND r.state IN ('waiting','blocked'))) AS pending_original_requests "
                "FROM companion.organize_state s WHERE actor_id=%s AND scope_key=%s",
                (context.actor_id,signature(sorted(context.scope_ids))))).fetchone()
            retries = await (await connection.execute(
                "SELECT retry_key,batch->'context' AS context,batch->'output_repair_count' AS output_repair_count,"
                "batch->'rbi_review' AS recovery_review,"
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
            input_failures = await (await connection.execute(
                "SELECT failure_key,episode_id,episode_revision,error_code,details,context FROM companion.growth_material_failures "
                "WHERE actor_id=%s AND scope_key=%s AND state='blocked' ORDER BY created_at LIMIT 20",
                (context.actor_id, signature(sorted(context.scope_ids))))).fetchall()
            unresolved = await (await connection.execute(
                "SELECT * FROM companion.habit_unresolved WHERE actor_id=%s AND state='pending' "
                "AND scope_ids<@%s::text[] AND (%s OR person_id IS NULL OR person_id=%s) "
                "AND (%s OR conversation_id IS NULL OR conversation_id=%s) ORDER BY created_at DESC LIMIT 40",
                (context.actor_id, list(context.scope_ids), management_scope(context), context.person_id,
                 management_scope(context), context.conversation_id))).fetchall()
        unresolved_allowed = await self.companion.identity.check_many(context,
            tuple(source_refs(row['source_refs']) for row in unresolved))
        if review:
            review["retry_summary"] = retry_summary
            review['input_failures'] = [{**{k: v for k, v in failure.items() if k != 'context'},
                'can_request_review': self.recovery.same_scene(context, context_from_record(failure['context']))
                and manual_retry_allowed(failure['error_code'])} for failure in input_failures]
            review["retry_batches"] = [{**{key: value for key, value in retry.items() if key != "context"},
                "attempt_id": attempt_for(retry["current_material_key"]),
                "can_request_review": retry["state"] == "blocked" and manual_retry_allowed(retry["last_error"])
                    and (retry.get('recovery_review') or {}).get('state') != 'requested'
                    and review_available({"output_repair_count": retry.get("output_repair_count") or 0}, retry["last_error"])
                    and self.recovery.same_scene(context, context_from_record(retry["context"]))}
                for retry in retries]
            original = context_from_record(review.pop("recovery_context"))
            same_scene = self.recovery.same_scene(context, original)
            review = {**review, "attempt_id": attempt_for(review["material_key"]) if review["material_key"] else None,
                "can_request_review": review["last_error"] in REVIEW_ERRORS,
                "recovery": "observe_original_attempt" if review["material_key"] and review["last_error"] not in REVIEW_ERRORS else None}
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
                    if review["material_key"] else "在原权限范围重新授权后，继续处理原材料。")
        return {"can_review":bool(context.permissions & {"*","companion.manage"}),"review":review,"items": items, "next_cursor": rows[limit-1]["id"] if len(rows)>limit else None,
                "reflections": notes, "missing": [] if all(permitted) else ["source_unavailable"],
                "unresolved": [row for row, allowed in zip(unresolved, unresolved_allowed, strict=True) if allowed],
                "evidence": {"evidence": items,
                    "counterexamples": [value for item in items for value in item['counterexamples']],
                    "source_refs": [ref for item in items for ref in item['source_refs']],
                    "coverage": Coverage(not (len(rows) > limit), missing=())}}

    async def material(self, context, *, purpose='conversation', query=None, budget=None):
        rules = await self.habits.material(context, purpose=purpose, query=query, budget=budget)
        examples = await self.examples.material(context, purpose=purpose, query=query, budget=budget)
        return rules + examples

    async def record_example_usage(self, context, example_ids, *, usage_id, outcome, turn_id=None, expression_id=None):
        return await self.examples.usage(context, example_ids, usage_id=usage_id, outcome=outcome,
                                         turn_id=turn_id, expression_id=expression_id)

    async def resolve_behaviors(self, context, refs, *, purpose):
        return await self.habits.resolve(context, refs, purpose=purpose)

    async def set_state(self, context, body, state):
        return await self.habits.command(context, body, state=state)

    async def revise(self, context, body):
        return await self.habits.command(context, body)
