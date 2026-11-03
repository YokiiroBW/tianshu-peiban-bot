"""Real PG/source/queue consolidation stories, authored but not run during development."""

from dataclasses import replace
import asyncio
from datetime import UTC,datetime,timedelta
import json

import pytest

from tianshu.memory.extraction_contract import SCHEMA_VERSION
from tianshu.models.types import ModelCompletion,ModelMessage,ModelPart,ModelReceipt,ModelRequest,ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.api import Page
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage,OriginKind,RequestBudget,ServiceClass
from psycopg.types.json import Jsonb


class FixedConsolidator:
    def __init__(self,*,unknown=False,invalid=False,truncated=False):
        self.requests=[]
        self.results={}
        self.unknown,self.invalid,self.truncated=unknown,invalid,truncated

    async def generation_profile(self, request):
        return {"route_id":"fixture","route_revision":1,"protocol":"openai.chat","model":"fixture",
            "output_parameters":{"max_tokens":request.max_output_tokens}}

    def answer(self,request):
        value=json.loads(request.messages[0].parts[0].text)
        operations=[]
        results=[]
        for target in value['targets']:
            owned=[item for item in value['materials'] if item['id'] in target['new_material_ids']]
            op_ids=[]
            for material in owned:
                if '尾部事实' not in material['text']:
                    continue
                op_id='op'+str(len(operations)+1)
                op_ids.append(op_id)
                operations.append({'op_id':op_id,'kind':'add_claim','subject_id':target['subject_id'],
                    'target':{'create':'n'+str(len(operations)+1)},
                    'payload':{'predicate':'interest','value':'尾部事实','speaker_id':material['speaker_id'],
                        'assertion':'self_confirmed' if material['origin_kind']=='human' else 'observed',
                        'polarity':True,'conditions':{},'valid_from':None,'valid_until':None},
                    'support_groups':[[material['id']]],'context_refs':[],'reads':[],'reason':'原件明确支持'})
            results.append({'subject_id':target['subject_id'],'status':'changed' if op_ids else 'no_change',
                'processed_material_ids':target['new_material_ids'],'operation_ids':op_ids})
        if self.invalid:
            results=[]
        output={'schema_version':SCHEMA_VERSION,'batch_id':value['batch_id'],'target_results':results,
                'operations':operations,'unresolved':[],'needs_context':[]}
        if value.get('model_protocol')=='memory.proposal.v2':
            output={'targets':[{'subject_id':target['subject_id'],'status':target['status'],
                'operations':[{key:item for key,item in operation.items() if key not in {'op_id','subject_id'}}
                    for operation in operations if operation['op_id'] in target['operation_ids']],
                'unresolved':[]} for target in results], 'needs_context':[]}
        receipt=ModelReceipt(request.attempt_id,request.intent_id,'succeeded','submitted','fixture',1,
            'fixture','fixture','openai.chat','fixture')
        return ModelResult((ModelPart('text',text=json.dumps(output,ensure_ascii=False)),),receipt,
            {'finish_reason':'length' if self.truncated else 'stop'},
            ModelCompletion('incomplete' if self.truncated else 'complete',('length',) if self.truncated else ('stop',)))

    async def generate(self,request):
        self.requests.append(request)
        self.results[request.attempt_id]=self.answer(request)
        if self.unknown:
            self.unknown=False
            raise TimeoutError('synthetic connection loss after provider completion')
        return self.results[request.attempt_id]

    async def result(self,attempt_id,*,context):
        return self.results.get(attempt_id)

    async def status(self,attempt_id):
        return self.results[attempt_id].receipt if attempt_id in self.results else None


async def observe(core,context,key,text):
    refs=await core.sources.record_event(context,producer='web',event_id=key,revision=1,
        occurred_at=datetime.now(UTC),parts=({'kind':'text','text':text},),origin_kind=OriginKind.HUMAN)
    return await core.memory.observe_episode(context,{'event_id':key,'occurred_at':datetime.now(UTC),
        'summary':text[:100],'source_refs':refs,'origin_kind':'human'})


def configure(core,model):
    core.steps.models=model
    policy=replace(core.memory.consolidation.policy,quiet_seconds=0,max_input_tokens=64000,context_messages=0)
    core.memory.consolidation.policy=policy
    core.memory.consolidation.materials.policy=policy


async def row(core,episode):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute('SELECT * FROM memory.episodes WHERE episode_id=%s',(episode.episode_id,))).fetchone()


async def latest_batch(core, partition):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute(
            'SELECT * FROM memory.consolidation_batches WHERE partition_id=%s ORDER BY created_at DESC LIMIT 1',
            (partition,))).fetchone()


def new_texts(request):
    value = json.loads(request.messages[0].parts[0].text)
    return [item['text'] for item in value['materials'] if item['role'] == 'new']


