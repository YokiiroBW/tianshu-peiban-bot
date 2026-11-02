"""Real PG/source/queue consolidation stories, authored but not run during development."""

from dataclasses import replace
import asyncio
from datetime import UTC,datetime,timedelta
import json

import pytest

from tianshu.memory.extraction_contract import SCHEMA_VERSION
from tianshu.models.types import ModelCompletion,ModelMessage,ModelPart,ModelReceipt,ModelRequest,ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind,RequestBudget,ServiceClass
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


async def test_unknown_smaller_attempt_is_observed_with_its_original_material_map(core, ordinary_users):
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
    assert active['status'] == 'waiting_model'
    assert len(model.requests) == 2 and new_texts(model.requests[1]) == ['先前原文']
    attempt = model.requests[1].attempt_id
    stored_map = active['manifest']['steps'][active['step_ids'][-1]]

    later = await observe(core, alice, 'later_during_unknown', '后来原文')
    await core.workers.consolidate(partition)
    observed = await latest_batch(core, partition)
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
                proposal['needs_context'] = [{'request_id':'context1', 'kind':'material', 'ref':'m1',
                    'start':None, 'end':None, 'query':None, 'reason':'需要原件后续内容'}]
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


async def test_candidate_preparation_uses_one_lease_budget_across_input_shrinks(core, ordinary_users, monkeypatch):
    from tianshu.memory.consolidation import COMMIT_RESERVE_SECONDS, MODEL_REQUEST_SECONDS

    alice, _ = ordinary_users
    model = FixedConsolidator()
    configure(core, model)
    pipeline = core.memory.consolidation
    first = await observe(core, alice, "candidate_budget_first", "前文")
    await observe(core, alice, "candidate_budget_last", "尾部事实")
    partition = (await row(core, first))["consolidation_partition_id"]
    budgets = []
    async def candidates(context, query, budget, **kwargs):
        async with core.database.transaction(read_only=True) as conn:
            state = await (await conn.execute(
                "SELECT lease_until FROM memory.consolidation_state WHERE partition_id=%s", (partition,))).fetchone()
        assert budget.deadline_at == state["lease_until"] - timedelta(
            seconds=MODEL_REQUEST_SECONDS + COMMIT_RESERVE_SECONDS)
        budgets.append(budget)
        return ()
    pipeline.materials.record_candidates = candidates
    request = pipeline._budgeted_request
    attempts = 0
    def shrink_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AppError("memory_consolidation_input_budget")
        return request(*args, **kwargs)
    monkeypatch.setattr(pipeline, "_budgeted_request", shrink_once)
    await core.workers.consolidate(partition)
    assert len(budgets) == 2 and budgets[0] is budgets[1]
    assert 0 < budgets[0].remaining() <= 55
    assert len(model.requests) == 1 and model.requests[0].budget.remaining() > 100


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
    model=FixedConsolidator(unknown=True)
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


async def test_unknown_recovery_keeps_original_attempt_and_local_material_map(core,ordinary_users):
    alice,_=ordinary_users
    model=FixedConsolidator(unknown=True)
    configure(core,model)
    first=await observe(core,alice,'unknown_original','尾部事实')
    original=await row(core,first)
    await core.workers.consolidate(original['consolidation_partition_id'])
    assert (await row(core,first))['extraction_cursor']=={'protocol':SCHEMA_VERSION}
    later=await observe(core,alice,'unrelated_later','后来新材料')
    await core.workers.consolidate(original['consolidation_partition_id'])
    assert len(model.requests)==1
    assert (await row(core,first))['extraction_state']=='completed'
    assert (await row(core,later))['extraction_state']=='pending'
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
    assert batch['error_code']==('memory_consolidation_output_truncated' if failure=='truncated' else 'memory_consolidation_invalid')

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
    model=FixedConsolidator(unknown=True)
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


async def test_late_stale_result_keeps_old_material_held_until_explicit_refresh(core,ordinary_users):
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
    status=await core.memory.extraction_status(alice,original.episode_id)
    assert status['status']=='stale' and status['held_for_refresh'] and status['action']=='refresh_material'
    assert (await row(core,original))['extraction_state']=='consolidation_stale'
    # The correction's own sourced change event may proceed in this same wake;
    # the held original must not be included in that unrelated extraction.
    after_observation=len(model.requests)
    assert all('原始未决材料' not in new_texts(request) for request in model.requests[1:])
    await core.workers.recover()
    await core.workers.consolidate(partition)
    assert len(model.requests)==after_observation and (await core.claims.get(alice,claim.claim_id)).value=='梨'
    async with core.database.transaction() as conn:
        resumed=await core.workers.recovery.resume_on(conn,alice,{'episode_id':original.episode_id,
            'expected_revision':original.revision,'client_request_id':'refresh-known-stale'})
    assert resumed.data['action']=='refresh_material'
    await core.workers.consolidate(partition)
    assert len(model.requests)==after_observation+1 and (await row(core,original))['extraction_state']=='completed'


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
            if failure=='create_shape':
                proposal['operations'][0]['target']['create']={'create':'n1'}
            else:
                rejected=json.loads(json.dumps(proposal['operations'][0]))
                rejected.update(op_id='op2',target={'create':'n2'})
                rejected['payload']['assertion']='self_confirmed'
                proposal['operations'].append(rejected)
                next(item for item in proposal['target_results'] if item['operation_ids'])['operation_ids'].append('op2')
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
                proposal['needs_context']=[{'request_id':'context1','kind':'material','ref':'m1',
                    'start':None,'end':None,'query':None,'reason':'需要剩余原文'}]
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
