"""Growth's bounded maintenance restores only native wakeups, not model work."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest

from tianshu.companion import growth as growth_module
from tianshu.conversation.codec import document
from tianshu.memory.types import EpisodePage
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Coverage
from test_growth_retry import retry_row
from test_rb_growth_async import GENERATION
from test_rbi_growth_recovery import setup_original, shifted_datetime


async def setup_maintenance(database):
    growth, context, memory, models, scope, original, failure = await setup_original(
        database, code='upstream_http_429',
        diagnostic={'failure_stage': 'unknown', 'error_type': 'UpstreamHTTPError', 'original_output_reusable': None})
    await migrate(database, (Migration('rbi_growth_maintenance',
        files('tianshu.companion').joinpath('rbi_growth_maintenance.sql').read_text(encoding='utf-8')),))
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.growth_retry_batches SET due_at=now()-interval '1 second'")
    jobs = JobQueue(database.config.dsn, database=database)
    for task in ('companion.organize', 'companion.growth.retry', 'companion.growth.input_retry'):
        jobs.register_route(task)
    growth.jobs = jobs
    growth.observations.jobs = jobs
    growth.operations = None  # The background maintenance path is independent of Operations.
    return growth, context, memory, models, scope, original, failure


async def force_scan_due(database):
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.rbi_growth_maintenance_scans SET next_scan_at=now()-interval '1 second'")


async def native_jobs(database, task):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute(
            'SELECT id,status,scheduled_at,args,queueing_lock FROM procrastinate_jobs WHERE task_name=%s ORDER BY id',
            (task,))).fetchall()


@pytest.mark.asyncio
async def test_missing_retry_wake_coalesces_with_todo_and_preserves_doing(database):
    growth, _context, _memory, models, _scope, _original, failure = await setup_maintenance(database)
    models.result = AsyncMock(side_effect=AssertionError('maintenance must not read Models'))
    models.submit = AsyncMock(side_effect=AssertionError('maintenance must not submit Models'))

    first = await growth.reconcile_due(limit=3)
    assert first['scanned'] == first['woken'] == 1 and first['lanes']['retry'] == 1
    jobs = await native_jobs(database, 'companion.growth.retry')
    assert len(jobs) == 1 and jobs[0]['status'] == 'todo'
    assert jobs[0]['args']['retry_key'] == failure['retry_key']
    first_id = jobs[0]['id']
    assert (await growth.reconcile_due(limit=3))['woken'] == 0

    # An existing later todo is advanced to the earliest legal persisted due.
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET scheduled_at=now()+interval '1 hour' WHERE id=%s", (first_id,))
    await force_scan_due(database)
    assert (await growth.reconcile_due(limit=3))['woken'] == 1
    jobs = await native_jobs(database, 'companion.growth.retry')
    assert len(jobs) == 1 and jobs[0]['id'] == first_id
    assert jobs[0]['scheduled_at'] == (await retry_row(database))['due_at']

    # Doing remains intact; a lost successor gets exactly one new todo.
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (first_id,))
    await force_scan_due(database)
    page = await growth.reconcile_due(limit=3)
    if not page['woken']:
        page = await growth.reconcile_due(limit=3)  # Wrap the persisted lane cursor.
    assert page['woken'] == 1
    await force_scan_due(database)
    assert (await growth.reconcile_due(limit=3))['woken'] == 0
    jobs = await native_jobs(database, 'companion.growth.retry')
    assert [job['status'] for job in jobs] == ['doing', 'todo']
    assert all(job['args']['retry_key'] == failure['retry_key'] for job in jobs)
    models.result.assert_not_awaited()
    models.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_cursor_reaches_later_rows_without_rescanning_the_oldest(database):
    growth, _context, _memory, _models, _scope, _original, _failure = await setup_maintenance(database)
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO companion.growth_retry_batches(actor_id,scope_key,retry_key,batch,current_material_key,"
            "state,due_at,last_error) SELECT actor_id,scope_key,retry_key||':later',batch,"
            "current_material_key||':later',state,due_at+interval '1 microsecond',last_error "
            "FROM companion.growth_retry_batches")

    first = await growth.reconcile_due(limit=3)
    second = await growth.reconcile_due(limit=3)

    assert first['lanes']['retry'] == second['lanes']['retry'] == 1
    jobs = await native_jobs(database, 'companion.growth.retry')
    assert len(jobs) == 2 and jobs[0]['args']['retry_key'] != jobs[1]['args']['retry_key']


@pytest.mark.asyncio
async def test_small_pages_rotate_between_retry_input_and_pending_material(database):
    growth, context, memory, models, scope, _original, failure = await setup_maintenance(database)
    later = replace(memory.episode, episode_id='later', event_id='later', sequence=2,
                    cursor='e:2', occurred_at=datetime.now(UTC)-timedelta(minutes=1))
    memory.episodes += (later,)
    # Simulate an existing pending version whose normal enqueue was lost.
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO companion.growth_inputs(actor_id,scope_key,episode_id,revision,sequence,context) "
            "VALUES('actor',%s,'later',1,2,%s)", (scope, document(context)))
        await connection.execute(
            "INSERT INTO companion.growth_material_failures(actor_id,scope_key,episode_id,episode_revision,"
            "failure_key,runtime_generation,error_code,details,context,due_at) "
            "VALUES('actor',%s,'missing',1,'input:missing',%s,'growth_model_profile_unavailable',%s,%s,"
            "now()-interval '1 second')", (scope, GENERATION, document({}), document(context)))
    models.result = AsyncMock(side_effect=AssertionError('maintenance must not read Models'))
    models.submit = AsyncMock(side_effect=AssertionError('maintenance must not submit Models'))

    pages = [await growth.reconcile_due(limit=1) for _ in range(3)]

    assert [next(name for name, count in page['lanes'].items() if count) for page in pages] == [
        'retry', 'input', 'organize']
    assert all(page['scanned'] == page['woken'] == 1 and not page['complete'] for page in pages)
    assert len(await native_jobs(database, 'companion.growth.retry')) == 1
    assert len(await native_jobs(database, 'companion.growth.input_retry')) == 1
    assert len(await native_jobs(database, 'companion.organize')) == 1
    assert (await retry_row(database))['retry_key'] == failure['retry_key']
    models.result.assert_not_awaited()
    models.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_input_stops_its_due_instead_of_repeated_wakes(database):
    growth, context, memory, _models, scope, _original, _failure = await setup_maintenance(database)
    memory.episodes_by_refs = AsyncMock(return_value=EpisodePage(
        (), None, Coverage(False, missing=('episodes_unavailable_or_revised',)), 'e:0'))
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO companion.growth_material_failures(actor_id,scope_key,episode_id,episode_revision,"
            "failure_key,runtime_generation,error_code,details,context,due_at) "
            "VALUES('actor',%s,'missing',1,'input:missing',%s,'growth_model_profile_unavailable',%s,%s,"
            "now()-interval '1 second')", (scope, GENERATION, document({}), document(context)))

    stopped = await growth.retry_input('actor', scope, 'input:missing', 1, GENERATION)

    assert stopped == {'state': 'blocked', 'reason': 'episode_unavailable_or_revised'}
    async with database.transaction(read_only=True) as connection:
        failure = await (await connection.execute(
            "SELECT error_code,details,due_at,observation_revision FROM companion.growth_material_failures "
            "WHERE failure_key='input:missing'")).fetchone()
    assert failure['error_code'] == 'growth_model_profile_unavailable' and failure['due_at'] is None
    assert failure['details']['retry_stop_reason'] == stopped['reason'] and failure['observation_revision'] == 2
    page = await growth.reconcile_due(limit=3)
    assert page['lanes']['input'] == 0 and not await native_jobs(database, 'companion.growth.input_retry')


@pytest.mark.asyncio
async def test_future_input_retry_uses_database_clock(database, monkeypatch):
    growth, context, memory, _models, scope, _original, _failure = await setup_maintenance(database)
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO companion.growth_material_failures(actor_id,scope_key,episode_id,episode_revision,"
            "failure_key,runtime_generation,error_code,details,context,due_at) "
            "VALUES('actor',%s,'missing',1,'input:future',%s,'growth_model_profile_unavailable',%s,%s,"
            "clock_timestamp()+interval '1 hour')", (scope, GENERATION, document({}), document(context)))
    memory.episodes_by_refs = AsyncMock(side_effect=AssertionError('future input must not be read'))
    monkeypatch.setattr(growth_module, 'datetime', shifted_datetime(7200))

    outcome = await growth.retry_input('actor', scope, 'input:future', 1, GENERATION)

    assert outcome == {'state': 'waiting'}
    memory.episodes_by_refs.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_retry_rechecks_a_due_postponed_during_material_read(database):
    growth, context, memory, _models, scope, _original, _failure = await setup_maintenance(database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE companion.growth_inputs SET retry_key='input:race' WHERE episode_id='episode'")
        await connection.execute(
            "INSERT INTO companion.growth_material_failures(actor_id,scope_key,episode_id,episode_revision,"
            "failure_key,runtime_generation,error_code,details,context,due_at) "
            "VALUES('actor',%s,'episode',1,'input:race',%s,'growth_model_profile_unavailable',%s,%s,"
            "clock_timestamp()-interval '1 second')", (scope, GENERATION, document({}), document(context)))
    read_episodes = memory.episodes_by_refs

    async def postpone_during_read(*args, **kwargs):
        page = await read_episodes(*args, **kwargs)
        async with database.transaction() as connection:
            await connection.execute("UPDATE companion.growth_material_failures SET due_at=clock_timestamp()+interval '1 hour' "
                                     "WHERE failure_key='input:race'")
        return page

    memory.episodes_by_refs = postpone_during_read
    outcome = await growth.retry_input('actor', scope, 'input:race', 1, GENERATION)

    assert outcome == {'state': 'waiting'}
    async with database.transaction(read_only=True) as connection:
        current = await (await connection.execute(
            "SELECT retry_key,reviewed_revision FROM companion.growth_inputs WHERE episode_id='episode'")).fetchone()
    assert current == {'retry_key': 'input:race', 'reviewed_revision': 0}


@pytest.mark.asyncio
async def test_unexplained_empty_input_observation_remains_retryable(database):
    growth, context, _memory, _models, scope, _original, _failure = await setup_maintenance(database)
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO companion.growth_material_failures(actor_id,scope_key,episode_id,episode_revision,"
            "failure_key,runtime_generation,error_code,details,context,due_at) "
            "VALUES('actor',%s,'missing',1,'input:unknown',%s,'growth_model_profile_unavailable',%s,%s,"
            "now()-interval '1 second')", (scope, GENERATION, document({}), document(context)))

    outcome = await growth.retry_input('actor', scope, 'input:unknown', 1, GENERATION)

    assert outcome['state'] == 'waiting' and outcome['reason'] == 'episode_observation_incomplete'
    async with database.transaction(read_only=True) as connection:
        failure = await (await connection.execute(
            "SELECT error_code,due_at,observation_revision,clock_timestamp() AS db_now FROM companion.growth_material_failures "
            "WHERE failure_key='input:unknown'")).fetchone()
    assert failure['error_code'] == 'growth_model_profile_unavailable'
    assert outcome['due_at'] > failure['db_now']
    assert failure['due_at'] == outcome['due_at'] and failure['observation_revision'] == 1
