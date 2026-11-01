from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document
from tianshu.life.service import LifeService
from tianshu.life.worker import LifeWorker
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, SourceRef, TrustedContext


@pytest.mark.asyncio
async def test_last_submission_boundary_defers_to_new_expression_generation(database):
    await migrate(database,(Migration('0031_life',files('tianshu.life').joinpath('schema.sql').read_text()),))
    context=TrustedContext('author','actor','person','private',('scope',),('scope',),1,OriginKind.ACTOR,frozenset({'*'}),delivery_allowed=True)
    identity=SimpleNamespace(check=AsyncMock(),refresh_scheduled_context=AsyncMock(return_value=context))
    jobs=JobQueue(database.config.dsn)
    life=LifeService(database,CommandStore(database),jobs,identity,None,None,
        sources=SimpleNamespace(validate_persistence=AsyncMock(return_value=True)),memory=None)
    now=datetime.now(UTC)
    preferences={'timezone':'UTC','quiet_start':'00:00','quiet_end':'00:00','cooldown_seconds':0,'daily_quota':3,
        'unanswered_limit':3,'enabled':True,'expiry_seconds':14400,'availability':'busy','available_until':now+timedelta(minutes=15)}
    life.contacts=SimpleNamespace(preferences=AsyncMock(return_value=preferences),history=AsyncMock(return_value=[]))
    expression=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(state='waiting_delivery',reason=None,receipt=None)))
    worker=LifeWorker(life,None,None,None,None,expression)
    jobs.register('life.contact_due',worker.contact_due)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.contact_candidates(id,actor_id,target_ref,person_id,kind,summary,due_at,expires_at,state,delivery_kind,context,payload,expression_id) "
            "VALUES ('candidate','actor','private','person','experience','A relevant actual result',%s,%s,'sending','share_candidate',%s,%s,'contact:candidate:1')",
            (now-timedelta(seconds=1),now+timedelta(hours=2),document(context),document({'salience':{'score':1,'thresholds':{'engage':0}}})))
    decision=await worker.contact_preflight(context,'candidate',expression_id='contact:candidate:1')
    assert decision['decision']=='defer'
    async with database.transaction(read_only=True) as connection:
        row=await (await connection.execute("SELECT * FROM life.contact_candidates WHERE id='candidate'")).fetchone()
    assert row['delivery_generation']==2 and row['expression_id'] is None and row['state']=='waiting'
    assert (await worker.contact_preflight(context,'candidate',expression_id='contact:candidate:1'))['decision']=='cancel'
    preferences.update(availability='unknown',available_until=None)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.contact_candidates SET due_at=now()-interval '1 second' WHERE id='candidate'")
    await worker.contact_due('candidate',row['revision'])
    assert expression.send.call_args.args[1].expression_id=='contact:candidate:2'
    await worker.expression_result(context,'contact:candidate:1',{'state':'cancelled','receipt':None,'reason':'contact_deferred'})
    async with database.transaction(read_only=True) as connection:
        current=await (await connection.execute("SELECT * FROM life.contact_candidates WHERE id='candidate'")).fetchone()
    assert current['state']=='waiting_expression' and current['expression_id']=='contact:candidate:2'


@pytest.mark.asyncio
async def test_old_self_report_cannot_renew_availability_or_enable_disabled_contact(database):
    from tianshu.life.availability import save
    from tianshu.life.contacts import Contacts
    await migrate(database,(Migration('0031_life',files('tianshu.life').joinpath('schema.sql').read_text()),
        Migration('0033_life_contact_expiry',files('tianshu.life').joinpath('contact_expiry.sql').read_text())))
    context=TrustedContext('author','actor','person','private',('scope',),('scope',),1,OriginKind.HUMAN,frozenset({'*'}),delivery_allowed=True)
    identity=SimpleNamespace(check=AsyncMock(),check_many=AsyncMock(return_value=(True,)))
    occurred=datetime.now(UTC)-timedelta(hours=2)
    sources=SimpleNamespace(validate_persistence=AsyncMock(return_value=True),describe_for_write_on=AsyncMock(return_value=[
        {'origin_kind':'human','speaker_id':'person','occurred_at':occurred,'evidence_unit':'original-input'}]))
    life=LifeService(database,CommandStore(database),None,identity,None,Contacts(database),sources=sources,memory=None)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.contact_preferences(actor_id,person_id,scope_ids,enabled) VALUES ('actor','',ARRAY['scope'],false)")
    for client in ('first-read','replayed-material'):
        await save(life,context,{'availability':'busy','valid_for_minutes':60,'client_request_id':client},(SourceRef('old-input',1),))
    current=await life.contacts.preferences(context)
    assert current['availability']=='unknown' and current['available_until'] is None
    assert current['enabled'] is False and current['revision']==1