async def test_truncated_short_inputs_reduce_the_actual_prefix_and_success_restores_capacity(core, ordinary_users):
    alice, _ = ordinary_users
    model = FixedConsolidator(truncated=True)
    configure(core, model)
    texts = ['第一条短原文', '第二条短原文', '第三条短原文']
    episodes = [await observe(core, alice, 'short_truncated_'+str(index), text)
        for index, text in enumerate(texts)]
    originals = [await row(core, episode) for episode in episodes]
    partition = originals[0]['consolidation_partition_id']
    await core.workers.consolidate(partition)
    failed = await latest_batch(core, partition)
    assert failed['status'] == 'failed' and failed['error_code'] == 'memory_consolidation_output_truncated'
    assert new_texts(model.requests[0]) == texts
    # Halving the old nominal character budget still admits all three short
    # originals. The next physical attempt must instead select a smaller prefix.
    assert sum(map(len, texts)) < failed['manifest']['source_budget']//2
    for episode, original in zip(episodes, originals, strict=True):
        assert (await row(core, episode))['extraction_cursor'] == original['extraction_cursor']

    model.truncated = False
    await core.workers.consolidate(partition)
    assert len(model.requests) == 2 and new_texts(model.requests[1]) == texts[:1]
    assert (await latest_batch(core, partition))['status'] == 'applied'
    assert (await row(core, episodes[0]))['extraction_state'] == 'completed'
    assert (await row(core, episodes[1]))['extraction_state'] == 'pending'

    later_texts = ['后续新原文', '尾部事实']
    later = [await observe(core, alice, 'after_success_'+str(index), text)
        for index, text in enumerate(later_texts)]
    await core.workers.consolidate(partition)
    assert len(model.requests) == 3
    assert new_texts(model.requests[2]) == texts[1:] + later_texts
    assert (await latest_batch(core, partition))['manifest']['source_budget'] == min(
        core.memory.consolidation.policy.max_source_characters,
        core.memory.consolidation.policy.max_input_tokens//2)
    for episode in episodes + later:
        assert (await row(core, episode))['extraction_state'] == 'completed'
    assert any(item.value == '尾部事实' for item in (await core.claims.list(alice)).items)


async def test_irreducible_truncated_input_stops_without_another_attempt_or_batch(core, ordinary_users):
    alice, _ = ordinary_users
    model = FixedConsolidator(truncated=True)
    configure(core, model)
    episode = await observe(core, alice, 'minimum_truncated', 'x')
    original = await row(core, episode)
    partition = original['consolidation_partition_id']
    await core.workers.consolidate(partition)
    first = await latest_batch(core, partition)
    assert (await row(core, episode))['extraction_state'] == 'consolidation_failed'
    assert (await core.memory.extraction_status(alice, episode.episode_id))['action'] == 'configure_budget'
    await core.workers.recover()
    await core.workers.consolidate(partition)
    assert len(model.requests) == 1

    # This is the pending wake the old nominal-budget rule could have left.
    # Upgrading must settle it without replaying the known failed input.
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET extraction_state='pending' WHERE episode_id=%s", (episode.episode_id,))
        await conn.execute('UPDATE memory.consolidation_state SET wake_due_at=now() WHERE partition_id=%s', (partition,))
    await core.workers.consolidate(partition)
    await core.workers.consolidate(partition)
    after = await row(core, episode)
    assert len(model.requests) == 1 and (await latest_batch(core, partition))['batch_id'] == first['batch_id']
    assert after['extraction_state'] == 'consolidation_failed' and after['extraction_cursor'] == original['extraction_cursor']
    async with core.database.transaction(read_only=True) as conn:
        state = await (await conn.execute(
            'SELECT active_batch_id,wake_due_at FROM memory.consolidation_state WHERE partition_id=%s', (partition,))).fetchone()
    assert state['active_batch_id'] is None and state['wake_due_at'] is None


async def test_lost_success_smaller_attempt_is_consumed_with_its_original_material_map(core, ordinary_users):
    alice, _ = ordinary_users
    model = FixedConsolidator(truncated=True)
    configure(core, model)
    first = await observe(core, alice, 'known_truncation_before_unknown', '先前原文')
    remaining = await observe(core, alice, 'unprocessed_after_truncation', '剩余原文')
    partition = (await row(core, first))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    model.truncated, model.unknown = False, True
    await core.workers.consolidate(partition)
    active = await latest_batch(core, partition)
    assert active['status'] == 'applied'
    assert len(model.requests) == 2 and new_texts(model.requests[1]) == ['先前原文']
    attempt = model.requests[1].attempt_id
    stored_map = active['manifest']['steps'][active['step_ids'][-1]]

    later = await observe(core, alice, 'later_during_unknown', '后来原文')
    await core.memory.consolidation._execute(active['batch_id'],alice)
    observed = await batch_by_id(core,active['batch_id'])
    assert len(model.requests) == 2 and model.requests[1].attempt_id == attempt
    assert observed['batch_id'] == active['batch_id'] and observed['status'] == 'applied'
    assert observed['manifest']['steps'][observed['step_ids'][-1]] == stored_map
    assert (await row(core, first))['extraction_state'] == 'completed'
    assert (await row(core, remaining))['extraction_state'] == 'pending'
    assert (await row(core, later))['extraction_state'] == 'pending'


async def test_supplemented_truncation_reduces_the_original_range_and_preserves_its_tail(core, ordinary_users):
    from tianshu.memory.materials import batch_frozen

    class SupplementThenTruncate(FixedConsolidator):
        def answer(self, request):
            result = super().answer(request)
            if len(self.requests) == 1:
                proposal = json.loads(result.parts[0].text)
                proposal['needs_context'] = [{'kind':'material', 'ref':'m1','reason':'需要原件后续内容'}]
                return replace(result, parts=(ModelPart('text', text=json.dumps(proposal, ensure_ascii=False)),))
            if len(self.requests) == 2:
                return replace(result, native_response={'choices':[{'finish_reason':'length'}]},
                    completion=ModelCompletion('incomplete', ('length',)))
            return result

    alice, _ = ordinary_users
    model = SupplementThenTruncate()
    configure(core, model)
    text = 'segment '*2000 + '尾部事实'
    episode = await observe(core, alice, 'supplement_before_truncation', text)
    original = await row(core, episode)
    partition = original['consolidation_partition_id']
    await core.workers.consolidate(partition)
    failed = await latest_batch(core, partition)
    assert len(model.requests) == 2 and len(failed['step_ids']) == 2
    assert failed['error_code'] == 'memory_consolidation_output_truncated'
    assert (await row(core, episode))['extraction_cursor'] == original['extraction_cursor']
    actual = batch_frozen(failed)
    assert any(item['role'] == 'context' for item in actual['materials'].values())
    new = next(item for item in actual['materials'].values() if item['role'] == 'new')
    old_span = new['source_refs'][0]['locator']
    assert any(item['role'] == 'context' and item['source_refs'][0]['source_id'] == new['source_refs'][0]['source_id']
        and item['source_refs'][0]['locator']['start'] == old_span['end']
        for item in actual['materials'].values())
    assert failed['manifest']['episodes'][0]['before'] == original['extraction_cursor']
    assert failed['manifest']['episodes'][0]['source_refs'] == original['source_refs']
    assert all(ref in model.requests[1].source_refs for ref in model.requests[0].source_refs)

    await core.workers.consolidate(partition)
    assert len(model.requests) == 3
    assert len(new_texts(model.requests[2])[0]) == (old_span['end']-old_span['start'])//2
    partial = await row(core, episode)
    assert partial['extraction_state'] == 'pending'
    assert partial['extraction_cursor']['position'] == len(new_texts(model.requests[2])[0])
    await core.workers.consolidate(partition)
    assert len(model.requests) == 4 and (await row(core, episode))['extraction_state'] == 'completed'
    assert ''.join(new_texts(model.requests[2]) + new_texts(model.requests[3])) == text
    assert any(item.value == '尾部事实' for item in (await core.claims.list(alice)).items)


async def test_candidate_preparation_recalls_once_after_multiple_input_shrinks(core, ordinary_users, monkeypatch):
    from tianshu.memory.consolidation import COMMIT_RESERVE_SECONDS, MODEL_REQUEST_SECONDS

    alice, _ = ordinary_users
    model = FixedConsolidator()
    configure(core, model)
    pipeline = core.memory.consolidation
    first = await observe(core, alice, "candidate_budget_first", "前文")
    for index in range(3):
        await observe(core,alice,"candidate_budget_later_"+str(index),"后续原文"+str(index))
    partition = (await row(core, first))["consolidation_partition_id"]
    budgets = []
    async def candidates(context, query, budget, **kwargs):
        async with core.database.transaction(read_only=True) as conn:
            state = await (await conn.execute(
                "SELECT lease_until FROM memory.consolidation_state WHERE partition_id=%s", (partition,))).fetchone()
        assert budget.deadline_at == state["lease_until"] - timedelta(
            seconds=MODEL_REQUEST_SECONDS + COMMIT_RESERVE_SECONDS)
        budgets.append(budget)
        assert query=="前文"
        return Page((),None,Coverage(True))
    pipeline.materials.record_candidates = candidates
    request = pipeline._budgeted_request
    attempts = 0
    def shrink_twice(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise AppError("memory_consolidation_input_budget")
        return request(*args, **kwargs)
    monkeypatch.setattr(pipeline, "_budgeted_request", shrink_twice)
    await core.workers.consolidate(partition)
    assert len(budgets) == 1
    assert 0 < budgets[0].remaining() <= 55
    assert len(model.requests) == 1 and model.requests[0].budget.remaining() > 100
    diagnostics=(await latest_batch(core,partition))["manifest"]["preparation"]
    assert diagnostics["assembly_attempts"]==3 and diagnostics["remote_calls"]==1
    assert diagnostics["source_reads"]==4 and diagnostics["final_episodes"]==1
    assert diagnostics["final_materials"]==1 and diagnostics["final_images"]==0
    assert diagnostics["trial_count"]>=2 and diagnostics["elapsed_ms"]>=diagnostics["remote_ms"]>=0


@pytest.mark.parametrize("remote_failure",["raised","rerank_unavailable"])
async def test_candidate_preparation_degrades_to_local_records_without_missing_originals(core,ordinary_users,remote_failure):
    alice,_=ordinary_users
    model=FixedConsolidator()
    configure(core,model)
    refs=await core.sources.record_event(alice,producer="web",event_id="local-candidate",revision=1,
        occurred_at=datetime.now(UTC),parts=({"text":"既有阅读兴趣"},),origin_kind=OriginKind.HUMAN)
    async with core.database.transaction() as conn:
        old=await core.claims.put_on(conn,alice,{"subject_id":alice.person_id,"speaker_id":alice.person_id,
            "predicate":"interest","value":"阅读","assertion":"self_confirmed","source_refs":refs},automatic=True)
    calls=[]
    async def candidates(context,query,budget,**kwargs):
        calls.append(query)
        if remote_failure=="raised":
            raise TimeoutError("synthetic candidate outage")
        return Page((),None,Coverage(False,missing=("ranked_selection","rerank_unavailable")))
    core.memory.consolidation.materials.record_candidates=candidates
    episode=await observe(core,alice,"new-under-local-fallback","可用的新原件")
    partition=(await row(core,episode))["consolidation_partition_id"]
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    assert batch["status"]=="applied" and len(calls)==1 and len(model.requests)==1
    assert any(item["id"]==old.claim_id for item in batch["manifest"]["frozen"]["records"].values())
    missing=batch["manifest"]["frozen"]["selection_coverage"]["record_candidates_missing"]
    assert ("remote_candidates_unavailable" if remote_failure=="raised" else "rerank_unavailable") in missing
    assert (await row(core,episode))["extraction_state"]=="completed"
    assert (await row(core,episode))["extraction_missing"]==[]
    diagnostics=batch["manifest"]["preparation"]
    assert diagnostics["remote_calls"]==1
    assert diagnostics["remote_failures"]==int(remote_failure=="raised")
    assert diagnostics["remote_degradations"]==int(remote_failure!="raised")


async def test_input_budget_change_resumes_the_same_unadvanced_material_without_a_new_episode(core,ordinary_users):
    alice,_=ordinary_users
    model=FixedConsolidator()
    configure(core,model)
    pipeline=core.memory.consolidation
    low=replace(pipeline.policy,max_input_tokens=4000)
    pipeline.policy=pipeline.materials.policy=low

    class FixedCounter:
        def measure(self,*_args):
            return (10000,None)

    pipeline.counter=FixedCounter()
    episode=await observe(core,alice,'budget_original_material','尾部事实')
    original=await row(core,episode)
    partition=original['consolidation_partition_id']
    await core.workers.consolidate(partition)
    stopped=await core.memory.extraction_status(alice,episode.episode_id)
    assert stopped['action']=='configure_budget'
    assert stopped['budget_failure']['configuration_changed'] is False
    assert stopped['budget_failure']['required_tokens']==10000
    assert (await row(core,episode))['extraction_cursor']==original['extraction_cursor']
    async with core.database.transaction(read_only=True) as conn:
        state=await (await conn.execute('SELECT * FROM memory.consolidation_state WHERE partition_id=%s',(partition,))).fetchone()
        count_before=await (await conn.execute("SELECT count(*) AS total FROM runtime.job_keys WHERE task='memory.consolidate' AND key LIKE %s",
            ('memory-consolidate:'+partition+':%',))).fetchone()
    assert state['wake_due_at'] is None and state['active_batch_id'] is None
    assert state['budget_failure']['configuration']['input_tokens']==4000

    # Neither a periodic recovery pass nor an unchanged explicit command submits
    # a model or re-arms the rejected material every 30 seconds.
    async with core.database.transaction() as conn:
        unchanged=await core.workers.recovery.resume_on(conn,alice,{'episode_id':episode.episode_id,
            'expected_revision':episode.revision,'client_request_id':'budget_unchanged'})
    await core.workers.recover()
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        count_after=await (await conn.execute("SELECT count(*) AS total FROM runtime.job_keys WHERE task='memory.consolidate' AND key LIKE %s",
            ('memory-consolidate:'+partition+':%',))).fetchone()
    assert unchanged.data['action']=='configure_budget' and unchanged.data['queued'] is False
    assert count_after['total']==count_before['total'] and not model.requests

    # Quiet/max-wait settings do not change input capacity.
    pipeline.policy=pipeline.materials.policy=replace(low,quiet_seconds=10,max_wait_seconds=700)
    assert (await core.memory.extraction_status(alice,episode.episode_id))['action']=='configure_budget'
    pipeline.policy=pipeline.materials.policy=replace(low,max_input_tokens=16000)
    changed=await core.memory.extraction_status(alice,episode.episode_id)
    assert changed['action']=='refresh_material'
    assert changed['budget_failure']['configuration_changed'] is True
    async with core.database.transaction() as conn:
        resumed=await core.workers.recovery.resume_on(conn,alice,{'episode_id':episode.episode_id,
            'expected_revision':episode.revision,'client_request_id':'budget_corrected'})
    assert resumed.data['action']=='refresh_material' and resumed.data['queued'] is True
    await core.workers.consolidate(partition)
    assert len(model.requests)==1
    assert (await row(core,episode))['extraction_state']=='completed'
    assert (await row(core,episode))['extraction_cursor']!=original['extraction_cursor']
    async with core.database.transaction(read_only=True) as conn:
        state=await (await conn.execute('SELECT * FROM memory.consolidation_state WHERE partition_id=%s',(partition,))).fetchone()
    assert state['budget_failure'] is None


async def test_budget_configuration_change_keeps_an_unknown_original_attempt(core,ordinary_users):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    episode=await observe(core,alice,'unknown_before_budget_change','尾部事实')
    original=await row(core,episode)
    partition=original['consolidation_partition_id']
    await core.workers.consolidate(partition)
    first_attempt=model.requests[0].attempt_id
    pipeline=core.memory.consolidation
    pipeline.policy=pipeline.materials.policy=replace(pipeline.policy,max_input_tokens=32000)
    status=await core.memory.extraction_status(alice,episode.episode_id)
    assert status['action']=='observe_original_attempt'
    async with core.database.transaction() as conn:
        result=await core.workers.recovery.resume_on(conn,alice,{'episode_id':episode.episode_id,
            'expected_revision':episode.revision,'client_request_id':'unknown_budget_observation'})
    assert result.data['action']=='observe_original_attempt'
    model.release()
    await core.workers.consolidate(partition)
    assert len(model.requests)==1 and model.requests[0].attempt_id==first_attempt
    assert (await row(core,episode))['extraction_state']=='completed'


async def test_shared_prefix_uses_one_model_request_for_several_new_episodes(core,ordinary_users):
    alice,_=ordinary_users
    model=FixedConsolidator()
    configure(core,model)
    first=await observe(core,alice,'first_shared','先说明前文')
    last=await observe(core,alice,'last_shared','补充尾部事实')
    original=await row(core,last)
    await core.workers.consolidate(original['consolidation_partition_id'])
    assert len(model.requests)==1
    payload=json.loads(model.requests[0].messages[0].parts[0].text)
    assert [item['text'] for item in payload['materials'] if item['role']=='new']==['先说明前文','补充尾部事实']
    assert (await row(core,first))['extraction_state']=='completed'
    assert (await row(core,last))['extraction_state']=='completed'
    assert any(item.value=='尾部事实' for item in (await core.claims.list(alice)).items)


async def test_lost_success_consumes_original_attempt_and_keeps_material_map(core,ordinary_users):
    alice,_=ordinary_users
    model=FixedConsolidator(unknown=True)
    configure(core,model)
    first=await observe(core,alice,'unknown_original','尾部事实')
    original=await row(core,first)
    await core.workers.consolidate(original['consolidation_partition_id'])
    assert (await row(core,first))['extraction_state']=='completed'
    completed=await latest_batch(core,original['consolidation_partition_id'])
    frozen_map=completed['manifest']['steps'][completed['step_ids'][-1]]
    later=await observe(core,alice,'unrelated_later','后来新材料')
    await core.memory.consolidation._execute(completed['batch_id'],alice)
    assert len(model.requests)==1
    assert (await row(core,first))['extraction_state']=='completed'
    assert (await row(core,later))['extraction_state']=='pending'
    assert (await batch_by_id(core,completed['batch_id']))['manifest']['steps'][completed['step_ids'][-1]]==frozen_map
    assert any(item.value=='尾部事实' for item in (await core.claims.list(alice)).items)


@pytest.mark.parametrize('failure',['invalid','truncated'])
async def test_invalid_or_truncated_output_does_not_partially_commit_or_advance(core,ordinary_users,failure):
    alice,_=ordinary_users
    model=FixedConsolidator(**{failure:True})
    configure(core,model)
    episode=await observe(core,alice,'bad_output','尾部事实')
    before=await row(core,episode)
    await core.workers.consolidate(before['consolidation_partition_id'])
    after=await row(core,episode)
    assert after['extraction_cursor']==before['extraction_cursor']
    assert not (await core.claims.list(alice)).items
    async with core.database.transaction(read_only=True) as conn:
        batch=await (await conn.execute('SELECT * FROM memory.consolidation_batches WHERE partition_id=%s ORDER BY created_at DESC LIMIT 1',
            (before['consolidation_partition_id'],))).fetchone()
        step=await (await conn.execute('SELECT state FROM memory.model_steps WHERE step_id=%s',(batch['step_ids'][0],))).fetchone()
    assert batch['status']=='failed'
    assert step['state']=='succeeded'
    assert batch['error_code']==('memory_consolidation_output_truncated' if failure=='truncated' else 'memory_consolidation_target_coverage')

async def test_sparse_identity_resolution_does_not_truncate_a_301_member_group(core):
    group='synthetic_large_group'
    scope='synthetic_group_scope'
    connection='synthetic_group_connection'
    async with core.database.transaction() as conn:
        await conn.execute("INSERT INTO identity.scopes(scope_id,actor_id,kind,label) VALUES(%s,%s,'group','synthetic')",(scope,core.actor))
        await conn.execute("INSERT INTO identity.scope_grants(scope_id,principal_id,permissions) VALUES(%s,%s,ARRAY['*'])",(scope,core.owner.principal_id))
        await conn.execute("INSERT INTO identity.conversations(conversation_id,actor_id,scope_id,kind,label) VALUES(%s,%s,%s,'group','synthetic')",(group,core.actor,scope))
        await conn.execute("INSERT INTO identity.connections(connection_id,principal_id,actor_id,namespace,self_external_id) VALUES(%s,%s,%s,'synthetic','0')",(connection,core.owner.principal_id,core.actor))
        await conn.execute("INSERT INTO identity.people(person_id,display_name) SELECT 'synthetic_member_'||n,'member_'||n FROM generate_series(0,300) n")
        await conn.execute("INSERT INTO identity.conversation_members(conversation_id,connection_id,external_id,person_id) SELECT %s,%s,n::text,'synthetic_member_'||n FROM generate_series(0,300) n",(group,connection))
    context=await core.identity.context(core.owner.principal_id,actor_id=core.actor,conversation_id=group)
    async with core.database.transaction(read_only=True) as conn:
        subjects=await core.identity.resolve_memory_subjects_on(conn,context,('synthetic_member_300',))
    assert {item['person_id'] for item in subjects}=={'synthetic_member_300','group:'+group}


async def test_quote_original_remains_observation_with_reported_author_metadata(core,ordinary_users):
    from tianshu.conversation.service import ConversationService
    from tianshu.conversation.types import InputPart
    alice,bob=ordinary_users
    part=InputPart('quote','这是一段转述',metadata={'original_author_display_name':'B',
        'verified_original_author_person_id':bob.person_id,'forward_node_index':0})
    ConversationService._validate_parts((part,))
    refs=await core.sources.record_event(alice,producer='web',event_id='synthetic_quote',revision=1,
        occurred_at=datetime.now(UTC),parts=({'kind':part.kind,'text':part.text,'metadata':part.metadata},),origin_kind=OriginKind.HUMAN)
    original=await core.sources.read(alice,refs[0],{})
    assert original['origin_kind']=='observation' and original['speaker_id'] is None
    assert original['locator']['metadata']['original_author_display_name']=='B'
    converted=ConversationService._message('user',({'kind':part.kind,'text':part.text,'metadata':part.metadata},))
    assert json.loads(converted.parts[0].text)['kind']=='quote'

async def test_explicit_episode_versions_do_not_depend_on_a_max_sequence_watermark(core,ordinary_users):
    alice,_=ordinary_users
    earlier=await observe(core,alice,'version_early','先分配身份的经历')
    later=await observe(core,alice,'version_later','后提交可见的经历')
    page=await core.memory.episodes_by_refs(alice,((earlier.episode_id,earlier.revision),))
    assert page.coverage.complete and page.items[0].episode_id==earlier.episode_id
    assert page.items[0].sequence<later.sequence
    changed=await core.memory.episodes_by_refs(alice,((earlier.episode_id,earlier.revision+1),))
    assert not changed.items and not changed.coverage.complete
    assert 'episodes_unavailable_or_revised' in changed.coverage.missing


async def test_episode_speaker_comes_from_original_sources_and_never_tool_prerequisites(core,ordinary_users):
    from tianshu.runtime.errors import AppError
    alice,bob=ordinary_users
    human=await observe(core,alice,'verified_author','唯一已核验作者')
    assert human.speaker_id == alice.person_id
    second=await observe(core,bob,'another_author','另一位真实作者')
    owner=await core.identity.context(core.owner.principal_id,actor_id=core.actor)
    proposal={'event_id':'mixed_authors','origin_kind':'human',
        'occurred_at':datetime.now(UTC),'summary':'多作者材料',
        'source_refs':(*human.source_refs,*second.source_refs)}
    # Owning the actor does not disclose either person's private conversation.
    with pytest.raises(AppError,match='source_unavailable'):
        await core.memory.observe_episode(owner,proposal)
    async with core.database.transaction() as conn:
        for scope in sorted(set(human.scope_ids)|set(second.scope_ids)):
            await conn.execute("INSERT INTO identity.scope_grants(scope_id,principal_id,permissions) "
                "VALUES(%s,%s,ARRAY['read'])",(scope,owner.principal_id))
    # This fixture now grants exactly these two source domains, without changing
    # the owner's global permissions or relabeling either real original author.
    shared_reader=await core.identity.context(owner.principal_id,actor_id=core.actor)
    mixed=await core.memory.observe_episode(shared_reader,proposal)
    assert mixed.speaker_id is None
    tool=await core.memory.observe_episode(alice,{'event_id':'human_prerequisite','origin_kind':'tool_result',
        'occurred_at':datetime.now(UTC),'summary':'由人类请求触发的工具结果','source_refs':human.source_refs})
    assert tool.speaker_id is None


async def test_reflection_time_view_and_frozen_episode_version_are_explicit(core,ordinary_users):
    from tianshu.companion.types import ReflectionProposal
    from tianshu.runtime.errors import AppError
    alice,_=ordinary_users
    episode=await observe(core,alice,'reflection_original','反思的实际原件')
    proposal=ReflectionProposal('synthetic_time_reflection',core.actor,(episode.episode_id,),
        '这是一条主观理解','interaction',datetime.now(UTC),episode.source_refs,episode.scope_ids)
    with pytest.raises(AppError,match='reflection_episode_revision_conflict'):
        await core.memory.record_reflection(alice,proposal,episode_revisions={episode.episode_id:episode.revision+1})
    await core.memory.record_reflection(alice,proposal,episode_revisions={episode.episode_id:episode.revision})
    past=await core.memory.reflection_page(alice,time_mode='known_at',known_at=datetime.now(UTC)-timedelta(days=1))
    assert not past.items
    valid=await core.memory.reflection_page(alice,time_mode='valid_at',valid_at=datetime.now(UTC))
    assert valid.items and not valid.coverage.complete and 'reflection_valid_time_unknown' in valid.coverage.missing


async def test_pending_receipt_reports_only_frozen_targets_without_inventing_results(core,ordinary_users):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    episode=await observe(core,alice,'pending_targets','尾部事实')
    before=await row(core,episode)
    await core.workers.consolidate(before['consolidation_partition_id'])
    page=await core.memory.episodes_by_refs(alice,((episode.episode_id,episode.revision),))
    status=page.items[0].consolidation
    assert {'subject_id':alice.person_id} in status['targets']
    assert not status['target_results'] and all(set(item)=={'subject_id'} for item in status['targets'])
    async with core.database.transaction(read_only=True) as conn:
        current=await (await conn.execute('SELECT * FROM memory.episodes WHERE episode_id=%s',(episode.episode_id,))).fetchone()
        batch=await (await conn.execute('SELECT * FROM memory.consolidation_batches WHERE batch_id=%s',(status['batch_id'],))).fetchone()
    hidden=core.workers.recovery._status_value(current,batch,{batch['batch_id']:False})
    assert not hidden['targets'] and not hidden['target_results']


async def test_legacy_application_error_is_converted_once_and_original_attempt_is_only_observed(core,ordinary_users):
    alice,_=ordinary_users
    episode=await observe(core,alice,'legacy_invalid_predicate','旧版真实可读原件')
    current=await row(core,episode)
    step_id=f'extract:{episode.episode_id}:{current["sequence"]}:0'
    material={'allowed_subject_ids':[alice.person_id],'occurred_at':datetime.now(UTC).isoformat(),
        'sources':[{'origin_kind':'human','speaker_id':alice.person_id,
            'coverage':{'ranges':[{'start':0,'end':8}],'total':8}}]}
    request=ModelRequest('legacy_fixed_intent','legacy_fixed_attempt','text',ServiceClass.ELASTIC,alice,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=10)),
        (ModelMessage('user',(ModelPart('text',text=json.dumps(material)),)),),source_refs=episode.source_refs)
    receipt=ModelReceipt(request.attempt_id,request.intent_id,'succeeded','submitted','fixture',1,
        'fixture','fixture','openai.chat','fixture')
    result=ModelResult((ModelPart('text',text=json.dumps({'claims':[{'subject_id':alice.person_id,
        'predicate':'非法旧谓词','value':'苹果','source_indexes':[0]}],'matters':[]})),),receipt)
    class OriginalBroker:
        def __init__(self):
            self.observed=[]
            self.status_calls=[]
        async def generate(self,request):
            raise AssertionError('legacy attempt must never be resubmitted')
        async def status(self,attempt_id):
            self.status_calls.append(attempt_id)
            return receipt
        async def result(self,attempt_id,*,context):
            self.observed.append(attempt_id)
            return result
    model=OriginalBroker()
    core.memory.models=core.steps.models=model
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET consolidation_partition_id=NULL,extraction_state='invalid_claim_predicate',"
            "extraction_cursor=%s WHERE episode_id=%s",(Jsonb({'source_index':0,'step':0}),episode.episode_id))
        await conn.execute("INSERT INTO memory.model_steps(step_id,request,state) VALUES(%s,%s,'succeeded')",(step_id,Jsonb(wire(request))))
    await core.workers.recovery.recover()
    await core.workers.recovery.recover()
    await core.workers.recovery.recover()
    after=await row(core,episode)
    assert after['extraction_state']=='pending' and after['extraction_cursor']['protocol']==SCHEMA_VERSION
    assert model.status_calls==[request.attempt_id] and model.observed==[request.attempt_id]
    async with core.database.transaction(read_only=True) as conn:
        batches=await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE schema_version='memory.legacy-extraction.v0' "
            "AND manifest->'episodes' @> %s",(Jsonb([{'episode_id':episode.episode_id,'sequence':current['sequence']}]),))).fetchall()
        stored=await (await conn.execute('SELECT request,state FROM memory.model_steps WHERE step_id=%s',(step_id,))).fetchone()
    assert len(batches)==1 and batches[0]['commit_refs']['action']=='application_abandoned'
    assert batches[0]['manifest']['conversion']['previous_state']=='invalid_claim_predicate'
    assert stored['request']['attempt_id']==request.attempt_id and stored['state']=='succeeded'


