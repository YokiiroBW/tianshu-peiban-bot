"""Physical completion and domain application retain independent recovery wakes."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest
from psycopg.types.json import Jsonb

from test_rbi_memory_recovery import core, ordinary_users, examples, waits, two_unknown_batch, mature
from tianshu.memory.model_outcome import OriginalOutcome
from tianshu.models.ledger import receipt as ledger_receipt


async def unapplied_success(core, alice):
    model,item,partition,batch = await two_unknown_batch(core,alice,'success-with-lost-domain-wake')
    request = model.submissions[-1][0]
    await waits.Model.submit(model,request,completion=None)
    model.submissions.pop()
    await model.ledger.finish(request.attempt_id,'succeeded')
    receipt = ledger_receipt(await model.ledger.get(request.attempt_id))
    result = replace(model.results[request.attempt_id],receipt=receipt)
    model.results[request.attempt_id] = result
    await core.workers.consolidation._policy_outcome(batch['batch_id'],request.intent_id,status='succeeded',
        outcome=OriginalOutcome(result=result,receipt=receipt),expected_batch=batch)
    async with core.database.transaction() as connection:
        await connection.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()-interval '1 second' "
            "WHERE batch_id=%s",(batch['batch_id'],))
        await connection.execute("UPDATE procrastinate_jobs SET status='succeeded' "
            "WHERE task_name='memory.consolidate' AND status='todo'")
        lineage = await (await connection.execute("SELECT due_at,attempts FROM runtime.recovery_lineages "
            "WHERE lineage_id=%s",(batch['manifest']['recovery']['lineage_id'],))).fetchone()
    assert lineage['due_at'] is None and lineage['attempts']==2
    return model,item,partition,batch


async def test_domain_scan_consumes_success_after_runtime_due_was_cleared(core,ordinary_users):
    alice,_ = ordinary_users
    model,item,partition,batch = await unapplied_success(core,alice)
    await core.workers.reconcile_due(limit=32)
    async with core.database.transaction(read_only=True) as connection:
        jobs = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE status='todo' "
            "AND task_name='memory.consolidate' AND args->>'batch_id'=%s",(batch['batch_id'],))).fetchall()
    assert len(jobs)==1
    await core.workers.consolidate(partition,batch_id=batch['batch_id'])
    _,after = await waits.batch_for_episode(core,item.episode_id)
    assert after['batch_id']==batch['batch_id'] and after['status']=='applied'
    assert after['step_ids']==batch['step_ids'] and len(model.submissions)==2


async def test_busy_partition_retains_one_targeted_success_wake(core,ordinary_users):
    alice,_ = ordinary_users
    model,item,partition,batch = await unapplied_success(core,alice)
    async with core.database.transaction() as connection:
        await connection.execute("UPDATE memory.consolidation_state SET lease_owner='other',"
            "lease_until=now()+interval '1 minute' WHERE partition_id=%s",(partition,))
    for _ in range(2):
        await core.workers.consolidate(partition,batch_id=batch['batch_id'])
    async with core.database.transaction(read_only=True) as connection:
        jobs = await (await connection.execute("SELECT scheduled_at FROM procrastinate_jobs "
            "WHERE status='todo' AND task_name='memory.consolidate' AND args->>'batch_id'=%s",(batch['batch_id'],))).fetchall()
    assert len(jobs)==1 and jobs[0]['scheduled_at'] > datetime.now(UTC)
    assert len(model.submissions)==2
    async with core.database.transaction() as connection:
        await connection.execute("UPDATE memory.consolidation_state SET lease_owner=NULL,lease_until=NULL "
            "WHERE partition_id=%s",(partition,))
    await core.workers.consolidate(partition,batch_id=batch['batch_id'])
    assert (await waits.batch_for_episode(core,item.episode_id))[1]['status']=='applied'


async def test_success_application_preserves_another_active_batch(core,ordinary_users):
    alice,_ = ordinary_users
    model,item,partition,batch = await unapplied_success(core,alice)
    async with core.database.transaction() as connection:
        await connection.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,"
            "manifest,context_fingerprint) VALUES('other-active',%s,%s,'prepared',%s,%s)",
            (partition,batch['schema_version'],Jsonb(batch['manifest']),batch['context_fingerprint']))
        await connection.execute("UPDATE memory.consolidation_state SET active_batch_id='other-active' "
            "WHERE partition_id=%s",(partition,))
    await core.workers.consolidate(partition,batch_id=batch['batch_id'])
    async with core.database.transaction(read_only=True) as connection:
        original = await (await connection.execute("SELECT status FROM memory.consolidation_batches WHERE batch_id=%s",(batch['batch_id'],))).fetchone()
        state = await (await connection.execute("SELECT active_batch_id FROM memory.consolidation_state WHERE partition_id=%s",(partition,))).fetchone()
    assert original['status']=='applied' and state['active_batch_id']=='other-active'
    assert len(model.submissions)==2


async def test_experience_material_read_skips_status_but_keeps_source_authority(core,ordinary_users,monkeypatch):
    alice,bob = ordinary_users
    episode = await examples.episode(core,alice,'material-does-not-need-consolidation-history')
    statuses = AsyncMock(side_effect=AssertionError('material read attempted status history'))
    monkeypatch.setattr(core.memory.pipeline_recovery,'statuses_on',statuses)
    page = await core.memory.episodes_by_refs(alice,((episode.episode_id,episode.revision),),include_consolidation=False)
    assert [row.episode_id for row in page.items]==[episode.episode_id]
    other = await core.memory.episodes_by_refs(bob,((episode.episode_id,episode.revision),),include_consolidation=False)
    assert not other.items and not other.coverage.complete
    statuses.assert_not_awaited()


async def test_only_latest_batch_per_episode_is_source_checked(core,ordinary_users,monkeypatch):
    alice,_ = ordinary_users
    model,item,partition,batch = await unapplied_success(core,alice)
    async with core.database.transaction() as connection:
        # Historical attempts can have large frozen requests. They must not all
        # be fetched/proved merely to render one current episode status.
        for number in range(12):
            manifest = deepcopy(batch['manifest'])
            manifest['historical_payload'] = 'x'*100000
            await connection.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,"
                "status,manifest,dependency_refs,context_fingerprint,created_at) "
                "VALUES(%s,%s,%s,'failed',%s,%s,%s,now()-interval '1 day')",
                (f'historical-{number}',partition,batch['schema_version'],Jsonb(manifest),
                 Jsonb(batch['dependency_refs']),batch['context_fingerprint']))
    from tianshu.memory import recovery
    original = recovery.check_ref_groups_on
    checked = []
    async def observe(sources,connection,context,groups):
        checked.append(len(groups))
        return await original(sources,connection,context,groups)
    monkeypatch.setattr(recovery,'check_ref_groups_on',observe)
    page = await core.memory.episodes_by_refs(alice,((item.episode_id,item.revision),))
    assert len(page.items)==1 and checked==[1]


@pytest.mark.parametrize('exhausted',[False,True])
async def test_upstream_524_uses_original_computation_budget_and_keeps_unknown_receipt(core,ordinary_users,exhausted):
    alice,_ = ordinary_users
    model,item,partition,batch = await two_unknown_batch(core,alice,'524-original-budget')
    request = model.submissions[-1][0]
    await model.ledger.finish(request.attempt_id,'unknown',error_code='upstream_http_524')
    unknown = ledger_receipt(await model.ledger.get(request.attempt_id))
    model.results[request.attempt_id] = replace(model.results[request.attempt_id],receipt=unknown)
    if exhausted:
        async with core.database.transaction() as connection:
            await connection.execute("UPDATE runtime.recovery_lineages SET max_attempts=attempts WHERE lineage_id=%s",
                (batch['manifest']['recovery']['lineage_id'],))
    await mature(core,batch)
    await core.workers.consolidate(partition)
    _,current = await waits.batch_for_episode(core,item.episode_id)
    assert len(model.submissions)==(2 if exhausted else 3)
    assert current['status']==('quarantined' if exhausted else 'applied')
    assert ledger_receipt(await model.ledger.get(request.attempt_id))==unknown


async def test_index_migration_restores_timeout_and_preserves_episode_rows(core,ordinary_users):
    alice,_ = ordinary_users
    item = await examples.episode(core,alice,'index-only-migration')
    async with core.database.transaction() as connection:
        before = await (await connection.execute('SELECT * FROM memory.episodes WHERE episode_id=%s',(item.episode_id,))).fetchone()
        await connection.execute('DROP INDEX memory.consolidation_episode_membership')
        await connection.execute('DROP INDEX memory.consolidation_recovery_lineage')
        await connection.execute("SET LOCAL statement_timeout='7s'")
        await connection.execute(files('tianshu.memory').joinpath('episode_batch_lookup.sql').read_text(),prepare=False)
        timeout = await (await connection.execute("SELECT current_setting('statement_timeout') AS value")).fetchone()
        after = await (await connection.execute('SELECT * FROM memory.episodes WHERE episode_id=%s',(item.episode_id,))).fetchone()
        indexes = await (await connection.execute("SELECT indexrelid::regclass::text AS name FROM pg_index "
            "WHERE indrelid='memory.consolidation_batches'::regclass AND indisvalid")).fetchall()
    assert before==after and timeout['value']=='7s'
    assert {'memory.consolidation_episode_membership','memory.consolidation_recovery_lineage'} <= {r['name'] for r in indexes}


@pytest.mark.parametrize('condition',['paused','future_due'])
async def test_success_wake_respects_pause_and_persisted_observation_backoff(core,ordinary_users,condition):
    alice,_ = ordinary_users
    _,_,partition,batch = await unapplied_success(core,alice)
    async with core.database.transaction() as connection:
        if condition=='paused':
            await connection.execute('UPDATE runtime.recovery_lineages SET paused=true WHERE lineage_id=%s',
                (batch['manifest']['recovery']['lineage_id'],))
        else:
            await connection.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()+interval '1 minute' "
                "WHERE batch_id=%s",(batch['batch_id'],))
        assert await core.workers.consolidation.retries.successful_on(connection,partition,
            await core.memory.jobs.current_generation_on(connection)) is None
