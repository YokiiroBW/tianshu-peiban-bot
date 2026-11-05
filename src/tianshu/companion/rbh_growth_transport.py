"""Bounded re-evaluation of Growth batches blocked by old connection failures.

Models remains the receipt authority and Runtime owns all evidence and budget
classification. This worker only discovers existing domain rows, revalidates
their frozen sources, and schedules the existing Growth retry task.
"""

from datetime import UTC, datetime, timedelta

import aiohttp

from tianshu.conversation.codec import context_from_record
from tianshu.conversation.generation import attempt_for
from tianshu.models.retry import transient_model_failure
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable

from .growth_policy import bind_receipt, budget_on, note_receipt_on
from .reflection_output import frozen_episodes, validate_batch_request


SCAN_DELAY = timedelta(minutes=5)
MAX_HISTORY_ATTEMPTS = 64
BUDGET_ERRORS = frozenset({'growth_retry_budget_exhausted', 'recovery_budget_exhausted',
                           'recovery_connection_budget_exhausted'})


def _identity(row, reason):
    return {'actor_id': row['actor_id'], 'scope_key': row['scope_key'],
            'retry_key': row['retry_key'], 'reason': reason}


async def _claim(growth, limit):
    database = growth.companion.database
    async with database.transaction() as connection:
        generation = await growth.jobs.current_generation_on(connection)
        rows = await (await connection.execute(
            "SELECT r.* FROM companion.growth_retry_batches r "
            "JOIN companion.organize_state s ON s.actor_id=r.actor_id AND s.scope_key=r.scope_key "
            "LEFT JOIN companion.rbh_growth_transport_scans c ON c.runtime_generation=%s "
            "AND c.actor_id=r.actor_id AND c.scope_key=r.scope_key AND c.retry_key=r.retry_key "
            "WHERE r.state='blocked' AND NOT r.waiting_on_original "
            "AND NOT (r.batch ? 'control_observation') AND r.batch->>'runtime_generation'=%s "
            "AND s.runtime_generation=%s AND r.last_error IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.history) AS h "
            "WHERE h->>'error_code'='transport_not_sent') "
            "AND (c.retry_key IS NULL OR c.retry_revision<>r.revision OR c.next_scan_at<=now()) "
            "ORDER BY c.next_scan_at NULLS FIRST,r.actor_id,r.scope_key,r.retry_key "
            "LIMIT %s FOR UPDATE OF r SKIP LOCKED", (generation, generation, generation, limit))).fetchall()
        for row in rows:
            await connection.execute(
                "INSERT INTO companion.rbh_growth_transport_scans "
                "(runtime_generation,actor_id,scope_key,retry_key,retry_revision,next_scan_at) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(runtime_generation,actor_id,scope_key,retry_key) "
                "DO UPDATE SET retry_revision=EXCLUDED.retry_revision,next_scan_at=EXCLUDED.next_scan_at",
                (generation, row['actor_id'], row['scope_key'], row['retry_key'], row['revision'],
                 datetime.now(UTC) + SCAN_DELAY))
        return generation, rows


def _history_ids(row):
    history = row.get('history') or []
    result, seen = [], set()
    for item in history:
        if not isinstance(item, dict):
            raise AppError('growth_recovery_history_invalid', status=409)
        identity, material = item.get('attempt_id'), item.get('material_key')
        if not isinstance(identity, str) or not isinstance(material, str) or identity != attempt_for(material):
            raise AppError('growth_recovery_history_invalid', status=409)
        if identity not in seen:
            seen.add(identity)
            result.append((identity, material))
            if len(result) > MAX_HISTORY_ATTEMPTS:
                raise AppError('growth_recovery_history_too_large', status=409)
    current = attempt_for(row['current_material_key'])
    if not result or result[-1] != (current, row['current_material_key']):
        raise AppError('growth_recovery_history_invalid', status=409)
    return result