@pytest.mark.parametrize('state',['completed','completed_with_missing','unproven_arbitrary_error'])
async def test_legacy_conversion_does_not_replay_completed_or_unproven_history(core,ordinary_users,state):
    alice,_=ordinary_users
    episode=await observe(core,alice,'legacy_history_'+state,'不应重播的历史')
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET consolidation_partition_id=NULL,extraction_state=%s,extraction_cursor='{}' WHERE episode_id=%s",
                           (state,episode.episode_id))
    await core.workers.recovery.recover()
    assert (await row(core,episode))['extraction_state']==state
    async with core.database.transaction(read_only=True) as conn:
        count=await (await conn.execute("SELECT count(*) AS n FROM memory.consolidation_batches WHERE schema_version='memory.legacy-extraction.v0'")).fetchone()
    assert count['n']==0


async def test_legacy_without_originals_gets_one_terminal_receipt_and_no_model_request(core,ordinary_users):
    alice,_=ordinary_users
    episode=await observe(core,alice,'legacy_missing_originals','之后缺失的原件')
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET consolidation_partition_id=NULL,source_refs='[]',extraction_state='invalid_claim_predicate',"
            "extraction_cursor=%s WHERE episode_id=%s",(Jsonb({'step':0}),episode.episode_id))
    await core.workers.recovery.recover()
    await core.workers.recovery.recover()
    after=await row(core,episode)
    assert after['extraction_state']=='legacy_abandoned' and 'legacy_original_evidence_missing' in after['extraction_missing']
    async with core.database.transaction(read_only=True) as conn:
        batches=await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE schema_version='memory.legacy-extraction.v0'")).fetchall()
    assert len(batches)==1 and batches[0]['status']=='revoked'


