"""Live receipt accounting honors the frozen material's current authority."""

from unittest.mock import AsyncMock

import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from test_growth_output import state
from test_growth_retry import retry_row
from test_rb_growth_async import GENERATION
from test_rb_growth_pure_retry import start_unknown
from test_rbf_growth_recovery import force_retry_due
from test_rbf_growth_remote_wait import remote_wait
from test_rbh_growth_transport import not_sent_result, setup_transport


def revoke_after_read(growth, models):
    """Change source authority after Models returns, before the domain commit."""
    read = models.result

    async def result(attempt_id, *, context):
        observed = await read(attempt_id, context=context)
        growth.companion.sources.validate_persistence = AsyncMock(return_value=False)
        return observed

    models.result = result


async def attempt_and_credit(database, attempt_id):
    async with database.transaction(read_only=True) as connection:
        attempt = await (await connection.execute(
            'SELECT status,error_code,observation_ref FROM runtime.recovery_attempts WHERE attempt_id=%s',
            (attempt_id,))).fetchone()
        credit = await (await connection.execute(
            'SELECT count(*) AS n FROM runtime.recovery_not_sent WHERE attempt_id=%s', (attempt_id,))).fetchone()
    return attempt, credit['n']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['source', 'authorization', 'input_version'])
async def test_live_failure_archives_after_material_changes_without_credit(database, change):
    growth, _context, _memory, models, scope = await setup_transport(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    models.results[original.attempt_id] = not_sent_result(original)
    growth.recovery_policy.note_not_sent_on = AsyncMock(wraps=growth.recovery_policy.note_not_sent_on)
    if change == 'source':
        revoke_after_read(growth, models)
    elif change == 'authorization':
        read = models.result

        async def deny_after_read(attempt_id, *, context):
            observed = await read(attempt_id, context=context)
            growth.companion.identity.refresh_scheduled_context = AsyncMock(
                side_effect=AppError('access_changed', status=409))
            return observed

        models.result = deny_after_read
    else:
        read = models.result

        async def change_input_after_read(attempt_id, *, context):
            observed = await read(attempt_id, context=context)
            async with database.transaction() as connection:
                await connection.execute(
                    "UPDATE companion.growth_inputs SET revision=revision+1 WHERE episode_id='episode'")
            return observed

        models.result = change_input_after_read

    outcome = await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)

    row = await retry_row(database)
    attempt, credit = await attempt_and_credit(database, original.attempt_id)
    assert outcome['state'] == 'retry_blocked' and row['state'] == 'blocked'
    assert outcome['error_code'] == {
        'source': 'source_unavailable', 'authorization': 'access_changed',
        'input_version': 'reflection_episode_revision_conflict',
    }[change]
    assert row['history'][-1]['error_code'] == 'transport_not_sent'
    assert attempt['status'] == 'failed' and attempt['error_code'] == 'transport_not_sent'
    assert credit == 0 and len(models.submitted) == 1
    growth.recovery_policy.note_not_sent_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_definitive_unknown_failure_archives_after_source_revocation(database):
    growth, _context, _memory, models, scope, original, failure = await start_unknown(database)
    models.results[original.attempt_id] = not_sent_result(original)
    revoke_after_read(growth, models)
    growth.recovery_policy.note_not_sent_on = AsyncMock(wraps=growth.recovery_policy.note_not_sent_on)
    await force_retry_due(database)

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    row = await retry_row(database)
    attempt, credit = await attempt_and_credit(database, original.attempt_id)
    assert outcome['state'] == 'retry_blocked' and outcome['error_code'] == 'source_unavailable'
    assert row['state'] == 'blocked' and not row['waiting_on_original']
    assert row['history'][-1]['error_code'] == 'transport_not_sent'
    assert attempt['status'] == 'failed' and attempt['error_code'] == 'transport_not_sent'
    assert attempt['observation_ref'] == 'models:' + original.attempt_id
    assert credit == 0 and len(models.submitted) == 1
    growth.recovery_policy.note_not_sent_on.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('withdrawal', ['source', 'invalidated_request'])
