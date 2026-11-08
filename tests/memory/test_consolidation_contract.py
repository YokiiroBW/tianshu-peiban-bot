"""Frozen multi-person contract behavior. Authored for the final integrated test run."""

from dataclasses import replace
from datetime import UTC, datetime
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from tianshu.memory.extraction_contract import (
    SCHEMA_VERSION, consolidation_example, consolidation_instruction,
    consolidation_schema, validate_proposal,
)
from tianshu.memory.materials import ConsolidationPolicy, frozen_mapping, partition_identity, prefix_boundary
from tianshu.memory.authority import context_record, request_identity
from tianshu.memory.module import index_cursor, next_index_cursor
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, TrustedContext


def frozen():
    return {"batch_id":"batch_synthetic","subjects":{
        "a":{"subject_id":"a","kind":"person"},"b":{"subject_id":"b","kind":"person"}},
        "materials":{
            "m1":{"speaker_id":"a","origin_kind":"human","text":"我同意","role":"new",
                  "source_refs":[{"source_id":"source_a","revision":1}]},
            "m2":{"speaker_id":"b","origin_kind":"human","text":"我同意","role":"new",
                  "source_refs":[{"source_id":"source_b","revision":1}]}},
        "targets":{"a":{"new_material_ids":["m1"]},"b":{"new_material_ids":["m2"]}},
        "records":{"r0":{"kind":"claim","id":"claim_1","revision":3,"subject_id":"a",
                          "content":"旧事实","source_refs":[{"source_id":"old","revision":1}]}},
        "read_set":[{"kind":"claim","id":"claim_1","revision":3}]}


def test_every_target_has_explicit_result_even_when_words_match():
    data=frozen()
    output=consolidation_example(data)
    Draft202012Validator.check_schema(consolidation_schema(data))
    assert validate_proposal(data,output) is output
    assert output["target_results"][0]["processed_material_ids"] == ["m1"]
    assert output["target_results"][1]["processed_material_ids"] == ["m2"]
    output["target_results"].pop()
    with pytest.raises(AppError,match="memory_consolidation_invalid"):
        validate_proposal(data,output)


def test_wrong_persons_material_and_unknown_revision_cannot_be_applied():
    data=frozen()
    output=consolidation_example(data)
    output["target_results"][0]["processed_material_ids"]=["m2"]
    with pytest.raises(AppError,match="material_coverage"):
        validate_proposal(data,output)
    output=consolidation_example(data)
    operation={"op_id":"op1","kind":"add_support","subject_id":"a",
        "target":{"ref":"r0","expected_revision":4},"payload":{},"support_groups":[["m1"]],
        "context_refs":[],"reads":["r0"],"reason":"本人补证"}
    output["operations"]=[operation]
    output["target_results"][0].update(status="changed",operation_ids=["op1"])
    with pytest.raises(AppError,match="revision_invalid"):
        validate_proposal(data,output)
    operation["target"]["expected_revision"]=3
    operation["reads"]=[]
    with pytest.raises(AppError,match="read_missing"):
        validate_proposal(data,output)


@pytest.mark.parametrize("kind", ["add_claim", "add_support", "revise_claim", "link"])
def test_model_target_shape_matches_the_domain_operation(kind):
    data = frozen()
    data["records"]["r1"] = {"kind": "claim", "id": "claim_2", "revision": 1,
        "subject_id": "b", "source_refs": [{"source_id": "other_old", "revision": 1}]}
    data["read_set"].append({"kind": "claim", "id": "claim_2", "revision": 1})
    claim = {"predicate": "interest", "value": "有原件支持的事实", "speaker_id": "a",
        "assertion": "self_confirmed", "polarity": True, "conditions": {},
        "valid_from": None, "valid_until": None}
    payloads = {"add_claim": claim, "add_support": {},
        "revise_claim": {**claim, "revision_mode": "correction"},
        "link": {"relation_kind": "supports", "from_ref": "r0", "to_ref": "r1",
            "valid_from": None, "valid_until": None}}
    creates = kind in {"add_claim", "link"}
    create_target = {"create": "n1"}
    record_target = {"ref": "r0", "expected_revision": 3}
    proposal = consolidation_example(data)
    proposal["operations"] = [{"op_id": "op1", "kind": kind, "subject_id": "a",
        "target": create_target if creates else record_target, "payload": payloads[kind],
        "support_groups": [["m1"]], "context_refs": [], "reads": ["r0", "r1"],
        "reason": "有原件支持的操作"}]
    proposal["target_results"][0].update(status="changed", operation_ids=["op1"])
    validator = Draft202012Validator(consolidation_schema(data))
    assert not list(validator.iter_errors(proposal))
    assert validate_proposal(data, proposal) is proposal

    invalid = deepcopy(proposal)
    invalid["operations"][0]["target"] = record_target if creates else create_target
    # The actual model-visible schema must reject this shape before any write;
    # sources, old revision, reads and target coverage are otherwise unchanged.
    assert any(list(error.absolute_path) == ["operations", 0, "target"]
        for error in validator.iter_errors(invalid))
    with pytest.raises(AppError, match="memory_consolidation_invalid"):
        validate_proposal(data, invalid)