class HeldConsolidator(FixedConsolidator):
    """An actually unknown ledger receipt, rather than a lost successful reply."""
    def __init__(self):
        super().__init__()
        self.held={}

    async def generate(self,request):
        if not self.requests:
            self.requests.append(request)
            self.held[request.attempt_id]=ModelReceipt(request.attempt_id,request.intent_id,
                'unknown','unknown','fixture',1,'fixture','fixture','openai.chat','fixture',
                finished_at=datetime.now(UTC),error_code='timeout_unknown')
            raise AppError('timeout_unknown',status=503,details={'status':'unknown'})
        return await super().generate(request)

    async def status(self,attempt_id):
        return self.held.get(attempt_id) or await super().status(attempt_id)

    def release(self):
        request=self.requests[0]
        self.results[request.attempt_id]=self.answer(request)
        self.held.pop(request.attempt_id)


async def test_unknown_is_held_without_replay_while_later_material_advances(core,ordinary_users):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    original=await observe(core,alice,'held_original','原始未决材料')
    before=await row(core,original)
    partition=before['consolidation_partition_id']
    await core.workers.consolidate(partition)
    held=await latest_batch(core,partition)
    assert held['status']=='quarantined'
    assert (await row(core,original))['extraction_state']=='consolidation_unknown'
    status=await core.memory.extraction_status(alice,original.episode_id)
    assert status['action']=='observe_original_attempt' and status['isolated'] and not status['blocks_following']
    async with core.database.transaction(read_only=True) as conn:
        original_step=await (await conn.execute('SELECT * FROM memory.model_steps WHERE step_id=%s',
            (held['step_ids'][0],))).fetchone()
    for _ in range(3):
        await core.workers.recover()
    wakes = await _wake_rows(core, partition)
    assert len([wake for wake in wakes if wake['status']=='todo']) == 1
    assert len(model.requests) == 1
    async with core.database.transaction(read_only=True) as conn:
        unchanged_step=await (await conn.execute('SELECT * FROM memory.model_steps WHERE step_id=%s',
            (held['step_ids'][0],))).fetchone()
    assert unchanged_step == original_step
    later=await observe(core,alice,'following_held','后续材料含尾部事实')
    await core.workers.recover()
    await core.workers.consolidate(partition)
    assert len(model.requests)==2 and new_texts(model.requests[1])==['后续材料含尾部事实']
    assert (await row(core,later))['extraction_state']=='completed'
    assert (await row(core,original))['extraction_cursor']==before['extraction_cursor']
    async with core.database.transaction() as conn:
        resumed=await core.workers.recovery.resume_on(conn,alice,{'episode_id':original.episode_id,
            'expected_revision':original.revision,'client_request_id':'observe-held-original'})
    assert resumed.data['action']=='observe_original_attempt'
    await core.workers.consolidate(partition)
    assert len(model.requests)==2
    model.release()
    await core.workers.consolidate(partition)
    assert (await row(core,original))['extraction_state']=='completed' and len(model.requests)==2
    async with core.database.transaction(read_only=True) as conn:
        after=await (await conn.execute('SELECT * FROM memory.model_steps WHERE step_id=%s',
            (held['step_ids'][0],))).fetchone()
    assert after['request']==original_step['request'] and after['state']=='succeeded'


async def test_late_stale_result_rebuilds_still_unconsumed_original_material(core,ordinary_users):
    alice,_=ordinary_users
    refs=await core.sources.record_event(alice,producer='web',event_id='prior_for_held',revision=1,
        occurred_at=datetime.now(UTC),parts=({'text':'原事实'},),origin_kind=OriginKind.HUMAN)
    async with core.database.transaction() as conn:
        claim=await core.claims.put_on(conn,alice,{'subject_id':alice.person_id,'speaker_id':alice.person_id,
            'predicate':'preference.ordinary','value':'苹果','assertion':'self_confirmed','source_refs':refs},automatic=True)
    model=HeldConsolidator()
    configure(core,model)
    original=await observe(core,alice,'stale_held_original','原始未决材料')
    before=await row(core,original)
    partition=before['consolidation_partition_id']
    await core.workers.consolidate(partition)
    held=await latest_batch(core,partition)
    assert any(item['id']==claim.claim_id for item in held['read_set'])
    await core.claims.correct(alice,{'claim_id':claim.claim_id,'expected_revision':claim.revision,
        'client_request_id':'change-before-late-result','value':'梨'})
    model.release()
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        old=await (await conn.execute('SELECT * FROM memory.consolidation_batches WHERE batch_id=%s',
            (held['batch_id'],))).fetchone()
    assert old['status']=='stale' and old['manifest']['quarantine']
    await core.workers.recover()
    await core.workers.consolidate(partition)
    assert (await row(core,original))['extraction_state']=='completed'
    assert any('原始未决材料' in new_texts(request) for request in model.requests[1:])
    assert (await core.claims.get(alice,claim.claim_id)).value=='梨'


async def test_held_source_revocation_prevents_late_application(core,ordinary_users):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    original=await observe(core,alice,'held_revoke','尾部事实')
    partition=(await row(core,original))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    held=await latest_batch(core,partition)
    await core.sources.invalidate(alice,{'source_id':original.source_refs[0].source_id,
        'expected_revision':1,'client_request_id':'forget-held'},forgotten=True)
    model.release()
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        final=await (await conn.execute('SELECT status FROM memory.consolidation_batches WHERE batch_id=%s',
            (held['batch_id'],))).fetchone()
    assert final['status']=='revoked' and len(model.requests)==1
    assert not (await core.claims.list(alice)).items


