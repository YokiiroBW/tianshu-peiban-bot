"""Durable yielding/retry of pure reflection computations on the Growth lane."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import aiohttp

from tianshu.conversation.codec import context_from_record, document, signature
from tianshu.conversation.generation import attempt_for
from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
from tianshu.models.types import ModelReceipt
from tianshu.runtime.database import classify_database_failure
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
from tianshu.runtime.serialization import wire
from tianshu.types import RequestBudget
from .growth_recovery import authorization_error, sequence
from .growth_policy import (OWNER, bind_receipt, budget_on as recovery_budget_on,
                            fail as fail_recovery, field, lock_receipt_on, note_receipt_on,
                            succeed as succeed_recovery)
from .growth_output_repair import (admitted_review_matches, correction_available, corrected_request,
                                   output_diagnostic, resumable_cancel, review_available)
from .reflection_output import (CORRECTABLE_ERRORS, IDENTITY_PROTOCOL, OUTPUT_ERRORS, OUTPUT_PROTOCOL, REVIEW_ERRORS,
                                frozen_episodes, frozen_material, prepare_reflections, validate_batch_request)


def error_code(error):
    return error.code if isinstance(error, AppError) else type(error).__name__


def later_cursor(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return left if sequence(left) >= sequence(right) else right


def material_time(episodes):
    return max(episode.occurred_at for episode in episodes.values())


def history_entry(batch, code, result=None):
    return {"material_key": batch["material_key"], "attempt_id": attempt_for(batch["material_key"]),
        "attempt_number": batch.get("attempt_number", 1),
        "status": result.receipt.status if result else "unobserved", "error_code": code,
        "observed_at": datetime.now(UTC).isoformat()}


def with_history(history, entry):
    if history and all(history[-1].get(key) == entry.get(key) for key in ("material_key", "status", "error_code")):
        return history
    return [*history, entry]


def manual_retry_allowed(code):
    return bool(code) and not any(marker in code for marker in (
        "source", "cancel", "withdraw",
        "retract", "business_deadline", "episode_revision", "episode_unavailable", "identity_conflict", "budget_exhausted"))


def finished_unknown(receipt, attempt_id, generation):
    return (isinstance(receipt, ModelReceipt) and receipt.attempt_id == attempt_id
            and receipt.data_generation == generation and receipt.status == 'unknown'
            and isinstance(receipt.finished_at, datetime) and receipt.finished_at.tzinfo is not None
            and receipt.cancellation_reason != 'explicit_cancel')


class GrowthRetries:
    def __init__(self, growth):
        self.growth = growth
        self.database = growth.companion.database

    async def current_status(self, attempt_id):
        # A missing or unavailable control-plane response is never permission
        # to repeat an attempt. Do not make this HTTP read in a transaction.
        try:
            receipt = await self.growth.models.status(attempt_id)
        except RecoveryWait:
            raise
        except Exception:
            return None
        return receipt if isinstance(receipt, ModelReceipt) and receipt.attempt_id == attempt_id else None

    async def propagate_unconfirmed_wait(self, wait, attempt_id):
        # Status is a read of the original ID, never a fresh submission. Even a
        # receipt cannot establish that the RPC transaction rolled back.
        try:
            await self.current_status(attempt_id)
        except Exception:
            pass
        raise wait

    async def observe_control_owner(self, actor_id, scope_key, generation):
        # A read only; in particular this does not interpret an active pointer
        # as proof that a transaction with unknown commit outcome rolled back.
        async with self.database.transaction(read_only=True) as connection:
            await self.growth.jobs.assert_generation_on(connection, generation)
            return await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s",
                (actor_id, scope_key))).fetchone()

    async def isolate_control_wait(self, actor_id, scope_key, material_key, generation, wait):
        current = await self.observe_control_owner(actor_id, scope_key, generation)
        # None means no local transaction unwound; True means its rollback was
        # confirmed. False is an unknown local outcome, even if the pointer is
        # still visible, and cannot authorize releasing the partition.
        if getattr(wait, 'transaction_rollback_confirmed', None) is False:
            return None
        batch = current['active_batch'] if current and current['runtime_generation'] == generation else None
        if not batch or batch.get('material_key') != material_key:
            return None
        original = context_from_record(batch['context'])
        request = await self.growth.generations.request(attempt_for(material_key), original)
        if not request or request.intent_id != material_key or request.data_generation != generation:
            return None
        observation = {'reason': wait.reason, 'dependency': wait.dependency,
            'retry_at': wait.retry_at.isoformat(),
            'remote_rollback_confirmed': getattr(wait, 'remote_rollback_confirmed', None),
            'transaction_rollback_confirmed': getattr(wait, 'transaction_rollback_confirmed', None)}
        return await self.defer(current, batch, 'growth_model_control_unconfirmed',
                                control_observation=observation)

    async def wait_dependency(self, row, wait):
        if not recovery_wait_retryable(wait):
            await self.propagate_unconfirmed_wait(wait, attempt_for(row['current_material_key']))
        if wait.reason == 'recovery_claim_lost':
            raise wait
        return await self.postpone(row, code=wait.reason, waiting_on_original=row['waiting_on_original'],
                                   retry_at=wait.retry_at)

    @staticmethod
    def invalid_material_error(error):
        return authorization_error(error) or (
            isinstance(error, AppError) and error.code in {
                'reflection_material_contract_invalid', 'reflection_episode_unavailable',
                'reflection_episode_revision_conflict', 'source_unavailable',
            })

    async def receipt_sources_on(self, connection, batch, request, generation):
        """Lock current Identity/Sources/Memory before any Growth or Runtime locks."""
        if (request is None or request.attempt_id != attempt_for(batch['material_key'])
                or request.intent_id != batch['material_key'] or request.data_generation != generation):
            return 'reflection_material_contract_invalid'
        try:
            original = context_from_record(batch['context'])
            if (self.growth.generations.context_key(original) != self.growth.generations.context_key(request.context)
                    or original.permissions != request.context.permissions
                    or original.origin_kind != request.context.origin_kind
                    or original.conversation_kind != request.context.conversation_kind
                    or original.delivery_allowed != request.context.delivery_allowed):
                return 'reflection_material_contract_invalid'
            current = await self.growth.recovery.reader(original, connection=connection)
            episodes = frozen_episodes(request)
            validate_batch_request(batch, request, episodes)
            await self.growth._validate_material(connection, current, episodes, request.source_refs,
                                                 generation=generation)
        except RecoveryWait:
            raise
        except AppError as error:
            if not self.invalid_material_error(error):
                raise
            return error.code
        return None

    async def receipt_inputs_on(self, connection, actor_id, scope_key, batch, expected_retry_key):
        for identifier, revision in sorted(batch['inputs']):
            row = await (await connection.execute(
                'SELECT revision,reviewed_revision,retry_key FROM companion.growth_inputs '
                'WHERE actor_id=%s AND scope_key=%s AND episode_id=%s FOR UPDATE',
                (actor_id, scope_key, identifier))).fetchone()
            if (not row or row['revision'] != revision or row['reviewed_revision'] >= revision
                    or row['retry_key'] != expected_retry_key):
                return 'reflection_episode_revision_conflict'
        return None

    async def note_current_receipt_on(self, connection, batch, attempt_id, receipt):
        # A late version fence must not roll back the authoritative old outcome.
        try:
            async with connection.transaction():
                return await note_receipt_on(self.growth.recovery_policy, connection, batch, attempt_id, receipt), None
        except RecoveryWait:
            raise
        except AppError as error:
            if error.code != 'recovery_version_changed':
                raise
            return None, error.code

    @staticmethod
    def bind_archivable_receipt(batch, attempt_id, receipt, generation):
        # With a withdrawn/erased frozen request only the authenticated original
        # Models identity is archived. It cannot authorize a connection credit.
        if (not isinstance(receipt, ModelReceipt) or receipt.attempt_id != attempt_id
                or receipt.intent_id != batch['material_key'] or receipt.data_generation != generation):
            raise AppError('growth_recovery_receipt_mismatch', status=409)

    async def settle_control_receipt(self, row, request, result, generation):
        # The remote wait was not a physical outcome. Only the original Models
        # receipt can settle that attempt, and it never permits a successor.
        receipt = getattr(result, 'receipt', None)
        if (not isinstance(receipt, ModelReceipt) or receipt.attempt_id != request.attempt_id
                or receipt.intent_id != request.intent_id or receipt.data_generation != generation
                or receipt.status not in {'succeeded', 'failed', 'cancelled'}):
            return None
        bind_receipt(request, receipt)
        async with self.database.transaction() as connection:
            await self.growth.jobs.assert_generation_on(connection, generation)
            material_error = None
            if receipt.status != 'succeeded' and receipt.cancellation_reason != 'explicit_cancel':
                material_error = await self.receipt_sources_on(connection, row['batch'], request, generation)
            current = await (await connection.execute(
                "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s "
                "AND retry_key=%s FOR UPDATE",
                (row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
            if (not current or current['state'] != 'waiting' or not current['waiting_on_original']
                    or current['revision'] != row['revision']
                    or current['current_material_key'] != request.intent_id
                    or not current['batch'].get('control_observation')):
                return {'state': 'changed_since_original_result_read'}
            if receipt.status != 'succeeded' and receipt.cancellation_reason != 'explicit_cancel' and not material_error:
                material_error = await self.receipt_inputs_on(
                    connection, row['actor_id'], row['scope_key'], current['batch'], row['retry_key'])
            attempt = await (await connection.execute(
                "SELECT status,lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s",
                (request.attempt_id,))).fetchone()
            if not attempt or attempt['lineage_id'] != current['batch'].get('lineage_id'):
                return None
            status = attempt['status']
            success = receipt.status == 'succeeded'
            if (success and status not in {'reserved', 'unknown', 'succeeded'}
                    or not success and status not in {'reserved', 'unknown', 'failed'}):
                return None
            code = ('explicit_cancel' if receipt.cancellation_reason == 'explicit_cancel'
                    else receipt.error_code or 'reflection_generation_' + receipt.status)
            batch = current['batch']
            if not success:
                if (not material_error and receipt.cancellation_reason != 'explicit_cancel'
                        and (receipt.status == 'failed' or resumable_cancel(receipt))):
                    await lock_receipt_on(connection, receipt, generation)
                else:
                    await lock_recovery_facts_on(connection, request.attempt_id)
            if status == 'unknown':
                from tianshu.runtime.recovery_policy import classify_failure
                await self.growth.recovery_policy.resolve_unknown_on(connection, request.attempt_id,
                    observation_ref='models:'+request.attempt_id, state='succeeded' if success else 'failed',
                    code=None if success else code, category=None if success else classify_failure(code),
                    probe_token=batch.get('probe_token'))
                if success:
                    batch = {**batch, 'recovery_succeeded_attempt_id': request.attempt_id}
            elif success and status == 'succeeded':
                batch = {**batch, 'recovery_succeeded_attempt_id': request.attempt_id}
            elif not success and status == 'reserved':
                await fail_recovery(self.growth.recovery_policy, connection, batch, request.attempt_id, code)
            if not success and receipt.cancellation_reason != 'explicit_cancel' and not material_error:
                _budget, material_error = await self.note_current_receipt_on(
                    connection, batch, request.attempt_id, receipt)
            if success:
                updated = await (await connection.execute(
                    "UPDATE companion.growth_retry_batches SET batch=%s,waiting_on_original=false,"
                    "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
                    (document(batch), row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
            else:
                updated = await (await connection.execute(
                    "UPDATE companion.growth_retry_batches SET state='blocked',due_at=NULL,last_error=%s,"
                    "waiting_on_original=false,history=%s,revision=revision+1,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
                    (material_error or code, document(with_history(current['history'], history_entry(batch, code, result))),
                     row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
            await self.growth.observations.emit_on(connection, self.growth.observations.review_event(updated),
                                                    generation=generation)
            return updated if success else {'state': 'retry_blocked', 'error_code': material_error or code}

    async def settle_superseded(self, state, batch, attempt_id, generation):
        """A late original receipt can settle its attempt, never its successor."""
        receipt = await self.current_status(attempt_id)
        if (receipt is None or receipt.data_generation != generation
                or not isinstance(receipt.finished_at, datetime) or receipt.finished_at.tzinfo is None
                or receipt.status not in {'succeeded', 'failed', 'cancelled'}):
            return
        from tianshu.runtime.recovery_policy import classify_failure
        frozen, material_error = None, None
        if receipt.status in {'failed', 'cancelled'} and receipt.cancellation_reason != 'explicit_cancel':
            original = context_from_record(batch['context'])
            try:
                frozen = await self.growth.generations.request(attempt_id, original)
            except RecoveryWait:
                raise
            except AppError as error:
                if not self.invalid_material_error(error):
                    raise
                material_error = error.code
            if frozen is None:
                material_error = material_error or 'reflection_material_contract_invalid'
                self.bind_archivable_receipt(batch, attempt_id, receipt, generation)
            else:
                bind_receipt(frozen, receipt)
        observed_state = 'succeeded' if receipt.status == 'succeeded' else 'failed'
        code = ('cancelled' if receipt.cancellation_reason == 'explicit_cancel'
                else receipt.error_code or 'reflection_generation_' + receipt.status)
        try:
            async with self.database.transaction() as connection:
                await self.growth.jobs.assert_generation_on(connection, generation)
                if observed_state == 'failed' and receipt.cancellation_reason != 'explicit_cancel' and not material_error:
                    material_error = await self.receipt_sources_on(connection, batch, frozen, generation)
                retry = await (await connection.execute(
                    'SELECT current_material_key,batch FROM companion.growth_retry_batches '
                    'WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR SHARE',
                    (state['actor_id'], state['scope_key'], batch.get('retry_key', batch['material_key'])))).fetchone()
                # Yielding an unknown is not itself supersession: the original
                # may still be selected for normal recovery after other work.
                if (not retry or retry['current_material_key'] == batch['material_key']
                        or retry['batch'].get('lineage_id') != batch.get('lineage_id')):
                    return
                if observed_state == 'failed' and receipt.cancellation_reason != 'explicit_cancel' and not material_error:
                    material_error = await self.receipt_inputs_on(connection, state['actor_id'], state['scope_key'],
                        batch, batch.get('retry_key', batch['material_key']))
                if observed_state == 'failed':
                    if (not material_error and receipt.cancellation_reason != 'explicit_cancel'
                            and (receipt.status == 'failed' or resumable_cancel(receipt))):
                        await lock_receipt_on(connection, receipt, generation)
                    else:
                        await lock_recovery_facts_on(connection, attempt_id)
                await self.growth.recovery_policy.resolve_unknown_on(connection, attempt_id,
                    observation_ref='models:'+attempt_id, state=observed_state,
                    code=None if observed_state == 'succeeded' else code,
                    category=None if observed_state == 'succeeded' else classify_failure(code))
                if (observed_state == 'failed' and receipt.cancellation_reason != 'explicit_cancel'
                        and not material_error and retry['batch'].get('recovery_version') == batch.get('recovery_version')):
                    await self.note_current_receipt_on(connection, batch, attempt_id, receipt)
        except RecoveryWait:
            raise
        except AppError as error:
            # Another observer may already have settled this exact attempt.
            if error.code != 'recovery_attempt_not_unknown':
                raise

    async def schedule_on(self, connection, row):
        generation = row['batch']['runtime_generation']
        await self.growth.jobs.assert_generation_on(connection, generation)
        await self.growth.jobs.enqueue(connection, JobSpec("companion.growth.retry",
            f"growth-retry:{generation}:{row['actor_id']}:{row['scope_key']}:{row['retry_key']}:{row['revision']}:{datetime.now(UTC).isoformat()}",
            {"actor_id": row["actor_id"], "scope_key": row["scope_key"], "retry_key": row["retry_key"], 'generation':generation},
            scheduled_at=row["due_at"] or datetime.now(UTC), subject_id=row["actor_id"], data_generation=generation),
            queueing_lock=f"growth-retry:{generation}:{row['actor_id']}:{row['scope_key']}:{row['retry_key']}")

    async def select_route(self, row, request, generation):
        candidate = replace(request, route_id=None, route_revision=None)
        profile = await self.growth.models.generation_profile(candidate)
        if not profile.get('quota_group') or not profile.get('route_id') or not profile.get('route_revision'):
            raise AppError('growth_model_quota_unavailable', status=503)
        dependency = 'models:quota:'+profile['quota_group']
        candidate = replace(candidate, route_id=profile['route_id'], route_revision=profile['route_revision'])
        if dependency == row['batch']['recovery_dependency'] and profile['route_id'] == row['batch']['recovery_route']:
            return row, candidate
        async with self.database.transaction() as connection:
            await self.growth.jobs.assert_generation_on(connection, generation)
            current = await (await connection.execute(
                "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
                (row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
            if not current or current['revision'] != row['revision'] or current['state'] != 'waiting':
                raise AppError('generation_no_longer_current', status=409)
            batch = current['batch']
            if dependency != batch['recovery_dependency']:
                await self.growth.recovery_policy.update_dependency_on(connection, batch['lineage_id'], owner=OWNER,
                    expected_version=batch['recovery_version'], expected_dependency=batch['recovery_dependency'],
                    new_dependency=dependency)
            batch = {**batch, 'recovery_dependency': dependency, 'recovery_route': profile['route_id']}
            current = await (await connection.execute(
                'UPDATE companion.growth_retry_batches SET batch=%s,revision=revision+1,updated_at=now() '
                'WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *',
                (document(batch), row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
            await self.growth.observations.emit_on(connection, self.growth.observations.review_event(current), generation=generation)
        return current, candidate

    async def defer(self, state, batch, code, result=None, *, diagnostic=None,
                    control_observation=None, frozen_request=None):
        outcome = await self._defer(state, batch, code, result, diagnostic=diagnostic,
                                    control_observation=control_observation, frozen_request=frozen_request)
        if (outcome.get('state') == 'changed_since_original_result_read' and result is not None
                and result.receipt.status in {'succeeded', 'failed', 'cancelled'}):
            # The owner can move while its Models result is in flight. Archive
            # a definitive old receipt after releasing this transaction's locks.
            await self.settle_superseded(state, batch, attempt_for(batch['material_key']),
                                         batch['runtime_generation'])
        return outcome

    async def _defer(self, state, batch, code, result=None, *, diagnostic=None,
                     control_observation=None, frozen_request=None):
        """One CAS moves this owner to the tail; later input never joins its batch."""
        key = batch.get("retry_key", batch["material_key"])
        number = batch.get("attempt_number", 1)
        attempt_id = attempt_for(batch['material_key'])
        generation = batch['runtime_generation']
        explicit_cancel = bool(result and getattr(result.receipt, "cancellation_reason", None) == "explicit_cancel")
        if explicit_cancel:
            code = "explicit_cancel"
        correcting = bool(result and result.receipt.status == "succeeded" and diagnostic is not None
                          and correction_available(batch, code))
        uncertain = (result is None or result.receipt.status == 'unknown'
                     or bool(batch.get('control_observation') and result.receipt.status in {'queued', 'running'}))
        if control_observation is None and uncertain and not explicit_cancel:
            control_observation = batch.get('control_observation')
        observing_control = control_observation is not None
        retryable = correcting or transient_model_failure(code, cancelled=explicit_cancel)
        if result is not None and result.receipt.status == 'cancelled' and not resumable_cancel(result.receipt):
            retryable = False
        if batch.get('control_observation') and not observing_control:
            retryable = False
        delay = 30 if observing_control else 0 if correcting else retry_delay_seconds(number)
        retryable = retryable or observing_control
        due = datetime.now(UTC) + timedelta(seconds=delay) if retryable else None
        status = (await self.current_status(attempt_id)
                  if uncertain and retryable and not observing_control else None)

        # Fetch the immutable request outside the write transaction. If it was
        # withdrawn, the original Models outcome is still archived by ID, but
        # it cannot be used to credit or schedule another computation.
        material_error = None
        needs_material = not observing_control and not explicit_cancel
        if frozen_request is None and (needs_material or result is not None or
                                       status and status.cancellation_reason == 'explicit_cancel'):
            original = context_from_record(batch['context'])
            try:
                frozen_request = await self.growth.generations.request(attempt_id, original)
            except RecoveryWait:
                raise
            except AppError as error:
                if not self.invalid_material_error(error):
                    raise
                material_error = error.code
            if frozen_request is None:
                material_error = material_error or 'reflection_material_contract_invalid'
        if result is not None and (result.receipt.status in {'failed', 'cancelled', 'unknown'} or explicit_cancel):
            self.bind_archivable_receipt(batch, attempt_id, result.receipt, generation)
            if frozen_request is not None:
                bind_receipt(frozen_request, result.receipt)
        if status and status.cancellation_reason == 'explicit_cancel':
            try:
                if frozen_request is None:
                    self.bind_archivable_receipt(batch, attempt_id, status, generation)
                else:
                    bind_receipt(frozen_request, status)
            except AppError as error:
                if error.code != 'growth_recovery_receipt_mismatch':
                    raise
                status = None
            else:
                explicit_cancel = True
                code, retryable, due = 'explicit_cancel', False, None
        elif status and finished_unknown(status, attempt_id, generation) and frozen_request is not None:
            try:
                bind_receipt(frozen_request, status)
            except AppError as error:
                if error.code != 'growth_recovery_receipt_mismatch':
                    raise
                status = None
        if explicit_cancel:
            needs_material = False

        async with self.database.transaction() as connection:
            await self.growth.jobs.assert_generation_on(connection, generation)
            if needs_material and not material_error:
                material_error = await self.receipt_sources_on(connection, batch, frozen_request, generation)
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"]))).fetchone()
            if (not current or not current["active_batch"]
                    or current["active_batch"]["material_key"] != batch["material_key"]):
                return {"state": "changed_since_original_result_read"}
            await connection.execute(
                "INSERT INTO companion.growth_retry_batches(actor_id,scope_key,retry_key,batch,current_material_key,state) "
                "VALUES(%s,%s,%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                (state["actor_id"], state["scope_key"], key, document(batch), batch["material_key"]))
            row = await (await connection.execute(
                "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"], key))).fetchone()
            if row["state"] == "completed" or row["current_material_key"] != batch["material_key"]:
                return {"state": "changed_since_original_result_read"}
            if needs_material and not material_error:
                material_error = await self.receipt_inputs_on(
                    connection, state['actor_id'], state['scope_key'], batch, batch.get('retry_key'))

            budget = None
            if not observing_control and (result is None or result.receipt.status != 'succeeded'):
                current_receipt = None
                if not material_error and not explicit_cancel:
                    if uncertain and retryable and finished_unknown(status, attempt_id, generation):
                        current_receipt = status
                    elif result is not None and (result.receipt.status == 'failed' or resumable_cancel(result.receipt)):
                        current_receipt = result.receipt
                if current_receipt is not None:
                    await lock_receipt_on(connection, current_receipt, generation)
                else:
                    await lock_recovery_facts_on(connection, attempt_id)
            if observing_control:
                # A control-plane wait is not a Models physical outcome.
                pass
            elif result is not None and result.receipt.status == 'succeeded':
                await succeed_recovery(self.growth.recovery_policy, connection, batch, attempt_id)
            else:
                await fail_recovery(self.growth.recovery_policy, connection, batch, attempt_id,
                    'cancelled' if explicit_cancel else code, unknown=uncertain)
                if result is not None and not explicit_cancel and not material_error:
                    budget, material_error = await self.note_current_receipt_on(
                        connection, batch, attempt_id, result.receipt)
                if (uncertain and retryable and not material_error
                        and finished_unknown(status, attempt_id, generation)):
                    fixed_due = await self.growth.recovery_policy.record_computation_unknown_on(
                        connection, attempt_id, owner=OWNER, expected_version=batch['recovery_version'],
                        model_receipt=status, retry_after=delay)
                    if fixed_due is not None:
                        due = fixed_due
            if material_error and not observing_control:
                # A known failure stays blocked; an unknown is only observed.
                correcting = False
                retryable = bool(uncertain and not explicit_cancel)
                due = datetime.now(UTC) + timedelta(minutes=5) if retryable else None
            elif retryable and not observing_control:
                budget = budget or await recovery_budget_on(self.growth.recovery_policy, connection, batch)
                if not uncertain and not budget['can_retry']:
                    retryable, due = False, None
                elif budget.get('retry_at') is not None:
                    due = (budget['retry_at'] if budget.get('reason') == 'confirmed_not_sent'
                           else max(due, budget['retry_at']) if due else budget['retry_at'])

            reserved = {**batch, "retry_key": key, "attempt_number": number}
            if observing_control:
                reserved['control_observation'] = control_observation
            if correcting and not reserved.get('control_observation'):
                reserved.update(output_repair_count=batch.get("output_repair_count", 0)+1,
                    output_repair={"rejected_material_key": batch["material_key"],
                        "diagnostic": output_diagnostic(code, diagnostic), "attempt_id": result.receipt.attempt_id})
            domain_error = code if explicit_cancel else material_error or code
            row = await (await connection.execute(
                "UPDATE companion.growth_retry_batches SET batch=%s,state=%s,due_at=%s,last_error=%s,"
                "waiting_on_original=%s,explicit_retry=false,history=%s,revision=revision+1,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
                (document(reserved), "waiting" if retryable else "blocked", due, domain_error, uncertain,
                 document(row["history"] if observing_control else
                          with_history(row["history"], history_entry(reserved, code, result))),
                 state["actor_id"], state["scope_key"], key))).fetchone()
            await self.growth.observations.emit_on(connection, self.growth.observations.review_event(row), generation=generation)
            for identifier, version in batch["inputs"]:
                await connection.execute(
                    "UPDATE companion.growth_inputs SET retry_key=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s AND reviewed_revision<revision",
                    (key, state["actor_id"], state["scope_key"], identifier, version))
            revision = current["revision"] + 1
            await connection.execute(
                "UPDATE companion.organize_state SET active_batch=NULL,blocked_material=NULL,last_error=NULL,"
                "revision=%s,updated_at=now() WHERE actor_id=%s AND scope_key=%s",
                (revision, state["actor_id"], state["scope_key"]))
            if retryable:
                await self.schedule_on(connection, row)
            await self.growth._enqueue(connection, state["actor_id"], state["scope_key"], revision, "failed-batch-yield", generation=generation)
        return {"state": "retry_waiting" if retryable else "retry_blocked", "retry_key": key,
            "error_code": domain_error, "attempt_id": attempt_id, "due_at": due,
            "attempt_number": number}

    async def postpone(self, row, *, code=None, blocked=False, waiting_on_original=True, retry_at=None):
        """An unreadable/live original is observed later, never double-submitted."""
        async with self.database.transaction() as connection:
            await self.growth.jobs.assert_generation_on(connection, row['batch']['runtime_generation'])
            updated = await (await connection.execute(
                "UPDATE companion.growth_retry_batches SET state=%s,due_at=%s,last_error=COALESCE(%s,last_error),"
                "waiting_on_original=%s,revision=revision+1,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND retry_key=%s AND revision=%s AND state='waiting' RETURNING *",
                ("blocked" if blocked else "waiting", None if blocked else retry_at or datetime.now(UTC)+timedelta(seconds=30),
                 code, waiting_on_original and not blocked, row["actor_id"], row["scope_key"], row["retry_key"], row["revision"]))).fetchone()
            if updated:
                await self.growth.observations.emit_on(connection, self.growth.observations.review_event(updated), generation=row['batch']['runtime_generation'])
                if blocked and code and any(part in code for part in (
                        'source', 'cancel', 'withdraw', 'retract', 'episode_revision', 'episode_unavailable')):
                    batch = updated['batch']
                    if batch.get('lineage_id'):
                        if code == 'explicit_cancel':
                            await lock_recovery_facts_on(connection, attempt_for(batch['material_key']))
                        await self.growth.recovery_policy.update_version_on(connection, batch['lineage_id'], owner=OWNER,
                            expected_version=batch['recovery_version'], current_version=batch['recovery_version'],
                            cancelled=True)
            if updated and not blocked:
                await self.schedule_on(connection, updated)
        if not updated:
            return {'state': 'changed_since_retry_claim'}
        return {"state": "retry_blocked" if blocked else "waiting_original", "error_code": code}

    async def run(self, actor_id, scope_key, retry_key, generation):
        async with self.database.transaction() as connection:
            await self.growth.jobs.assert_generation_on(connection, generation)
            row = await (await connection.execute(
                "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s",
                (actor_id, scope_key, retry_key))).fetchone()
            state = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s",
                (actor_id, scope_key))).fetchone()
        pending_review = bool(row and row['state'] == 'blocked'
                              and (row['batch'].get('rbi_review') or {}).get('state') == 'requested')
        if not row or not state or (row["state"] not in {"waiting", "active"} and not pending_review):
            return
        if row['batch'].get('runtime_generation') != generation or state['runtime_generation'] != generation:
            raise AppError('growth_generation_changed', status=409)
        if pending_review:
            if row['due_at'] and row['due_at'] > datetime.now(UTC):
                async with self.database.transaction() as connection:
                    current = await (await connection.execute(
                        "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s "
                        "AND retry_key=%s FOR UPDATE", (actor_id, scope_key, retry_key))).fetchone()
                    if (current and current['state'] == 'blocked'
                            and (current['batch'].get('rbi_review') or {}).get('state') == 'requested'):
                        await self.schedule_on(connection, current)
                return {'state': 'review_waiting', 'due_at': row['due_at']}
            from .rbi_growth_review import process_review
            reviewed = await process_review(self, row, generation)
            if reviewed.get('state') == 'review_admitted' and reviewed['due_at'] <= datetime.now(UTC):
                return await self.run(actor_id, scope_key, retry_key, generation)
            return reviewed
        active = state["active_batch"]
        if active:
            if active["material_key"] == row["current_material_key"]:
                return await self.growth.organize(actor_id, scope_key, state["revision"], generation)
            return await self.postpone(row, waiting_on_original=row['waiting_on_original'])
        if row["state"] == "active":
            # The pointer is transactional with this row. An externally changed
            # owner is not permission to manufacture another execution.
            return {"state": "changed_since_retry_claim"}
        if row["due_at"] and row["due_at"] > datetime.now(UTC):
            async with self.database.transaction() as connection:
                current = await (await connection.execute(
                    "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
                    (actor_id, scope_key, retry_key))).fetchone()
                if current and current["state"] == "waiting":
                    await self.schedule_on(connection, current)
            return
        original = context_from_record(row["batch"]["context"])
        dependency_wait = None
        activated = None
        try:
            try:
                await self.growth.companion.identity.check(original)
                context = original
            except AppError as error:
                if not authorization_error(error):
                    raise
                context = await self.growth.recovery.reader(original)
            request = await self.growth.generations.request(attempt_for(row["current_material_key"]), original)
            if request is None:
                if row['waiting_on_original']:
                    return await self.postpone(row, code='reflection_material_contract_invalid',
                        waiting_on_original=True, retry_at=datetime.now(UTC) + timedelta(minutes=5))
                return await self.postpone(row, code="reflection_material_contract_invalid", blocked=True)
            if request.data_generation != generation:
                return await self.postpone(row, code="growth_generation_changed", blocked=True)
            result = await self.growth.generations.read(request.attempt_id, context)
            if result is not None and (result.receipt.status in {'failed', 'cancelled', 'unknown'}
                    or (row['explicit_retry'] and (row['batch'].get('rbi_review') or {}).get('state') == 'admitted')):
                try:
                    bind_receipt(request, result.receipt)
                except AppError as error:
                    if error.code != 'growth_recovery_receipt_mismatch':
                        raise
                    return await self.postpone(row, code=error.code, waiting_on_original=row['waiting_on_original'])
            unknown_receipt = None
            if row['waiting_on_original'] and row['batch'].get('control_observation'):
                try:
                    observed = await self.settle_control_receipt(row, request, result, generation)
                except AppError as error:
                    if error.code != 'growth_recovery_receipt_mismatch':
                        raise
                    return await self.postpone(row, code=error.code, waiting_on_original=True)
                if observed is None:
                    return await self.postpone(row, waiting_on_original=True)
                if observed.get('state') != 'waiting':
                    return observed
                row = observed
            if row['waiting_on_original']:
                if result is None or result.receipt.status in {'queued', 'running', 'unknown'}:
                    if result and result.receipt.cancellation_reason == 'explicit_cancel':
                        return await self.postpone(row, code='explicit_cancel', blocked=True)
                    status = await self.current_status(request.attempt_id)
                    if status and status.cancellation_reason == 'explicit_cancel':
                        try:
                            bind_receipt(request, status)
                        except AppError as error:
                            if error.code != 'growth_recovery_receipt_mismatch':
                                raise
                            return await self.postpone(row, code=error.code, waiting_on_original=True)
                        return await self.postpone(row, code='explicit_cancel', blocked=True)
                    if not finished_unknown(status, request.attempt_id, generation):
                        return await self.postpone(row)
                    try:
                        bind_receipt(request, status)
                    except AppError as error:
                        if error.code != 'growth_recovery_receipt_mismatch':
                            raise
                        return await self.postpone(row, code=error.code, waiting_on_original=True)
                    async with self.database.transaction() as connection:
                        await self.growth.jobs.assert_generation_on(connection, generation)
                        material_error = await self.receipt_sources_on(connection, row['batch'], request, generation)
                        current_retry = await (await connection.execute(
                            'SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s '
                            'AND retry_key=%s FOR UPDATE', (actor_id, scope_key, retry_key))).fetchone()
                        if (not current_retry or current_retry['revision'] != row['revision']
                                or current_retry['state'] != 'waiting' or not current_retry['waiting_on_original']):
                            return {'state': 'changed_since_original_result_read'}
                        if not material_error:
                            material_error = await self.receipt_inputs_on(
                                connection, actor_id, scope_key, current_retry['batch'], retry_key)
                        if material_error:
                            next_observation = datetime.now(UTC) + timedelta(minutes=5)
                            current_retry = await (await connection.execute(
                                'UPDATE companion.growth_retry_batches SET due_at=%s,last_error=%s,'
                                'revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s '
                                'AND retry_key=%s RETURNING *',
                                (next_observation, material_error, actor_id, scope_key, retry_key))).fetchone()
                            await self.growth.observations.emit_on(
                                connection, self.growth.observations.review_event(current_retry), generation=generation)
                            await self.schedule_on(connection, current_retry)
                            return {'state': 'waiting_original', 'error_code': material_error, 'due_at': next_observation}
                        await lock_receipt_on(connection, status, generation)
                        fixed_due = await self.growth.recovery_policy.record_computation_unknown_on(
                            connection, request.attempt_id, owner=OWNER, expected_version=row['batch']['recovery_version'],
                            model_receipt=status, retry_after=retry_delay_seconds(row['attempt_number']))
                        wait_for_due = fixed_due is not None and fixed_due > datetime.now(UTC)
                        if wait_for_due:
                            current_retry = await (await connection.execute(
                                'UPDATE companion.growth_retry_batches SET due_at=%s,revision=revision+1,updated_at=now() '
                                'WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *',
                                (fixed_due, actor_id, scope_key, retry_key))).fetchone()
                            await self.schedule_on(connection, current_retry)
                    if fixed_due is None:
                        return await self.postpone(row, code='growth_retry_budget_exhausted')
                    if wait_for_due:
                        return {'state': 'waiting_original', 'due_at': fixed_due}
                    unknown_receipt = status
                else:
                    # The original Models ledger, not a timeout, settles unknown.
                    from tianshu.runtime.recovery_policy import classify_failure
                    observed_state = 'succeeded' if result.receipt.status == 'succeeded' else 'failed'
                    if observed_state == 'failed' and result.receipt.cancellation_reason != 'explicit_cancel':
                        bind_receipt(request, result.receipt)
                    observed_code = ('cancelled' if getattr(result.receipt, 'cancellation_reason', None) == 'explicit_cancel'
                        else result.receipt.error_code or 'reflection_generation_'+result.receipt.status)
                    async with self.database.transaction() as connection:
                        await self.growth.jobs.assert_generation_on(connection, generation)
                        material_error = None
                        if observed_state == 'failed' and result.receipt.cancellation_reason != 'explicit_cancel':
                            material_error = await self.receipt_sources_on(connection, row['batch'], request, generation)
                        current_retry = await (await connection.execute(
                            'SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s '
                            'AND retry_key=%s FOR UPDATE', (actor_id, scope_key, retry_key))).fetchone()
                        if not current_retry or current_retry['revision'] != row['revision'] or not current_retry['waiting_on_original']:
                            return {'state': 'changed_since_original_result_read'}
                        if (observed_state == 'failed' and result.receipt.cancellation_reason != 'explicit_cancel'
                                and not material_error):
                            material_error = await self.receipt_inputs_on(
                                connection, actor_id, scope_key, current_retry['batch'], retry_key)
                        if observed_state == 'failed':
                            receipt = result.receipt
                            if (not material_error and receipt.cancellation_reason != 'explicit_cancel'
                                    and (receipt.status == 'failed' or resumable_cancel(receipt))):
                                await lock_receipt_on(connection, receipt, generation)
                            else:
                                await lock_recovery_facts_on(connection, request.attempt_id)
                        await self.growth.recovery_policy.resolve_unknown_on(connection, request.attempt_id,
                            observation_ref='models:'+request.attempt_id, state=observed_state,
                            code=None if observed_state == 'succeeded' else observed_code,
                            category=None if observed_state == 'succeeded' else classify_failure(observed_code),
                            probe_token=row['batch'].get('probe_token'))
                        if (observed_state == 'failed' and result.receipt.cancellation_reason != 'explicit_cancel'
                                and not material_error):
                            _budget, material_error = await self.note_current_receipt_on(
                                connection, current_retry['batch'], request.attempt_id, result.receipt)
                        resolved_batch = current_retry['batch']
                        if observed_state == 'succeeded':
                            resolved_batch = {**resolved_batch, 'recovery_succeeded_attempt_id': request.attempt_id}
                        if material_error:
                            blocked = await (await connection.execute(
                                "UPDATE companion.growth_retry_batches SET batch=%s,state='blocked',due_at=NULL,"
                                "last_error=%s,waiting_on_original=false,history=%s,revision=revision+1,updated_at=now() "
                                "WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
                                (document(resolved_batch), material_error,
                                 document(with_history(current_retry['history'],
                                     history_entry(resolved_batch, observed_code, result))),
                                 actor_id, scope_key, retry_key))).fetchone()
                            await self.growth.observations.emit_on(
                                connection, self.growth.observations.review_event(blocked), generation=generation)
                            return {'state': 'retry_blocked', 'error_code': material_error, 'attempt_id': request.attempt_id}
                        row = await (await connection.execute(
                            'UPDATE companion.growth_retry_batches SET batch=%s,waiting_on_original=false,revision=revision+1,updated_at=now() '
                            'WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *',
                            (document(resolved_batch), actor_id, scope_key, retry_key))).fetchone()
            if unknown_receipt is None and (result is None or result.receipt.status == 'unknown'):
                return await self.postpone(row)
            if result and getattr(result.receipt, "cancellation_reason", None) == "explicit_cancel":
                return await self.postpone(row, code="explicit_cancel", blocked=True)
            if result and result.receipt.status == 'cancelled' and not resumable_cancel(result.receipt):
                return await self.postpone(row, code=result.receipt.error_code or 'original_cancelled', blocked=True)
            if unknown_receipt is None and result and result.receipt.status in {"queued", "running"}:
                return await self.postpone(row)
            if request.business_deadline_at and request.business_deadline_at <= datetime.now(UTC):
                return await self.postpone(row, code="business_deadline_expired", blocked=True)
            code = (result.receipt.error_code or ("reflection_outcome_unknown" if result.receipt.status == "unknown"
                    else "reflection_generation_" + result.receipt.status)) if result else row["last_error"]
            if (unknown_receipt is None and result and result.receipt.status != "succeeded" and not row["explicit_retry"]
                    and not transient_model_failure(code)):
                return await self.postpone(row, code=code, blocked=True)
            episodes = frozen_episodes(request)
            row = {**row, "batch": validate_batch_request(row["batch"], request, episodes)}
            pending_repair = (row["batch"].get("output_repair") or {}).get("rejected_material_key") == row["current_material_key"]
            if pending_repair and (result is None or result.receipt.status != "succeeded"):
                return await self.postpone(row)
            manual_output = row["explicit_retry"] and row["last_error"] in REVIEW_ERRORS
            review = row['batch'].get('rbi_review') or {}
            if row['explicit_retry'] and not admitted_review_matches(review, result.receipt if result else None):
                return await self.postpone(row, code='growth_original_receipt_changed', blocked=True)
            output_error = None
            if manual_output and result and result.receipt.status == "succeeded":
                try:
                    prepare_reflections(result, context, episodes, request.source_refs, request=request)
                except RecoveryWait:
                    raise
                except AppError as error:
                    output_error = error
                    if not review_available(row['batch'], row['last_error']):
                        return await self.postpone(row, code=row['last_error'], blocked=True)
                else:
                    # Exact duplicates may now be harmless. Reuse the known
                    # original rather than paying for an unnecessary candidate.
                    manual_output = False
                    pending_repair = False
            repeat_rejected_output = pending_repair or manual_output
            successor = result is None or result.receipt.status != "succeeded" or repeat_rejected_output
            if successor and row['batch'].get('control_observation'):
                return await self.postpone(row, code='growth_control_observation_requires_review', blocked=True)
            if successor and unknown_receipt is None:
                # Cancellation is a current Models projection, not an immutable
                # snapshot of the first failure. Re-read immediately before
                # choosing/freezing a replacement computation.
                result = await self.growth.generations.read(request.attempt_id, context)
                if result is not None and (result.receipt.status in {'failed', 'cancelled', 'unknown'}
                        or (row['explicit_retry'] and review.get('state') == 'admitted')):
                    try:
                        bind_receipt(request, result.receipt)
                    except AppError as error:
                        if error.code != 'growth_recovery_receipt_mismatch':
                            raise
                        return await self.postpone(row, code=error.code, waiting_on_original=row['waiting_on_original'])
                if result is None or result.receipt.status == 'unknown':
                    return await self.postpone(row)
                if result and getattr(result.receipt, "cancellation_reason", None) == "explicit_cancel":
                    return await self.postpone(row, code="explicit_cancel", blocked=True)
                if result and result.receipt.status == 'cancelled' and not resumable_cancel(result.receipt):
                    return await self.postpone(row, code=result.receipt.error_code or 'original_cancelled', blocked=True)
                if row['explicit_retry'] and not admitted_review_matches(review, result.receipt):
                    return await self.postpone(row, code='growth_original_receipt_changed', blocked=True)
                if result and result.receipt.status in {"queued", "running"}:
                    return await self.postpone(row)
                if result and result.receipt.status == "succeeded" and not repeat_rejected_output:
                    successor = False
                elif result and result.receipt.status != "succeeded" and not row["explicit_retry"]:
                    latest_code = result.receipt.error_code or ("reflection_outcome_unknown"
                        if result.receipt.status == "unknown" else "reflection_generation_" + result.receipt.status)
                    if not transient_model_failure(latest_code):
                        return await self.postpone(row, code=latest_code, blocked=True)
            continuation = bool(successor and result and result.receipt.status == 'succeeded' and
                                (pending_repair or (output_error and output_error.code in CORRECTABLE_ERRORS)))
            if successor:
                async with self.database.transaction(read_only=True) as connection:
                    budget = await recovery_budget_on(self.growth.recovery_policy, connection, row['batch'])
                if not budget['can_retry']:
                    code = budget.get('reason') or 'growth_retry_budget_exhausted'
                    return await self.postpone(row, code=code, blocked=unknown_receipt is None,
                                               waiting_on_original=unknown_receipt is not None)
                if result is not None and result.receipt.status == 'succeeded' and not continuation:
                    return await self.postpone(row, code=row['last_error'], blocked=True)
                if (not continuation and unknown_receipt is None and result is not None
                        and result.receipt.status in {'failed', 'cancelled'} and budget.get('retry_at') is None):
                    from .rbi_growth_review import block_unclaimable
                    return await block_unclaimable(self, row, generation)
                if (row['explicit_retry'] and review.get('state') != 'admitted'
                        and unknown_receipt is None and result is not None
                        and result.receipt.status in {'failed', 'cancelled'}
                        and not transient_model_failure(result.receipt.error_code)):
                    from .rbi_growth_review import block_unclaimable
                    return await block_unclaimable(self, row, generation, reason='growth_review_evidence_required')
            number = row["attempt_number"] + int(successor)
            key = f"{retry_key}:retry:{number}" if successor else row["current_material_key"]
            candidate = replace(request, intent_id=key, attempt_id=attempt_for(key), context=context,
                budget=RequestBudget(datetime.now(UTC)+timedelta(minutes=3)), execution_timeout_seconds=180.0,
                max_queue_wait=None, provider_session_scope="intent") if successor else request
            if successor:
                row, candidate = await self.select_route(row, candidate, generation)
                # Route selection may move the lineage to another dependency.
                # Re-read its shared due instead of retaining the former route's cooldown.
                async with self.database.transaction(read_only=True) as connection:
                    budget = await recovery_budget_on(self.growth.recovery_policy, connection, row['batch'])
                if not budget['can_retry']:
                    code = budget.get('reason') or 'growth_retry_budget_exhausted'
                    return await self.postpone(row, code=code, blocked=unknown_receipt is None,
                                               waiting_on_original=unknown_receipt is not None)
                if budget.get('retry_at') and budget['retry_at'] > datetime.now(UTC):
                    return await self.postpone(row, code=row['last_error'], retry_at=budget['retry_at'],
                                               waiting_on_original=row['waiting_on_original'])
            repair_update = {}
            if successor and (pending_repair or output_error and output_error.code in CORRECTABLE_ERRORS):
                if not pending_repair and not correction_available(row["batch"], output_error.code):
                    return await self.postpone(row, code=output_error.code, blocked=True)
                diagnostic = (row["batch"]["output_repair"]["diagnostic"] if pending_repair
                              else output_diagnostic(output_error.code, output_error.details))
                candidate = corrected_request(candidate, result, diagnostic, self.growth.assembler.counter)
                repair_update = {"output_repair_count": row["batch"].get("output_repair_count", 0) + int(not pending_repair),
                    "output_repair": {"rejected_material_key": row["current_material_key"],
                        "diagnostic": diagnostic, "attempt_id": request.attempt_id}}
            if unknown_receipt is not None:
                # A separate, fresh authority read is required immediately
                # before reserving a successor of this unknown computation.
                status = await self.current_status(request.attempt_id)
                if status and status.cancellation_reason == 'explicit_cancel':
                    try:
                        bind_receipt(request, status)
                    except AppError as error:
                        if error.code != 'growth_recovery_receipt_mismatch':
                            raise
                        return await self.postpone(row, code=error.code, waiting_on_original=True)
                    return await self.postpone(row, code='explicit_cancel', blocked=True)
                if not finished_unknown(status, request.attempt_id, generation):
                    return await self.postpone(row)
                try:
                    bind_receipt(request, status)
                except AppError as error:
                    if error.code != 'growth_recovery_receipt_mismatch':
                        raise
                    return await self.postpone(row, code=error.code, waiting_on_original=True)
                unknown_receipt = status
            async def claim(connection):
                nonlocal activated, dependency_wait
                current = await (await connection.execute(
                    "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                    (actor_id, scope_key))).fetchone()
                saved = await (await connection.execute(
                    "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
                    (actor_id, scope_key, retry_key))).fetchone()
                if (not current or not saved or current["active_batch"] or saved["revision"] != row["revision"]
                        or saved["state"] != "waiting" or saved["current_material_key"] != row["current_material_key"]):
                    return False
                for identifier, version in row["batch"]["inputs"]:
                    reserved = await (await connection.execute(
                        "SELECT revision,reviewed_revision,retry_key FROM companion.growth_inputs "
                        "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s FOR UPDATE",
                        (actor_id, scope_key, identifier))).fetchone()
                    if (not reserved or reserved["revision"] != version or reserved["reviewed_revision"] >= version
                            or reserved["retry_key"] != retry_key):
                        raise AppError("reflection_episode_revision_conflict", status=409)
                batch = {**row["batch"], "material_key": key, "context": wire(candidate.context),
                    "start_cursor": current["cursor"], "end_cursor": later_cursor(current["cursor"], row["batch"]["end_cursor"]),
                    "watermark": current["watermark"], "attempt_number": number,
                    "reflection_identity_protocol": IDENTITY_PROTOCOL, "material_at": material_time(episodes).isoformat(),
                    **repair_update}
                if successor:
                    original_receipt = (unknown_receipt if unknown_receipt is not None else
                                        result.receipt if not continuation and result is not None else None)
                    if not continuation:
                        if original_receipt is None:
                            raise AppError('recovery_model_receipt_unconfirmed', status=409)
                        await lock_receipt_on(connection, original_receipt, generation)
                    claim = (await self.growth.recovery_policy.claim_continuation_on(connection,
                        batch['lineage_id'], owner=OWNER, expected_version=batch['recovery_version'],
                        prior_attempt_id=request.attempt_id, reason='schema_correction')) if continuation else (
                        await self.growth.recovery_policy.claim_lineage_due_on(connection,
                            batch['lineage_id'], owner=OWNER, lease_seconds=30))
                    if not claim:
                        dependency_wait = await self.growth.recovery_policy.inspect_wait_on(connection,
                            batch['lineage_id'], owner=OWNER, expected_version=batch['recovery_version'])
                        return False
                    reservation_options = {'model_receipt': original_receipt} if original_receipt is not None else {}
                    reservation = await self.growth.recovery_policy.reserve_attempt_on(connection,
                        batch['lineage_id'], owner=OWNER, expected_version=batch['recovery_version'],
                        attempt_id=candidate.attempt_id, request_digest=signature(candidate),
                        route=batch['recovery_route'], original_state='succeeded' if continuation else (
                            'unknown' if unknown_receipt is not None else 'failed'),
                        claim_token=field(claim, 'claim_token'), **reservation_options)
                    batch['probe_token'] = field(reservation, 'probe_token')
                    batch['recovery_observe_only'] = field(reservation, 'status') != 'reserved'
                payload = frozen_material(candidate)
                batch.update(reflection_output_protocol=OUTPUT_PROTOCOL, episode_refs=payload["episode_refs"])
                updated_retry = await (await connection.execute(
                    "UPDATE companion.growth_retry_batches SET state='active',batch=%s,current_material_key=%s,"
                    "previous_material_key=%s,attempt_number=%s,due_at=NULL,explicit_retry=false,waiting_on_original=false,"
                    "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
                    (document(batch), key, row["current_material_key"] if successor else row["previous_material_key"],
                     number, actor_id, scope_key, retry_key))).fetchone()
                await self.growth.observations.emit_on(connection, self.growth.observations.review_event(updated_retry), generation=generation)
                activated = await (await connection.execute(
                    "UPDATE companion.organize_state SET active_batch=%s,blocked_material=%s,last_error=NULL,"
                    "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s RETURNING *",
                    (document(batch), None if successor else document({"recovery": "observe_original_attempt"}),
                     actor_id, scope_key))).fetchone()
                return True

            async with self.database.transaction() as connection:
                await self.growth._validate_material(connection, context, episodes, request.source_refs, generation=generation)
                if successor:
                    await self.growth.generations.prepare(key, candidate, connection=connection, guard=claim)
                else:
                    await claim(connection)
            if activated is None:
                if dependency_wait is not None:
                    raise dependency_wait
                return await self.postpone(row, waiting_on_original=row['waiting_on_original'])
            return await self.growth.organize(actor_id, scope_key, activated["revision"], generation)
        except RecoveryWait as wait:
            if activated is not None:
                # The nested organizer has already handled its active pointer.
                raise
            if row['batch'].get('control_observation') and not recovery_wait_retryable(wait):
                # Only a read of the original ID is scheduled; the control
                # wait does not authorize repeating its remote action.
                if getattr(wait, 'transaction_rollback_confirmed', None) is not False:
                    try:
                        await self.postpone(row, code='growth_model_control_unconfirmed', waiting_on_original=True)
                    except Exception as scheduling_error:
                        raise wait from scheduling_error
                await self.propagate_unconfirmed_wait(wait, attempt_for(row['current_material_key']))
            return await self.wait_dependency(row, wait)
        except (AppError, TimeoutError, aiohttp.ClientError) as error:
            if classify_database_failure(error) is not None:
                raise
            if (isinstance(error, AppError) and row['waiting_on_original'] and error.code in {
                    'recovery_model_receipt_unconfirmed', 'recovery_model_receipt_mismatch',
                    'recovery_model_outcome_changed'}):
                return await self.postpone(row, code=error.code, waiting_on_original=True,
                                           retry_at=datetime.now(UTC) + timedelta(minutes=5))
            if isinstance(error, AppError) and row['waiting_on_original'] and self.invalid_material_error(error):
                return await self.postpone(row, code=error.code, waiting_on_original=True,
                                           retry_at=datetime.now(UTC) + timedelta(minutes=5))
            if isinstance(error, AppError) and error.code == "generation_no_longer_current":
                if dependency_wait is not None:
                    return await self.wait_dependency(row, dependency_wait)
                return await self.postpone(row, waiting_on_original=row['waiting_on_original'])
            code = error_code(error)
            return await self.postpone(row, code=code, blocked=not transient_model_failure(code))

    async def complete_on(self, connection, current, batch, *, history_only=False):
        if not batch.get("retry_key"):
            return
        entry = {**history_entry(batch, None), "status": "succeeded"}
        row = await (await connection.execute(
            "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
            (current["actor_id"], current["scope_key"], batch["retry_key"]))).fetchone()
        if not row or row["state"] != "active" or row["current_material_key"] != batch["material_key"]:
            raise AppError("generation_no_longer_current", status=409)
        updated = await (await connection.execute(
            "UPDATE companion.growth_retry_batches SET state='completed',due_at=NULL,last_error=NULL,history=%s,"
            "outcome=%s,revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
            (document(with_history(row["history"], entry)), "historical_only" if history_only else "applied",
             row["actor_id"], row["scope_key"], row["retry_key"]))).fetchone()
        await self.growth.observations.emit_on(connection, self.growth.observations.review_event(updated), generation=current['runtime_generation'])

    async def resume_on(self, connection, current, caller, retry_key, *, repair_ref=None):
        await self.growth.jobs.assert_generation_on(connection, current['runtime_generation'])
        row = await (await connection.execute(
            "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
            (current["actor_id"], current["scope_key"], retry_key))).fetchone()
        if not row or row['batch'].get('control_observation') or row['waiting_on_original']:
            raise AppError("reflection_retry_not_available", status=409)
        if not self.growth.recovery.same_scene(caller, context_from_record(row["batch"]["context"])):
            raise AppError("reflection_recovery_context_mismatch", status=403)
        review = row['batch'].get('rbi_review') or {}
        if review.get('state') in {'requested', 'admitted'}:
            same_original = review.get('original_attempt_id') == attempt_for(row['current_material_key'])
            if (review.get('repair_ref') == repair_ref and
                    ((review['state'] == 'requested' and same_original)
                     or (review['state'] == 'admitted' and row['state'] in {'waiting', 'active', 'completed'}))):
                return current['revision']
            if row['state'] != 'blocked' or review['state'] == 'requested':
                raise AppError('reflection_review_in_progress', status=409)
            if repair_ref and review.get('repair_ref') == repair_ref:
                raise AppError('growth_repair_already_used', status=409)
        if (row['state'] != 'blocked' or not manual_retry_allowed(row['last_error'])
                or (not review_available(row['batch'], row['last_error'])
                    and not (repair_ref and row['last_error'] in OUTPUT_ERRORS))):
            raise AppError("reflection_retry_not_available", status=409)
        review = {'state': 'requested', 'repair_ref': repair_ref,
                  'original_attempt_id': attempt_for(row['current_material_key'])}
        batch = {**row['batch'], 'rbi_review': review}
        updated = await (await connection.execute(
            "UPDATE companion.growth_retry_batches SET batch=%s,due_at=now(),explicit_retry=false,"
            "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
            (document(batch), row["actor_id"], row["scope_key"], retry_key))).fetchone()
        await self.growth.observations.emit_on(connection, self.growth.observations.review_event(updated), generation=current['runtime_generation'])
        revision = current["revision"] + 1
        await connection.execute(
            "UPDATE companion.organize_state SET revision=%s,updated_at=now() WHERE actor_id=%s AND scope_key=%s",
            (revision, current["actor_id"], current["scope_key"]))
        await self.schedule_on(connection, updated)
        return revision

    async def blocked_for_scene(self, connection, state, caller, *, repair_ref=None):
        rows = await (await connection.execute(
            "SELECT retry_key,batch->'context' AS context,batch->'output_repair_count' AS output_repair_count,"
            "last_error FROM companion.growth_retry_batches "
            "WHERE actor_id=%s AND scope_key=%s AND state='blocked' AND NOT waiting_on_original "
            "AND COALESCE(batch->'rbi_review'->>'state','')<>'requested' "
            "AND NOT (batch ? 'control_observation') ORDER BY created_at,retry_key",
            (state["actor_id"], state["scope_key"]))).fetchall()
        return next((row["retry_key"] for row in rows if manual_retry_allowed(row["last_error"])
            and (review_available({"output_repair_count": row.get("output_repair_count") or 0}, row["last_error"])
                 or repair_ref and row['last_error'] in OUTPUT_ERRORS)
            and self.growth.recovery.same_scene(caller, context_from_record(row["context"]))), None)

    async def reconcile_transport_waits(self, limit=32):
        from .rbh_growth_transport import reconcile_transport_waits
        return await reconcile_transport_waits(self.growth, limit=limit)

    async def recover(self):
        async with self.database.transaction() as connection:
            generation = await self.growth.jobs.current_generation_on(connection)
            rows = await (await connection.execute(
                "SELECT * FROM companion.growth_retry_batches WHERE (state IN ('waiting','active') "
                "OR (state='blocked' AND batch->'rbi_review'->>'state'='requested')) "
                "AND batch->>'runtime_generation'=%s ORDER BY actor_id,scope_key,retry_key FOR UPDATE", (generation,))).fetchall()
            for row in rows:
                await self.schedule_on(connection, row)
