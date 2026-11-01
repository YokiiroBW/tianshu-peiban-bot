"""Real PG/source/queue consolidation stories, authored but not run during development."""

from dataclasses import replace
from datetime import UTC,datetime,timedelta
import json

import pytest

from tianshu.memory.extraction_contract import SCHEMA_VERSION
from tianshu.models.types import ModelCompletion,ModelMessage,ModelPart,ModelReceipt,ModelRequest,ModelResult
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind,RequestBudget,ServiceClass
from psycopg.types.json import Jsonb


class FixedConsolidator:
    def __init__(self,*,unknown=False,invalid=False,truncated=False):
        self.requests=[]
        self.results={}
        self.unknown,self.invalid,self.truncated=unknown,invalid,truncated

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
    alice,bob=ordinary_users
    human=await observe(core,alice,'verified_author','唯一已核验作者')
    assert human.speaker_id == alice.person_id
    second=await observe(core,bob,'another_author','另一位真实作者')
    owner=await core.identity.context(core.owner.principal_id,actor_id=core.actor)
    mixed=await core.memory.observe_episode(owner,{'event_id':'mixed_authors','origin_kind':'human',
        'occurred_at':datetime.now(UTC),'summary':'多作者材料',
        'source_refs':(*human.source_refs,*second.source_refs)})
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
