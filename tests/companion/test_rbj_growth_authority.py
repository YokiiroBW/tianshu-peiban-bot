"""Concurrent native Growth owners must use the committed frozen batch."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from tianshu.companion.growth import GrowthService
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryPolicy
from test_growth_output import state
from test_growth_retry import current_retry_setup, observed_result, retry_row
from test_rb_growth_async import GENERATION
from test_rbi_growth_recovery import LedgerModels


@asynccontextmanager
async def concurrent_preparation(database):
    growth, context, memory, _models, scope = await current_retry_setup(database)
    models = LedgerModels(database)
    growth.models = models
    growth.generations.models = models
    other = GrowthService(growth.companion, growth.jobs, models, growth.assembler,
                          on_projection_changed=growth.on_projection_changed)
    other.set_observability(statistics=None, recovery_policy=RecoveryPolicy())
    first_prepare, second_prepare = growth.generations.prepare, other.generations.prepare
    first_ready, second_ready, release_second = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def prepare_first(material_key, request, **kwargs):
        calls.append((material_key, request.attempt_id))
        first_ready.set()
        await second_ready.wait()
        return await first_prepare(material_key, request, **kwargs)

    async def prepare_second(material_key, request, **kwargs):
        calls.append((material_key, request.attempt_id))
        second_ready.set()
        await release_second.wait()
        return await second_prepare(material_key, request, **kwargs)

    growth.generations.prepare, other.generations.prepare = prepare_first, prepare_second
    first = second = None
    try:
        first = asyncio.create_task(growth.organize('actor', scope, 1, GENERATION))
        await asyncio.wait_for(first_ready.wait(), 20)
        second = asyncio.create_task(other.organize('actor', scope, 1, GENERATION))
        await asyncio.wait_for(second_ready.wait(), 20)
        first_outcome = await asyncio.wait_for(first, 20)
        assert first_outcome['state'] == 'waiting'
        assert len(calls) == 2 and calls[0] == calls[1] and len(models.submitted) == 1
        original = models.submitted[0]
        persisted = (await state(database))['active_batch']
        assert persisted['material_key'] == original.intent_id
        assert persisted['lineage_id'] and persisted['recovery_version']
        yield growth, context, memory, models, scope, original, persisted, second, release_second
    finally:
        release_second.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        growth.generations.prepare, other.generations.prepare = first_prepare, second_prepare


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['succeeded', 'failed', 'unknown'])
async def test_second_native_owner_uses_first_frozen_batch_and_lineage(database, outcome):
    async with concurrent_preparation(database) as prepared:
        growth, _context, _memory, models, _scope, original, saved, second, release = prepared
        async with database.transaction(read_only=True) as connection:
            frozen = await (await connection.execute(
                'SELECT material_key,attempt_id,request FROM conversation.generation_requests '
                'WHERE material_key=%s', (original.intent_id,))).fetchone()
        result = observed_result(original, 'succeeded') if outcome == 'succeeded' else None
        code = {'succeeded': None, 'failed': 'invalid_upstream_response', 'unknown': 'transport_unknown'}[outcome]
        await models.finish(original, outcome, code=code, result=result)

        release.set()
        applied = await asyncio.wait_for(second, 20)

        async with database.transaction(read_only=True) as connection:
            lineage = await (await connection.execute(
                'SELECT lineage_id,attempts,max_attempts FROM runtime.recovery_lineages')).fetchall()
            attempts = await (await connection.execute(
                'SELECT attempt_id,lineage_id,status FROM runtime.recovery_attempts')).fetchall()
            current_frozen = await (await connection.execute(
                'SELECT material_key,attempt_id,request FROM conversation.generation_requests '
                'WHERE material_key=%s', (original.intent_id,))).fetchone()
            item = await (await connection.execute(
                "SELECT revision,reviewed_revision FROM companion.growth_inputs WHERE episode_id='episode'")).fetchone()
        assert current_frozen == frozen and len(models.submitted) == 1
        assert lineage == [{'lineage_id': saved['lineage_id'], 'attempts': 1, 'max_attempts': 4}]
        assert attempts == [{'attempt_id': original.attempt_id, 'lineage_id': saved['lineage_id'], 'status': outcome}]
        if outcome == 'succeeded':
            assert applied['state'] == 'reviewed' and item == {'revision': 1, 'reviewed_revision': 1}
        else:
            retry = await retry_row(database)
            assert applied['state'] == ('retry_waiting' if outcome == 'unknown' else 'retry_blocked')
            assert retry['last_error'] == code and retry['waiting_on_original'] == (outcome == 'unknown')
            assert retry['batch']['lineage_id'] == saved['lineage_id']
            assert retry['batch']['recovery_version'] == saved['recovery_version']
            assert item == {'revision': 1, 'reviewed_revision': 0}


@pytest.mark.asyncio
async def test_new_episode_version_exits_old_concurrent_preparation(database):
    async with concurrent_preparation(database) as prepared:
        growth, context, memory, models, scope, original, saved, second, release = prepared
        revised = replace(memory.episode, revision=2)
        memory.episode, memory.episodes = revised, (revised,)
        await growth.changed(context, 'episode:revision-2', episode=revised, generation=GENERATION)

        release.set()
        outcome = await asyncio.wait_for(second, 20)

        current = await state(database)
        assert outcome is None and len(models.submitted) == 1
        assert current['active_batch'] == saved and models.submitted[0].attempt_id == original.attempt_id
        async with database.transaction(read_only=True) as connection:
            item = await (await connection.execute(
                "SELECT revision,reviewed_revision FROM companion.growth_inputs WHERE episode_id='episode'")).fetchone()
            lineage = await (await connection.execute('SELECT attempts FROM runtime.recovery_lineages')).fetchone()
        assert item == {'revision': 2, 'reviewed_revision': 0} and lineage['attempts'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('missing,error', [
    ('lineage_id', 'growth_recovery_lineage_missing'),
    ('recovery_version', 'growth_recovery_version_missing'),
])
async def test_missing_authoritative_recovery_identity_is_not_synthesized_or_suppressed(database, missing, error):
    async with concurrent_preparation(database) as prepared:
        _growth, _context, _memory, models, _scope, original, _saved, second, release = prepared
        async with database.transaction() as connection:
            await connection.execute(
                'UPDATE companion.organize_state SET active_batch=active_batch-%s::text', (missing,))

        release.set()
        with pytest.raises(AppError, match=error):
            await asyncio.wait_for(second, 20)

        current = await state(database)
        assert missing not in current['active_batch'] and len(models.submitted) == 1
        async with database.transaction(read_only=True) as connection:
            attempt = await (await connection.execute(
                'SELECT attempt_id,status FROM runtime.recovery_attempts')).fetchone()
        assert attempt == {'attempt_id': original.attempt_id, 'status': 'reserved'}


@pytest.mark.asyncio
async def test_missing_frozen_request_is_not_rebuilt_from_second_candidate(database):
    async with concurrent_preparation(database) as prepared:
        _growth, _context, _memory, models, _scope, original, saved, second, release = prepared
        async with database.transaction() as connection:
            await connection.execute(
                'DELETE FROM conversation.generation_requests WHERE material_key=%s', (original.intent_id,))

        release.set()
        with pytest.raises(AppError, match='generation_request_not_found'):
            await asyncio.wait_for(second, 20)

        assert (await state(database))['active_batch'] == saved and len(models.submitted) == 1
        async with database.transaction(read_only=True) as connection:
            frozen = await (await connection.execute(
                'SELECT * FROM conversation.generation_requests WHERE material_key=%s', (original.intent_id,))).fetchone()
            lineage = await (await connection.execute('SELECT attempts FROM runtime.recovery_lineages')).fetchone()
        assert frozen is None and lineage['attempts'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('conflict,error', [
    ('generation', 'generation_identity_conflict'),
    ('context', 'generation_context_changed'),
])
async def test_frozen_request_conflict_is_not_replaced_or_submitted(database, conflict, error):
    async with concurrent_preparation(database) as prepared:
        _growth, _context, _memory, models, _scope, original, saved, second, release = prepared
        async with database.transaction() as connection:
            if conflict == 'generation':
                await connection.execute(
                    "UPDATE conversation.generation_requests SET request=jsonb_set(request, '{data_generation}', "
                    "to_jsonb(%s::text)) WHERE material_key=%s",
                    ('00000000-0000-4000-8000-000000000099', original.intent_id))
            else:
                await connection.execute(
                    'UPDATE conversation.generation_requests SET context_key=%s WHERE material_key=%s',
                    ('different-context', original.intent_id))
            frozen = await (await connection.execute(
                'SELECT * FROM conversation.generation_requests WHERE material_key=%s',
                (original.intent_id,))).fetchone()

        release.set()
        with pytest.raises(AppError, match=error):
            await asyncio.wait_for(second, 20)

        assert (await state(database))['active_batch'] == saved and len(models.submitted) == 1
        async with database.transaction(read_only=True) as connection:
            current = await (await connection.execute(
                'SELECT * FROM conversation.generation_requests WHERE material_key=%s',
                (original.intent_id,))).fetchone()
            lineage = await (await connection.execute('SELECT attempts FROM runtime.recovery_lineages')).fetchone()
        assert current == frozen and lineage['attempts'] == 1