@pytest.mark.parametrize(("field", "wrapped"), [
    pytest.param("from_ref", {"create": "n1"}, id="local-create-object"),
    pytest.param("to_ref", {"ref": "r0", "expected_revision": 3}, id="record-ref-object"),
])
def test_link_endpoints_are_alias_strings_while_target_creates_the_relation(field, wrapped):
    data = frozen()
    proposal = consolidation_example(data)
    proposal["operations"] = [
        {"op_id": "op1", "kind": "add_claim", "subject_id": "a", "target": {"create": "n1"},
            "payload": {"predicate": "interest", "value": "有原件支持的新事实", "speaker_id": "a",
                "assertion": "self_confirmed", "polarity": True, "conditions": {},
                "valid_from": None, "valid_until": None},
            "support_groups": [["m1"]], "context_refs": [], "reads": [], "reason": "原件支持"},
        {"op_id": "op2", "kind": "link", "subject_id": "a", "target": {"create": "n2"},
            "payload": {"relation_kind": "supports", "from_ref": "n1", "to_ref": "r0",
                "valid_from": None, "valid_until": None},
            "support_groups": [["m1"]], "context_refs": [], "reads": ["r0"], "reason": "新事实补证旧记录"},
    ]
    proposal["target_results"][0].update(status="changed", operation_ids=["op1", "op2"])
    validator = Draft202012Validator(consolidation_schema(data))
    assert not list(validator.iter_errors(proposal))
    assert validate_proposal(data, proposal) is proposal

    invalid = deepcopy(proposal)
    invalid["operations"][1]["payload"][field] = wrapped
    unchanged = deepcopy(invalid)
    assert any(list(error.absolute_path) == ["operations", 1, "payload", field]
        for error in validator.iter_errors(invalid))
    with pytest.raises(AppError, match="memory_consolidation_invalid"):
        validate_proposal(data, invalid)
    assert invalid == unchanged


@pytest.mark.parametrize('kind',['add_claim','correction','transition','revise_hypothesis'])
def test_shared_claim_schema_keeps_required_and_extra_property_validation(kind):
    data=frozen()
    output=consolidation_example(data)
    payload={'predicate':'interest','value':'原文支持的事实','speaker_id':'a','assertion':'self_confirmed',
        'polarity':True,'conditions':{},'valid_from':None,'valid_until':None}
    target={'create':'n1'}
    reads=[]
    operation_kind=kind
    if kind in {'correction','transition'}:
        operation_kind='revise_claim'
        target={'ref':'r0','expected_revision':3}
        reads=['r0']
        payload['revision_mode']=kind
    if kind=='transition':
        payload.update(successor_ref='n2',effective_at=None)
    if kind=='revise_hypothesis':
        payload['counterexample_refs']=[]
    operation={'op_id':'op1','kind':operation_kind,'subject_id':'a','target':target,'payload':payload,
        'support_groups':[['m1']],'context_refs':[],'reads':reads,'reason':'原件支持'}
    output['operations']=[operation]
    output['target_results'][0].update(status='changed',operation_ids=['op1'])
    assert validate_proposal(data,output) is output
    invalid=deepcopy(output)
    invalid['operations'][0]['payload']['uncontracted_property']='forbidden'
    with pytest.raises(AppError,match='memory_consolidation_invalid'):
        validate_proposal(data,invalid)
    invalid=deepcopy(output)
    del invalid['operations'][0]['payload']['value']
    with pytest.raises(AppError,match='memory_consolidation_invalid'):
        validate_proposal(data,invalid)
    invalid=deepcopy(output)
    invalid['operations'][0]['support_groups']=[['m3']]
    with pytest.raises(AppError,match='memory_consolidation_invalid'):
        validate_proposal(data,invalid)


