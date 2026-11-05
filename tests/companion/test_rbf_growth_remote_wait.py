"""RPC rollback evidence must survive every Growth recovery path."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from psycopg import OperationalError
import pytest

from tianshu.runtime.database import DatabaseFailure
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from test_growth_output import note, state
from test_growth_retry import current_retry_setup, observed_result, retry_row
from test_rb_growth_async import GENERATION, setup_growth
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
    assert current['active_batch']['material_key'] == request.intent_id
    assert current['last_error'] is None and current['blocked_material'] is None
    assert (await growth.generations.request(request.attempt_id, context)).attempt_id == request.attempt_id
    if confirmed is False:
        models.status.assert_awaited_once_with(request.attempt_id)
    else:
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
    assert current['active_batch']['material_key'] == request.intent_id
    assert retry['state'] == 'active' and retry['current_material_key'] == request.intent_id
    assert request.attempt_id != original.attempt_id
    if confirmed is False:
        models.status.assert_awaited_once_with(request.attempt_id)
    else:
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
    assert await state(database) == before and len(growth.jobs.items) == jobs_before
    models.status.assert_awaited_once_with(original.attempt_id)
    assert len(models.submitted) == 1
