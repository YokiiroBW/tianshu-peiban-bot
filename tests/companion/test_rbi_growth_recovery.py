"""Original Growth reviews use real Models and Runtime ledgers in the fixture DB."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tianshu.companion.rbi_growth_review import process_review
from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import RoutePlan
from tianshu.models.ledger import receipt
from tianshu.models.types import ModelPart
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from test_growth_output import state
from test_growth_retry import current_retry_setup, observed_result, retry_row
from test_rb_growth_async import GENERATION


class LedgerModels:
    """No provider calls: submission/result authority is the real Models ledger."""

    def __init__(self, database):
        async def validate(_context, _refs):
            return None
        self.broker = ModelBroker(database, None, None, validate_submission=validate)
        self.submitted = []
        self.plan = RoutePlan('route', 1, 'provider', 'model', 'openai.chat', '', '',
                              'quota', frozenset({'text'}), {})

    async def generation_profile(self, _request):
        return {'route_id': 'route', 'route_revision': 1, 'quota_group': 'quota'}

    async def submit(self, request, *, completion=None):
        await self.broker.ledger.create(request, self.plan, {'tokens': 10}, frozen=request,
                                        data_generation=request.data_generation)
        self.submitted.append(request)
        return receipt(await self.broker.ledger.get(request.attempt_id))

    async def result(self, attempt_id, *, context):
        return await self.broker.result(attempt_id, context=context)

    async def status(self, attempt_id):
        return await self.broker.status(attempt_id)

    async def finish(self, request, status, *, code=None, diagnostic=None, result=None):
        await self.broker.ledger.started(request.attempt_id, None,
                                         deadline_at=datetime.now(UTC) + timedelta(minutes=2))
        if diagnostic is not None:
            await self.broker.ledger.save_observation(request.attempt_id, diagnostic=diagnostic)
        await self.broker.ledger.finish(request.attempt_id, status, error_code=code,
                                        result=wire(result) if result is not None else None)


async def setup_original(database, *, status='failed', code='invalid_upstream_response', diagnostic=None,
                         cancellation_reason=None):
    growth, context, memory, _models, scope = await current_retry_setup(database)
    models = LedgerModels(database)
    growth.models = models
    growth.generations.models = models
    await growth.organize('actor', scope, 1, GENERATION)
    request = models.submitted[0]
    await models.finish(request, status, code=code, diagnostic=diagnostic)
    if cancellation_reason is not None:
        await models.broker.ledger.request_cancel(request.attempt_id, reason=cancellation_reason)
    outcome = await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)
    return growth, context, memory, models, scope, request, outcome


async def request_review(growth, context, database, retry_key, request_id, repair_ref=None):
    body = {'client_request_id': request_id, 'expected_revision': (await state(database))['revision'],
            'retry_key': retry_key}
    if repair_ref is not None:
        body['repair_ref'] = repair_ref
    return await growth.request_review(context, body)


async def ledger_state(database):
    async with database.transaction(read_only=True) as connection:
        lineage = await (await connection.execute(
            'SELECT attempts,max_attempts,due_at,cancelled,source_withdrawn FROM runtime.recovery_lineages')).fetchone()
        attempts = await (await connection.execute(
            'SELECT attempt_id,status,error_code FROM runtime.recovery_attempts ORDER BY attempt_number')).fetchall()
        inputs = await (await connection.execute(
            'SELECT episode_id,revision,reviewed_revision,retry_key FROM companion.growth_inputs')).fetchall()
    return lineage, attempts, inputs


@pytest.mark.asyncio
async def test_old_waiting_without_runtime_due_returns_to_original_blocked(database):
    growth, context, _memory, models, scope, original, failure = await setup_original(database)
    assert failure['state'] == 'retry_blocked'
    before = await retry_row(database)
    lineage, attempts, inputs = await ledger_state(database)
    assert lineage['due_at'] is None and lineage['attempts'] == 1
    async with database.transaction() as connection:
        # Reproduce the old review transition: budget available but no Runtime due.
        await connection.execute("UPDATE companion.growth_retry_batches SET state='waiting',due_at=now(),explicit_retry=true")

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    row = await retry_row(database)
    assert outcome['state'] == 'retry_blocked' and outcome['reason'] == 'growth_recovery_due_unavailable'
    assert row['state'] == 'blocked' and row['last_error'] == 'invalid_upstream_response'
    assert row['history'] == before['history'] and row['batch']['rbi_review']['reason'] == outcome['reason']
    assert await ledger_state(database) == (lineage, attempts, inputs) and len(models.submitted) == 1

    await request_review(growth, context, database, failure['retry_key'], 'inspect-historical')
    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    row = await retry_row(database)
    assert outcome == {'state': 'review_stopped', 'reason': 'growth_failure_stage_unknown'}
    assert row['last_error'] == 'invalid_upstream_response' and row['batch']['rbi_review']['state'] == 'stopped'
    assert await ledger_state(database) == (lineage, attempts, inputs) and len(models.submitted) == 1


@pytest.mark.asyncio
async def test_legacy_explicit_wait_cannot_bypass_repair_evidence_even_with_due(database):
    growth, _context, _memory, models, scope, original, failure = await setup_original(database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.growth_retry_batches SET state='waiting',due_at=now(),explicit_retry=true")
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    row = await retry_row(database)
    lineage, attempts, _inputs = await ledger_state(database)
    assert outcome['state'] == 'retry_blocked' and outcome['reason'] == 'growth_review_evidence_required'
    assert row['state'] == 'blocked' and row['last_error'] == 'invalid_upstream_response'
    assert lineage['attempts'] == 1 and len(models.submitted) == 1
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'invalid_upstream_response'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('reusable', [False, None])
async def test_known_program_repair_uses_original_receipt_and_one_budgeted_successor(database, reusable):
    diagnostic = {'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': reusable}
    growth, context, _memory, models, scope, original, failure = await setup_original(
        database, code='model_local_error', diagnostic=diagnostic)
    before = await retry_row(database)
    before_lineage, before_attempts, before_inputs = await ledger_state(database)
    repair_ref = 'repair:models-local-normalizer-v1'
    await request_review(growth, context, database, failure['retry_key'], 'repair-a', repair_ref)
    queued = await retry_row(database)
    assert queued['state'] == 'blocked' and queued['batch']['rbi_review']['state'] == 'requested'
    assert (await ledger_state(database))[0]['due_at'] is None and len(models.submitted) == 1
    jobs = len(growth.jobs.items)
    await request_review(growth, context, database, failure['retry_key'], 'repair-a-again', repair_ref)
    assert (await retry_row(database))['revision'] == queued['revision'] and len(growth.jobs.items) == jobs

    await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    after = await retry_row(database)
    lineage, attempts, inputs = await ledger_state(database)
    assert len(models.submitted) == 2 and models.submitted[1].attempt_id != original.attempt_id
    assert attempts[0] == before_attempts[0] and attempts[1]['status'] == 'reserved'
    assert lineage['attempts'] == 2 and lineage['max_attempts'] == before_lineage['max_attempts']
    assert after['history'] == before['history'] and inputs == before_inputs
    assert after['batch'].get('output_repair_count', 0) == before['batch'].get('output_repair_count', 0)
    jobs = len(growth.jobs.items)
    await request_review(growth, context, database, failure['retry_key'], 'repair-a-active', repair_ref)
    assert len(models.submitted) == 2 and len(growth.jobs.items) == jobs


@pytest.mark.asyncio
@pytest.mark.parametrize('change,reason', [
    ('source', 'source_unavailable'), ('authorization', 'access_changed'),
    ('input', 'reflection_episode_revision_conflict'), ('cancel', 'explicit_cancel'),
    ('budget', 'recovery_budget_exhausted'),
])
async def test_repair_cannot_bypass_original_gates(database, change, reason):
    diagnostic = {'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': False}
    growth, context, _memory, models, scope, original, failure = await setup_original(
        database, code='model_local_error', diagnostic=diagnostic)
    await request_review(growth, context, database, failure['retry_key'], 'repair-with-gate', 'repair:fix-v1')
    if change == 'source':
        growth.companion.sources.validate_persistence = AsyncMock(return_value=False)
    elif change == 'authorization':
        growth.recovery.reader = AsyncMock(side_effect=AppError('access_changed', status=403))
    elif change == 'input':
        async with database.transaction() as connection:
            await connection.execute("UPDATE companion.growth_inputs SET revision=revision+1 WHERE episode_id='episode'")
    elif change == 'cancel':
        await models.broker.ledger.request_cancel(original.attempt_id, reason='explicit_cancel')
    else:
        async with database.transaction() as connection:
            await connection.execute('UPDATE runtime.recovery_lineages SET max_attempts=1')

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    row = await retry_row(database)
    lineage, attempts, _inputs = await ledger_state(database)
    assert outcome == {'state': 'review_stopped', 'reason': reason}
    assert row['state'] == 'blocked' and row['batch']['rbi_review']['reason'] == reason
    assert row['last_error'] == 'model_local_error' and len(models.submitted) == 1
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'model_local_error'}]
    assert lineage['attempts'] == 1 and lineage['due_at'] is None
    if change == 'cancel':
        assert lineage['cancelled']


@pytest.mark.asyncio
@pytest.mark.parametrize('diagnostic,repair_ref,reason', [
    ({}, 'repair:fix-v1', 'growth_failure_stage_unknown'),
    ({'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': False},
     None, 'growth_repair_ref_required'),
    ({'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': 'false'},
     'repair:fix-v1', 'growth_failure_diagnostic_incomplete'),
    ({'failure_stage': 'local_program', 'original_output_reusable': False},
     'repair:fix-v1', 'growth_failure_diagnostic_incomplete'),
    ({'failure_stage': 'protocol_decode', 'error_type': 'DecodeError', 'original_output_reusable': True},
     'repair:fix-v1', 'original_reparse_unavailable'),
])
async def test_unproven_failure_never_starts_a_new_attempt(database, diagnostic, repair_ref, reason):
    growth, context, _memory, models, scope, original, failure = await setup_original(database, diagnostic=diagnostic)
    await request_review(growth, context, database, failure['retry_key'], 'review-unproven', repair_ref)
    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    lineage, attempts, _inputs = await ledger_state(database)
    assert outcome == {'state': 'review_stopped', 'reason': reason}
    assert (await retry_row(database))['last_error'] == 'invalid_upstream_response'
    assert lineage['attempts'] == 1 and lineage['due_at'] is None and len(models.submitted) == 1
    assert attempts[0]['attempt_id'] == original.attempt_id and attempts[0]['status'] == 'failed'


@pytest.mark.asyncio
async def test_cancellation_after_admission_stops_before_successor_reservation(database):
    diagnostic = {'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': False}
    growth, context, _memory, models, scope, original, failure = await setup_original(
        database, code='model_local_error', diagnostic=diagnostic)
    await request_review(growth, context, database, failure['retry_key'], 'repair-cancel', 'repair:fix-v1')
    admitted = await process_review(growth.retries, await retry_row(database), GENERATION)
    assert admitted['state'] == 'review_admitted'

    await models.broker.ledger.request_cancel(original.attempt_id, reason='explicit_cancel')
    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    lineage, attempts, _inputs = await ledger_state(database)
    assert outcome == {'state': 'retry_blocked', 'error_code': 'explicit_cancel'}
    assert lineage['cancelled'] and lineage['attempts'] == 1 and len(models.submitted) == 1
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'model_local_error'}]


@pytest.mark.asyncio
async def test_cancel_between_observation_and_claim_cannot_reserve_a_successor(database):
    diagnostic = {'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': False}
    growth, context, _memory, models, scope, original, failure = await setup_original(
        database, code='model_local_error', diagnostic=diagnostic)
    await request_review(growth, context, database, failure['retry_key'], 'repair-race', 'repair:fix-v1')
    admitted = await process_review(growth.retries, await retry_row(database), GENERATION)
    assert admitted['state'] == 'review_admitted'
    original_prepare = growth.generations.prepare

    async def cancel_before_claim(*args, **kwargs):
        # Commit the Models cancellation after the last RPC read and before
        # Growth locks the authoritative facts and Runtime lineage.
        await models.broker.ledger.request_cancel(original.attempt_id, reason='explicit_cancel')
        return await original_prepare(*args, **kwargs)

    growth.generations.prepare = cancel_before_claim
    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    lineage, attempts, _inputs = await ledger_state(database)
    assert outcome == {'state': 'retry_blocked', 'error_code': 'recovery_model_cancelled'}
    assert lineage['cancelled'] and lineage['attempts'] == 1 and len(models.submitted) == 1
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'model_local_error'}]


@pytest.mark.asyncio
async def test_changed_original_diagnostic_invalidates_prior_repair_review(database):
    diagnostic = {'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': False}
    growth, context, _memory, models, scope, original, failure = await setup_original(
        database, code='model_local_error', diagnostic=diagnostic)
    await request_review(growth, context, database, failure['retry_key'], 'repair-changed', 'repair:fix-v1')
    admitted = await process_review(growth.retries, await retry_row(database), GENERATION)
    assert admitted['state'] == 'review_admitted'

    await models.broker.ledger.save_observation(original.attempt_id, diagnostic={'failure_stage': 'unknown'})
    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    lineage, attempts, _inputs = await ledger_state(database)
    assert outcome == {'state': 'retry_blocked', 'error_code': 'growth_original_receipt_changed'}
    assert lineage['attempts'] == 1 and len(models.submitted) == 1
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'model_local_error'}]


@pytest.mark.asyncio
async def test_unknown_original_is_not_a_repairable_failure(database):
    growth, context, _memory, models, _scope, original, failure = await setup_original(
        database, status='unknown', code='transport_unknown')
    before = await retry_row(database)
    with pytest.raises(AppError, match='reflection_retry_not_available'):
        await request_review(growth, context, database, failure['retry_key'], 'repair-unknown', 'repair:fix-v1')
    assert await retry_row(database) == before and len(models.submitted) == 1
    lineage, attempts, _inputs = await ledger_state(database)
    assert lineage['attempts'] == 1 and attempts[0]['attempt_id'] == original.attempt_id
    assert attempts[0]['status'] == 'unknown'


@pytest.mark.asyncio
async def test_review_signal_keeps_active_original_and_its_generation(database):
    growth, context, _memory, _models, scope = await current_retry_setup(database)
    models = LedgerModels(database)
    growth.models = models
    growth.generations.models = models
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0]
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.organize_state SET last_error='reflection_output_invalid'")
    before = await state(database)
    command = await growth.request_review(context, {
        'client_request_id': 'observe-active-original', 'expected_revision': before['revision']})
    after = await state(database)
    assert command.data['recovery'] == 'observe_original_attempt'
    assert after['active_batch'] == before['active_batch'] and after['generation_revision'] == before['generation_revision']
    assert len(models.submitted) == 1 and models.submitted[0].attempt_id == original.attempt_id


@pytest.mark.asyncio
async def test_real_input_revision_releases_new_material_but_not_old_repair(database):
    diagnostic = {'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': False}
    growth, context, memory, models, scope, original, failure = await setup_original(
        database, code='model_local_error', diagnostic=diagnostic)
    await request_review(growth, context, database, failure['retry_key'], 'repair-old-input', 'repair:fix-v1')
    revised = replace(memory.episode, revision=2)
    memory.episode, memory.episodes = revised, (revised,)
    await growth.changed(context, 'episode-revision-2', episode=revised, generation=GENERATION)

    stopped = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert stopped == {'state': 'review_stopped', 'reason': 'reflection_episode_revision_conflict'}
    old = await retry_row(database)
    assert old['state'] == 'blocked' and old['last_error'] == 'model_local_error'
    assert old['batch']['inputs'] == [['episode', 1]] and len(models.submitted) == 1

    await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)
    newer = models.submitted[-1]
    assert newer.attempt_id != original.attempt_id
    await models.finish(newer, 'succeeded', result=observed_result(newer, 'succeeded'))
    assert (await growth.organize('actor', scope, (await state(database))['revision'], GENERATION))['state'] == 'reviewed'
    async with database.transaction(read_only=True) as connection:
        item = await (await connection.execute(
            "SELECT revision,reviewed_revision FROM companion.growth_inputs WHERE episode_id='episode'")).fetchone()
        prior = await (await connection.execute(
            'SELECT status,error_code FROM runtime.recovery_attempts WHERE attempt_id=%s', (original.attempt_id,))).fetchone()
    assert item == {'revision': 2, 'reviewed_revision': 2}
    assert prior == {'status': 'failed', 'error_code': 'model_local_error'}


@pytest.mark.asyncio
async def test_429_remains_submitted_failure_and_uses_existing_budget(database):
    growth, _context, _memory, models, scope, original, failure = await setup_original(
        database, code='upstream_http_429', diagnostic={'failure_stage': 'unknown'})
    assert failure['state'] == 'retry_waiting'
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.growth_retry_batches SET due_at=now()-interval '1 second'")
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")
        await connection.execute('UPDATE runtime.recovery_dependencies SET cooldown_until=NULL')
    await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    async with database.transaction(read_only=True) as connection:
        recorded = await (await connection.execute(
            'SELECT count(*) AS n FROM runtime.recovery_not_sent WHERE attempt_id=%s', (original.attempt_id,))).fetchone()
    lineage, attempts, _inputs = await ledger_state(database)
    assert len(models.submitted) == 2 and lineage['attempts'] == 2 and recorded['n'] == 0
    assert attempts[0] == {'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'upstream_http_429'}


@pytest.mark.asyncio
async def test_consumer_interruption_uses_its_actual_cancelled_receipt(database):
    growth, _context, _memory, models, scope, original, failure = await setup_original(
        database, status='cancelled', code='consumer_interrupted', cancellation_reason='consumer_interrupted')
    assert failure['state'] == 'retry_waiting'
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.growth_retry_batches SET due_at=now()-interval '1 second'")
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")
        await connection.execute('UPDATE runtime.recovery_dependencies SET cooldown_until=NULL')

    await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    old_receipt = await models.status(original.attempt_id)
    lineage, attempts, _inputs = await ledger_state(database)
    assert (old_receipt.status, old_receipt.error_code, old_receipt.cancellation_reason) == (
        'cancelled', 'consumer_interrupted', 'consumer_interrupted')
    assert attempts[0] == {'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'consumer_interrupted'}
    assert len(models.submitted) == lineage['attempts'] == 2


@pytest.mark.asyncio
async def test_unconfirmed_cancelled_interruption_does_not_authorize_retry(database):
    growth, context, _memory, models, scope, original, failure = await setup_original(
        database, status='cancelled', code='consumer_interrupted')
    assert failure['state'] == 'retry_blocked'
    await request_review(growth, context, database, failure['retry_key'], 'inspect-cancelled')

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    lineage, attempts, _inputs = await ledger_state(database)
    assert outcome == {'state': 'review_stopped', 'reason': 'original_cancelled'}
    assert lineage['attempts'] == 1 and len(models.submitted) == 1
    assert attempts[0] == {'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'consumer_interrupted'}


@pytest.mark.asyncio
async def test_installation_change_cannot_qualify_a_repair(database):
    diagnostic = {'failure_stage': 'local_program', 'error_type': 'TypeError', 'original_output_reusable': False}
    growth, context, _memory, models, scope, original, failure = await setup_original(
        database, code='model_local_error', diagnostic=diagnostic)
    await request_review(growth, context, database, failure['retry_key'], 'repair-old-generation', 'repair:fix-v1')
    before = await retry_row(database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.installation_generation SET id='00000000-0000-4000-8000-000000000099'")
    with pytest.raises(AppError) as rejected:
        await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert rejected.value.code == 'job_generation_expired'
    assert await retry_row(database) == before and len(models.submitted) == 1
    _lineage, attempts, _inputs = await ledger_state(database)
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'failed', 'error_code': 'model_local_error'}]


@pytest.mark.asyncio
async def test_reparse_saved_success_without_spending_another_correction(database):
    growth, context, _memory, _models, scope = await current_retry_setup(database)
    models = LedgerModels(database)
    growth.models = models
    growth.generations.models = models
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0]
    saved_result = observed_result(original, 'succeeded')
    await models.finish(original, 'succeeded', result=saved_result)
    current = await state(database)
    result = await models.result(original.attempt_id, context=original.context)
    failure = await growth.retries.defer(current, current['active_batch'], 'reflection_output_invalid',
                                          result, diagnostic={'validator': 'old-parser'})
    async with database.transaction() as connection:
        # A prior parser rejected this saved success; its one correction is already spent.
        await connection.execute("UPDATE companion.growth_retry_batches SET state='blocked',due_at=NULL")
    before = await retry_row(database)
    assert before['batch']['output_repair_count'] == 1
    await request_review(growth, context, database, failure['retry_key'], 'reparse-fixed', 'repair:parser-v2')

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    row = await retry_row(database)
    lineage, attempts, inputs = await ledger_state(database)
    assert outcome['state'] == 'reviewed' and row['state'] == 'completed'
    assert len(models.submitted) == 1 and lineage['attempts'] == 1
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'succeeded', 'error_code': None}]
    assert row['batch']['output_repair_count'] == 1 and inputs[0]['reviewed_revision'] == 1


@pytest.mark.asyncio
async def test_still_invalid_saved_output_cannot_reset_the_one_correction_limit(database):
    growth, context, _memory, _models, scope = await current_retry_setup(database)
    models = LedgerModels(database)
    growth.models = models
    growth.generations.models = models
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0]
    bad = replace(observed_result(original, 'succeeded'), parts=(ModelPart('text', text='{'),))
    await models.finish(original, 'succeeded', result=bad)
    first = await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)
    await growth.retries.run('actor', scope, first['retry_key'], GENERATION)
    second = models.submitted[-1]
    assert second.attempt_id != original.attempt_id
    bad_again = replace(observed_result(second, 'succeeded'), parts=(ModelPart('text', text='{'),))
    await models.finish(second, 'succeeded', result=bad_again)
    await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)
    stopped = await retry_row(database)
    assert stopped['state'] == 'blocked' and stopped['batch']['output_repair_count'] == 1
    await request_review(growth, context, database, first['retry_key'], 'reparse-still-invalid', 'repair:parser-v2')

    await growth.retries.run('actor', scope, first['retry_key'], GENERATION)

    row = await retry_row(database)
    lineage, attempts, _inputs = await ledger_state(database)
    assert row['state'] == 'blocked' and row['batch']['output_repair_count'] == 1
    assert len(models.submitted) == 2 and len(attempts) == lineage['attempts'] == 2