async def test_withdrawn_unknown_keeps_observing_original(database, withdrawal):
    growth, _context, _memory, models, scope, original, failure = await start_unknown(database)
    growth.recovery_policy.note_not_sent_on = AsyncMock(wraps=growth.recovery_policy.note_not_sent_on)
    growth.recovery_policy.record_computation_unknown_on = AsyncMock(
        wraps=growth.recovery_policy.record_computation_unknown_on)
    if withdrawal == 'source':
        revoke_after_read(growth, models)
    else:
        async with database.transaction() as connection:
            await connection.execute(
                "UPDATE conversation.generation_requests SET state='invalidated',request=NULL WHERE attempt_id=%s",
                (original.attempt_id,))
    await force_retry_due(database)

    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)

    row = await retry_row(database)
    attempt, credit = await attempt_and_credit(database, original.attempt_id)
    assert outcome['state'] == 'waiting_original' and row['waiting_on_original']
    assert row['state'] == 'waiting' and row['last_error'] == 'source_unavailable'
    assert attempt['status'] == 'unknown' and credit == 0 and len(models.submitted) == 1
    growth.recovery_policy.note_not_sent_on.assert_not_awaited()
    growth.recovery_policy.record_computation_unknown_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidated_active_request_yields_unknown_for_observation(database):
    growth, _context, _memory, models, scope = await setup_transport(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE conversation.generation_requests SET state='invalidated',request=NULL WHERE attempt_id=%s",
            (original.attempt_id,))
    growth.recovery_policy.note_not_sent_on = AsyncMock(wraps=growth.recovery_policy.note_not_sent_on)

    outcome = await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)

    row = await retry_row(database)
    attempt, credit = await attempt_and_credit(database, original.attempt_id)
    assert outcome['state'] == 'retry_waiting' and outcome['error_code'] == 'source_unavailable'
    assert row['state'] == 'waiting' and row['waiting_on_original']
    assert attempt['status'] == 'unknown' and credit == 0 and len(models.submitted) == 1
    growth.recovery_policy.note_not_sent_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_observation_failure_archives_after_source_revocation(database):
    growth, _context, _memory, models, scope = await setup_transport(database)
    models.submit = AsyncMock(side_effect=remote_wait(False))
    models.status = AsyncMock(return_value=None)
    with pytest.raises(RecoveryWait):
        await growth.organize('actor', scope, 1, GENERATION)
    original = models.submit.await_args.args[0]
    models.results[original.attempt_id] = not_sent_result(original)
    revoke_after_read(growth, models)
    growth.recovery_policy.note_not_sent_on = AsyncMock(wraps=growth.recovery_policy.note_not_sent_on)
    row = await retry_row(database)
    await force_retry_due(database)

    outcome = await growth.retries.run('actor', scope, row['retry_key'], GENERATION)

    attempt, credit = await attempt_and_credit(database, original.attempt_id)
    row = await retry_row(database)
    assert outcome['state'] == 'retry_blocked' and outcome['error_code'] == 'source_unavailable'
    assert row['history'][-1]['error_code'] == 'transport_not_sent'
    assert attempt['status'] == 'failed' and attempt['error_code'] == 'transport_not_sent'
    assert credit == 0 and models.submit.await_count == 1
    growth.recovery_policy.note_not_sent_on.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['source', 'runtime_version'])
async def test_late_superseded_failure_archives_without_credit(database, change):
    growth, _context, _memory, models, scope, original, failure = await start_unknown(database)
    old_batch = (await retry_row(database))['batch']
    await force_retry_due(database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_computation_unknowns SET due_at=now()-interval '1 second'")
    await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert len(models.submitted) == 2
    successor = models.submitted[-1][0]
    models.results[original.attempt_id] = not_sent_result(original)
    growth.recovery_policy.note_not_sent_on = AsyncMock(wraps=growth.recovery_policy.note_not_sent_on)
    if change == 'source':
        growth.companion.sources.validate_persistence = AsyncMock(return_value=False)
    else:
        async with database.transaction() as connection:
            await connection.execute("UPDATE runtime.recovery_lineages SET current_version='new-version'")

    outcome = await growth.retries.defer({'actor_id': 'actor', 'scope_key': scope}, old_batch,
                                         'transport_not_sent', models.results[original.attempt_id],
                                         frozen_request=original)

    attempt, credit = await attempt_and_credit(database, original.attempt_id)
    successor_attempt, _ = await attempt_and_credit(database, successor.attempt_id)
    assert outcome['state'] == 'changed_since_original_result_read'
    assert attempt['status'] == 'failed' and attempt['error_code'] == 'transport_not_sent'
    assert attempt['observation_ref'] == 'models:' + original.attempt_id
    assert successor_attempt['status'] == 'reserved' and credit == 0 and len(models.submitted) == 2
    if change == 'source':
        growth.recovery_policy.note_not_sent_on.assert_not_awaited()
    else:
        growth.recovery_policy.note_not_sent_on.assert_awaited_once()