def test_transition_cannot_redeclare_a_successor_created_by_another_operation():
    data = frozen()
    proposal = consolidation_example(data)
    claim = {'predicate':'interest', 'value':'原文支持的新状态', 'speaker_id':'a',
        'assertion':'self_confirmed', 'polarity':True, 'conditions':{},
        'valid_from':None, 'valid_until':None}
    proposal['operations'] = [
        {'op_id':'op1', 'kind':'add_claim', 'subject_id':'a', 'target':{'create':'n1'},
            'payload':claim, 'support_groups':[['m1']], 'context_refs':[], 'reads':[], 'reason':'原件支持'},
        {'op_id':'op2', 'kind':'revise_claim', 'subject_id':'a',
            'target':{'ref':'r0', 'expected_revision':3},
            'payload':{**claim, 'revision_mode':'transition', 'successor_ref':'n1', 'effective_at':None},
            'support_groups':[['m1']], 'context_refs':[], 'reads':['r0'], 'reason':'后来变化'},
    ]
    proposal['target_results'][0].update(status='changed', operation_ids=['op1', 'op2'])
    assert not list(Draft202012Validator(consolidation_schema(data)).iter_errors(proposal))
    with pytest.raises(AppError, match='memory_consolidation_duplicate_create'):
        validate_proposal(data, proposal)


def test_default_byte_counter_can_hold_a_prefix_without_serializing_unselected_sources():
    from tianshu.conversation.context.assembly import TokenCounter
    from tianshu.memory.consolidation import Consolidation
    from tianshu.memory.generation import repair_input_estimate
    from tianshu.types import SourceRef
    memory=SimpleNamespace(database=None,identity=None,sources=None,jobs=None)
    pipeline=Consolidation(memory)
    pipeline.counter=TokenCounter()
    context=TrustedContext('principal','actor','a',None,('scope',),('scope',),1,OriginKind.HUMAN)
    data=frozen()
    data['context']={'actor_id':'actor','conversation_id':None,'time_zone':'Asia/Shanghai',
        'cutoff_at':datetime.now(UTC).isoformat(),'message_cutoff':0}
    for item in data['materials'].values():
        item.update(occurred_at=datetime.now(UTC).isoformat(),reply_to=[])
    data['materials']['m1']['text']='填'*256
    data['episodes']=[{'source_refs':[{'source_id':'unselected_'+str(index),'revision':1} for index in range(129)]}]
    request=pipeline._request(context,data,'synthetic_prefix')
    assert request.estimated_input_tokens<=pipeline.policy.max_input_tokens
    assert repair_input_estimate(pipeline.counter,request.system,request.messages,request.attempt_id)<=pipeline.policy.max_input_tokens
    assert SourceRef('unselected_128',1) in request.source_refs
    assert 'unselected_128' not in request.messages[0].parts[0].text
    small=deepcopy(data)
    small['episodes'][0]['source_refs']=small['episodes'][0]['source_refs'][:1]
    smaller=pipeline._request(context,small,'synthetic_prefix')
    assert smaller.estimated_input_tokens==request.estimated_input_tokens


def test_input_layout_change_rearms_only_a_known_blocked_receipt():
    from tianshu.memory.consolidation import Consolidation
    from tianshu.memory.recovery import ConsolidationRecovery
    memory=SimpleNamespace(database=None,identity=None,sources=None,jobs=None)
    memory.consolidation=Consolidation(memory)
    recovery=ConsolidationRecovery(memory)
    episode={'extraction_state':'pending','extraction_cursor':{'protocol':SCHEMA_VERSION},'extraction_missing':[]}
    current=memory.consolidation._input_budget_record()
    old={'configuration':{**current,'input_layout':'expanded-before-shared-defs'},'required_tokens':12803}
    assert recovery._status_value(episode,None,{},'memory_consolidation_input_budget',old)['action']=='refresh_material'
    unchanged={'configuration':current,'required_tokens':12803}
    assert recovery._status_value(episode,None,{},'memory_consolidation_input_budget',unchanged)['action']=='configure_budget'
    active={'batch_id':'original','status':'waiting_model','error_code':'model_outcome_unknown'}
    assert recovery._status_value(episode,active,{},'memory_consolidation_input_budget',old)['action']=='observe_original_attempt'
    finished={**episode,'extraction_state':'completed'}
    assert recovery._status_value(finished,None,{},'memory_consolidation_input_budget',old)['action']=='wait_for_new_evidence'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['episode_identity', 'episode_revision', 'source_revision', 'cursor'])