async def batch_by_id(core,batch_id):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute('SELECT * FROM memory.consolidation_batches WHERE batch_id=%s',(batch_id,))).fetchone()


async def retry_due_now(core,batch_id):
    # Advance only the isolated fixture's durable business due, without sleeps
    # or extending a ModelRequest execution deadline.
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()-interval '1 second' WHERE batch_id=%s",(batch_id,))


async def test_unknown_gets_independent_attempt_and_late_original_cannot_apply_twice(core,ordinary_users):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    episode=await observe(core,alice,'retry-unknown','独立重算尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    original_batch=await latest_batch(core,partition)
    original_request=model.requests[0]
    async with core.database.transaction(read_only=True) as conn:
        original_step=await (await conn.execute('SELECT * FROM memory.model_steps WHERE step_id=%s',
            (original_batch['step_ids'][0],))).fetchone()
    await retry_due_now(core,original_batch['batch_id'])
    await core.workers.consolidate(partition)
    final=await batch_by_id(core,original_batch['batch_id'])
    assert final['status']=='applied' and len(model.requests)==2
    successor=model.requests[1]
    assert successor.attempt_id!=original_request.attempt_id
    assert successor.messages==original_request.messages and successor.source_refs==original_request.source_refs
    assert successor.route_id==original_request.route_id and successor.parameters==original_request.parameters
    assert successor.execution_timeout_seconds==300 and successor.business_deadline_at is None
    assert final['selected_step_id']==successor.intent_id
    async with core.database.transaction(read_only=True) as conn:
        attempts=await (await conn.execute('SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s ORDER BY number',
            (final['batch_id'],))).fetchall()
        unchanged=await (await conn.execute('SELECT * FROM memory.model_steps WHERE step_id=%s',
            (original_batch['step_ids'][0],))).fetchone()
    assert [attempt['number'] for attempt in attempts]==[1,2]
    assert attempts[1]['previous_step_id']==attempts[0]['step_id'] and attempts[1]['kind']=='retry'
    assert attempts[0]['first_failure']['status']=='unknown' and attempts[0]['first_failure']['error_code']=='timeout_unknown'
    assert unchanged==original_step and len((await core.claims.list(alice)).items)==1
    model.release()
    await core.workers.recover()
    await core.workers.consolidate(partition)
    assert len(model.requests)==2 and len((await core.claims.list(alice)).items)==1
    assert (await batch_by_id(core,final['batch_id']))['selected_step_id']==successor.intent_id


async def test_successful_original_is_consumed_before_due_retry_or_resubmission(core,ordinary_users):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    episode=await observe(core,alice,'late-before-retry','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    await retry_due_now(core,batch['batch_id'])
    model.release()
    await core.workers.consolidate(partition)
    final=await batch_by_id(core,batch['batch_id'])
    assert final['status']=='applied' and len(model.requests)==1
    assert final['selected_step_id']==model.requests[0].intent_id


async def test_late_original_can_win_while_an_independent_retry_is_unknown(core,ordinary_users):
    alice,_=ordinary_users
    class TwoUnknown(HeldConsolidator):
        async def generate(self,request):
            if len(self.requests)==1:
                self.requests.append(request)
                self.held[request.attempt_id]=ModelReceipt(request.attempt_id,request.intent_id,
                    'unknown','unknown','fixture',1,'fixture','fixture','openai.chat','fixture',error_code='transport_unknown')
                raise AppError('transport_unknown',status=503,details={'status':'unknown'})
            return await super().generate(request)
    model=TwoUnknown()
    configure(core,model)
    episode=await observe(core,alice,'two-candidate-results','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    await retry_due_now(core,batch['batch_id'])
    await core.workers.consolidate(partition)
    assert len(model.requests)==2 and (await batch_by_id(core,batch['batch_id']))['status']=='quarantined'
    model.release()
    await core.workers.consolidate(partition)
    final=await batch_by_id(core,batch['batch_id'])
    assert final['status']=='applied' and final['selected_step_id']==model.requests[0].intent_id
    model.results[model.requests[1].attempt_id]=model.answer(model.requests[1])
    await core.workers.consolidate(partition)
    assert len((await core.claims.list(alice)).items)==1 and len(model.requests)==2


@pytest.mark.parametrize('status',['queued','running'])
async def test_queued_or_running_original_is_observed_without_new_attempt(core,ordinary_users,status):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    episode=await observe(core,alice,'original-'+status,'未决原文')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    receipt=model.held[model.requests[0].attempt_id]
    model.held[receipt.attempt_id]=replace(receipt,status=status,error_code=None)
    await retry_due_now(core,batch['batch_id'])
    await core.workers.consolidate(partition)
    assert len(model.requests)==1 and (await batch_by_id(core,batch['batch_id']))['status']=='quarantined'
    assert (await batch_by_id(core,batch['batch_id']))['retry_due_at']>datetime.now(UTC)


async def test_deferred_retry_yields_to_new_input_and_persists_due_across_worker_restart(core,ordinary_users):
    from tianshu.memory.workers import MemoryWorkers
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    original=await observe(core,alice,'yield-original','原批次未决')
    partition=(await row(core,original))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    due=batch['retry_due_at']
    later=await observe(core,alice,'yield-later','后续尾部事实')
    await core.workers.consolidate(partition)
    assert len(model.requests)==2 and new_texts(model.requests[1])==['后续尾部事实']
    assert (await row(core,later))['extraction_state']=='completed'
    assert (await row(core,original))['extraction_state']=='consolidation_unknown'
    restarted=MemoryWorkers(core.memory,core.assembler,core.workers.embeddings,core.steps)
    await restarted.recover()
    assert (await batch_by_id(core,batch['batch_id']))['retry_due_at']==due
    await restarted.consolidate(partition)
    assert len(model.requests)==2
    await retry_due_now(core,batch['batch_id'])
    await restarted.consolidate(partition)
    assert (await row(core,original))['extraction_state']=='completed' and len(model.requests)==3


@pytest.mark.parametrize('failure',['explicit_cancel','configuration'])
async def test_cancel_or_permanent_configuration_stops_automatic_retry(core,ordinary_users,failure):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    episode=await observe(core,alice,'terminal-'+failure,'终止原文')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    receipt=model.held[model.requests[0].attempt_id]
    model.held[receipt.attempt_id]=replace(receipt,status='cancelled' if failure=='explicit_cancel' else 'failed',
        error_code='cancelled' if failure=='explicit_cancel' else 'model_function_not_configured',
        cancellation_reason='explicit_cancel' if failure=='explicit_cancel' else None)
    await retry_due_now(core,batch['batch_id'])
    await core.workers.consolidate(partition)
    final=await batch_by_id(core,batch['batch_id'])
    assert final['status']=='failed' and final['retry_due_at'] is None
    await core.workers.recover()
    await core.workers.consolidate(partition)
    assert len(model.requests)==1 and final['selected_step_id'] is None


@pytest.mark.parametrize('known_original',[False,True])
async def test_old_quarantine_adopts_audit_without_mutating_the_original_request(core,ordinary_users,monkeypatch,known_original):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    build=core.memory.consolidation.materials.build
    async def old_build(*args,**kwargs):
        frozen=await build(*args,**kwargs)
        frozen.pop('model_protocol',None)
        return frozen
    monkeypatch.setattr(core.memory.consolidation.materials,'build',old_build)
    episode=await observe(core,alice,'old-hold-adoption','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    async with core.database.transaction() as conn:
        await conn.execute('DELETE FROM memory.consolidation_attempts WHERE batch_id=%s',(batch['batch_id'],))
        await conn.execute('UPDATE memory.consolidation_batches SET retry_due_at=NULL WHERE batch_id=%s',(batch['batch_id'],))
    if known_original:
        model.release()
    await core.workers.consolidate(partition)
    assert (await batch_by_id(core,batch['batch_id']))['status']=='applied'
    assert len(model.requests)==(1 if known_original else 2)
    assert 'model_protocol' not in json.loads(model.requests[0].messages[0].parts[0].text)
    if not known_original:
        assert model.requests[1].messages==model.requests[0].messages


async def test_route_binding_change_rebuilds_original_input_under_current_binding(core,ordinary_users):
    alice,_=ordinary_users
    class BindingChanged(HeldConsolidator):
        async def generate(self,request):
            if ':retry:' in request.intent_id:
                raise AppError('model_route_binding_changed',status=409)
            return await super().generate(request)
        async def generation_profile(self,request):
            profile=await super().generation_profile(request)
            return {**profile,'route_id':'old-route' if not self.requests else 'new-route'}
    model=BindingChanged()
    configure(core,model)
    episode=await observe(core,alice,'binding-refresh','待重建原文')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    await retry_due_now(core,batch['batch_id'])
    await core.workers.consolidate(partition)
    assert (await batch_by_id(core,batch['batch_id']))['status']=='stale'
    await core.workers.consolidate(partition)
    assert (await row(core,episode))['extraction_state']=='completed'
    assert model.requests[-1].route_id=='new-route'


async def test_v2_semantic_failures_allow_only_one_explicit_independent_retry(core,ordinary_users):
    alice,_=ordinary_users
    model=FixedConsolidator(invalid=True)
    configure(core,model)
    episode=await observe(core,alice,'bounded-semantic-recovery','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    failed=await latest_batch(core,partition)
    assert len(model.requests)==2 and failed['status']=='failed'
    for index in range(2):
        async with core.database.transaction() as conn:
            resumed=await core.workers.recovery.resume_on(conn,alice,{'episode_id':episode.episode_id,
                'expected_revision':episode.revision,'client_request_id':'bounded-explicit-'+str(index)})
        await core.workers.consolidate(partition)
        assert resumed.data['queued'] is (index==0)
    assert len(model.requests)==3
    assert (await latest_batch(core,partition))['batch_id']==failed['batch_id']


async def test_failed_old_protocol_refreshes_material_and_preserves_old_output_audit(core,ordinary_users,monkeypatch):
    alice,_=ordinary_users
    model=FixedConsolidator(invalid=True)
    configure(core,model)
    build=core.memory.consolidation.materials.build
    async def old_build(*args,**kwargs):
        frozen=await build(*args,**kwargs)
        frozen.pop('model_protocol',None)
        return frozen
    monkeypatch.setattr(core.memory.consolidation.materials,'build',old_build)
    episode=await observe(core,alice,'old-output-upgrade','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    failed=await latest_batch(core,partition)
    async with core.database.transaction() as conn:
        resumed=await core.workers.recovery.resume_on(conn,alice,{'episode_id':episode.episode_id,
            'expected_revision':episode.revision,'client_request_id':'upgrade-known-output'})
    assert resumed.data['action']=='refresh_material'
    model.invalid=False
    monkeypatch.setattr(core.memory.consolidation.materials,'build',build)
    await core.workers.consolidate(partition)
    current=await latest_batch(core,partition)
    assert current['batch_id']!=failed['batch_id'] and current['status']=='applied'
    assert current['manifest']['frozen']['model_protocol']=='memory.proposal.v2'
    assert (await batch_by_id(core,failed['batch_id']))['status']=='failed'


async def test_actual_image_inline_bytes_are_budgeted_before_model_dispatch(core,ordinary_users):
    from tianshu.models.budgets import MAX_INLINE_MEDIA_BYTES
    from tianshu.types import MediaRef
    alice,_=ordinary_users
    model=FixedConsolidator()
    configure(core,model)
    episodes=[await observe(core,alice,'bytes-'+str(index),'图片原件') for index in range(2)]
    pipeline=core.memory.consolidation
    rows=[await row(core,episode) for episode in episodes]
    frozen=await pipeline.materials.build(alice,rows,'image-inline-budget')
    for index,material in enumerate(frozen['materials'].values()):
        material['media']=wire(MediaRef('image-'+str(index),'sha256:'+str(index)*64,
            'image/png',MAX_INLINE_MEDIA_BYTES//2+1,str(index)*64))
        material['evidence_kind']='text_image'
    with pytest.raises(AppError) as error:
        pipeline._request(alice,frozen,'image-inline-overflow')
    assert error.value.code=='memory_consolidation_input_budget'
    assert error.value.details['media_bytes']==MAX_INLINE_MEDIA_BYTES+2 and not model.requests
    for material in frozen['materials'].values():
        material['media']['size_bytes']=MAX_INLINE_MEDIA_BYTES//2
    request=pipeline._request(alice,frozen,'image-inline-equal')
    assert request.required_capabilities==frozenset({'text','vision'})
    assert sum(part.kind=='image' for message in request.messages for part in message.parts)==2
    assert request.estimated_input_tokens>=4096


@pytest.mark.parametrize('supplement_fits',[False,True])
async def test_stub_dependency_gets_one_deterministic_supplement_or_explicit_unresolved(core,ordinary_users,monkeypatch,supplement_fits):
    alice,_=ordinary_users
    refs=await core.sources.record_event(alice,producer='web',event_id='stub-existing',revision=1,
        occurred_at=datetime.now(UTC),parts=({'text':'以前偏好苹果'},),origin_kind=OriginKind.HUMAN)
    async with core.database.transaction() as conn:
        claim=await core.claims.put_on(conn,alice,{'subject_id':alice.person_id,'speaker_id':alice.person_id,
            'predicate':'preference.ordinary','value':'苹果','assertion':'self_confirmed','source_refs':refs},automatic=True)
    class NeedsStub(FixedConsolidator):
        def answer(self,request):
            result=super().answer(request)
            if len(self.requests)!=1:
                return result
            value=json.loads(request.messages[0].parts[0].text)
            ref=next(item['ref'] for item in value['existing_records'] if item.get('content_omitted'))
            proposal=json.loads(result.parts[0].text)
            target=next(item for item in proposal['targets'] if item['subject_id']==alice.person_id)
            target['status']='changed'
            target['operations']=[{'kind':'revise_claim','target':{'ref':ref},
                'payload':{'revision_mode':'correction','predicate':'preference.ordinary','value':'梨',
                    'speaker_id':alice.person_id,'assertion':'self_confirmed','polarity':True,
                    'conditions':{},'valid_from':None,'valid_until':None},
                'support_groups':[['m1']],'context_refs':[],'reads':[],'reason':'需要核验旧正文'}]
            return replace(result,parts=(ModelPart('text',text=json.dumps(proposal)),))
    model=NeedsStub()
    configure(core,model)
    pipeline=core.memory.consolidation
    request=pipeline._request
    def request_with_budget(context,frozen,step_id,**kwargs):
        if kwargs.get('context_round') and not supplement_fits:
            raise AppError('memory_consolidation_input_budget')
        return request(context,frozen,step_id,**kwargs)
    def stub_request(context,frozen,step_id,**kwargs):
        for key,record in frozen['records'].items():
            frozen['records'][key]={name:record[name] for name in
                ('kind','id','revision','head_revision','current_status','subject_id','source_refs','scope_ids') if name in record}
            frozen['records'][key]['content_omitted']=True
        return request_with_budget(context,frozen,step_id)
    monkeypatch.setattr(pipeline,'_budgeted_request',stub_request)
    monkeypatch.setattr(pipeline,'_request',request_with_budget)
    episode=await observe(core,alice,'stub-needs-context','请核对以前的偏好')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    assert batch['status']=='applied' and not batch['manifest'].get('output_repairs')
    assert len(model.requests)==(2 if supplement_fits else 1)
    assert (await core.claims.get(alice,claim.claim_id)).value=='苹果'
    if supplement_fits:
        assert batch['manifest']['context_round']==1
        assert not batch['commit_refs']['context_unfulfilled']
    else:
        assert batch['commit_refs']['target_results'][0]['status']=='unresolved'
        assert batch['commit_refs']['context_unfulfilled'][0]['kind']=='record'
        assert 'semantic_unresolved' in (await row(core,episode))['extraction_missing']


async def test_grouped_partial_change_retains_unresolved_episode_coverage(core,ordinary_users):
    alice,_=ordinary_users
    class Partial(FixedConsolidator):
        def answer(self,request):
            result=super().answer(request)
            proposal=json.loads(result.parts[0].text)
            value=json.loads(request.messages[0].parts[0].text)
            ambiguous=next(item['id'] for item in value['materials'] if item['text']=='仍需确认的材料')
            target=next(item for item in proposal['targets'] if item['subject_id']==alice.person_id)
            assert target['status']=='changed'
            target['unresolved']=[{'material_ids':[ambiguous],'reason':'仍缺明确依据','needed_refs':[]}]
            return replace(result,parts=(ModelPart('text',text=json.dumps(proposal)),))
    model=Partial()
    configure(core,model)
    known=await observe(core,alice,'partial-known','明确尾部事实')
    ambiguous=await observe(core,alice,'partial-unresolved','仍需确认的材料')
    partition=(await row(core,known))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    assert batch['status']=='applied' and len((await core.claims.list(alice)).items)==1
    assert batch['commit_refs']['target_results'][0]['status']=='changed'
    missing=await row(core,ambiguous)
    assert missing['extraction_state']=='completed_with_missing' and 'semantic_unresolved' in missing['extraction_missing']


@pytest.mark.parametrize('failure',['json','create_shape','actor_confirmation'])
async def test_one_persisted_output_repair_preserves_full_evidence_and_rolls_back_bad_proposals(core,ordinary_users,failure):
    alice,_=ordinary_users
    class Repairing(FixedConsolidator):
        def answer(self,request):
            result=super().answer(request)
            if len(self.requests)!=1:
                return result
            if failure=='json':
                return replace(result,parts=(ModelPart('text',text='{"operations" [}'),))
            proposal=json.loads(result.parts[0].text)
            operations=next((item['operations'] for item in proposal.get('targets',()) if item['operations']),None)
            operations=operations if operations is not None else proposal.get('operations',[])
            if failure=='create_shape':
                operations[0]['target']['create']={'create':'n1'}
            else:
                rejected=json.loads(json.dumps(operations[0]))
                rejected['target']={'create':'n2'}
                rejected['payload']['assertion']='self_confirmed'
                if 'targets' not in proposal:
                    rejected['op_id']='op2'
                    next(item for item in proposal['target_results'] if item['operation_ids'])['operation_ids'].append('op2')
                operations.append(rejected)
            return replace(result,parts=(ModelPart('text',text=json.dumps(proposal)),))

        async def generate(self,request):
            if self.requests:
                assert not (await core.claims.list(alice)).items, 'rejected operations must roll back together'
            return await super().generate(request)

    model=Repairing()
    configure(core,model)
    if failure=='actor_confirmation':
        refs=await core.sources.record_event(alice,producer='runtime-test',event_id='actor-confirmation',revision=1,
            occurred_at=datetime.now(UTC),parts=({'text':'角色经历尾部事实'},),origin_kind=OriginKind.ACTOR)
        episode=await core.memory.observe_episode(alice,{'event_id':'actor-confirmation','occurred_at':datetime.now(UTC),
            'summary':'角色经历','source_refs':refs,'origin_kind':'actor'})
    else:
        episode=await observe(core,alice,'repair-'+failure,'真人原件尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    assert batch['status']=='applied' and len(model.requests)==2
    assert len(batch['manifest']['output_repairs'])==1
    first,repair=model.requests
    assert first.attempt_id!=repair.attempt_id and first.messages[0]==repair.messages[0]
    assert first.source_refs==repair.source_refs and first.route_id==repair.route_id
    assert batch['manifest']['steps'][batch['step_ids'][0]]==batch['manifest']['steps'][batch['step_ids'][1]]
    assert (await row(core,episode))['extraction_state']=='completed'
    claims=(await core.claims.list(alice)).items
    assert len(claims)==1 and claims[0].assertion==('observed' if failure=='actor_confirmation' else 'self_confirmed')


async def test_reasoning_only_truncation_has_one_disabled_repair_with_original_allowance(core,ordinary_users):
    alice,_=ordinary_users
    class ReasoningOnly(FixedConsolidator):
        async def generation_profile(self,request):
            return {**await super().generation_profile(request),'model':'deepseek-v4.1-flash'}

        def answer(self,request):
            result=super().answer(request)
            if request.parameters.get('thinking')!={'type':'disabled'}:
                return replace(result,parts=(),native_response={'finish_reason':'length'},
                    completion=ModelCompletion('incomplete',('length',)))
            return result

    model=ReasoningOnly()
    configure(core,model)
    episode=await observe(core,alice,'reasoning_only','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    assert len(model.requests)==2
    first,repair=model.requests
    assert first.parameters['reasoning_effort']=='low'
    assert first.parameters['response_format']=={'type':'json_object'}
    assert repair.parameters['thinking']=={'type':'disabled'}
    assert 'reasoning_effort' not in repair.parameters
    assert first.max_output_tokens==repair.max_output_tokens==12288
    assert first.parameters['max_tokens']==repair.parameters['max_tokens']==12288
    assert (await latest_batch(core,partition))['status']=='applied'


async def test_repair_limit_survives_recovery_and_keeps_cursors_unadvanced(core,ordinary_users):
    alice,_=ordinary_users
    model=FixedConsolidator(invalid=True)
    configure(core,model)
    episode=await observe(core,alice,'repair_limit','尾部事实')
    before=await row(core,episode)
    partition=before['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    assert len(model.requests)==2 and batch['status']=='failed'
    assert len(batch['manifest']['output_repairs'])==1
    await core.workers.recover()
    await core.workers.consolidate(partition)
    assert len(model.requests)==2 and (await row(core,episode))['extraction_cursor']==before['extraction_cursor']
    assert not (await core.claims.list(alice)).items


async def test_prepared_repair_survives_interruption_without_another_repair_identity(core,ordinary_users,monkeypatch):
    alice,_=ordinary_users
    class InitiallyInvalid(FixedConsolidator):
        def answer(self,request):
            self.invalid=len(self.requests)==1
            return super().answer(request)
    model=InitiallyInvalid()
    configure(core,model)
    execute=core.steps.execute_recorded
    async def interrupt_before_repair_dispatch(step_id,**kwargs):
        if ':repair:' in step_id:
            raise TimeoutError('synthetic process interruption before crossing Broker')
        return await execute(step_id,**kwargs)
    monkeypatch.setattr(core.steps,'execute_recorded',interrupt_before_repair_dispatch)
    episode=await observe(core,alice,'prepared_repair_restart','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    interrupted=await latest_batch(core,partition)
    assert len(model.requests)==1 and len(interrupted['manifest']['output_repairs'])==1
    async with core.database.transaction(read_only=True) as conn:
        repair=await (await conn.execute('SELECT * FROM memory.model_steps WHERE step_id=%s',
            (interrupted['step_ids'][-1],))).fetchone()
    assert repair['state']=='prepared'
    monkeypatch.setattr(core.steps,'execute_recorded',execute)
    await core.workers.recover()
    await core.workers.consolidate(partition)
    final=await latest_batch(core,partition)
    assert final['status']=='applied' and final['step_ids']==interrupted['step_ids']
    assert len(model.requests)==2 and model.requests[-1].attempt_id==repair['request']['attempt_id']


async def test_long_request_window_is_frozen_and_supplement_renews_its_owner_lease(core,ordinary_users):
    from tianshu.memory.consolidation import LEASE_SECONDS
    alice,_=ordinary_users
    lease_snapshots=[]
    class Supplement(FixedConsolidator):
        async def generate(self,request):
            assert request.budget.remaining()>290
            assert request.execution_timeout_seconds==300 and request.business_deadline_at is None
            async with core.database.transaction() as conn:
                state=await (await conn.execute('SELECT lease_owner,lease_until FROM memory.consolidation_state WHERE partition_id=%s',
                    (partition,))).fetchone()
                lease_snapshots.append(state)
                assert (state['lease_until']-datetime.now(UTC)).total_seconds()>LEASE_SECONDS-10
                if not self.requests:
                    # Model the old three-minute lease already being exhausted.
                    await conn.execute("UPDATE memory.consolidation_state SET lease_until=now()-interval '1 second' WHERE partition_id=%s",
                        (partition,))
            return await super().generate(request)

        def answer(self,request):
            result=super().answer(request)
            if len(self.requests)==1:
                proposal=json.loads(result.parts[0].text)
                proposal['needs_context']=[{'kind':'material','ref':'m1','reason':'需要剩余原文'}]
                return replace(result,parts=(ModelPart('text',text=json.dumps(proposal)),))
            return result

    model=Supplement()
    configure(core,model)
    original_text='前文 '*5000+'尾部事实'
    episode=await observe(core,alice,'long_native_window',original_text)
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    batch=await latest_batch(core,partition)
    assert len(model.requests)==2 and batch['status']=='applied'
    assert len(new_texts(model.requests[0])[0])<len(original_text)
    supplemented=json.loads(model.requests[1].messages[0].parts[0].text)
    assert any(item['role']=='context' and item['text'] for item in supplemented['materials'])
    assert (await row(core,episode))['extraction_state']=='pending'
    assert lease_snapshots[0]['lease_owner']==lease_snapshots[1]['lease_owner']
    async with core.database.transaction(read_only=True) as conn:
        steps=await (await conn.execute('SELECT request_timeout_seconds FROM memory.model_steps WHERE step_id=ANY(%s::text[])',
            (batch['step_ids'],))).fetchall()
    assert len(steps)==2 and all(item['request_timeout_seconds']==300 for item in steps)


async def test_model_step_dispatch_preserves_frozen_execution_duration_after_prepare_budget_expires(core,ordinary_users):
    from tianshu.models.timing import waiting_timeout
    alice,_=ordinary_users
    episode=await observe(core,alice,'expired-preparation-only','原件保持完整')
    original=ModelRequest('frozen-execution','frozen-execution','memory.extract',ServiceClass.ELASTIC,alice,
        RequestBudget(datetime.now(UTC)-timedelta(minutes=1)),(),
        source_refs=episode.source_refs,execution_timeout_seconds=300)
    frozen=wire(original)
    class Models:
        async def generate(self,request):
            # Dataclass equality compares permissions as a set. Their wire
            # array order may change on reconstruction without changing authority.
            assert request==original and wire(request.budget)==frozen['budget']
            assert waiting_timeout(request) is None
            return ModelResult((),ModelReceipt(request.attempt_id,request.intent_id,'succeeded','submitted',
                'fixture',1,'fixture','fixture','openai.chat','fixture'))
    core.steps.models=Models()
    async with core.database.transaction() as conn:
        await core.steps.prepare_on(conn,'frozen-execution',original)
    await core.steps.execute_recorded('frozen-execution')
    async with core.database.transaction(read_only=True) as conn:
        step=await (await conn.execute('SELECT * FROM memory.model_steps WHERE step_id=%s',('frozen-execution',))).fetchone()
    assert step['request']==frozen and step['request_timeout_seconds']==300 and step['state']=='succeeded'


async def test_consolidation_renews_owned_lease_while_original_model_waits_in_queue(core,ordinary_users,monkeypatch):
    from tianshu.memory import consolidation as module
    monkeypatch.setattr(module,'LEASE_RENEW_SECONDS',0.02)
    alice,_=ordinary_users
    entered,release=asyncio.Event(),asyncio.Event()
    class Queued(FixedConsolidator):
        async def generate(self,request):
            entered.set()
            await release.wait()
            return await super().generate(request)
    model=Queued()
    configure(core,model)
    episode=await observe(core,alice,'renew-during-model-queue','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    task=asyncio.create_task(core.workers.consolidate(partition))
    try:
        await asyncio.wait_for(entered.wait(),5)
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE memory.consolidation_state SET lease_until=now()+interval '150 milliseconds' WHERE partition_id=%s",(partition,))
        await asyncio.sleep(0.3)
        async with core.database.transaction(read_only=True) as conn:
            state=await (await conn.execute('SELECT * FROM memory.consolidation_state WHERE partition_id=%s',(partition,))).fetchone()
        assert state['lease_owner'] and (state['lease_until']-datetime.now(UTC)).total_seconds()>300
        await core.workers.consolidate(partition)
        assert not task.done() and not model.requests
        release.set()
        await asyncio.wait_for(task,5)
        assert len(model.requests)==1 and (await latest_batch(core,partition))['status']=='applied'
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


async def test_waiting_worker_losing_lease_stops_without_mutating_successor_batch(core,ordinary_users,monkeypatch):
    from tianshu.memory import consolidation as module
    monkeypatch.setattr(module,'LEASE_RENEW_SECONDS',0.02)
    alice,_=ordinary_users
    entered=asyncio.Event()
    class Queued(FixedConsolidator):
        async def generate(self,request):
            entered.set()
            await asyncio.Event().wait()
    configure(core,Queued())
    episode=await observe(core,alice,'lost-lease-while-queued','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    task=asyncio.create_task(core.workers.consolidate(partition))
    try:
        await asyncio.wait_for(entered.wait(),5)
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE memory.consolidation_state SET lease_owner='next-owner',lease_until=now()+interval '6 minutes' WHERE partition_id=%s",(partition,))
            before_state=await (await conn.execute('SELECT * FROM memory.consolidation_state WHERE partition_id=%s',(partition,))).fetchone()
        before_batch=await latest_batch(core,partition)
        before_episode=await row(core,episode)
        await asyncio.wait_for(task,5)
        async with core.database.transaction(read_only=True) as conn:
            state=await (await conn.execute('SELECT * FROM memory.consolidation_state WHERE partition_id=%s',(partition,))).fetchone()
        assert state==before_state and state['lease_owner']=='next-owner'
        assert await latest_batch(core,partition)==before_batch
        assert await row(core,episode)==before_episode
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


async def test_late_apply_never_releases_another_active_pointer_or_lease(core,ordinary_users):
    alice,_=ordinary_users
    model=HeldConsolidator()
    configure(core,model)
    episode=await observe(core,alice,'late_pointer','原始未决材料')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    held=await latest_batch(core,partition)
    async with core.database.transaction() as conn:
        await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,context_fingerprint) "
            "VALUES('another-active',%s,%s,'prepared','{}','another')",(partition,SCHEMA_VERSION))
        await conn.execute("UPDATE memory.consolidation_state SET active_batch_id='another-active',lease_owner='another-owner',"
            "lease_until=now()+interval '6 minutes' WHERE partition_id=%s",(partition,))
    model.release()
    # Without owning that lease, even the previously valid original cannot apply.
    with pytest.raises(AppError,match='memory_consolidation_lease_changed'):
        await core.memory.consolidation._execute(held['batch_id'],alice,owner='expired-owner')
    async with core.database.transaction(read_only=True) as conn:
        state=await (await conn.execute('SELECT active_batch_id,lease_owner FROM memory.consolidation_state WHERE partition_id=%s',
            (partition,))).fetchone()
    assert state=={'active_batch_id':'another-active','lease_owner':'another-owner'}
    assert (await row(core,episode))['extraction_state']=='consolidation_unknown' and len(model.requests)==1
    # The actual partition owner can accept the unchanged late result while
    # leaving a different prepared batch's pointer and its own lease intact.
    await core.memory.consolidation._execute(held['batch_id'],alice,owner='another-owner')
    async with core.database.transaction(read_only=True) as conn:
        state=await (await conn.execute('SELECT active_batch_id,lease_owner FROM memory.consolidation_state WHERE partition_id=%s',
            (partition,))).fetchone()
    assert state=={'active_batch_id':'another-active','lease_owner':'another-owner'}
    assert (await row(core,episode))['extraction_state']=='completed' and len(model.requests)==1


async def test_expired_owner_does_not_settle_a_batch_already_taken_over(core,ordinary_users):
    alice,_=ordinary_users
    successor_snapshot={}
    class TakenOver(FixedConsolidator):
        async def generate(self,request):
            result=await super().generate(request)
            async with core.database.transaction() as conn:
                await conn.execute("UPDATE memory.consolidation_state SET lease_owner='successor-owner',"
                    "lease_until=now()+interval '6 minutes' WHERE partition_id=%s",(partition,))
                successor_snapshot['state']=await (await conn.execute(
                    'SELECT * FROM memory.consolidation_state WHERE partition_id=%s',(partition,))).fetchone()
                successor_snapshot['batch']=await (await conn.execute(
                    'SELECT * FROM memory.consolidation_batches WHERE batch_id=%s',
                    (successor_snapshot['state']['active_batch_id'],))).fetchone()
                successor_snapshot['episode']=await (await conn.execute(
                    'SELECT * FROM memory.episodes WHERE episode_id=%s',(episode.episode_id,))).fetchone()
            return result
    model=TakenOver()
    configure(core,model)
    episode=await observe(core,alice,'lease-takeover-during-model','尾部事实')
    partition=(await row(core,episode))['consolidation_partition_id']
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        state=await (await conn.execute('SELECT * FROM memory.consolidation_state WHERE partition_id=%s',
            (partition,))).fetchone()
        batch=await (await conn.execute('SELECT * FROM memory.consolidation_batches WHERE batch_id=%s',
            (successor_snapshot['state']['active_batch_id'],))).fetchone()
        current=await (await conn.execute('SELECT * FROM memory.episodes WHERE episode_id=%s',
            (episode.episode_id,))).fetchone()
    assert state==successor_snapshot['state'] and state['lease_owner']=='successor-owner'
    assert batch==successor_snapshot['batch'] and batch['status']=='waiting_model'
    assert current==successor_snapshot['episode'] and current['extraction_state']=='running'
    assert not (await core.claims.list(alice)).items and len(model.requests)==1


async def _wake_rows(core, partition):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute("SELECT j.*,k.key,k.cancelled_at FROM procrastinate_jobs j "
            "LEFT JOIN runtime.job_keys k ON k.job_id=j.id WHERE j.task_name='memory.consolidate' "
            "AND j.args=%s ORDER BY j.id", (Jsonb({'partition_id':partition}),))).fetchall()


async def _start_wake(core, partition):
    pending = next(wake for wake in await _wake_rows(core, partition) if wake['status']=='todo')
    async with core.database.transaction() as conn:
        # Admit this isolated fixture now, without comparing Broker and PG UTC.
        await conn.execute("UPDATE procrastinate_jobs SET scheduled_at=NULL WHERE id=%s", (pending['id'],))
        fetched = await (await conn.execute("SELECT * FROM procrastinate_fetch_job_v2(ARRAY['memory_consolidation']::varchar[],NULL)")).fetchone()
    assert fetched['id'] == pending['id']
    return pending['id']


async def _finish_wake(core, job_id):
    async with core.database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (job_id,))


async def test_memory_wake_coalesces_earlier_input_concurrent_recovery_and_repeated_ticks(core, ordinary_users):
    alice, _ = ordinary_users
    model = FixedConsolidator()
    configure(core, model)
    pipeline = core.memory.consolidation
    pipeline.policy = pipeline.materials.policy = replace(pipeline.policy, quiet_seconds=120)
    episode = await observe(core, alice, 'wake-initial', '待唤醒原件')
    partition = (await row(core, episode))['consolidation_partition_id']
    initial = (await _wake_rows(core, partition))[0]
    pipeline.policy = pipeline.materials.policy = replace(pipeline.policy, quiet_seconds=0)
    await observe(core, alice, 'wake-earlier', '更早到期原件')
    earlier = (await _wake_rows(core, partition))[0]
    assert earlier['id'] == initial['id'] and earlier['key'] == initial['key']
    assert earlier['scheduled_at'] < initial['scheduled_at']
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=now()-interval '1 second' WHERE partition_id=%s", (partition,))
    for _ in range(3):
        await core.workers.recover()
    _, _, concurrent = await asyncio.gather(core.workers.recover(), core.workers.recover(),
        observe(core, alice, 'wake-concurrent-input', '并发新原件'))
    wakes = await _wake_rows(core, partition)
    assert len(wakes) == 1 and wakes[0]['status']=='todo'
    assert wakes[0]['id'] == initial['id'] and wakes[0]['scheduled_at'] <= earlier['scheduled_at']
    assert wakes[0]['queueing_lock'] == 'memory-consolidate:'+partition
    assert (await row(core, concurrent))['consolidation_partition_id'] == partition
    assert not model.requests


async def test_memory_wake_recover_retires_only_original_partition_todo_and_preserves_doing_audit(core, ordinary_users):
    alice, _ = ordinary_users
    model = FixedConsolidator()
    configure(core, model)
    episode = await observe(core, alice, 'wake-old-duplicates', '原审计保持')
    original_episode = await row(core, episode)
    partition = original_episode['consolidation_partition_id']
    original = (await _wake_rows(core, partition))[0]
    async with core.database.transaction() as conn:
        ids = []
        for label in ('old-one','old-two','doing','terminal'):
            ids.append(await core.memory.jobs.enqueue(conn, JobSpec('memory.consolidate',
                'memory-consolidate:'+partition+':'+label, {'partition_id':partition})))
        await conn.execute("UPDATE procrastinate_jobs SET attempts=2 WHERE id=%s", (ids[1],))
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=ANY(%s::bigint[])", (ids[2:],))
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'failed',false)", (ids[3],))
        unrelated = await core.memory.jobs.enqueue(conn, JobSpec('memory.consolidate',
            'memory-consolidate:unrelated:old', {'partition_id':'unrelated'}))
        await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=now()-interval '1 second' WHERE partition_id=%s", (partition,))
        keys = await (await conn.execute("SELECT * FROM runtime.job_keys WHERE task='memory.consolidate' ORDER BY key")).fetchall()
    before = {wake['id']:wake for wake in await _wake_rows(core, partition)}
    await core.workers.recover()
    after = {wake['id']:wake for wake in await _wake_rows(core, partition)}
    assert after[original['id']]['status'] == 'todo'
    assert [after[identity]['status'] for identity in ids] == ['cancelled','cancelled','doing','failed']
    assert after[ids[1]]['attempts'] == 2 and after[ids[0]]['abort_requested'] is False
    assert after[ids[2]] == before[ids[2]] and after[ids[3]] == before[ids[3]]
    async with core.database.transaction(read_only=True) as conn:
        assert await (await conn.execute("SELECT * FROM runtime.job_keys WHERE task='memory.consolidate' ORDER BY key")).fetchall() == keys
        assert (await (await conn.execute("SELECT status FROM procrastinate_jobs WHERE id=%s", (unrelated,))).fetchone())['status']=='todo'
        for identity in ids[:2]:
            events = await (await conn.execute("SELECT type FROM procrastinate_events WHERE job_id=%s ORDER BY id", (identity,))).fetchall()
            assert events[0]['type']=='deferred' and events[-1]['type']=='cancelled'
        assert all(await core.sources.check_many_on(conn, alice, (episode.source_refs,)))
    assert await row(core, episode) == original_episode and not model.requests


async def test_memory_wake_leaves_one_successor_and_rearms_if_it_ran_before_owner_finished(core, ordinary_users):
    alice, _ = ordinary_users
    entered, release = asyncio.Event(), asyncio.Event()
    class Waiting(FixedConsolidator):
        async def generate(self, request):
            if not self.requests:
                entered.set()
                await release.wait()
            return await super().generate(request)
    model = Waiting()
    configure(core, model)
    first = await observe(core, alice, 'wake-running-first', '第一份原件')
    partition = (await row(core, first))['consolidation_partition_id']
    doing = await _start_wake(core, partition)
    owner = asyncio.create_task(core.workers.consolidate(partition))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        later = await observe(core, alice, 'wake-running-later', '后续尾部事实')
        pending = [wake for wake in await _wake_rows(core, partition) if wake['status']=='todo']
        assert len(pending)==1 and pending[0]['id'] != doing
        premature = await _start_wake(core, partition)
        await core.workers.consolidate(partition)  # Existing lease still belongs to owner.
        await _finish_wake(core, premature)
        assert not any(wake['status']=='todo' for wake in await _wake_rows(core, partition))
        release.set()
        await asyncio.wait_for(owner, 5)
        await _finish_wake(core, doing)
        assert (await row(core, first))['extraction_state']=='completed'
        assert (await row(core, later))['extraction_state']=='pending'
        successor = await _start_wake(core, partition)
        await core.workers.consolidate(partition)
        await _finish_wake(core, successor)
        assert (await row(core, later))['extraction_state']=='completed'
        assert len(model.requests)==2 and len({request.attempt_id for request in model.requests})==2
    finally:
        release.set()
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)


async def test_memory_wake_idle_cas_preserves_new_input_and_same_due_after_cancelled_key(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model = FixedConsolidator()
    configure(core, model)
    pipeline = core.memory.consolidation
    first = await observe(core, alice, 'wake-idle-first', '第一份待处理')
    partition = (await row(core, first))['consolidation_partition_id']
    doing = await _start_wake(core, partition)
    pending = pipeline.materials.pending
    arrived = []
    async def input_after_empty_read(*_args):
        arrived.append(await observe(core, alice, 'wake-idle-new', '后来尾部事实'))
        return []
    monkeypatch.setattr(pipeline.materials, 'pending', input_after_empty_read)
    await core.workers.consolidate(partition)
    await _finish_wake(core, doing)
    assert not model.requests and (await row(core, arrived[0]))['extraction_state']=='pending'
    wakes = await _wake_rows(core, partition)
    next_wake = next(wake for wake in wakes if wake['status']=='todo')
    same_due = next_wake['scheduled_at']
    async with core.database.transaction() as conn:
        await core.memory.jobs.cancel(conn, next_wake['key'])
        fresh = await pipeline._schedule_on(conn, partition, same_due)
    assert fresh != next_wake['id']
    monkeypatch.setattr(pipeline.materials, 'pending', pending)
    successor = await _start_wake(core, partition)
    await core.workers.consolidate(partition)
    await _finish_wake(core, successor)
    assert len(model.requests)==1
    assert (await row(core, first))['extraction_state']=='completed'
    assert (await row(core, arrived[0]))['extraction_state']=='completed'
