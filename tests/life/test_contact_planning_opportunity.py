"""Opted-in check-ins use semantic planning and the normal delivery controls."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.life import contact_planning, planner
from tianshu.life.attention import AttentionPolicy
from tianshu.life.salience import evaluate
from tianshu.models.types import ModelCompletion, ModelPart, ModelResult
from tianshu.runtime.serialization import wire
from test_rbf_planning_recovery import planning_context


def preferences():
    return {'enabled':True,'timezone':'UTC','quiet_start':'23:00','quiet_end':'08:00',
        'cooldown_seconds':1800,'daily_quota':3,'unanswered_limit':3,'availability':'unknown'}


async def test_global_routine_gets_check_in_opportunity_without_private_messages():
    context = planning_context()
    target = replace(context,conversation_id='private',person_id='person',delivery_allowed=True)
    private_read = AsyncMock(side_effect=AssertionError('routine may not read destination private scene'))
    life = SimpleNamespace(contacts=SimpleNamespace(resolve_target=AsyncMock(return_value=target),
        preferences=AsyncMock(return_value=preferences()),history=AsyncMock(return_value=[])),
        identity=object(),attention=AttentionPolicy(),memory=SimpleNamespace(episodes_between=private_read))
    value,basis,refs = await contact_planning.material(SimpleNamespace(life=life),context,datetime(2026,11,9,12,tzinfo=UTC))
    assert value['enabled'] and not value['conversation_read'] and value['window']['decision']=='send'
    assert basis[0]['kind']=='contact_opportunity' and basis[0]['source_refs']==[] and not refs
    assert value['basis_id']==basis[0]['id']
    private_read.assert_not_awaited()


@pytest.mark.parametrize('kind,accepted',[
    ('contact_opportunity',True),('conversation',True),('commitment',True),
    ('actual_result',True),('authored_persona',False)])
def test_contact_uses_declared_basis_and_keeps_persona_out_of_factual_support(monkeypatch,kind,accepted):
    data = {'basis':[{'id':'basis','kind':kind,'source_refs':[]}],
        'goals':[],'capabilities':[],'unfinished':[],'contact_opportunity':{'enabled':True}}
    monkeypatch.setattr(planner,'frozen_material',lambda _request:data)
    body = {'activity':None,'reason':'quiet hour between activities','goal_changes':[],
        'arrangements':[],'cancel_activity_ids':[],'free_time_reason':'leave space',
        'contact_reason':'a brief friendly check-in is welcome',
        'contact':{'summary':'午安，想和你聊会儿。','reason':'a brief friendly check-in is welcome',
            'basis_ids':['basis'],'after_minutes':0,'expires_minutes':30}}
    result = ModelResult((ModelPart('text',text=json.dumps(body)),),SimpleNamespace(status='succeeded'),
        completion=ModelCompletion('complete',('stop',)))
    decision,_ = planner.decision(result,object())
    assert bool(decision['contact']) is accepted


@pytest.mark.parametrize('condition,expected',[
    ('planned','send'),('plain','archive'),('quiet','defer'),('disabled','archive'),('duplicate','archive')])
def test_semantic_contact_still_obeys_timing_opt_out_and_duplication(condition,expected):
    now = datetime(2026,11,9,7 if condition=='quiet' else 12,30 if condition=='quiet' else 0,tzinfo=UTC)
    settings = preferences()
    if condition=='disabled':settings['enabled']=False
    assessment = evaluate({'novelty':1,'interest_fit':0,'affect_pull':0,'recency_need':.5,
        'social_cost':.2,'repetition':1 if condition=='duplicate' else 0})
    assert assessment['score'] < assessment['thresholds']['engage']
    payload = {'salience':assessment}
    if condition!='plain':payload['planner_decision']={'basis_ids':['opportunity'],'reason':'friendly check-in'}
    candidate = {'kind':'experience','delivery_kind':'share_candidate','due_at':now,
        'created_at':now,'expires_at':now+timedelta(hours=1),'target_ref':'chat','payload':payload}
    assert AttentionPolicy().decide(candidate,settings,[],now=now).decision==expected


async def test_silence_reason_is_persisted_with_application(database):
    from historical_support import planner_setup
    output = json.dumps({'activity':None,'reason':'rest','goal_changes':[],'arrangements':[],
        'cancel_activity_ids':[],'free_time_reason':'quiet time','contact':None,
        'contact_reason':'leave the person time to reply'})
    value = await planner_setup(database,output)
    await value.worker.plan('actor',1,wire(value.context),value.data_generation)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT application_results->'contact' AS contact "
            "FROM life.plan_generations WHERE state='applied' ORDER BY created_at DESC LIMIT 1")).fetchone()
    assert row['contact']=={'decision':'not_proposed','reason':'leave the person time to reply','candidate_id':None}


@pytest.mark.parametrize('current_contract',[False,True])
def test_unexplained_silence_is_unknown_for_new_contact_material_and_old_receipts_remain_readable(monkeypatch,current_contract):
    data = {'basis':[],'goals':[],'capabilities':[],'unfinished':[]}
    if current_contract:data['contact_opportunity']={'enabled':True}
    monkeypatch.setattr(planner,'frozen_material',lambda _request:data)
    body = {'activity':None,'reason':'rest','goal_changes':[],'arrangements':[],
        'cancel_activity_ids':[],'free_time_reason':'quiet time'}
    result = ModelResult((ModelPart('text',text=json.dumps(body)),),SimpleNamespace(status='succeeded'),
        completion=ModelCompletion('complete',('stop',)))
    decision,_ = planner.decision(result,object())
    assert any(item['reason']=='planner_contact_decision_missing' for item in decision['_invalid_candidates']) is current_contract
