"""The background candidate port retains coverage without changing query budgets."""

from types import SimpleNamespace

import pytest

from tianshu.memory.retrieval import MemoryRetrieval
from tianshu.memory.preparation import CANDIDATE_GAP_CODES, candidate_coverage_view
from tianshu.memory.materials import material_query
from tianshu.runtime.api import Page
from tianshu.types import Coverage, ServiceClass


@pytest.mark.asyncio
async def test_background_candidates_keep_the_query_page_and_degraded_coverage():
    retrieval=MemoryRetrieval(SimpleNamespace(database=None,models=None),None)
    page=Page(("fixture-evidence",),None,Coverage(False,missing=("ranked_selection","rerank_unavailable")))
    calls=[]
    async def query(context,plan,budget,**kwargs):
        calls.append((context,plan,budget,kwargs))
        return page
    retrieval.query=query
    result=await retrieval.candidates("fixture-context","search",None,subject_ids=("alice",),
        matter_refs=("matter",),limit=100)
    assert result is page and result.items==("fixture-evidence",)
    assert result.coverage.missing==("ranked_selection","rerank_unavailable")
    context,plan,budget,options=calls[0]
    assert context=="fixture-context" and plan.query=="search" and plan.subject_ids==("alice",)
    assert plan.matter_refs==("matter",) and plan.evidence_kinds==("claim","hypothesis","matter")
    assert budget is None and options=={"limit":32,"service_class":ServiceClass.ELASTIC}


def test_candidate_coverage_bounds_model_diagnostics_without_losing_manifest_details():
    missing=["rerank_unavailable", "x"*1000, *["reason_"+str(index) for index in range(20)]]
    saved={"record_candidates_missing":missing,"record_omitted":["r0","r1"]}
    visible=candidate_coverage_view(saved)
    assert visible["record_candidates_missing"][0]=="rerank_unavailable"
    assert len(visible["record_candidates_missing"])==CANDIDATE_GAP_CODES
    assert visible["record_candidate_gap_details_omitted"]==len(missing)-CANDIDATE_GAP_CODES
    assert visible["record_omitted"]==2
    assert saved["record_candidates_missing"]==missing and saved["record_omitted"]==["r0","r1"]


def test_image_only_material_does_not_create_a_whitespace_remote_query():
    materials={"m1":{"role":"new","text":""},"m2":{"role":"new","text":""},
        "m3":{"role":"context","text":"历史文字不能代替新查询"}}
    assert material_query(materials)==""
    materials["m2"]["text"]=" 新原文 "
    assert material_query(materials)=="新原文"
