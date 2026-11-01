"""Real PG/source/queue consolidation stories, authored but not run during development."""

from dataclasses import replace
from datetime import UTC,datetime
import json

import pytest

from tianshu.memory.extraction_contract import SCHEMA_VERSION
from tianshu.models.types import ModelCompletion,ModelPart,ModelReceipt,ModelResult
from tianshu.types import OriginKind


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
