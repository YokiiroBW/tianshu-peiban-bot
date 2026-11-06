"""Qualify an explicit Growth review against its original physical outcome."""

from datetime import UTC, datetime, timedelta
from collections.abc import Mapping

import aiohttp

from tianshu.conversation.codec import context_from_record, document
from tianshu.conversation.generation import attempt_for
from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable

from .growth_output_repair import review_original
from .growth_policy import OWNER, bind_receipt, budget_on


async def _current_on(retries, connection, row, generation):
    await retries.growth.jobs.assert_generation_on(connection, generation)
    state = await (await connection.execute(
        'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE',
        (row['actor_id'], row['scope_key']))).fetchone()
    saved = await (await connection.execute(
        'SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s '
        'AND retry_key=%s FOR UPDATE', (row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
    if (not state or state['runtime_generation'] != generation or not saved or saved['state'] != 'blocked'
            or saved['revision'] != row['revision'] or saved['current_material_key'] != row['current_material_key']
            or saved['batch'].get('rbi_review') != row['batch'].get('rbi_review')):
        return None, None
    return state, saved


async def _finish_on(retries, connection, row, generation, *, reason=None, due_at=None, admitted=False,
                     receipt=None):
    review = {**row['batch']['rbi_review'], 'state': 'admitted' if admitted else 'stopped' if reason else 'requested'}
    if reason:
        review['reason'] = reason
    else:
        review.pop('reason', None)
    if receipt is not None:
        diagnostic = receipt.diagnostic if isinstance(receipt.diagnostic, Mapping) else {}
        review.update(error_code=receipt.error_code, failure_stage=diagnostic.get('failure_stage'),
                      error_type=diagnostic.get('error_type'))
    batch = {**row['batch'], 'rbi_review': review}
    if admitted and receipt is not None and receipt.status == 'succeeded':
        batch['recovery_succeeded_attempt_id'] = receipt.attempt_id
    updated = await (await connection.execute(
        "UPDATE companion.growth_retry_batches SET batch=%s,state=%s,due_at=%s,explicit_retry=%s,"
        "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
        (document(batch), 'waiting' if admitted else 'blocked', due_at, admitted,
         row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
    await retries.growth.observations.emit_on(connection, retries.growth.observations.review_event(updated),
                                               generation=generation)
    if due_at is not None:
        await retries.schedule_on(connection, updated)
    return updated


async def _finish(retries, row, generation, *, reason=None, due_at=None):
    async with retries.database.transaction() as connection:
        _state, saved = await _current_on(retries, connection, row, generation)
        if not saved:
            return {'state': 'changed_since_review_read'}
        await _finish_on(retries, connection, saved, generation, reason=reason, due_at=due_at)
    return {'state': 'review_stopped' if reason else 'review_waiting', 'reason': reason, 'due_at': due_at}


async def block_unclaimable(retries, row, generation, *, reason='growth_recovery_due_unavailable'):
    """Stop a legacy wait without altering the original physical failure."""
    async with retries.database.transaction() as connection:
        await retries.growth.jobs.assert_generation_on(connection, generation)
        saved = await (await connection.execute(
            "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s "
            "AND retry_key=%s AND state='waiting' AND revision=%s FOR UPDATE",
            (row['actor_id'], row['scope_key'], row['retry_key'], row['revision']))).fetchone()
        if not saved:
            return {'state': 'changed_since_retry_claim'}
        review = {**(saved['batch'].get('rbi_review') or {}), 'state': 'stopped',
                  'original_attempt_id': attempt_for(saved['current_material_key']),
                  'reason': reason}
        batch = {**saved['batch'], 'rbi_review': review}
        updated = await (await connection.execute(
            "UPDATE companion.growth_retry_batches SET batch=%s,state='blocked',due_at=NULL,"
            "explicit_retry=false,revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s "
            "AND retry_key=%s RETURNING *",
            (document(batch), row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
        await retries.growth.observations.emit_on(connection, retries.growth.observations.review_event(updated),
                                                  generation=generation)
    return {'state': 'retry_blocked', 'error_code': row['last_error'],
            'reason': reason}


async def process_review(retries, row, generation):
    """Only original reads occur before source and domain CAS locks are held."""
    growth = retries.growth
    review = row['batch']['rbi_review']
    original = context_from_record(row['batch']['context'])
    attempt_id = attempt_for(row['current_material_key'])
    if review.get('original_attempt_id') != attempt_id:
        return await _finish(retries, row, generation, reason='growth_review_identity_changed')
    try:
        current = await growth.recovery.reader(original)
        request = await growth.generations.observe_request(attempt_id, observer=current)
        if request is None or request.data_generation != generation:
            return await _finish(retries, row, generation, reason='original_request_unavailable')
        result = await growth.generations.read(attempt_id, request.context, observer=current)
        if result is None:
            return await _finish(retries, row, generation, reason='original_attempt_unresolved')
        bind_receipt(request, result.receipt)
        if request.business_deadline_at and request.business_deadline_at <= datetime.now(UTC):
            return await _finish(retries, row, generation, reason='business_deadline_expired')
        action, reason = review_original(result.receipt, review.get('repair_ref'))
    except RecoveryWait as wait:
        if not recovery_wait_retryable(wait):
            raise
        return await _finish(retries, row, generation, due_at=wait.retry_at)
    except AppError as error:
        if retries.invalid_material_error(error) or error.code in {
            'generation_observation_context_mismatch', 'model_result_context_forbidden',
            'growth_recovery_receipt_mismatch', 'model_attempt_not_found', 'attempt_not_found',
        }:
            return await _finish(retries, row, generation, reason=error.code)
        raise
    except (TimeoutError, aiohttp.ClientError):
        return await _finish(retries, row, generation, due_at=datetime.now(UTC) + timedelta(minutes=5))

    async with retries.database.transaction() as connection:
        await growth.jobs.assert_generation_on(connection, generation)
        material_error = await retries.receipt_sources_on(connection, row['batch'], request, generation)
        state, saved = await _current_on(retries, connection, row, generation)
        if not saved:
            return {'state': 'changed_since_review_read'}
        if state['active_batch']:
            due = datetime.now(UTC) + timedelta(seconds=30)
            await _finish_on(retries, connection, saved, generation, due_at=due)
            return {'state': 'review_waiting', 'reason': 'growth_owner_busy', 'due_at': due}
        if material_error in {None, 'reflection_episode_unavailable'}:
            input_error = await retries.receipt_inputs_on(
                connection, row['actor_id'], row['scope_key'], saved['batch'], row['retry_key'])
            material_error = input_error or material_error
        if result.receipt.cancellation_reason == 'explicit_cancel':
            batch = saved['batch']
            await lock_recovery_facts_on(connection, attempt_id)
            await growth.recovery_policy.update_version_on(
                connection, batch['lineage_id'], owner=OWNER,
                expected_version=batch['recovery_version'], current_version=batch['recovery_version'],
                cancelled=True)
        if material_error or action == 'stop':
            reason = material_error or reason
            await _finish_on(retries, connection, saved, generation, reason=reason, receipt=result.receipt)
            return {'state': 'review_stopped', 'reason': reason}
        if action == 'repaired' and not result.receipt.request_digest:
            await _finish_on(retries, connection, saved, generation,
                             reason='growth_recovery_receipt_digest_missing', receipt=result.receipt)
            return {'state': 'review_stopped', 'reason': 'growth_recovery_receipt_digest_missing'}
        if (action == 'repaired' and (not isinstance(result.receipt.finished_at, datetime)
                                      or result.receipt.finished_at.utcoffset() is None)):
            await _finish_on(retries, connection, saved, generation,
                             reason='growth_original_receipt_unfinished', receipt=result.receipt)
            return {'state': 'review_stopped', 'reason': 'growth_original_receipt_unfinished'}

        # A physically different or already settled owner cannot be revived by
        # an old Models snapshot. Runtime owns subsequent claims and budgets.
        attempt = await (await connection.execute(
            'SELECT status,lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s',
            (attempt_id,))).fetchone()
        expected_status = 'succeeded' if action == 'reuse' else 'failed'
        if not attempt or attempt['lineage_id'] != saved['batch'].get('lineage_id') or attempt['status'] != expected_status:
            await _finish_on(retries, connection, saved, generation, reason='growth_original_receipt_conflict',
                             receipt=result.receipt)
            return {'state': 'review_stopped', 'reason': 'growth_original_receipt_conflict'}

        due = datetime.now(UTC)
        if action == 'repaired':
            await lock_recovery_facts_on(connection, attempt_id)
            qualification = await growth.recovery_policy.resume_repaired_on(
                connection, saved['batch']['lineage_id'], owner=OWNER,
                expected_version=saved['batch']['recovery_version'], original_attempt_id=attempt_id,
                model_receipt=result.receipt, repair_ref=review['repair_ref'])
            if not qualification['eligible']:
                reason = qualification.get('reason') or 'recovery_repair_not_eligible'
                await _finish_on(retries, connection, saved, generation, reason=reason, receipt=result.receipt)
                return {'state': 'review_stopped', 'reason': reason}
            retry_at = qualification.get('retry_at')
            if not isinstance(retry_at, datetime) or retry_at.utcoffset() is None:
                raise AppError('recovery_repair_due_missing', status=503)
            due = max(due, retry_at)
        if action in {'repaired', 'transient'}:
            budget = await budget_on(growth.recovery_policy, connection, saved['batch'])
            if not budget['can_retry'] or budget.get('retry_at') is None:
                reason = budget.get('reason') if not budget['can_retry'] else 'growth_recovery_due_unavailable'
                if action == 'repaired':
                    # Roll the qualification back too; waiting without Runtime
                    # due is never a successfully admitted repair.
                    raise AppError(reason, status=409)
                await _finish_on(retries, connection, saved, generation, reason=reason, receipt=result.receipt)
                return {'state': 'review_stopped', 'reason': reason}
            due = max(due, budget['retry_at'])
        await _finish_on(retries, connection, saved, generation, admitted=True, due_at=due, receipt=result.receipt)
    return {'state': 'review_admitted', 'due_at': due}
