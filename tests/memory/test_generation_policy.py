"""Native extraction policy and bounded repair, for centralized verification."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from tianshu.memory.consolidation import Consolidation
from tianshu.memory.generation import native_request, repairable, repair_request
from tianshu.models.budgets import effective_output_tokens
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


def test_disabled_thinking_repair_omits_effort_without_changing_the_original():
    original=replace(request(),protocol='openai.chat',parameters={'max_tokens':12288,
        'reasoning_effort':'low','response_format':{'type':'json_object'}})
    counter=SimpleNamespace(measure=lambda *args:(1000,None))
    repaired=repair_request(original,'repair',result(completion=ModelCompletion('incomplete',('length',))),
        AppError('memory_consolidation_output_truncated',status=502),counter,12000)
    assert repaired.parameters['thinking']=={'type':'disabled'}
    assert 'reasoning_effort' not in repaired.parameters and repaired.parameters['max_tokens']==12288
    assert original.parameters['reasoning_effort']=='low' and 'thinking' not in original.parameters


def test_invalid_json_reports_position_without_loosening_the_decoder():
    with pytest.raises(AppError,match='memory_consolidation_output_invalid') as caught:
        Consolidation._parse({},result(text='{"operations" [}'))
    assert caught.value.details['position']>0 and caught.value.details['line']==1


def test_new_native_policy_rearms_an_old_irreducible_known_truncation_without_mutating_its_request():
    from tianshu.memory.recovery import ConsolidationRecovery
    memory=SimpleNamespace(database=None,identity=None,sources=None,jobs=None)
    memory.consolidation=Consolidation(memory)
    recovery=ConsolidationRecovery(memory,None)
    episode={'extraction_state':'consolidation_failed','extraction_cursor':{'protocol':'memory.consolidation.v1'},
        'extraction_missing':[]}
    batch={'batch_id':'old-minimum','status':'failed','error_code':'memory_consolidation_output_truncated',
        'step_ids':['original-step'],'manifest':{'budget':{'input_tokens':12000,'output_tokens':4096,'reasoning_tokens':8192},
            'steps':{'original-step':{'materials':{'m1':{'role':'new','source_refs':[{'source_id':'source','revision':1,
                'locator':{'start':0,'end':1}}]}}}}}}
    assert recovery._status_value(episode,batch,{})['action']=='refresh_material'
    assert set(batch['manifest']['budget'])=={'input_tokens','output_tokens','reasoning_tokens'}
    current={**batch,'manifest':{**batch['manifest'],'budget':memory.consolidation._budget_record()}}
    assert recovery._status_value(episode,current,{})['action']=='configure_budget'