async def test_truncation_limits_apply_only_to_the_same_unadvanced_source_prefix(change):
    from tianshu.memory.consolidation import Consolidation
    from tianshu.types import SourceRef

    pipeline = Consolidation(SimpleNamespace(database=None, identity=None, sources=None, jobs=None))
    current = {'episode_id':'episode', 'revision':2, 'sequence':5, 'scope_ids':['scope'],
        'extraction_cursor':{'protocol':SCHEMA_VERSION, 'source_index':0, 'position':None}}
    old = {key:deepcopy(current[key]) for key in ('episode_id', 'revision', 'sequence', 'scope_ids')}
    old.update(before=deepcopy(current['extraction_cursor']), source_refs=[{'source_id':'source', 'revision':3}])
    previous = {'status':'failed', 'error_code':'memory_consolidation_output_truncated',
        'manifest':{'budget':pipeline._budget_record(), 'episodes':[old]}}

    class Reads:
        revision = 3

        async def source_set(self, *_args):
            return (SourceRef('source', self.revision),), set()

    reads = Reads()
    assert await pipeline._related_truncation(previous, [current], None, reads)
    if change == 'episode_identity':
        current['episode_id'] = 'different_episode'
    elif change == 'episode_revision':
        current['revision'] += 1
    elif change == 'source_revision':
        reads.revision += 1
    else:
        current['extraction_cursor']['position'] = 1
    assert not await pipeline._related_truncation(previous, [current], None, reads)


@pytest.mark.asyncio
async def test_preparation_cache_keeps_exact_authority_revision_and_source_bounds():
    from tianshu.memory.materials import PreparationReads
    from tianshu.types import Coverage,SourceRef

    class Sources:
        def __init__(self):
            self.calls=[]

        async def read(self,context,source,requested):
            self.calls.append((context,source,requested))
            start=requested.get('position',(source.locator or {}).get('start',0))
            lower=(source.locator or {}).get('start',0)
            upper=min(10,(source.locator or {}).get('end',10))
            if start<lower or start>upper:
                raise AppError('source_position_outside_reference')
            end=min(upper,start+requested['max_characters'])
            return {'source_ref':source,'content':'abcdefghij'[start:end],
                'coverage':Coverage(start==lower and end==upper,10,ranges=({'start':start,'end':end},)),
                'next_position':end if end<upper else None}

    sources=Sources()
    cache=PreparationReads(sources)
    context=TrustedContext('principal','actor','a',None,('scope',),('scope',),1,OriginKind.HUMAN)
    source=SourceRef('exact',1,{'start':4,'end':10})
    first=await cache.read(context,source,{'max_characters':6})
    smaller=await cache.read(context,source,{'max_characters':3})
    assert len(sources.calls)==1 and first['content']=='efghij' and smaller['content']=='efg'
    assert smaller['coverage'].ranges==({'start':4,'end':7},) and smaller['next_position']==7
    assert smaller['coverage'].complete is False
    smaller['content']='tampered'
    assert (await cache.read(context,source,{'max_characters':6}))['content']=='efghij'
    await cache.read(context,replace(source,revision=2),{'max_characters':6})
    await cache.read(context,replace(source,locator={'start':4,'end':8}),{'max_characters':6})
    await cache.read(replace(context,scope_ids=('another',),audience=('another',)),source,{'max_characters':6})
    assert len(sources.calls)==4
    with pytest.raises(AppError,match='source_position_outside_reference'):
        await cache.read(context,source,{'position':0,'max_characters':2})


def test_group_partition_ignores_current_speaker_and_keeps_authority_domain():
    context=TrustedContext("principal","actor","a","group",("group_scope",),("group_scope",),1,
        OriginKind.HUMAN,frozenset({"memory.manage"}),"group",True)
    first=partition_identity(context,("group_scope",))
    assert first[0] == partition_identity(replace(context,person_id="b"),("group_scope",))[0]
    assert first[0] != partition_identity(replace(context,principal_id="another"),("group_scope",))[0]
    assert first[0] != partition_identity(context,("private_scope",))[0]
    assert first[2]["person_id"] is None
    assert first[2]["write_scope_ids"] == ["group_scope"]


def test_fixed_request_authority_has_stable_set_order_after_restart():
    first={"scope_ids":["b","a"],"audience":["b","a"],"permissions":["read","write"]}
    second={"scope_ids":["a","b"],"audience":["a","b"],"permissions":["write","read"]}
    assert context_record(first) == context_record(second)
    assert request_identity({"context":first,"attempt_id":"same","budget":{"deadline_at":"old"}}) == request_identity(
        {"context":second,"attempt_id":"same","budget":{"deadline_at":"new"}})


