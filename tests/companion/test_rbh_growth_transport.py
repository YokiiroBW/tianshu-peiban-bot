"""Growth uses Runtime's connection budget and revives only proven old batches."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest

from tianshu.models.ledger import submission_digest
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from test_growth_output import note, state
from test_growth_retry import current_retry_setup, observed_result, retry_row
from test_rb_growth_async import GENERATION
from test_rb_growth_pure_retry import start_unknown, unknown_result
from test_rbf_growth_recovery import force_retry_due


async def setup_transport(database):
    growth, context, memory, models, scope = await current_retry_setup(database)
    await migrate(database, (Migration('rbh_growth_transport',
        files('tianshu.companion').joinpath('rbh_growth_transport.sql').read_text(encoding='utf-8')),))
    return growth, context, memory, models, scope


def not_sent_result(request, *, legacy=True, digest=None, certificate=False):
    result = observed_result(request, 'failed', error_code='transport_not_sent')
    diagnostic = {'category': 'not_sent', 'part_count': 0, 'text_bytes': 0,
                  'part_kinds': [], 'native_completed': False}
    if not legacy:
        diagnostic['transport'] = {
            'schema_version': 'rbh.transport.v1',
            'phase': 'tls' if certificate else 'connect',
            'error_class': 'ClientConnectorCertificateError' if certificate else 'ClientConnectorError',
            'os_errno': None, 'tls_reason': None, 'host': 'model.example', 'port': 443,
            'headers_sent': False, 'body_sent': False, 'evidence': 'confirmed_not_sent',
        }
    points = {'connect_start': 0.1, 'connect_end': None, 'headers_sent': None, 'body_sent': None,
              'response_headers': None, 'first_body_byte': None, 'first_valid_output': None,
              'read_complete': None}
    return replace(result, receipt=replace(result.receipt, dispatch='not_started',
        finished_at=datetime.now(UTC), data_generation=GENERATION, request_digest=digest,
        diagnostic=diagnostic, http_timing={'connection_reused': False, 'points_ms': points}))


async def budget(growth, database, row):
    async with database.transaction(read_only=True) as connection:
        return await growth.recovery_policy.computation_budget_on(connection, row['batch']['lineage_id'],
            owner='companion.growth', expected_version=row['batch']['recovery_version'])


async def four_failures(database, *, classify=True, confirmed=True, unknown_at=None,
                        latest_code='transport_not_sent', latest_unproven=False):
    growth, context, memory, models, scope = await setup_transport(database)
    policy = growth.recovery_policy
    original_note = policy.note_not_sent_on

    async def old_policy(connection, attempt_id, *, owner, expected_version, model_receipt):
        attempt = await (await connection.execute(
            'SELECT lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s', (attempt_id,))).fetchone()
        current = await policy.computation_budget_on(connection, attempt['lineage_id'],
                                                       owner=owner, expected_version=expected_version)
        if current['can_retry']:
            current['reason'] = 'not_confirmed_not_sent'
        return current

    if not classify:
        policy.note_not_sent_on = old_policy
    await growth.organize('actor', scope, 1, GENERATION)
    outcomes = []
    for index in range(1, 5):
        request = models.submitted[-1][0]
        if index == unknown_at:
            model_result = unknown_result(request)
        elif index == 4 and latest_unproven:
            model_result = observed_result(request, 'failed', error_code=latest_code)
            model_result = replace(model_result, receipt=replace(model_result.receipt, finished_at=datetime.now(UTC)))
        elif confirmed:
            model_result = not_sent_result(request)
        else:
            model_result = observed_result(request, 'failed', error_code='transport_not_sent')
        models.results[request.attempt_id] = model_result
        outcome = await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)
        outcomes.append(outcome)
        if index < 4:
            assert outcome['state'] == 'retry_waiting'
            await force_retry_due(database)
            if index == unknown_at:
                async with database.transaction() as connection:
                    await connection.execute(
                        "UPDATE runtime.recovery_computation_unknowns SET due_at=now()-interval '1 second'")
            await growth.retries.run('actor', scope, outcome['retry_key'], GENERATION)
    policy.note_not_sent_on = original_note
    return growth, context, memory, models, scope, outcomes


@pytest.mark.asyncio
async def test_four_new_proven_connections_allow_fifth_monotonic_attempt(database):
    growth, _context, _memory, models, scope, outcomes = await four_failures(database)
    row = await retry_row(database)
    assert outcomes[-1]['state'] == 'retry_waiting' and row['attempt_number'] == 4
    before = await budget(growth, database, row)
    assert (before['total_attempts'], before['computation_attempts'], before['connection_attempts']) == (4, 0, 4)
    originals = tuple(request.attempt_id for request, _ in models.submitted)
    await force_retry_due(database)

    await growth.retries.run('actor', scope, row['retry_key'], GENERATION)

    after = await retry_row(database)
    assert len(models.submitted) == 5 and after['attempt_number'] == 5
    assert models.submitted[-1][0].attempt_id not in originals
    effective = await budget(growth, database, after)
    assert (effective['total_attempts'], effective['computation_attempts'], effective['connection_attempts']) == (5, 1, 4)
    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute(
            'SELECT attempt_id,status,attempt_number FROM runtime.recovery_attempts ORDER BY attempt_number')).fetchall()
    assert [item['attempt_id'] for item in attempts[:4]] == list(originals)
    assert [item['status'] for item in attempts[:4]] == ['failed'] * 4


@pytest.mark.asyncio
async def test_historical_four_reconcile_original_batch_after_cooldown_then_review(database):
    growth, _context, _memory, models, scope, outcomes = await four_failures(database, classify=False)
    before = await retry_row(database)
    assert outcomes[-1]['state'] == 'retry_blocked' and before['attempt_number'] == 4
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()+interval '2 minutes'")
        saved = await (await connection.execute(
            'SELECT cooldown_until FROM runtime.recovery_dependencies')).fetchone()
    original_history = before['history']

    response = await growth.retries.reconcile_transport_waits(limit=1)

    row = await retry_row(database)
    assert response['inspected'] == response['scheduled'] == 1 and response['ineligible'] == 0
    assert row['state'] == 'waiting' and row['retry_key'] == before['retry_key']
    assert row['attempt_number'] == 4 and row['history'] == original_history
    assert row['due_at'] >= saved['cooldown_until'] and len(models.submitted) == 4
    assert (await budget(growth, database, row))['connection_attempts'] == 4
    assert (await growth.retries.reconcile_transport_waits(limit=1))['inspected'] == 0

    await force_retry_due(database)
    await growth.retries.run('actor', scope, row['retry_key'], GENERATION)
    successor = models.submitted[-1][0]
    assert len(models.submitted) == 5 and (await retry_row(database))['attempt_number'] == 5
    models.results[successor.attempt_id] = observed_result(successor, 'succeeded', notes=(note('reflection'),))
    assert (await growth.organize('actor', scope, (await state(database))['revision'], GENERATION))['state'] == 'reviewed'
    async with database.transaction(read_only=True) as connection:
        input_row = await (await connection.execute(
            "SELECT revision,reviewed_revision,retry_key FROM companion.growth_inputs WHERE episode_id='episode'")).fetchone()
    assert input_row == {'revision': 1, 'reviewed_revision': 1, 'retry_key': None}


@pytest.mark.asyncio
async def test_four_unproven_failures_remain_computation_exhausted(database):
    growth, _context, _memory, models, _scope, outcomes = await four_failures(database, confirmed=False)
    row = await retry_row(database)
    assert outcomes[-1]['state'] == 'retry_blocked'
    response = await growth.retries.reconcile_transport_waits(limit=1)
    after = await retry_row(database)

    assert response['scheduled'] == 0 and response['ineligible'] == 1
    assert after == row and len(models.submitted) == 4
    effective = await budget(growth, database, row)
    assert effective['computation_attempts'] == 4 and effective['connection_attempts'] == 0
    assert not effective['can_retry']


@pytest.mark.asyncio
async def test_history_unknown_is_preserved_and_counts_as_computation(database):
    growth, _context, _memory, models, _scope, outcomes = await four_failures(
        database, classify=False, unknown_at=2)
    row = await retry_row(database)
    assert outcomes[-1]['state'] == 'retry_blocked'
    unknown = models.submitted[1][0].attempt_id

    response = await growth.retries.reconcile_transport_waits(limit=1)

    assert response['scheduled'] == 1
    effective = await budget(growth, database, await retry_row(database))
    assert (effective['total_attempts'], effective['computation_attempts'], effective['connection_attempts']) == (4, 1, 3)
    async with database.transaction(read_only=True) as connection:
        original = await (await connection.execute(
            'SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s', (unknown,))).fetchone()
    assert original['status'] == 'unknown'


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['digest', 'intent', 'generation', 'unknown', 'missing', 'cancel', 'version', 'source', 'runtime_version'])
async def test_historical_reconcile_rejects_unproven_or_stale_inputs(database, problem):
    growth, _context, _memory, models, _scope, _outcomes = await four_failures(database, classify=False)
    row = await retry_row(database)
    request = models.submitted[-1][0]
    result = models.results[request.attempt_id]
    if problem == 'digest':
        models.results[request.attempt_id] = replace(result, receipt=replace(result.receipt, request_digest='wrong-digest'))
    elif problem == 'intent':
        models.results[request.attempt_id] = replace(result, receipt=replace(result.receipt, intent_id='wrong-intent'))
    elif problem == 'generation':
        models.results[request.attempt_id] = replace(result, receipt=replace(result.receipt,
            data_generation='00000000-0000-4000-8000-000000000099'))
    elif problem == 'unknown':
        models.results[request.attempt_id] = unknown_result(request)
    elif problem == 'missing':
        models.results.pop(request.attempt_id)
    elif problem == 'cancel':
        models.results[request.attempt_id] = replace(result, receipt=replace(result.receipt, cancellation_reason='explicit_cancel'))
    elif problem == 'version':
        async with database.transaction() as connection:
            await connection.execute("UPDATE companion.growth_inputs SET revision=revision+1 WHERE episode_id='episode'")
    elif problem == 'source':
        growth.companion.sources.validate_persistence = AsyncMock(return_value=False)
    else:
        async with database.transaction() as connection:
            await connection.execute("UPDATE runtime.recovery_lineages SET current_version='changed'")

    response = await growth.retries.reconcile_transport_waits(limit=1)

    assert response['scheduled'] == 0 and response['ineligible'] == 1
    assert (await retry_row(database))['state'] == 'blocked' and len(models.submitted) == 4
    assert response['changes'][0]['reason'] != 'scheduled'
    if problem in {'digest', 'intent', 'generation'}:
        assert response['changes'][0]['reason'] == 'growth_recovery_receipt_mismatch'
    if problem == 'runtime_version':
        assert response['changes'][0]['reason'] == 'recovery_version_changed'
    # The persistent discovery throttle avoids rescanning an unchanged blocked row every tick.
    assert (await growth.retries.reconcile_transport_waits(limit=1))['inspected'] == 0


@pytest.mark.asyncio
async def test_concurrent_scans_schedule_one_existing_batch(database):
    growth, _context, _memory, models, _scope, _outcomes = await four_failures(database, classify=False)
    row = await retry_row(database)
    jobs_before = len(growth.jobs.items)

    first, second = await asyncio.gather(
        growth.retries.reconcile_transport_waits(limit=1),
        growth.retries.reconcile_transport_waits(limit=1))

    assert first['scheduled'] + second['scheduled'] == 1
    assert len(growth.jobs.items) == jobs_before + 1 and len(models.submitted) == 4
    assert (await retry_row(database))['retry_key'] == row['retry_key']


@pytest.mark.asyncio
async def test_bounded_scans_move_past_ineligible_unchanged_rows(database):
    growth, _context, _memory, models, _scope, _outcomes = await four_failures(database, classify=False)
    # Synthetic blocked identities share an unproven receipt; no row can resume.
    original = models.submitted[-1][0]
    models.results.pop(original.attempt_id)
    original_key = (await retry_row(database))['retry_key']
    async with database.transaction() as connection:
        for index in range(4):
            await connection.execute(
                'INSERT INTO companion.growth_retry_batches(actor_id,scope_key,retry_key,batch,current_material_key,'
                'previous_material_key,attempt_number,state,due_at,last_error,waiting_on_original,explicit_retry,history,outcome,revision) '
                "SELECT actor_id,scope_key,retry_key||%s,batch,current_material_key,previous_material_key,attempt_number,"
                "state,due_at,last_error,waiting_on_original,explicit_retry,history,outcome,revision "
                'FROM companion.growth_retry_batches WHERE retry_key=%s',
                (':copy:'+str(index), original_key))
    seen = []
    for _ in range(5):
        response = await growth.retries.reconcile_transport_waits(limit=1)
        assert response['inspected'] == 1 and response['scheduled'] == 0
        seen.append(response['changes'][0]['retry_key'])
    assert len(set(seen)) == 5
    assert (await growth.retries.reconcile_transport_waits(limit=1))['inspected'] == 0


@pytest.mark.asyncio
async def test_certificate_not_sent_uses_runtime_bounded_connection_budget(database):
    growth, _context, _memory, models, scope = await setup_transport(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    models.results[original.attempt_id] = not_sent_result(
        original, legacy=False, certificate=True, digest=submission_digest(original))

    outcome = await growth.organize('actor', scope, 1, GENERATION)

    row = await retry_row(database)
    assert outcome['state'] == 'retry_waiting' and row['attempt_number'] == 1
    effective = await budget(growth, database, row)
    assert effective['can_retry'] and effective['connection_attempts'] == 1
    assert effective['computation_attempts'] == 0 and len(models.submitted) == 1
    assert effective['retry_at'] is not None and row['due_at'] == effective['retry_at']



@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['digest', 'intent', 'generation'])
async def test_new_failure_rejects_receipt_not_bound_to_original_request(database, problem):
    growth, _context, _memory, models, scope = await setup_transport(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    result = not_sent_result(original, legacy=False, digest=submission_digest(original))
    if problem == 'digest':
        receipt = replace(result.receipt, request_digest='incorrect')
    elif problem == 'intent':
        receipt = replace(result.receipt, intent_id='wrong-intent')
    else:
        receipt = replace(result.receipt, data_generation='00000000-0000-4000-8000-000000000099')
    models.results[original.attempt_id] = replace(result, receipt=receipt)

    with pytest.raises(AppError, match='growth_recovery_receipt_mismatch'):
        await growth.organize('actor', scope, 1, GENERATION)

    assert (await state(database))['active_batch']['material_key'] == original.intent_id
    assert len(models.submitted) == 1 and await retry_row(database) is None
    async with database.transaction(read_only=True) as connection:
        attempt = await (await connection.execute(
            'SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s', (original.attempt_id,))).fetchone()
        classified = await (await connection.execute(
            'SELECT count(*) AS n FROM runtime.recovery_not_sent WHERE attempt_id=%s', (original.attempt_id,))).fetchone()
    assert attempt['status'] == 'reserved' and classified['n'] == 0



@pytest.mark.asyncio
@pytest.mark.parametrize('missing_earlier', [False, True])
@pytest.mark.parametrize('latest_code', ['transport_not_sent', 'upstream_http_503'])
async def test_older_confirmed_credits_do_not_discount_unproven_latest(database, missing_earlier, latest_code):
    growth, _context, _memory, models, _scope, _outcomes = await four_failures(
        database, classify=False, latest_code=latest_code, latest_unproven=True)
    latest = models.submitted[-1][0]
    if missing_earlier:
        models.results.pop(models.submitted[0][0].attempt_id)

    response = await growth.retries.reconcile_transport_waits(limit=1)

    row = await retry_row(database)
    effective = await budget(growth, database, row)
    expected_connections = 2 if missing_earlier else 3
    assert response['scheduled'] == 1 and row['state'] == 'waiting'
    assert effective['connection_attempts'] == expected_connections
    assert effective['computation_attempts'] == 4 - expected_connections
    assert len(models.submitted) == 4
    async with database.transaction(read_only=True) as connection:
        latest_attempt = await (await connection.execute(
            'SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s', (latest.attempt_id,))).fetchone()
        latest_credit = await (await connection.execute(
            'SELECT count(*) AS n FROM runtime.recovery_not_sent WHERE attempt_id=%s', (latest.attempt_id,))).fetchone()
    assert latest_attempt['status'] == 'failed' and latest_credit['n'] == 0



@pytest.mark.asyncio
async def test_older_connection_evidence_does_not_revive_permanent_latest_failure(database):
    growth, _context, _memory, models, _scope, outcomes = await four_failures(
        database, classify=False, latest_code='invalid_upstream_response', latest_unproven=True)
    row = await retry_row(database)
    assert outcomes[-1]['state'] == 'retry_blocked'

    response = await growth.retries.reconcile_transport_waits(limit=1)

    assert response['scheduled'] == 0 and response['ineligible'] == 1
    assert response['changes'][0]['reason'] == 'original_failure_not_retryable'
    assert await retry_row(database) == row and len(models.submitted) == 4



@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['intent', 'digest', 'cancel_wrong_generation'])
async def test_unknown_status_must_bind_to_original_before_successor(database, problem):
    growth, _context, _memory, models, scope, original, failure = await start_unknown(database)
    receipt = models.results[original.attempt_id].receipt
    if problem == 'intent':
        receipt = replace(receipt, intent_id='wrong-intent')
    elif problem == 'digest':
        receipt = replace(receipt, request_digest='incorrect')
    else:
        receipt = replace(receipt, cancellation_reason='explicit_cancel',
                          data_generation='00000000-0000-4000-8000-000000000099')
    models.status = AsyncMock(return_value=receipt)
    await force_retry_due(database)

    result = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    row = await retry_row(database)
    assert result['state'] == 'waiting_original' and row['waiting_on_original']
    assert row['last_error'] == 'growth_recovery_receipt_mismatch'
    assert len(models.submitted) == 1
    async with database.transaction(read_only=True) as connection:
        attempt = await (await connection.execute(
            'SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s', (original.attempt_id,))).fetchone()
    assert attempt['status'] == 'unknown'


@pytest.mark.asyncio
async def test_scheduled_retry_rechecks_receipt_identity_before_reservation(database):
    growth, _context, _memory, models, scope = await setup_transport(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    valid = not_sent_result(original, legacy=False, digest=submission_digest(original))
    models.results[original.attempt_id] = valid
    waiting = await growth.organize('actor', scope, 1, GENERATION)
    models.results[original.attempt_id] = replace(valid, receipt=replace(valid.receipt, request_digest='incorrect'))
    await force_retry_due(database)

    result = await growth.retries.run('actor', scope, waiting['retry_key'], GENERATION)

    row = await retry_row(database)
    assert result['state'] == 'waiting_original' and not row['waiting_on_original']
    assert row['last_error'] == 'growth_recovery_receipt_mismatch'
    assert row['attempt_number'] == 1 and len(models.submitted) == 1
    models.results[original.attempt_id] = valid
    await force_retry_due(database)
    await growth.retries.run('actor', scope, waiting['retry_key'], GENERATION)
    assert len(models.submitted) == 2 and (await retry_row(database))['attempt_number'] == 2
