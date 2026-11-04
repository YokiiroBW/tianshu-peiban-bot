"""Native extraction policy and bounded repair, for centralized verification."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from tianshu.memory.consolidation import Consolidation
from tianshu.memory.extraction_contract import MODEL_PROTOCOL, SCHEMA_VERSION, parse_model_proposal
from tianshu.memory.generation import decode_model_json, native_request, repairable, repair_request, repair_input_estimate
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS, effective_output_tokens
from tianshu.models.types import ModelCompletion, ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


def request():
    context=TrustedContext('principal','actor','person',None,('scope',),('scope',),1,OriginKind.HUMAN)
    return ModelRequest('original','attempt','memory.extract',ServiceClass.ELASTIC,context,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=300)),
        (ModelMessage('user',(ModelPart('text',text='{"materials":["complete source"]}'),)),),
        system='按冻结来源输出JSON',max_output_tokens=12288,source_refs=(SourceRef('source',1),))


def result(*,text=' ',completion=ModelCompletion('complete',('stop',))):
    original=request()
    receipt=ModelReceipt(original.attempt_id,original.intent_id,'succeeded','submitted','route',2,
        'provider','deepseek-v4.1-flash','openai.chat','quota')
    return ModelResult((ModelPart('text',text=text),),receipt,completion=completion)


@pytest.mark.parametrize('protocol,key,model',[
    ('openai.chat','max_tokens','deepseek-v4.1-flash'),
    ('openai.chat','max_completion_tokens','other-chat-model'),
    ('openai.responses','max_output_tokens','responses-model'),
    ('anthropic.messages','max_tokens','claude-model'),
    ('gemini.generate','maxOutputTokens','gemini-model'),
])
async def test_extraction_pins_profile_and_overrides_native_route_budget_only_for_this_request(protocol,key,model):
    original=request()
    output=({'generationConfig':{key:8192}} if protocol=='gemini.generate' else {key:8192})
    profile={'route_id':'route','route_revision':2,'protocol':protocol,'model':model,'output_parameters':output}
    class Models:
        async def generation_profile(self,request):
            return profile
    configured=await native_request(Models(),original)
    assert configured.route_id=='route' and configured.route_revision==2 and configured.protocol==protocol
    assert configured.budget.remaining()>290
    plan=SimpleNamespace(protocol=protocol,parameters=output)
    assert effective_output_tokens(plan,configured)==12288
    assert profile['output_parameters']==output and original.parameters=={}
    if model.startswith('deepseek'):
        assert configured.parameters['reasoning_effort']=='low'
        assert configured.parameters['response_format']=={'type':'json_object'}
    else:
        assert 'reasoning_effort' not in configured.parameters and 'response_format' not in configured.parameters
    if protocol=='gemini.generate':
        assert configured.parameters['generationConfig']['responseMimeType']=='application/json'
        assert 'responseMimeType' not in profile['output_parameters']['generationConfig']


def test_unknown_or_incomplete_output_never_authorizes_a_contract_repair():
    original=replace(request(),protocol='openai.chat',parameters={'reasoning_effort':'low','response_format':{'type':'json_object'}})
    assert repairable(result(),original,'memory_consolidation_output_invalid')
    unknown=replace(result(),completion=ModelCompletion('unknown'))
    assert not repairable(unknown,original,'memory_consolidation_invalid')
    incomplete=result(completion=ModelCompletion('incomplete',('safety',)))
    assert not repairable(incomplete,original,'memory_consolidation_invalid')
    assert repairable(result(completion=ModelCompletion('incomplete',('length',))),original,'memory_consolidation_output_truncated')
    assert not repairable(result(),original,'source_unavailable')
    assert not repairable(result(),original,'revision_conflict')


def test_optional_rejected_output_is_omitted_as_a_whole_before_touching_source_evidence():
    original=request()
    class Counter:
        def measure(self,instruction,messages,tools):
            payload=json.loads(messages[-1].parts[0].text)['output_repair']
            return (20000 if 'previous_output' in payload else 10000,None)
    error=AppError('memory_consolidation_invalid',status=502,details={'path':['operations',0,'target','create'],'validator':'type'})
    repaired=repair_request(original,'repair',result(text='rejected candidate'),error,Counter(),12000)
    assert repaired.messages[0]==original.messages[0] and repaired.source_refs==original.source_refs
    diagnostic=json.loads(repaired.messages[-1].parts[0].text)['output_repair']
    assert diagnostic['previous_output_omitted']=='input_budget' and 'previous_output' not in diagnostic
    assert diagnostic['diagnostic']['path']==['operations',0,'target','create']
    assert diagnostic['previous_output_fragment']['json_excerpt']=='rejected candidate'


def test_disabled_thinking_repair_omits_effort_without_changing_the_original():
    original=replace(request(),protocol='openai.chat',parameters={'max_tokens':12288,
        'reasoning_effort':'low','response_format':{'type':'json_object'}})
    counter=SimpleNamespace(measure=lambda *args:(1000,None))
    repaired=repair_request(original,'repair',result(completion=ModelCompletion('incomplete',('length',))),
        AppError('memory_consolidation_output_truncated',status=502),counter,12000)
    assert repaired.parameters['thinking']=={'type':'disabled'}
    assert 'reasoning_effort' not in repaired.parameters and repaired.parameters['max_tokens']==12288
    assert original.parameters['reasoning_effort']=='low' and 'thinking' not in original.parameters


@pytest.mark.parametrize('context_round',[0,1])
@pytest.mark.parametrize('input_cap',[None,BACKGROUND_INPUT_TOKENS],ids=['configured-small','background-default'])
def test_near_cap_input_reserves_a_complete_repair_before_freezing_sources(context_round,input_cap):
    from tianshu.conversation.context.assembly import TokenCounter
    pipeline=Consolidation(SimpleNamespace(database=None,identity=None,sources=None,jobs=None))
    pipeline.counter=TokenCounter()
    pipeline.policy=replace(pipeline.policy,max_input_tokens=input_cap or 32000)
    original=request()
    data={'batch_id':'near_cap','context':{'actor_id':'actor','conversation_id':None,
        'time_zone':'Asia/Shanghai','cutoff_at':datetime.now(UTC).isoformat(),'message_cutoff':0},
        'subjects':{'person':{'subject_id':'person','kind':'person'}},
        'targets':{'person':{'new_material_ids':['m1']}},'records':{},'read_set':[],
        'materials':{'m1':{'role':'new','speaker_id':'person','origin_kind':'human',
            'occurred_at':datetime.now(UTC).isoformat(),'reply_to':[],'text':'',
            'source_refs':[{'source_id':'source','revision':1}]}},
        'episodes':[{'source_refs':[{'source_id':'source','revision':1}]}]}
    requests=({'request_id':'context1','kind':'material','ref':'m1','start':None,'end':None,
        'query':None,'reason':'需要上下文','outcome':'provided'},) if context_round else ()
    step_id='near_cap:step:'+str(context_round)
    bare=pipeline._request(original.context,data,step_id,context_round=context_round,requests=requests)
    overhead=repair_input_estimate(pipeline.counter,bare.system,bare.messages,bare.attempt_id)-bare.estimated_input_tokens
    input_limit=input_cap or max(16384,bare.estimated_input_tokens+overhead+256)
    near_cap=input_limit-64
    data['materials']['m1']['text']='x'*(near_cap-bare.estimated_input_tokens)
    original_text=data['materials']['m1']['text']
    pipeline.policy=replace(pipeline.policy,max_input_tokens=input_limit)
    with pytest.raises(AppError,match='memory_consolidation_input_budget') as caught:
        pipeline._request(original.context,data,step_id,context_round=context_round,requests=requests)
    assert caught.value.details['request_estimated']==near_cap
    assert caught.value.details['repair_required_tokens']>input_limit
    assert data['materials']['m1']['text']==original_text
    # Production selects a smaller complete source prefix before freezing. Place
    # this first request exactly at the combined request+reserved-repair limit.
    data['materials']['m1']['text']='x'*(input_limit-overhead-bare.estimated_input_tokens)
    first=pipeline._request(original.context,data,step_id,context_round=context_round,requests=requests)
    assert repair_input_estimate(pipeline.counter,first.system,first.messages,first.attempt_id)==input_limit
    rejected=result(text='invalid candidate '*10000)
    repaired=repair_request(first,'near_cap:repair',rejected,
        AppError('memory_consolidation_invalid',status=502,
            details={'path':['operations',0,'target','create'],'validator':'type'}),pipeline.counter,input_limit)
    assert repaired.estimated_input_tokens<=input_limit
    assert repaired.max_output_tokens==first.max_output_tokens==12288
    assert repaired.messages[0]==first.messages[0] and repaired.source_refs==first.source_refs
    assert json.loads(first.messages[0].parts[0].text)['materials'][0]['text']==data['materials']['m1']['text']
    assert json.loads(repaired.messages[-1].parts[0].text)['output_repair']['previous_output_omitted']=='input_budget'


def test_invalid_json_reports_position_without_loosening_the_decoder():
    with pytest.raises(AppError,match='memory_consolidation_output_invalid') as caught:
        Consolidation._parse({},result(text='{"operations" [}'))
    assert caught.value.details['position']>0 and caught.value.details['line']==1


@pytest.mark.parametrize('template',[
    '{}', '```json\n{}\n```', '```\n{}\n```', '\r\n```json\r\n{}\r\n```\r\n',
])
def test_only_a_complete_single_json_fence_is_normalized_before_full_validation(template):
    frozen={'model_protocol':MODEL_PROTOCOL,'batch_id':'single-json-fence',
        'subjects':{'person':{'subject_id':'person'}},'targets':{'person':{'new_material_ids':[]}},
        'materials':{},'records':{}}
    output={'targets':[{'subject_id':'person','status':'no_change','operations':[],'unresolved':[]}],
        'needs_context':[]}
    decoded=decode_model_json(template.format(json.dumps(output)))
    assert decoded==output
    assert parse_model_proposal(frozen,decoded)['target_results'][0]['status']=='no_change'
    invalid={**output,'targets':[]}
    with pytest.raises(AppError,match='memory_consolidation_target_coverage'):
        parse_model_proposal(frozen,decode_model_json(template.format(json.dumps(invalid))))


@pytest.mark.parametrize('text',[
    '说明\n```json\n{"a":1}\n```',
    '```json\n{"a":1}\n```\n说明',
    '```json\n{"a":1}\n```\n```json\n{"b":2}\n```',
    '```json\n{"a":}\n```',
    '```python\n{"a":1}\n```',
])
def test_surrounding_prose_multiple_blocks_and_invalid_json_are_rejected(text):
    with pytest.raises(json.JSONDecodeError):
        decode_model_json(text)


def test_budgeted_repair_excerpt_uses_the_same_fenced_json_decoder():
    original=request()
    candidate={'targets':[{'operations':[{'target':{'create':{'id':'n1'}}}]}],
        'padding':'x'*20000}
    class Counter:
        def measure(self,instruction,messages,tools):
            correction=json.loads(messages[-1].parts[0].text)['output_repair']
            return (20000 if 'previous_output' in correction else 1000,None)
    repaired=repair_request(original,'fenced:repair',result(text='```json\n'+json.dumps(candidate)+'\n```'),
        AppError('memory_consolidation_invalid',status=502,
            details={'path':['targets',0,'operations',0,'target','create'],'validator':'type'}),Counter(),12000)
    correction=json.loads(repaired.messages[-1].parts[0].text)['output_repair']
    fragment=correction['previous_output_fragment']
    assert fragment['path']==['targets',0,'operations',0,'target']
    assert json.loads(fragment['json_excerpt'])=={'create':{'id':'n1'}}
    assert repaired.messages[0]==original.messages[0] and repaired.source_refs==original.source_refs


def test_known_old_protocol_refresh_and_finite_v2_retry_preserve_the_frozen_request():
    from copy import deepcopy
    from tianshu.memory.consolidation_retry import explicit_retry_available
    from tianshu.memory.recovery import ConsolidationRecovery
    memory=SimpleNamespace(database=None,identity=None,sources=None,jobs=None)
    memory.consolidation=Consolidation(memory)
    recovery=ConsolidationRecovery(memory)
    episode={'extraction_state':'consolidation_failed','extraction_cursor':{'protocol':'memory.consolidation.v1'},
        'extraction_missing':[]}
    batch={'batch_id':'old-minimum','schema_version':SCHEMA_VERSION,
        'status':'failed','error_code':'memory_consolidation_output_truncated',
        'step_ids':['original-step'],'manifest':{'budget':{'input_tokens':12000,'output_tokens':4096,'reasoning_tokens':8192},
            'steps':{'original-step':{'materials':{'m1':{'role':'new','source_refs':[{'source_id':'source','revision':1,
                'locator':{'start':0,'end':1}}]}}}}}}
    original=deepcopy(batch)
    assert recovery._status_value(episode,batch,{})['action']=='refresh_material'
    assert set(batch['manifest']['budget'])=={'input_tokens','output_tokens','reasoning_tokens'}
    current={**batch,'manifest':{**batch['manifest'],'budget':memory.consolidation._budget_record()}}
    assert recovery._status_value(episode,current,{})['action']=='refresh_material'
    native=deepcopy(current)
    native['manifest']['steps']['original-step']['model_protocol']=MODEL_PROTOCOL
    assert recovery._status_value(episode,native,{})['action']=='configure_budget'
    native['error_code']='memory_consolidation_invalid'
    assert recovery._status_value(episode,native,{})['action']=='retry_computation'
    assert explicit_retry_available(native)
    native['manifest']['explicit_recoveries']=[{'step_id':'one-explicit-recovery'}]
    assert not explicit_retry_available(native)
    assert batch==original
