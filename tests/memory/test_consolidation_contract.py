"""Frozen multi-person contract behavior. Authored for the final integrated test run."""

from dataclasses import replace
from datetime import UTC, datetime
import json

import pytest
from jsonschema import Draft202012Validator

from tianshu.memory.extraction_contract import (
    SCHEMA_VERSION, consolidation_example, consolidation_instruction,
    consolidation_schema, validate_proposal,
)
from tianshu.memory.materials import ConsolidationPolicy, frozen_mapping, partition_identity, prefix_boundary
from tianshu.memory.authority import context_record, request_identity
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
