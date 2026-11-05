"""Temporary local/dependency waits preserve the original Growth work."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from psycopg import OperationalError
from psycopg_pool import PoolTimeout
import pytest

from tianshu.companion.growth import GrowthService
from tianshu.companion.module import create_module
from tianshu.runtime.database import DatabaseFailure
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from test_growth_output import note, state
from test_growth_retry import current_retry_setup, due, observed_result, retry_row
from test_rb_growth_async import GENERATION, setup_growth


async def force_retry_due(database):
    await due(database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")
        await connection.execute('UPDATE runtime.recovery_dependencies SET cooldown_until=NULL')


async def known_failed_attempt(database):
    growth, context, memory, models, scope = await current_retry_setup(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    models.results[original.attempt_id] = observed_result(original, 'failed', error_code='upstream_http_503')
    failure = await growth.organize('actor', scope, 1, GENERATION)
    assert failure['state'] == 'retry_waiting'
    await force_retry_due(database)
    return growth, context, memory, models, scope, original, failure


def test_only_reentrant_growth_jobs_opt_into_resource_recovery():
    async def handler(**_kwargs):
        return None

    growth = SimpleNamespace(organize=handler, retry_input=handler, consume_episode_change=handler,
        retries=SimpleNamespace(run=handler), observations=SimpleNamespace(replay=handler))
    registered = {}
    queue = SimpleNamespace(register=lambda name, _handler, **kwargs: registered.update({name: kwargs}))

    create_module(None, growth, None).register_tasks(queue, None)

    assert {name for name, options in registered.items() if options.get('resource_recovery')} == {
        'companion.organize', 'companion.episode_changed', 'companion.growth.retry',
        'companion.growth.input_retry'}
    assert not registered['companion.growth.statistics'].get('resource_recovery', False)


@pytest.mark.asyncio
async def test_database_pool_wait_is_not_reclassified_as_model_failure():
    error = PoolTimeout('business pool unavailable')
    error.database_failure = DatabaseFailure('database_pool_timeout', 'connection_acquire', 'business',
        'bot-background', None, True, True, False, datetime.now(UTC) + timedelta(seconds=10))
    growth = GrowthService.__new__(GrowthService)
    growth._organize = AsyncMock(side_effect=error)
    growth.retries = SimpleNamespace(defer=AsyncMock())

    with pytest.raises(PoolTimeout):
        await growth.organize('actor', 'scope', 1, GENERATION)

    growth.retries.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_dependency_wait_keeps_material_and_durable_wake(database):
    growth, context, memory, models, policy, scope = await setup_growth(database)
    retry_at = datetime.now(UTC) + timedelta(minutes=2)
    wait = RecoveryWait('recovery_dependency_probe_in_progress', retry_at, dependency='models:quota:quota')
    reserve = policy.reserve_attempt_on
    policy.reserve_attempt_on = AsyncMock(side_effect=wait)

    outcome = await growth.organize('actor', scope, 1, GENERATION)
    waiting = await state(database)
    assert outcome['state'] == 'waiting_dependency' and waiting['revision'] == 2
    assert waiting['active_batch'] is None and waiting['last_error'] == wait.reason
    assert waiting['blocked_material']['inputs'] == [['episode', 1]]
    assert datetime.fromisoformat(waiting['blocked_material']['retry_at']) == retry_at
    assert growth.jobs.items[-1].scheduled_at == retry_at and not models.submitted
    material_key = waiting['blocked_material']['material_key']
    async with database.transaction(read_only=True) as connection:
        count = await (await connection.execute('SELECT count(*) AS n FROM conversation.generation_requests')).fetchone()
    assert count['n'] == 0

    # An early duplicate cannot bypass the due time or reserve a new attempt.
    await growth.changed(context, 'duplicate', episode=memory.episode, generation=GENERATION)
    assert (await state(database))['revision'] == 2
    assert (await growth.organize('actor', scope, 2, GENERATION))['state'] == 'waiting_dependency'
    assert policy.reserve_attempt_on.await_count == 1 and not models.submitted

    policy.reserve_attempt_on = reserve
    expired = datetime.now(UTC) - timedelta(seconds=1)
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.organize_state SET blocked_material="
            "jsonb_set(blocked_material,'{retry_at}',to_jsonb(%s::text),false) WHERE actor_id=%s AND scope_key=%s",
            (expired.isoformat(), 'actor', scope))
    assert (await growth.organize('actor', scope, 2, GENERATION))['state'] == 'waiting'
    assert len(models.submitted) == 1 and (await state(database))['active_batch']['material_key'] == material_key


@pytest.mark.asyncio
async def test_new_episode_during_dependency_wait_reschedules_current_revision(database):
    growth, context, memory, _models, policy, scope = await setup_growth(database)
    retry_at = datetime.now(UTC) + timedelta(minutes=2)
    policy.reserve_attempt_on = AsyncMock(side_effect=RecoveryWait('recovery_dependency_cooling', retry_at))
    await growth.organize('actor', scope, 1, GENERATION)
    later = replace(memory.episode, episode_id='later', event_id='later', sequence=2, cursor='e:2')

    await growth.changed(context, 'event:2', episode=later, generation=GENERATION)

    current = await state(database)
    wake = growth.jobs.items[-1]
    assert current['revision'] == 3 and current['watermark'] == 'event:2'
    assert wake.payload['expected_revision'] == 3 and wake.scheduled_at >= retry_at
    assert current['blocked_material']['recovery'] == 'dependency_wait'


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['recovery_dependency_cooling', 'recovery_dependency_probe_in_progress'])
async def test_retry_dependency_wait_keeps_attempt_budget_and_resumes(database, reason):
    growth, _context, _memory, models, scope, original, failure = await known_failed_attempt(database)
    retry_at = datetime.now(UTC) + timedelta(minutes=2)
    policy = growth.recovery_policy
    reserve = policy.reserve_attempt_on
    policy.reserve_attempt_on = AsyncMock(side_effect=RecoveryWait(reason, retry_at, dependency='models:quota:quota'))
    original_row = await retry_row(database)

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    waiting = await retry_row(database)
    assert outcome['state'] == 'waiting_original' and waiting['state'] == 'waiting'
    assert waiting['last_error'] == reason and waiting['due_at'] == retry_at
    assert waiting['attempt_number'] == original_row['attempt_number'] == 1
    assert waiting['current_material_key'] == original_row['current_material_key'] and len(models.submitted) == 1
    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute(
            'SELECT attempt_id,status FROM runtime.recovery_attempts ORDER BY attempt_number')).fetchall()
        lineage = await (await connection.execute('SELECT attempts FROM runtime.recovery_lineages')).fetchone()
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'failed'}] and lineage['attempts'] == 1

    policy.reserve_attempt_on = reserve
    await force_retry_due(database)
    await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert len(models.submitted) == 2 and models.submitted[1][0].source_refs == original.source_refs


@pytest.mark.asyncio
async def test_claim_lost_does_not_update_domain_or_spend_budget(database):
    growth, _context, _memory, models, scope, _original, failure = await known_failed_attempt(database)
    retry_at = datetime.now(UTC) + timedelta(seconds=30)
    growth.recovery_policy.reserve_attempt_on = AsyncMock(
        side_effect=RecoveryWait('recovery_claim_lost', retry_at, dependency='models:quota:quota'))
    before, jobs_before = await retry_row(database), len(growth.jobs.items)

    with pytest.raises(RecoveryWait) as wait:
        await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    assert wait.value.reason == 'recovery_claim_lost'
    assert await retry_row(database) == before and len(growth.jobs.items) == jobs_before
    assert len(models.submitted) == 1


@pytest.mark.asyncio
async def test_unclaimed_lineage_records_observed_wait_without_reserving(database):
    growth, _context, _memory, models, scope, _original, failure = await known_failed_attempt(database)
    retry_at = datetime.now(UTC) + timedelta(minutes=2)
    policy = growth.recovery_policy
    policy.claim_lineage_due_on = AsyncMock(return_value=None)
    policy.inspect_wait_on = AsyncMock(return_value=RecoveryWait('recovery_not_due', retry_at))
    reserve = policy.reserve_attempt_on = AsyncMock(wraps=policy.reserve_attempt_on)

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    waiting = await retry_row(database)
    assert outcome['state'] == 'waiting_original' and waiting['state'] == 'waiting'
    assert waiting['last_error'] == 'recovery_not_due' and waiting['due_at'] == retry_at
    policy.inspect_wait_on.assert_awaited_once()
    reserve.assert_not_awaited()
    assert len(models.submitted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_type', [PoolTimeout, TimeoutError])
async def test_local_commit_retry_reuses_successful_original_result(database, failure_type):
    growth, _context, memory, models, _policy, scope = await setup_growth(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    result = observed_result(original, 'succeeded', notes=(note('reflection'),))
    models.results[original.attempt_id] = result
    validate = growth._validate_material

    async def local_failure(*_args, **_kwargs):
        raise failure_type('local commit temporarily unavailable')

    growth._validate_material = local_failure
    with pytest.raises(failure_type):
        await growth.organize('actor', scope, 1, GENERATION)
    preserved = await state(database)
    assert preserved['active_batch']['material_key'] == original.intent_id
    assert models.results[original.attempt_id] is result and len(models.submitted) == 1

    growth._validate_material = validate
    outcome = await growth.organize('actor', scope, preserved['revision'], GENERATION)
    assert outcome['state'] == 'reviewed' and len(models.submitted) == 1
    assert models.results[original.attempt_id] is result


@pytest.mark.asyncio
@pytest.mark.parametrize('available', [True, False])
async def test_unknown_local_commit_observes_original_before_any_wake(database, available):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    result = observed_result(original, 'succeeded', notes=(note('reflection'),))
    result = replace(result, receipt=replace(result.receipt, data_generation=GENERATION))
    models.results[original.attempt_id] = result
    models.reusable_result = AsyncMock(return_value=result) if available else AsyncMock(
        side_effect=AppError('model_result_not_reusable', status=409))
    original_validate = growth._validate_material

    async def commit_unknown(*_args, **_kwargs):
        error = OperationalError('connection lost during commit')
        error.database_failure = DatabaseFailure('database_commit_unknown', 'commit', 'business', 'bot-background',
                                                 None, False, False, True, None)
        raise error

    growth._validate_material = commit_unknown
    jobs_before = len(growth.jobs.items)
    if available:
        outcome = await growth.organize('actor', scope, 1, GENERATION)
        assert outcome == {'state': 'waiting_commit_recovery', 'attempt_id': original.attempt_id}
        assert len(growth.jobs.items) == jobs_before + 1
    else:
        with pytest.raises(OperationalError):
            await growth.organize('actor', scope, 1, GENERATION)
        assert len(growth.jobs.items) == jobs_before
    models.reusable_result.assert_awaited_once()
    assert len(models.submitted) == 1 and (await state(database))['active_batch']['material_key'] == original.intent_id

    if available:
        growth._validate_material = original_validate
        assert (await growth.organize('actor', scope, 1, GENERATION))['state'] == 'reviewed'
        assert len(models.submitted) == 1 and models.results[original.attempt_id] is result


@pytest.mark.asyncio
async def test_commit_reconciliation_does_not_reapply_reviewed_material(database):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    models.results[original.attempt_id] = observed_result(original, 'succeeded', notes=(note('reflection'),))
    assert (await growth.organize('actor', scope, 1, GENERATION))['state'] == 'reviewed'
    models.reusable_result = AsyncMock(side_effect=AssertionError('completed work must not be reused'))
    jobs_before = len(growth.jobs.items)

    outcome = await growth.recovery.reconcile_commit(
        'actor', scope, original.intent_id, (('episode', 1),), GENERATION)

    assert outcome['state'] == 'changed_since_original_result_read'
    models.reusable_result.assert_not_awaited()
    assert len(models.submitted) == 1 and len(growth.jobs.items) == jobs_before


@pytest.mark.asyncio
async def test_style_result_emits_growth_wake_on_same_commit(database):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    notifications = []

    async def signal(context, watermark, *, connection):
        notifications.append((context, watermark, connection))

    growth.on_projection_changed = signal
    await growth.organize('actor', scope, 1, GENERATION)
    request = models.submitted[0][0]
    models.results[request.attempt_id] = observed_result(request, 'succeeded', notes=(note('style'),))

    assert (await growth.organize('actor', scope, 1, GENERATION))['state'] == 'reviewed'
    assert len(notifications) == 1
    assert notifications[0][1].startswith('growth:' + scope + ':')
    assert notifications[0][2] is not None
