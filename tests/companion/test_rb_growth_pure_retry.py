"""Growth retries only a confirmed finished pure computation on the same lineage."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.companion.reflection_output import frozen_episodes
from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef
from test_growth_output import note, state
from test_growth_retry import current_retry_setup, due, observed_result, retry_row
from test_rb_growth_async import GENERATION


def unknown_result(request):
    result = observed_result(request, 'unknown', error_code='transport_unknown')
    return replace(result, receipt=replace(result.receipt, finished_at=datetime.now(UTC), data_generation=GENERATION))


async def make_due(database, attempt_id=None):
    await due(database)
    async with database.transaction() as connection:
        if attempt_id is None:
            await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")
        else:
            await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' "
                                     "WHERE lineage_id=(SELECT lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s)",
                                     (attempt_id,))


async def start_unknown(database, *, status=None):
    growth, context, memory, models, scope = await current_retry_setup(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    models.results[original.attempt_id] = unknown_result(original)

    async def live_status(attempt_id):
        return models.results[attempt_id].receipt

    models.status = status or live_status
    failure = await growth.organize('actor', scope, 1, GENERATION)
    return growth, context, memory, models, scope, original, failure


@pytest.mark.asyncio
async def test_finished_unknown_uses_fixed_due_independent_successor_and_original_receipt(database):
    growth, _context, _memory, models, scope, original, failure = await start_unknown(database)
    original_result = models.results[original.attempt_id]
    async with database.transaction(read_only=True) as connection:
        fixed = await (await connection.execute(
            'SELECT due_at FROM runtime.recovery_computation_unknowns WHERE attempt_id=%s',
            (original.attempt_id,))).fetchone()
        frozen_before = await (await connection.execute(
            'SELECT request FROM conversation.generation_requests WHERE attempt_id=%s',
            (original.attempt_id,))).fetchone()
    assert failure['state'] == 'retry_waiting' and fixed is not None

    # Re-observation before the persisted due date neither submits nor delays it.
    await due(database)
    waiting = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert waiting['state'] == 'waiting_original' and waiting['due_at'] == fixed['due_at']
    assert len(models.submitted) == 1 and (await retry_row(database))['due_at'] == fixed['due_at']

    calls = []
    async def live_status(attempt_id):
        calls.append(attempt_id)
        return replace(models.results[attempt_id].receipt)
    models.status = live_status
    await make_due(database)
    await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    successor = models.submitted[1][0]
    assert calls == [original.attempt_id, original.attempt_id]
    assert successor.attempt_id != original.attempt_id
    assert frozen_episodes(successor) == frozen_episodes(original)
    assert successor.source_refs == original.source_refs and successor.data_generation == GENERATION

    models.results[successor.attempt_id] = observed_result(successor, 'succeeded', notes=(note('reflection'),))
    assert (await growth.organize('actor', scope, (await state(database))['revision'], GENERATION))['state'] == 'reviewed'
    async with database.transaction(read_only=True) as connection:
        frozen_after = await (await connection.execute(
            'SELECT request FROM conversation.generation_requests WHERE attempt_id=%s',
            (original.attempt_id,))).fetchone()
        attempts = await (await connection.execute(
            'SELECT attempt_id,status,supersedes_attempt_id FROM runtime.recovery_attempts ORDER BY attempt_number')).fetchall()
        lineage = await (await connection.execute(
            'SELECT attempts,max_attempts,recovery_kind FROM runtime.recovery_lineages')).fetchone()
        reflections = await (await connection.execute(
            'SELECT attempt_id,episode_ids,source_refs FROM companion.learning_reflections')).fetchall()
    assert models.results[original.attempt_id] is original_result and frozen_after == frozen_before
    assert attempts == [
        {'attempt_id': original.attempt_id, 'status': 'unknown', 'supersedes_attempt_id': None},
        {'attempt_id': successor.attempt_id, 'status': 'succeeded', 'supersedes_attempt_id': original.attempt_id},
    ]
    assert lineage == {'attempts': 2, 'max_attempts': 4, 'recovery_kind': 'pure_computation'}
    assert len(reflections) == 1 and reflections[0]['attempt_id'] == successor.attempt_id
    assert reflections[0]['episode_ids'] == ['episode'] and len(reflections[0]['source_refs']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['record', 'reserve'])
@pytest.mark.parametrize('failure_mode', [
    'none', 'not_found', 'exception', 'queued', 'running', 'wrong_id', 'unfinished',
    'naive_time', 'wrong_generation', 'not_receipt', 'explicit_cancel',
])
async def test_unconfirmed_control_status_cannot_authorize_successor(database, phase, failure_mode):
    calls = []
    models_ref = {}

    async def status(attempt_id):
        calls.append(attempt_id)
        receipt = models_ref['models'].results[attempt_id].receipt
        invalid_now = phase == 'record' or len(calls) == 3
        if not invalid_now:
            return replace(receipt)
        if failure_mode == 'none':
            return None
        if failure_mode == 'not_found':
            raise AppError('model_attempt_not_found', status=404)
        if failure_mode == 'exception':
            raise RuntimeError('control plane unavailable')
        if failure_mode in {'queued', 'running'}:
            return replace(receipt, status=failure_mode)
        if failure_mode == 'wrong_id':
            return replace(receipt, attempt_id='another-attempt')
        if failure_mode == 'unfinished':
            return replace(receipt, finished_at=None)
        if failure_mode == 'naive_time':
            return replace(receipt, finished_at=datetime.now())
        if failure_mode == 'wrong_generation':
            return replace(receipt, data_generation='different-generation')
        if failure_mode == 'not_receipt':
            return {'attempt_id': attempt_id, 'status': 'unknown'}
        return replace(receipt, cancellation_reason='explicit_cancel')

    growth, _context, _memory, models, scope = await current_retry_setup(database)
    models_ref['models'] = models
    models.status = status
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]
    models.results[original.attempt_id] = unknown_result(original)
    failure = await growth.organize('actor', scope, 1, GENERATION)
    if failure['state'] == 'retry_waiting':
        await make_due(database)
        await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute('SELECT attempt_id,status FROM runtime.recovery_attempts')).fetchall()
        recorded = await (await connection.execute('SELECT count(*) AS n FROM runtime.recovery_computation_unknowns')).fetchone()
    assert len(models.submitted) == 1
    assert attempts == [{'attempt_id': original.attempt_id, 'status': 'unknown'}]
    assert recorded['n'] == (0 if phase == 'record' else 1)
    assert (await retry_row(database))['state'] == ('blocked' if failure_mode == 'explicit_cancel' else 'waiting')


@pytest.mark.asyncio
async def test_repeated_finished_unknowns_share_one_bounded_attempt_budget(database):
    growth, _context, _memory, models, scope, original, failure = await start_unknown(database)
    for number in range(1, 5):
        current = models.submitted[-1][0]
        if number > 1:
            models.results[current.attempt_id] = unknown_result(current)
            failure = await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)
        assert failure['state'] == 'retry_waiting'
        await make_due(database)
        await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
        assert len(models.submitted) == min(number + 1, 4)

    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute(
            'SELECT attempt_id,status,supersedes_attempt_id FROM runtime.recovery_attempts ORDER BY attempt_number')).fetchall()
        lineage = await (await connection.execute('SELECT attempts,max_attempts FROM runtime.recovery_lineages')).fetchone()
    assert lineage == {'attempts': 4, 'max_attempts': 4}
    assert all(attempt['status'] == 'unknown' for attempt in attempts)
    assert [item['supersedes_attempt_id'] for item in attempts] == [None] + [item['attempt_id'] for item in attempts[:-1]]
    assert attempts[0]['attempt_id'] == original.attempt_id
    assert (await retry_row(database))['waiting_on_original']


@pytest.mark.asyncio
async def test_other_material_running_does_not_drop_unknown_observation(database):
    growth, context, memory, models, scope, original, failure = await start_unknown(database)
    later = replace(memory.episode, episode_id='later', event_id='later', sequence=2, cursor='e:2',
                    occurred_at=memory.episode.occurred_at+timedelta(minutes=1), source_refs=(SourceRef('later', 1),))
    memory.episodes += (later,)
    await growth.changed(context, 'event:2', episode=later, generation=GENERATION)
    await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)
    tail = models.submitted[1][0]
    await make_due(database, original.attempt_id)
    await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert (await retry_row(database))['waiting_on_original'] and len(models.submitted) == 2

    models.results[tail.attempt_id] = observed_result(tail, 'succeeded')
    await growth.organize('actor', scope, (await state(database))['revision'], GENERATION)
    await make_due(database, original.attempt_id)
    await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    successor = models.submitted[2][0]
    assert set(frozen_episodes(successor)) == {'episode'}
    assert (await state(database))['cursor'] == 'e:2'


@pytest.mark.asyncio
async def test_late_original_before_successor_still_recovers_original_batch(database):
    growth, _context, _memory, models, scope = await current_retry_setup(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]

    async def live_status(attempt_id):
        return models.results[attempt_id].receipt
    models.status = live_status
    success = observed_result(original, 'succeeded', notes=(note('reflection'),))
    success = replace(success, receipt=replace(success.receipt, finished_at=datetime.now(UTC), data_generation=GENERATION))
    models.results[original.attempt_id] = success
    started, release = asyncio.Event(), asyncio.Event()
    read, held = growth.generations.read, False

    async def delayed_read(attempt_id, *args, **kwargs):
        nonlocal held
        result = await read(attempt_id, *args, **kwargs)
        if attempt_id == original.attempt_id and not held:
            held = True
            started.set()
            await release.wait()
        return result

    growth.generations.read = delayed_read
    original_task = asyncio.create_task(growth.organize('actor', scope, 1, GENERATION))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        models.results[original.attempt_id] = unknown_result(original)
        failure = await growth.organize('actor', scope, 1, GENERATION)
        models.results[original.attempt_id] = success
        release.set()
        assert (await original_task)['state'] == 'superseded_attempt'
        async with database.transaction(read_only=True) as connection:
            attempt = await (await connection.execute(
                'SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s', (original.attempt_id,))).fetchone()
        assert attempt['status'] == 'unknown'
        await due(database)
        assert (await growth.retries.run('actor', scope, failure['retry_key'], GENERATION))['state'] == 'reviewed'
        assert len(models.submitted) == 1 and (await retry_row(database))['state'] == 'completed'
    finally:
        release.set()
        await original_task


@pytest.mark.asyncio
async def test_late_original_settles_only_itself_after_unknown_successor_selected(database):
    growth, _context, _memory, models, scope = await current_retry_setup(database)
    await growth.organize('actor', scope, 1, GENERATION)
    original = models.submitted[0][0]

    async def live_status(attempt_id):
        return models.results[attempt_id].receipt
    models.status = live_status
    success = observed_result(original, 'succeeded', notes=(note('style'),))
    success = replace(success, receipt=replace(success.receipt, finished_at=datetime.now(UTC), data_generation=GENERATION))
    models.results[original.attempt_id] = success
    started, release = asyncio.Event(), asyncio.Event()
    read, held = growth.generations.read, False

    async def delayed_read(attempt_id, *args, **kwargs):
        nonlocal held
        result = await read(attempt_id, *args, **kwargs)
        if attempt_id == original.attempt_id and not held:
            held = True
            started.set()
            await release.wait()
        return result

    growth.generations.read = delayed_read
    original_task = asyncio.create_task(growth.organize('actor', scope, 1, GENERATION))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        models.results[original.attempt_id] = unknown_result(original)
        failure = await growth.organize('actor', scope, 1, GENERATION)
        await make_due(database)
        await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
        successor = models.submitted[1][0]
        async with database.transaction() as connection:
            await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()+interval '2 minutes'")
            await connection.execute("UPDATE runtime.recovery_dependencies SET probe_token=%s,"
                                     "probe_expires_at=now()+interval '2 minutes'", (successor.attempt_id,))
            before = await (await connection.execute(
                'SELECT l.attempts,l.due_at,d.probe_token,d.probe_expires_at FROM runtime.recovery_lineages l '
                'JOIN runtime.recovery_dependencies d ON d.dependency=l.dependency AND d.data_generation=l.data_generation')).fetchone()
        models.results[original.attempt_id] = success
        release.set()
        assert (await original_task)['state'] == 'superseded_attempt'

        async with database.transaction(read_only=True) as connection:
            after = await (await connection.execute(
                'SELECT l.attempts,l.due_at,d.probe_token,d.probe_expires_at FROM runtime.recovery_lineages l '
                'JOIN runtime.recovery_dependencies d ON d.dependency=l.dependency AND d.data_generation=l.data_generation')).fetchone()
            attempts = await (await connection.execute(
                'SELECT attempt_id,status FROM runtime.recovery_attempts ORDER BY attempt_number')).fetchall()
            rules = await (await connection.execute('SELECT count(*) AS n FROM companion.style_rules')).fetchone()
        assert after == before and rules['n'] == 0
        assert attempts == [
            {'attempt_id': original.attempt_id, 'status': 'succeeded'},
            {'attempt_id': successor.attempt_id, 'status': 'reserved'},
        ]
        models.results[successor.attempt_id] = observed_result(successor, 'succeeded', notes=(note('style'),))
        assert (await growth.organize('actor', scope, 1, GENERATION))['state'] == 'reviewed'
        async with database.transaction(read_only=True) as connection:
            rules = await (await connection.execute('SELECT count(*) AS n FROM companion.style_rules')).fetchone()
        assert rules['n'] == 1 and len(models.submitted) == 2
    finally:
        release.set()
        await original_task


@pytest.mark.asyncio
async def test_revoked_source_prevents_unknown_successor(database):
    growth, _context, _memory, models, scope, original, failure = await start_unknown(database)
    async def revoked(*_args):
        return False
    growth.companion.sources.validate_persistence = revoked
    await make_due(database)
    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert outcome['state'] == 'retry_blocked' and outcome['error_code'] == 'source_unavailable'
    assert len(models.submitted) == 1 and models.submitted[0][0].attempt_id == original.attempt_id


@pytest.mark.asyncio
async def test_revised_original_episode_prevents_unknown_successor(database):
    growth, _context, memory, models, scope, original, failure = await start_unknown(database)
    memory.episodes = (replace(memory.episode, revision=2),)
    await make_due(database)
    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert outcome['state'] == 'retry_blocked' and outcome['error_code'] == 'reflection_episode_unavailable'
    assert len(models.submitted) == 1 and models.submitted[0][0].attempt_id == original.attempt_id


@pytest.mark.asyncio
async def test_lost_authorization_prevents_unknown_successor(database):
    growth, _context, _memory, models, scope, original, failure = await start_unknown(database)
    async def denied(*_args, **_kwargs):
        raise AppError('access_changed', status=403)
    growth.companion.identity.check = denied
    growth.companion.identity.refresh_scheduled_context = denied
    await make_due(database)
    outcome = await growth.retries.run('actor', scope, failure['retry_key'], GENERATION)
    assert outcome['state'] == 'retry_blocked' and outcome['error_code'] == 'access_changed'
    assert len(models.submitted) == 1 and models.submitted[0][0].attempt_id == original.attempt_id
