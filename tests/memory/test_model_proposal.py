"""NR-07B proposal compilation and actionable semantic failures; run centrally."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from tianshu.memory.extraction_contract import (
    MODEL_PROTOCOL, consolidation_example, consolidation_instruction,
    consolidation_schema, model_schema, parse_model_proposal, validate_proposal,
)
from tianshu.memory.generation import repair_diagnostic
from tianshu.runtime.errors import AppError


def frozen():
    return {"model_protocol": MODEL_PROTOCOL, "batch_id": "synthetic_model_proposal",
        "subjects": {subject: {"subject_id": subject, "kind": "person"} for subject in ("a", "b")},
        "targets": {"a": {"new_material_ids": ["m1", "m2"]}, "b": {"new_material_ids": ["m3"]}},
        "materials": {
            "m1": {"speaker_id": "a", "origin_kind": "human", "evidence_kind": "text", "text": "我不喜欢香菜"},
            "m2": {"speaker_id": "a", "origin_kind": "human", "evidence_kind": "image",
                   "media": {"mime_type": "image/png"}, "locator": {"part_kind": "image"}},
            "m3": {"speaker_id": "b", "origin_kind": "human", "evidence_kind": "text", "text": "我明白了"}},
        "records": {
            "r1": {"revision": 3, "head_revision": 3, "content": "旧事实", "kind": "claim", "subject_id": "a"},
            "r2": {"revision": 2, "head_revision": 2, "content": "必要解读", "kind": "claim", "subject_id": "a"},
            "r3": {"revision": 1, "head_revision": 1, "content_omitted": True, "kind": "claim", "subject_id": "a"}},
        "read_set": []}


def proposal():
    return {"targets": [{"subject_id": subject, "status": "no_change", "operations": [], "unresolved": []}
                        for subject in ("a", "b")], "needs_context": []}


def claim_operation():
    return {"kind": "add_claim", "target": {"create": "n1"},
        "payload": {"predicate": "preference.food", "value": "不喜欢香菜", "speaker_id": "a",
            "assertion": "self_confirmed", "polarity": True, "conditions": {},
            "valid_from": None, "valid_until": None},
        "support_groups": [["m1"]], "context_refs": [], "reads": [], "reason": "本人明确陈述"}


def changed(operation=None):
    result = proposal()
    result["targets"][0].update(status="changed", operations=[operation or claim_operation()])
    return result


def test_compiler_preserves_semantic_dependencies_and_supplies_frozen_cas_and_coverage():
    data, output = frozen(), changed()
    operation = output["targets"][0]["operations"][0]
    operation.update(kind="revise_claim", target={"ref": "r1"}, reads=["r2"], context_refs=["m3"])
    operation["payload"]["revision_mode"] = "correction"
    original = deepcopy(output)
    compiled = parse_model_proposal(data, output)
    assert output == original
    assert compiled["batch_id"] == data["batch_id"]
    assert compiled["operations"][0]["target"] == {"ref": "r1", "expected_revision": 3}
    assert compiled["operations"][0]["reads"] == ["r2", "r1"]
    assert compiled["operations"][0]["context_refs"] == ["m3"]
    assert compiled["target_results"][0]["processed_material_ids"] == ["m1", "m2"]
    assert compiled["target_results"][0]["operation_ids"] == ["op1"]
    assert compiled["target_results"][1]["status"] == "no_change"
    assert validate_proposal(data, compiled) is compiled


@pytest.mark.parametrize("mutation", ["missing_target", "duplicate_target", "missing_status", "false_no_change"])
def test_missing_or_inconsistent_target_decisions_are_never_completed_by_the_server(mutation):
    output = changed()
    if mutation == "missing_target":
        output["targets"].pop()
    elif mutation == "duplicate_target":
        output["targets"][1]["subject_id"] = "a"
    elif mutation == "missing_status":
        del output["targets"][1]["status"]
    else:
        output["targets"][0]["status"] = "no_change"
    with pytest.raises(AppError) as caught:
        parse_model_proposal(frozen(), output)
    assert caught.value.details["path"][0] == "targets"
    assert "actual" in caught.value.details


def test_old_frozen_contract_and_new_protocol_cannot_be_confused():
    data = frozen()
    del data["model_protocol"]
    old = consolidation_example(data)
    assert parse_model_proposal(data, old) is old
    with pytest.raises(AppError):
        parse_model_proposal(data, proposal())
    data["model_protocol"] = MODEL_PROTOCOL
    with pytest.raises(AppError):
        parse_model_proposal(data, old)
    Draft202012Validator.check_schema(model_schema(data))
    assert MODEL_PROTOCOL in consolidation_instruction(data)
    assert len(json.dumps(proposal())) < len(json.dumps(old))


@pytest.mark.parametrize("use", ["target", "read", "endpoint", "counterexample"])
def test_stub_is_context_only_and_yields_an_exact_bounded_record_request(use):
    data, output = frozen(), changed()
    operation = output["targets"][0]["operations"][0]
    if use == "target":
        operation.update(kind="add_support", target={"ref": "r3"}, payload={})
    elif use == "read":
        operation["reads"] = ["r3"]
    elif use == "endpoint":
        operation.update(kind="link", payload={"relation_kind": "supports", "from_ref": "n1", "to_ref": "r3",
            "valid_from": None, "valid_until": None})
    else:
        operation["kind"] = "revise_hypothesis"
        operation["payload"].update(assertion="inferred", counterexample_refs=["r3"])
    with pytest.raises(AppError, match="memory_consolidation_record_context_required") as caught:
        parse_model_proposal(data, output)
    assert caught.value.details["path"][:4] == ["targets", 0, "operations", 0]
    assert caught.value.details["requests"][0]["ref"] == "r3"
    context = proposal()
    context["targets"][0].update(status="unresolved", unresolved=[{
        "material_ids": ["m1"], "reason": "需要旧记录正文", "needed_refs": ["r3"]}])
    context["needs_context"] = [{"kind": "record", "ref": "r3", "reason": "索引未提供正文"}]
    compiled = parse_model_proposal(data, context)
    assert compiled["needs_context"][0]["ref"] == "r3"
    assert compiled["target_results"][0]["status"] == "unresolved"


def test_raw_message_id_and_missing_range_have_specific_finite_diagnostics():
    data, output = frozen(), proposal()
    output["needs_context"] = [{"kind": "material", "ref": "qq_raw_12345", "reason": "补查"}]
    with pytest.raises(AppError) as caught:
        parse_model_proposal(data, output)
    diagnostic = repair_diagnostic(caught.value)
    assert diagnostic["path"] == ["needs_context", 0, "ref"]
    assert diagnostic["actual"] == "qq_raw_12345"
    assert diagnostic["allowed"] == ["m1", "m2", "m3"]

    legacy = consolidation_example(data)
    legacy["needs_context"] = [{"request_id": "c1", "kind": "conversation_range", "ref": None,
        "start": None, "end": None, "query": None, "reason": "补上下文"}]
    assert not list(Draft202012Validator(consolidation_schema(data)).iter_errors(legacy))
    with pytest.raises(AppError, match="memory_context_range_required") as caught:
        validate_proposal(data, legacy)
    diagnostic = repair_diagnostic(caught.value)
    assert diagnostic["path"] == ["needs_context", 0]
    assert diagnostic["actual"] == {"start": None, "end": None}
    assert diagnostic["missing"] == ["start", "end"]
    assert len(json.dumps(diagnostic, ensure_ascii=False).encode()) <= 1200


@pytest.mark.parametrize("kind", ["image", "reference", "text_image"])
def test_upload_or_technical_reference_cannot_alone_become_self_confirmation(kind):
    data, output = frozen(), changed()
    data["materials"]["m2"]["evidence_kind"] = kind
    output["targets"][0]["operations"][0]["support_groups"] = [["m2"]]
    with pytest.raises(AppError, match="claim_self_confirmation_unproven") as caught:
        parse_model_proposal(data, output)
    assert caught.value.details["path"] == ["targets", 0, "operations", 0, "support_groups", 0]
    output["targets"][0]["operations"][0]["payload"]["assertion"] = "observed"
    assert parse_model_proposal(data, output)["operations"][0]["payload"]["assertion"] == "observed"


def test_context_range_matches_the_existing_business_bound():
    output = proposal()
    output["needs_context"] = [{"kind": "conversation_range", "start": "2026-11-01T00:00:00+08:00",
        "end": "2026-11-03T00:00:00+08:00", "reason": "需要上下文"}]
    with pytest.raises(AppError, match="memory_context_range_invalid") as caught:
        parse_model_proposal(frozen(), output)
    assert caught.value.details["path"] == ["needs_context", 0]
