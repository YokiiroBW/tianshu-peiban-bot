"""Native collection failure must not strand the original actor/day generation."""
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.errors import QueryCanceled

from test_rb_diary_experience import rb_fixture, diary_state
from tianshu.runtime.database import classify_database_failure
from tianshu.runtime.jobs import JobQueue
from tianshu.models.types import ModelRequest
from tianshu.types import RequestBudget, ServiceClass


async def collecting(database):
    value = await rb_fixture(database)
    jobs = JobQueue(database.config.dsn,database=database)
    jobs.register_static_routes()
    value.jobs = value.life.jobs = jobs
    await value.diaries.day_closed(value.context,"2026-11-02")
    generation = await jobs.current_generation()
    await value.diaries.review_day("actor",value.identity.scene_id(value.context),"2026-11-02",1,generation)
    return value, await diary_state(database), generation


async def test_failed_native_collection_is_recovered_once_without_replacing_generation(database):
    value, before, generation = await collecting(database)
    async with database.transaction() as connection:
        old = await (await connection.execute("SELECT id FROM procrastinate_jobs "
            "WHERE task_name='life.diary_collect'")).fetchone()
        await connection.execute("UPDATE procrastinate_jobs SET status='failed',attempts=3 WHERE id=%s",(old['id'],))
    assert (await value.diaries.collection.reconcile(limit=1))["woken"] == 1
    assert (await value.diaries.collection.reconcile(limit=1))["woken"] == 0
    async with database.transaction(read_only=True) as connection:
        original = await (await connection.execute("SELECT status,attempts FROM procrastinate_jobs WHERE id=%s",(old['id'],))).fetchone()
        pending = await (await connection.execute("SELECT args FROM procrastinate_jobs "
            "WHERE task_name='life.diary_collect' AND status='todo'")).fetchall()
    assert original == {"status":"failed","attempts":3}
    assert len(pending) == 1 and pending[0]['args']['generation_id'] == before['generation_id']
    assert pending[0]['args']['cursor'] is None
    await value.diaries.collect(before['id'],before['generation_id'],None,data_generation=generation)
    after = await diary_state(database)
    assert after['state'] == 'generating' and after['generation_id'] == before['generation_id']
    assert not value.models.requests


@pytest.mark.parametrize('condition',['doing','cancelled','prepared_request'])
async def test_collection_reconciliation_preserves_ongoing_and_cancelled_work(database,condition):
    value, row, generation = await collecting(database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status=%s WHERE task_name='life.diary_collect'",
            ('doing' if condition=='doing' else 'failed',))
        if condition=='cancelled':
            await connection.execute("UPDATE life.diaries SET generation_cancel_requested=true WHERE id=%s",(row['id'],))
    if condition=='prepared_request':
        request = ModelRequest('diary:'+row['generation_id'],row['generation_id'],'reflect',ServiceClass.ELASTIC,
            value.context,RequestBudget(datetime.now(UTC)+timedelta(minutes=1)),(),data_generation=generation)
        await value.diaries.generations.prepare('diary:'+row['generation_id'],request)
    assert (await value.diaries.collection.reconcile(limit=1))['woken'] == 0


async def test_statement_timeout_records_wait_and_same_collection_can_finish(database,monkeypatch):
    value, row, generation = await collecting(database)
    original = value.memory.episodes_between
    async def timeout(*_args,**_kwargs):
        async with database.transaction(read_only=True) as connection:
            await connection.execute("SET LOCAL statement_timeout='10ms'")
            await connection.execute('SELECT pg_sleep(0.05)')
    monkeypatch.setattr(value.memory,'episodes_between',timeout)
    with pytest.raises(QueryCanceled) as captured:
        await value.diaries.collect(row['id'],row['generation_id'],None,data_generation=generation)
    failure = classify_database_failure(captured.value)
    assert failure.reason == 'database_statement_timeout' and failure.retryable and failure.rollback_confirmed
    waiting = await diary_state(database)
    assert waiting['state']=='collecting' and waiting['reason']=='database_statement_timeout'
    assert waiting['generation_id']==row['generation_id'] and waiting['current_revision']==0
    monkeypatch.setattr(value.memory,'episodes_between',original)
    await value.diaries.collect(row['id'],row['generation_id'],None,data_generation=generation)
    assert (await diary_state(database))['state']=='generating'


async def test_unconfirmed_cancellation_does_not_become_automatic_collection_retry(database,monkeypatch):
    value,row,generation = await collecting(database)
    async def cancelled(*_args,**_kwargs):
        raise QueryCanceled('canceling statement due to user request')
    monkeypatch.setattr(value.memory,'episodes_between',cancelled)
    with pytest.raises(QueryCanceled):
        await value.diaries.collect(row['id'],row['generation_id'],None,data_generation=generation)
    after = await diary_state(database)
    assert after['state']=='failed' and after['reason']=='diary_collection_cancelled'
    assert (await value.diaries.collection.reconcile(limit=1))['woken']==0
