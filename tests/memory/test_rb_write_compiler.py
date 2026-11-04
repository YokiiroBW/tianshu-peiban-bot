"""Shared-proposal compilation and closed input boundaries, for central execution."""

from copy import deepcopy

import pytest

from tianshu.memory.extraction_contract import MODEL_PROTOCOL
from tianshu.memory.materials import closed_episode_split, partition_identity
from tianshu.memory.proposal import compile_proposal
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, TrustedContext


def frozen():
    return {
        "batch_id": "batch-a", "model_protocol": MODEL_PROTOCOL,
        "context": {"conversation_id": "g"},
        "subjects": {name: {"subject_id": name, "kind": "person"} for name in ("a", "b")},
        "targets": {"a": {"new_material_ids": ["m1"]}, "b": {"new_material_ids": ["m2"]}},
        "materials": {"m1": {"speaker_id": "a", "origin_kind": "human", "evidence_kind": "text"},
                      "m2": {"speaker_id": "b", "origin_kind": "human", "evidence_kind": "text"}},
        "records": {}, "read_set": [],
    }


def claim(subject, material, alias="n1"):
    return {"kind": "add_claim", "target": {"create": alias},
        "payload": {"predicate": "preference.food", "value": subject, "speaker_id": subject,
                    "assertion": "self_confirmed", "polarity": True, "conditions": {},
                    "valid_from": None, "valid_until": None},
        "support_groups": [[material]], "context_refs": [], "reads": [], "reason": "原件陈述"}


def relation():
    return {"kind": "link", "target": {"create": "n2"},
        "payload": {"relation_kind": "same_event", "from_ref": "n1",
                    "to_ref": {"subject_id": "b", "ref": "n1"},
                    "valid_from": None, "valid_until": None},
        "support_groups": [["m1", "m2"]], "context_refs": [], "reads": [], "reason": "同次讨论"}


def grouped():
    return {"targets": [
        {"subject_id": "a", "status": "changed", "operations": [relation(), claim("a", "m1")], "unresolved": []},
        {"subject_id": "b", "status": "changed", "operations": [claim("b", "m2")], "unresolved": []},
    ], "needs_context": []}


def test_subject_local_aliases_and_forward_cross_subject_reference_are_stable():
    source = grouped()
    original = deepcopy(source)
    proposal, aliases = compile_proposal(frozen(), source)
    again, again_aliases = compile_proposal(frozen(), source)
    by_key = {(item["subject_id"], item["local_ref"]): item["canonical_ref"] for item in aliases}
    assert source == original
    assert by_key[("a", "n1")] != by_key[("b", "n1")]
    link = proposal["operations"][0]
    assert link["payload"]["from_ref"] == by_key[("a", "n1")]
    assert link["payload"]["to_ref"] == by_key[("b", "n1")]
    assert proposal == again and aliases == again_aliases
    assert body_hash(proposal) == body_hash(again)


def test_duplicate_in_one_subject_reports_both_locations():
    output = grouped()
    output["targets"][0]["operations"].append(claim("a", "m1"))
    with pytest.raises(AppError, match="memory_consolidation_duplicate_create") as caught:
        compile_proposal(frozen(), output)
    assert caught.value.details["first_path"] == ["targets", 0, "operations", 1, "target", "create"]
    assert caught.value.details["second_path"] == ["targets", 0, "operations", 2, "target", "create"]


def test_unqualified_cross_subject_alias_is_not_guessed():
    output = grouped()
    output["targets"][0]["operations"] = [relation()]
    with pytest.raises(AppError, match="memory_consolidation_unknown_endpoint") as caught:
        compile_proposal(frozen(), output)
    assert caught.value.details["candidates"][0]["subject_id"] == "b"


