"""Growth collects all source dependencies before locking domain records."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.companion.growth import GrowthService
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage, SourceRef
from test_rb_growth_async import GENERATION, setup_growth


@pytest.mark.asyncio
async def test_material_collects_supplements_and_example_sources_before_episode_lock():
    original = SourceRef('original', 1)
    supplement = SourceRef('supplement', 2)
    generated = SourceRef('generated', 3)
    example = SourceRef('example', 4)
    episode = SimpleNamespace(episode_id='episode', revision=1, source_refs=(original,),
                              supplement_refs=(supplement,))
    snapshot = SimpleNamespace(items=(episode,), coverage=Coverage(True))
    operations = []

    async def lock(_context, refs, *, connection, extra_source_refs):
        operations.append((refs, connection, extra_source_refs))
        return snapshot

    growth = GrowthService.__new__(GrowthService)
    growth.jobs = SimpleNamespace(assert_generation_on=AsyncMock())
    growth.companion = SimpleNamespace(memory=SimpleNamespace(lock_episodes=lock))
    connection, context = object(), object()
    frozen = {'episode': SimpleNamespace(episode_id='episode', revision=1, source_refs=(original,))}

    await growth._validate_material(connection, context, frozen, (generated,),
                                    generation=GENERATION, additional_refs=(example,))

    assert len(operations) == 1 and operations[0][1] is connection
    assert operations[0][0] == (('episode', 1),)
    # Memory's lock_episodes adds the live supplement before locking sources.
    assert {(ref.source_id, ref.revision) for ref in operations[0][2]} == {
        ('original', 1), ('generated', 3), ('example', 4)}


@pytest.mark.asyncio
async def test_changed_episode_is_not_accepted_for_commit():
    snapshot = SimpleNamespace(items=(), coverage=Coverage(False, missing=('episodes_unavailable_or_revised',)))
    lock = AsyncMock(return_value=snapshot)
    growth = GrowthService.__new__(GrowthService)
    growth.jobs = SimpleNamespace(assert_generation_on=AsyncMock())
    growth.companion = SimpleNamespace(memory=SimpleNamespace(lock_episodes=lock))
    episode = SimpleNamespace(episode_id='episode', revision=1, source_refs=(SourceRef('original', 1),))

    with pytest.raises(AppError) as failure:
        await growth._validate_material(object(), object(), {'episode': episode}, (), generation=GENERATION)

    assert failure.value.code == 'reflection_episode_unavailable'
    lock.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_episode_notification_reuses_current_growth_batch(database):
    growth, context, memory, models, _policy, scope = await setup_growth(database)
    original_enqueue = growth.jobs.enqueue
    pending = {}

    async def coalescing_enqueue(connection, spec, *, queueing_lock=None):
        if queueing_lock in pending:
            assert pending[queueing_lock].payload == spec.payload
            return
        pending[queueing_lock] = spec
        await original_enqueue(connection, spec, queueing_lock=queueing_lock)

    growth.jobs.enqueue = coalescing_enqueue
    await growth.organize('actor', scope, 1, GENERATION)
    before_jobs = len(growth.jobs.items)
    async with database.transaction(read_only=True) as connection:
        before = await (await connection.execute(
            'SELECT revision,watermark,active_batch FROM companion.organize_state '
            'WHERE actor_id=%s AND scope_key=%s', ('actor', scope))).fetchone()

    await growth.changed(context, 'duplicate-notification', episode=memory.episode, generation=GENERATION)
    await growth.organize('actor', scope, before['revision'], GENERATION)

    async with database.transaction(read_only=True) as connection:
        after = await (await connection.execute(
            'SELECT revision,watermark,active_batch FROM companion.organize_state '
            'WHERE actor_id=%s AND scope_key=%s', ('actor', scope))).fetchone()
    assert after == before
    assert len(models.submitted) == 1 and len(growth.jobs.items) == before_jobs