def test_request_capabilities_are_a_set_and_legacy_digest_remains_immutable():
    from tianshu.memory.authority import request_digest_matches
    from tianshu.runtime.commands import body_hash

    request = {"context":{"permissions":["*"],"scope_ids":["actor"],"audience":["actor"]},
               "attempt_id":"original","route_id":"original-route","required_capabilities":["vision","text"]}
    old = body_hash(request_identity(request,canonical_capabilities=False))
    reordered = {**request,"required_capabilities":["text","vision"]}
    assert request_identity(request) == request_identity(reordered)
    assert request_digest_matches(reordered,old)
    assert not request_digest_matches({**reordered,"route_id":"different"},old)
    assert not request_digest_matches({**reordered,"required_capabilities":["text"]},old)
    assert request["required_capabilities"] == ["vision","text"]


def test_index_rebuild_cursor_is_bound_to_actual_kinds_and_stable_object_id():
    kinds=("claim","episode","hypothesis","matter","reflection")
    cursor=next_index_cursor({"kind":"episode","id":"expression:synthetic"},kinds)
    assert index_cursor(cursor,kinds) == ("episode","expression:synthetic")
    with pytest.raises(AppError,match="invalid_memory_index_cursor"):
        index_cursor(cursor,("claim",))


def test_manifest_keeps_mapping_without_a_second_full_model_input():
    data=frozen()
    saved=frozen_mapping(data)
    assert "text" not in saved["materials"]["m1"] and "content" not in saved["records"]["r0"]
    assert saved["records"]["r0"]["revision"] == 3
    assert data["materials"]["m1"]["text"] == "我同意"
    instruction=consolidation_instruction(data)
    assert SCHEMA_VERSION in instruction and "无新增事实用no_change" in instruction
    example=consolidation_example(data)
    assert json.loads(json.dumps(example))["batch_id"] == data["batch_id"]


def test_nearby_prefix_prefers_a_boundary_keeping_a_reply_with_its_parent():
    episodes=[{"episode_id":str(i),"complete":True} for i in range(4)]
    materials={"m1":{"episode_id":"0","locator":{"event_id":"e0"}},
               "m2":{"episode_id":"1","locator":{"event_id":"e1"}},
               "m3":{"episode_id":"2","locator":{"event_id":"e2","metadata":{"reply_to_event_id":"e3"}}},
               "m4":{"episode_id":"3","locator":{"event_id":"e3"}}}
    assert prefix_boundary(materials,episodes)==4


def test_reasoning_budget_is_separate_from_visible_output():
    policy=ConsolidationPolicy.from_settings({"max_output_tokens":4096,"reasoning_reserve_tokens":8192})
    assert policy.max_output_tokens+policy.reasoning_reserve_tokens==12288
    with pytest.raises(AppError,match="invalid_memory_consolidation_settings"):
        ConsolidationPolicy.from_settings({"quiet_seconds":601,"max_wait_seconds":600})


def test_overlapping_context_is_sent_once_by_exact_source_range():
    from tianshu.memory.materials import append_context_material
    from tianshu.types import Coverage,SourceRef
    materials={'m1':{'source_refs':[{'source_id':'same','revision':1,'locator':{'start':0,'end':4}}]}}
    read={'source_ref':SourceRef('same',1,{}),'content':'abcdefgh','coverage':Coverage(True,8,ranges=({'start':0,'end':8},)),
        'speaker_id':'a','origin_kind':'human','occurred_at':datetime.now(UTC),'locator':{}}
    assert append_context_material(materials,read)==1
    assert materials['m2']['text']=='efgh'
    assert materials['m2']['source_refs'][0]['locator']=={'start':4,'end':8}
    assert append_context_material(materials,read)==0


def test_full_evidence_endpoint_versions_enter_the_frozen_read_set():
    from tianshu.memory.materials import expand_record_endpoints
    records={'r0':{'kind':'hypothesis','id':'h1','revision':2,'source_refs':[],
        'relations':[{'from_ref':{'kind':'claim','id':'c1','revision':3},
            'to_ref':{'kind':'hypothesis','id':'h1','revision':2},'relation_kind':'supports',
            'from_evidence':{'kind':'claim','id':'c1','revision':3,'source_refs':[],'content':'必要前提'},
            'to_evidence':{'kind':'hypothesis','id':'h1','revision':2,'source_refs':[]}}]}}
    expanded=expand_record_endpoints(records)
    assert expanded['r1']['id']=='c1' and expanded['r1']['revision']==3
    assert expanded['r0']['relations'][0]['from_ref']=='r1'
    assert 'from_evidence' not in expanded['r0']['relations'][0]