async def _reconcile_one(growth, row, generation):
    if row['last_error'] not in BUDGET_ERRORS and not transient_model_failure(row['last_error']):
        return 'original_failure_not_retryable'
    batch = row['batch']
    original = context_from_record(batch['context'])
    current = await growth.recovery.reader(original)
    await growth.companion.identity.check(current)
    attempt_ids = _history_ids(row)
    original_attempt = attempt_ids[-1][0]
    request = await growth.generations.observe_request(original_attempt, observer=current)
    if not request or request.attempt_id != original_attempt or request.data_generation != generation:
        return 'original_request_unavailable'
    if request.business_deadline_at and request.business_deadline_at <= datetime.now(UTC):
        return 'business_deadline_expired'
    episodes = frozen_episodes(request)
    validate_batch_request(batch, request, episodes)

    receipts = {}
    for attempt_id, material_key in attempt_ids:
        frozen = (request if attempt_id == original_attempt
                  else await growth.generations.observe_request(attempt_id, observer=current))
        if not frozen or frozen.intent_id != material_key or frozen.data_generation != generation:
            continue
        receipt = await growth.models.status(attempt_id)
        if receipt is not None:
            bind_receipt(frozen, receipt)
            if receipt.cancellation_reason == 'explicit_cancel':
                return 'explicit_cancel'
            receipts[attempt_id] = receipt
    latest = receipts.get(original_attempt)
    if (latest is None or latest.status != 'failed'
            or not transient_model_failure(latest.error_code, cancelled=latest.cancellation_reason == 'explicit_cancel')):
        return 'original_attempt_unconfirmed'
    if (row['history'][-1].get('error_code') != latest.error_code
            or row['last_error'] not in BUDGET_ERRORS | {latest.error_code}):
        return 'domain_receipt_mismatch'

    async with growth.companion.database.transaction() as connection:
        # Locks and validates the same source closure as normal Growth apply.
        await growth._validate_material(connection, current, episodes, request.source_refs, generation=generation)
        state = await (await connection.execute(
            'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE',
            (row['actor_id'], row['scope_key']))).fetchone()
        saved = await (await connection.execute(
            'SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s '
            'AND retry_key=%s FOR UPDATE', (row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
        if (not state or state['runtime_generation'] != generation or not saved or saved['state'] != 'blocked'
                or saved['revision'] != row['revision'] or saved['current_material_key'] != row['current_material_key']
                or saved['waiting_on_original'] or saved['batch'].get('control_observation')):
            return 'changed_since_scan'
        active = state['active_batch']
        if active and active.get('material_key') == saved['current_material_key']:
            return 'batch_already_active'
        for identifier, revision in sorted(saved['batch']['inputs']):
            reserved = await (await connection.execute(
                'SELECT revision,reviewed_revision,retry_key FROM companion.growth_inputs '
                'WHERE actor_id=%s AND scope_key=%s AND episode_id=%s FOR UPDATE',
                (row['actor_id'], row['scope_key'], identifier))).fetchone()
            if (not reserved or reserved['revision'] != revision or reserved['reviewed_revision'] >= revision
                    or reserved['retry_key'] != row['retry_key']):
                return 'material_changed'
        latest_note = None
        for attempt_id, _material_key in attempt_ids:
            receipt = receipts.get(attempt_id)
            if receipt is None:
                continue
            note = await note_receipt_on(growth.recovery_policy, connection, saved['batch'], attempt_id, receipt)
            if attempt_id == original_attempt:
                latest_note = note
        budget = await budget_on(growth.recovery_policy, connection, saved['batch'])
        if not budget['can_retry']:
            return budget.get('reason') or 'recovery_budget_exhausted'
        if (not latest_note or latest_note.get('reason') not in {'confirmed_not_sent', 'not_confirmed_not_sent'}
                or budget['connection_attempts'] < 1):
            return 'original_attempt_unconfirmed'
        if budget.get('retry_at') is None:
            return 'recovery_due_unavailable'
        due = max(datetime.now(UTC), budget['retry_at'])
        updated = await (await connection.execute(
            "UPDATE companion.growth_retry_batches SET state='waiting',due_at=%s,explicit_retry=false,"
            "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s "
            "AND state='blocked' AND revision=%s RETURNING *",
            (due, row['actor_id'], row['scope_key'], row['retry_key'], row['revision']))).fetchone()
        if not updated:
            return 'changed_since_scan'
        await growth.observations.emit_on(connection, growth.observations.review_event(updated), generation=generation)
        await growth.retries.schedule_on(connection, updated)
    return 'scheduled'


async def reconcile_transport_waits(growth, limit=32):
    if type(limit) is not int or not 1 <= limit <= 128:
        raise AppError('invalid_growth_transport_limit')
    generation, rows = await _claim(growth, limit)
    result = {'inspected': 0, 'scheduled': 0, 'ineligible': 0, 'changes': []}
    for row in rows:
        result['inspected'] += 1
        try:
            reason = await _reconcile_one(growth, row, generation)
        except RecoveryWait as wait:
            if not recovery_wait_retryable(wait):
                raise
            reason = wait.reason
        except AppError as error:
            reason = error.code
        except (TimeoutError, aiohttp.ClientError):
            reason = 'dependency_unavailable'
        result['scheduled' if reason == 'scheduled' else 'ineligible'] += 1
        result['changes'].append(_identity(row, reason))
    return result