def test_no_change_requires_a_real_target_reason():
    output = grouped()
    output["targets"][0]["operations"] = [claim("a", "m1")]
    output["targets"][1].update(status="no_change", operations=[])
    with pytest.raises(AppError):
        compile_proposal(frozen(), output)
    output["targets"][1]["reason"] = "该材料只是提问，没有新增事实"
    proposal, _ = compile_proposal(frozen(), output)
    assert proposal["target_results"][1]["reason"] == "该材料只是提问，没有新增事实"


def test_group_meaning_remains_a_speaker_attributed_group_claim():
    data = frozen()
    data["subjects"]["group:g"] = {"subject_id": "group:g", "kind": "group"}
    data["targets"]["group:g"] = {"new_material_ids": []}
    output = grouped()
    operation = claim("a", "m1")
    operation["payload"].update(predicate="language.term_meaning", value={"term": "代号", "meaning": "桌游"},
                                assertion="reported")
    output["targets"].append({"subject_id": "group:g", "status": "changed", "operations": [operation], "unresolved": []})
    proposal, _ = compile_proposal(data, output)
    assert proposal["operations"][-1]["subject_id"] == "group:g"
    assert proposal["operations"][-1]["payload"]["speaker_id"] == "a"
    operation["payload"]["assertion"] = "self_confirmed"
    with pytest.raises(AppError):
        compile_proposal(data, output)


def test_reorganization_does_not_cut_replies_sources_matters_or_shared_old_records():
    data = {"episodes": [{"episode_id": "e1", "complete": True}, {"episode_id": "e2", "complete": True}],
            "materials": {
                "m1": {"role": "new", "episode_id": "e1", "coverage_subject_id": "a", "reply_to": [],
                       "source_refs": [{"source_id": "s1", "revision": 1}]},
                "m2": {"role": "new", "episode_id": "e2", "coverage_subject_id": "b", "reply_to": [],
                       "source_refs": [{"source_id": "s2", "revision": 1}]}}, "records": {},
            "dependency_closure": {"complete": True, "components": [["e1"], ["e2"]]}}
    assert closed_episode_split(data) is not None
    for mutation in ("reply", "source", "matter", "record", "incomplete", "unknown_closure"):
        candidate = deepcopy(data)
        if mutation == "reply":
            candidate["materials"]["m2"]["reply_to"] = ["m1"]
        elif mutation == "source":
            candidate["materials"]["m2"]["source_refs"] = candidate["materials"]["m1"]["source_refs"]
        elif mutation == "matter":
            candidate["materials"]["m1"]["matter_id"] = candidate["materials"]["m2"]["matter_id"] = "same"
        elif mutation == "record":
            candidate["records"]["r1"] = {"subject_id": "group:g"}
        elif mutation == "incomplete":
            candidate["episodes"][0]["complete"] = False
        else:
            del candidate["dependency_closure"]
        assert closed_episode_split(candidate) is None


def test_partition_is_the_stable_scene_and_keeps_authority_separate():
    first = TrustedContext("owner", "actor", "a", "conversation", ("group",), ("group",), 1,
                           OriginKind.HUMAN, frozenset({"memory.read"}), "group")
    second = TrustedContext("owner", "actor", "b", "conversation", ("group", "extra"), ("group",), 2,
                            OriginKind.HUMAN, frozenset({"memory.read"}), "group")
    partition1, scope1, authority1 = partition_identity(first, ("group",), "scene-1")
    partition2, scope2, authority2 = partition_identity(second, ("group", "extra"), "scene-1")
    assert partition1 == partition2 and scope1 != scope2
    assert authority1["person_id"] is None and authority2["person_id"] is None
    assert authority1["scope_ids"] != authority2["scope_ids"]
    third = TrustedContext("different-principal", "actor", "b", "conversation", ("group",), ("group",), 1,
                           OriginKind.HUMAN, frozenset({"memory.read"}), "group")
    partition3, _, authority3 = partition_identity(third, ("group",), "scene-1")
    assert partition3 == partition1 and authority3["principal_id"] != authority1["principal_id"]
    assert partition_identity(first, ("group",), "scene-2")[0] != partition1
