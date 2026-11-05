"""RPC rollback evidence must survive every Growth recovery path."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from psycopg import OperationalError
import pytest

from tianshu.runtime.database import DatabaseFailure
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.types import SourceRef
from test_growth_output import note, state
from test_growth_retry import current_retry_setup, observed_result, retry_row
from test_rb_growth_async import GENERATION, setup_growth
from test_rb_growth_pure_retry import unknown_result
from test_rbf_growth_recovery import force_retry_due, known_failed_attempt


def remote_wait(confirmed):
    wait = RecoveryWait('recovery_dependency_cooling', datetime.now(UTC) + timedelta(minutes=2),
                        dependency='models:quota:quota')
    wait.remote_rollback_confirmed = confirmed
    return wait


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [False, True, None])
async def test_profile_wait_needs_rollback_evidence_before_persisting(database, confirmed):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    wait = remote_wait(confirmed)
    models.generation_profile = AsyncMock(side_effect=wait)
    models.status = AsyncMock(return_value=None)
    before, jobs_before = await state(database), len(growth.jobs.items)

    if confirmed is False:
        with pytest.raises(RecoveryWait) as raised:
            await growth.organize('actor', scope, 1, GENERATION)
        assert raised.value is wait
        assert await state(database) == before and len(growth.jobs.items) == jobs_before
        original_request = models.generation_profile.await_args.args[0]
        models.status.assert_awaited_once_with(original_request.attempt_id)
    else:
        result = await growth.organize('actor', scope, 1, GENERATION)
        current = await state(database)
        assert result['state'] == 'waiting_dependency'
        assert current['blocked_material']['reason'] == wait.reason
        assert datetime.fromisoformat(current['blocked_material']['retry_at']) == wait.retry_at
        assert growth.jobs.items[-1].scheduled_at == wait.retry_at
        models.status.assert_not_awaited()
    assert not models.submitted


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [False, True])
async def test_frozen_request_wait_keeps_original_attempt_for_runtime(database, confirmed):
    growth, context, _memory, models, _policy, scope = await setup_growth(database)
    wait = remote_wait(confirmed)
    submit = models.submit
    models.submit = AsyncMock(side_effect=wait)
    models.status = AsyncMock(return_value=None)

    with pytest.raises(RecoveryWait) as raised:
        await growth.organize('actor', scope, 1, GENERATION)

    assert raised.value is wait
    request = models.submit.await_args.args[0]
    current = await state(database)
    assert current['last_error'] is None and current['blocked_material'] is None
    assert (await growth.generations.request(request.attempt_id, context)).attempt_id == request.attempt_id
    if confirmed is False:
        retry = await retry_row(database)
        assert current['active_batch'] is None and retry['waiting_on_original']
        assert retry['current_material_key'] == request.intent_id
        assert retry['batch']['control_observation']['remote_rollback_confirmed'] is False
        models.status.assert_awaited_once_with(request.attempt_id)
    else:
        assert current['active_batch']['material_key'] == request.intent_id
        models.status.assert_not_awaited()
        models.submit = submit
        result = await growth.organize('actor', scope, current['revision'], GENERATION)
        assert result['state'] == 'waiting'
        assert len(models.submitted) == 1 and models.submitted[0][0].attempt_id == request.attempt_id


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [False, True])
async def test_retry_profile_wait_preserves_attempt_and_original_row(database, confirmed):
    growth, _context, _memory, models, scope, original, failure = await known_failed_attempt(database)
    wait = remote_wait(confirmed)
    models.generation_profile = AsyncMock(side_effect=wait)
    models.status = AsyncMock(return_value=None)
    before, jobs_before = await retry_row(database), len(growth.jobs.items)

    if confirmed is False:
        with pytest.raises(RecoveryWait) as raised:
            await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
        assert raised.value is wait
        assert await retry_row(database) == before and len(growth.jobs.items) == jobs_before
        models.status.assert_awaited_once_with(original.attempt_id)
    else:
        result = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
        current = await retry_row(database)
        assert result['state'] == 'waiting_original' and current['due_at'] == wait.retry_at
        assert current['last_error'] == wait.reason and current['attempt_number'] == 1
        assert current['current_material_key'] == before['current_material_key']
        models.status.assert_not_awaited()
    assert len(models.submitted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [False, True])
async def test_original_status_wait_cannot_become_unknown_or_terminal(database, confirmed):
    growth, _context, _memory, models, scope = await current_retry_setup(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    models.results[original.attempt_id] = observed_result(original, 'unknown', error_code='transport_unknown')
    failure = await growth.organize('actor', scope, 1, GENERATION)
    await force_retry_due(database)
    wait = remote_wait(confirmed)
    models.status = AsyncMock(side_effect=wait)
    before, jobs_before = await retry_row(database), len(growth.jobs.items)

    if confirmed is False:
        with pytest.raises(RecoveryWait) as raised:
            await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
        assert raised.value is wait
        assert await retry_row(database) == before and len(growth.jobs.items) == jobs_before
        assert all(call.args == (original.attempt_id,) for call in models.status.await_args_list)
    else:
        result = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
        current = await retry_row(database)
        assert result['state'] == 'waiting_original'
        assert current['last_error'] == wait.reason and current['due_at'] == wait.retry_at
        assert current['attempt_number'] == before['attempt_number']
    assert len(models.submitted) == 1


@pytest.mark.asyncio
async def test_unclaimed_lineage_does_not_persist_unconfirmed_wait(database):
    growth, _context, _memory, models, scope, original, failure = await known_failed_attempt(database)
    wait = remote_wait(False)
    growth.recovery_policy.claim_lineage_due_on = AsyncMock(return_value=None)
    growth.recovery_policy.inspect_wait_on = AsyncMock(return_value=wait)
    models.status = AsyncMock(return_value=None)
    before, jobs_before = await retry_row(database), len(growth.jobs.items)

    with pytest.raises(RecoveryWait) as raised:
        await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    assert raised.value is wait
    assert await retry_row(database) == before and len(growth.jobs.items) == jobs_before
    models.status.assert_awaited_once_with(original.attempt_id)
    assert len(models.submitted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [False, True])
async def test_unknown_local_commit_keeps_original_remote_wait_evidence(database, confirmed):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    result = observed_result(original, 'succeeded', notes=(note('reflection'),))
    models.results[original.attempt_id] = replace(
        result, receipt=replace(result.receipt, data_generation=GENERATION))
    wait = remote_wait(confirmed)
    models.reusable_result = AsyncMock(side_effect=wait)

    async def commit_unknown(*_args, **_kwargs):
        error = OperationalError('connection lost during commit')
        error.database_failure = DatabaseFailure('database_commit_unknown', 'commit', 'business', 'bot-background',
                                                 None, False, False, True, None)
        raise error

    growth._validate_material = commit_unknown
    before, jobs_before = await state(database), len(growth.jobs.items)
    expected = RecoveryWait if confirmed is False else OperationalError
    with pytest.raises(expected) as raised:
        await growth.organize('actor', scope, 1, GENERATION)

    if confirmed is False:
        assert raised.value is wait
    assert await state(database) == before and len(growth.jobs.items) == jobs_before
    assert models.reusable_result.await_args.args[0] == original.attempt_id
    assert len(models.submitted) == 1


@pytest.mark.asyncio
async def test_local_rollback_denial_is_not_overridden_by_remote_confirmation(database):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    wait = remote_wait(True)
    wait.transaction_rollback_confirmed = False
    models.generation_profile = AsyncMock(side_effect=wait)
    models.status = AsyncMock(return_value=None)
    before, jobs_before = await state(database), len(growth.jobs.items)

    with pytest.raises(RecoveryWait) as raised:
        await growth.organize('actor', scope, 1, GENERATION)

    assert raised.value is wait
    assert await state(database) == before and len(growth.jobs.items) == jobs_before
    models.status.assert_awaited_once_with(models.generation_profile.await_args.args[0].attempt_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [False, True])
async def test_activated_retry_wait_resumes_same_batch_without_stale_cas(database, confirmed):
    growth, _context, _memory, models, scope, original, failure = await known_failed_attempt(database)
    wait = remote_wait(confirmed)
    submit = models.submit
    models.submit = AsyncMock(side_effect=wait)
    models.status = AsyncMock(return_value=None)

    with pytest.raises(RecoveryWait) as raised:
        await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    assert raised.value is wait
    request = models.submit.await_args.args[0]
    current, retry = await state(database), await retry_row(database)
    assert retry['current_material_key'] == request.intent_id
    assert request.attempt_id != original.attempt_id
    if confirmed is False:
        assert current['active_batch'] is None
        assert retry['state'] == 'waiting' and retry['waiting_on_original']
        assert retry['batch']['control_observation']['remote_rollback_confirmed'] is False
        models.status.assert_awaited_once_with(request.attempt_id)
    else:
        assert current['active_batch']['material_key'] == request.intent_id and retry['state'] == 'active'
        models.status.assert_not_awaited()
        models.submit = submit
        result = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
        assert result['state'] == 'waiting'
        assert len(models.submitted) == 2 and models.submitted[-1][0].attempt_id == request.attempt_id


@pytest.mark.asyncio
async def test_second_result_read_does_not_erase_unconfirmed_wait(database):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    wait = remote_wait(False)
    models.result = AsyncMock(side_effect=[AppError('model_rpc_unavailable', status=503), wait])
    models.status = AsyncMock(return_value=None)
    before, jobs_before = await state(database), len(growth.jobs.items)

    with pytest.raises(RecoveryWait) as raised:
        await growth.organize('actor', scope, before['revision'], GENERATION)

    assert raised.value is wait
    assert (await state(database))['active_batch'] is None
    retry = await retry_row(database)
    assert retry['current_material_key'] == original.intent_id and retry['waiting_on_original']
    assert len(growth.jobs.items) > jobs_before
    models.status.assert_awaited_once_with(original.attempt_id)
    assert len(models.submitted) == 1



@pytest.mark.asyncio
async def test_control_unknown_yields_new_material_and_applies_late_original_historically(database):
    growth, context, memory, models, scope = await current_retry_setup(database)
    wait = remote_wait(False)
    submit = models.submit
    failed_submit = models.submit = AsyncMock(side_effect=wait)
    with pytest.raises(RecoveryWait) as raised:
        await growth.organize('actor', scope, 1, GENERATION)
    assert raised.value is wait
    original = failed_submit.await_args.args[0]
    models.submit = submit

    retry = await retry_row(database)
    assert retry['waiting_on_original'] and retry['batch']['control_observation']['remote_rollback_confirmed'] is False
    async with database.transaction(read_only=True) as connection:
        physical = await (await connection.execute(
            'SELECT status,error_code FROM runtime.recovery_attempts WHERE attempt_id=%s',
            (original.attempt_id,))).fetchone()
        observations = await (await connection.execute(
            'SELECT count(*) AS n FROM runtime.recovery_attempt_observations WHERE attempt_id=%s',
            (original.attempt_id,))).fetchone()
    assert physical == {'status': 'reserved', 'error_code': None} and observations['n'] == 0

    # Even an authoritative finished unknown stays an observation of this ID;
    # it does not authorize a successor for this control-uncertain batch.
    models.results[original.attempt_id] = unknown_result(original)
    await force_retry_due(database)
    assert (await growth.retries.run('actor', scope, retry['retry_key'], GENERATION))['state'] == 'waiting_original'
    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute(
            'SELECT attempt_id,status FROM runtime.recovery_attempts ORDER BY attempt_number')).fetchall()
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'reserved'}]
    assert failed_submit.await_count == 1 and not models.submitted

    later = replace(memory.episode, episode_id='later', event_id='later', sequence=2, cursor='e:2',
        occurred_at=memory.episode.occurred_at + timedelta(minutes=1), source_refs=(SourceRef('later', 1),))
    memory.episodes += (later,)
    await growth.changed(context, 'event:2', episode=later, generation=GENERATION)
    assert (await growth.organize('actor', scope, (await state(database))['revision'], GENERATION))['state'] == 'waiting'
    new_request = models.submitted[0][0]
    assert new_request.attempt_id != original.attempt_id
    models.results[new_request.attempt_id] = observed_result(new_request, 'succeeded')
    assert (await growth.organize('actor', scope, (await state(database))['revision'], GENERATION))['state'] == 'reviewed'
    assert (await state(database))['cursor'] == 'e:2'

    late = observed_result(original, 'succeeded', notes=(note('reflection'),))
    models.results[original.attempt_id] = replace(
        late, receipt=replace(late.receipt, data_generation=GENERATION, finished_at=datetime.now(UTC)))
    await force_retry_due(database)
    result = await growth.retries.run('actor', scope, retry['retry_key'], GENERATION)

    assert result['state'] == 'reviewed' and (await state(database))['cursor'] == 'e:2'
    assert (await retry_row(database))['outcome'] == 'historical_only'
    assert failed_submit.await_count == 1 and len(models.submitted) == 1
    async with database.transaction(read_only=True) as connection:
        physical = await (await connection.execute(
            'SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s',
            (original.attempt_id,))).fetchone()
    assert physical['status'] == 'succeeded'


@pytest.mark.asyncio
async def test_control_unknown_with_local_commit_uncertain_keeps_active_batch(database):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    wait = remote_wait(False)
    wait.transaction_rollback_confirmed = False
    models.submit = AsyncMock(side_effect=wait)
    models.status = AsyncMock(return_value=None)

    with pytest.raises(RecoveryWait) as raised:
        await growth.organize('actor', scope, 1, GENERATION)

    request = models.submit.await_args.args[0]
    assert raised.value is wait and (await state(database))['active_batch']['material_key'] == request.intent_id
    assert await retry_row(database) is None
    models.status.assert_awaited_once_with(request.attempt_id)


@pytest.mark.asyncio
async def test_control_observation_only_blocks_on_real_failure_and_cannot_retry(database):
    growth, context, _memory, models, scope = await current_retry_setup(database)
    wait = remote_wait(False)
    models.submit = AsyncMock(side_effect=wait)
    with pytest.raises(RecoveryWait):
        await growth.organize('actor', scope, 1, GENERATION)
    original = models.submit.await_args.args[0]
    retry = await retry_row(database)
    frozen = await growth.generations.request(original.attempt_id, context)
    assert frozen is not None
    assert frozen.attempt_id == original.attempt_id and frozen.intent_id == original.intent_id
    assert frozen.data_generation == original.data_generation == GENERATION
    async with database.transaction(read_only=True) as connection:
        attempt = await (await connection.execute(
            'SELECT a.status,a.lineage_id,l.data_generation FROM runtime.recovery_attempts a '
            'JOIN runtime.recovery_lineages l ON l.lineage_id=a.lineage_id WHERE a.attempt_id=%s',
            (original.attempt_id,))).fetchone()
    assert attempt == {'status': 'reserved', 'lineage_id': retry['batch']['lineage_id'],
                       'data_generation': GENERATION}
    failure = observed_result(original, 'failed', error_code='upstream_http_503')
    models.results[original.attempt_id] = replace(
        failure, receipt=replace(failure.receipt, data_generation=GENERATION, finished_at=datetime.now(UTC)))
    receipt = models.results[original.attempt_id].receipt
    assert (receipt.attempt_id, receipt.intent_id, receipt.data_generation, receipt.status) == (
        frozen.attempt_id, frozen.intent_id, GENERATION, 'failed')
    await force_retry_due(database)

    result = await growth.retries.run('actor', scope, retry['retry_key'], GENERATION)

    assert result['state'] == 'retry_blocked'
    final = await retry_row(database)
    assert final['state'] == 'blocked' and final['last_error'] == 'upstream_http_503'
    assert final['history'][-1]['status'] == 'failed'
    assert models.submit.await_count == 1 and (await state(database))['active_batch'] is None
    verdict = await growth.operations.inspect(None, {
        'actor_id': 'actor', 'scope_key': scope, 'retry_key': retry['retry_key']})
    assert verdict['eligible'] is False
    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute(
            'SELECT attempt_id,status FROM runtime.recovery_attempts ORDER BY attempt_number')).fetchall()
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'failed'}]


@pytest.mark.asyncio
async def test_repeated_unconfirmed_control_read_only_reschedules_observation(database):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    first = remote_wait(False)
    models.submit = AsyncMock(side_effect=first)
    with pytest.raises(RecoveryWait):
        await growth.organize('actor', scope, 1, GENERATION)
    original = models.submit.await_args.args[0]
    retry = await retry_row(database)
    await force_retry_due(database)
    second = remote_wait(False)
    models.result = AsyncMock(side_effect=second)
    models.status = AsyncMock(return_value=None)
    jobs_before = len(growth.jobs.items)

    with pytest.raises(RecoveryWait) as raised:
        await growth.retries.run('actor', scope, retry['retry_key'], GENERATION)

    assert raised.value is second and len(growth.jobs.items) == jobs_before + 1
    current = await retry_row(database)
    assert current['waiting_on_original'] and current['due_at'] > datetime.now(UTC)
    assert current['current_material_key'] == original.intent_id
    models.status.assert_awaited_once_with(original.attempt_id)
    assert models.submit.await_count == 1 and (await state(database))['active_batch'] is None



@pytest.mark.asyncio
@pytest.mark.parametrize('local_rollback', [False, True])
async def test_control_isolation_requires_safe_local_transaction(database, local_rollback):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    wait = remote_wait(False)
    wait.transaction_rollback_confirmed = local_rollback
    models.submit = AsyncMock(side_effect=wait)
    models.status = AsyncMock(return_value=None)

    with pytest.raises(RecoveryWait) as raised:
        await growth.organize('actor', scope, 1, GENERATION)

    request = models.submit.await_args.args[0]
    current = await state(database)
    assert raised.value is wait
    if local_rollback:
        retry = await retry_row(database)
        assert current['active_batch'] is None and retry['waiting_on_original']
        assert retry['batch']['control_observation']['transaction_rollback_confirmed'] is True
    else:
        assert current['active_batch']['material_key'] == request.intent_id
        assert await retry_row(database) is None
    models.status.assert_awaited_once_with(request.attempt_id)


@pytest.mark.asyncio
async def test_uncertain_isolation_commit_reads_actual_owner_and_preserves_wait(database):
    growth, _context, _memory, models, _policy, scope = await setup_growth(database)
    wait = remote_wait(False)
    models.submit = AsyncMock(side_effect=wait)
    models.status = AsyncMock(return_value=None)
    error = OperationalError('connection lost during isolation commit')
    error.database_failure = DatabaseFailure('database_commit_unknown', 'commit', 'business', 'bot-background',
                                             None, False, False, True, None)
    growth.retries.defer = AsyncMock(side_effect=error)
    growth.retries.observe_control_owner = AsyncMock(wraps=growth.retries.observe_control_owner)

    with pytest.raises(RecoveryWait) as raised:
        await growth.organize('actor', scope, 1, GENERATION)

    request = models.submit.await_args.args[0]
    assert raised.value is wait and raised.value.__cause__ is error
    assert growth.retries.observe_control_owner.await_count == 2
    assert (await state(database))['active_batch']['material_key'] == request.intent_id
    assert await retry_row(database) is None
    models.status.assert_awaited_once_with(request.attempt_id)
