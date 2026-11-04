"""A failed statistics sink never changes Growth's business result."""

from importlib.resources import files

import pytest

from tianshu.companion.growth_statistics import GrowthStatistics
from tianshu.conversation.codec import document
from tianshu.runtime.migrations import Migration, migrate

GENERATION = '00000000-0000-4000-8000-000000000001'


class Jobs:
    def __init__(self):
        self.items = []

    async def enqueue(self, _connection, spec, **_kwargs):
        self.items.append(spec)

    async def assert_generation_on(self, _connection, generation):
        assert generation == GENERATION


class Statistics:
    def __init__(self):
        self.available = False
        self.events, self.coverage = [], []

    async def record_safely_on(self, _connection, event):
        if not self.available:
            return False
        self.events.append(event)
        return True

    async def mark_producer_on(self, _connection, producer, **kwargs):
        self.coverage.append((producer, kwargs))


@pytest.mark.asyncio
async def test_failed_delivery_replays_from_original_material_ledger(database):
    await migrate(database, tuple(Migration(name, files('tianshu.companion').joinpath(path).read_text())
        for name, path in (('0030_companion', 'schema.sql'), ('0034_companion_growth_batches', 'growth_batches.sql'),
                           ('0037_companion_growth_retry', 'growth_retry.sql'), ('0039_companion_learning', 'habit_revisions.sql'))))
    jobs, statistics = Jobs(), Statistics()
    observer = GrowthStatistics(database, jobs)
    observer.bind(statistics)
    async with database.transaction() as connection:
        row = await (await connection.execute(
            "INSERT INTO companion.growth_material_failures(actor_id,scope_key,episode_id,episode_revision,"
            "failure_key,error_code,details,context,runtime_generation) VALUES('a','scope','e',1,'input-e','reflection_material_budget_exceeded',%s,%s,%s) RETURNING *",
            (document({}), document({}), GENERATION))).fetchone()
        assert not await observer.emit_on(connection, observer.material_event(row), generation=GENERATION)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute('SELECT state FROM companion.growth_material_failures')).fetchone()
        pending = await (await connection.execute('SELECT delivered FROM companion.growth_statistics_outbox')).fetchone()
    assert row['state'] == 'blocked' and pending['delivered'] is False
    assert not any(value['complete'] for _, value in statistics.coverage)

    statistics.available = True
    state = await observer.replay(GENERATION)
    state = await observer.replay(GENERATION, state['cursor'])
    state = await observer.replay(GENERATION, state['cursor'])
    assert state['state'] == 'complete'
    assert statistics.events[-1]['family'] == 'growth'
    assert statistics.events[-1]['metrics']['growth_material_blocked'] == 1
    assert statistics.coverage[-1][1]['complete'] is True
