"""Whole evidence must reach the actual ModelMessage context or be omitted together."""

from datetime import UTC, datetime, timedelta
from dataclasses import replace
import json
from types import SimpleNamespace

from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.memory.retrieval import contributions
from tianshu.memory.display import display_evidence_on
from tianshu.memory.types import MemoryEvidenceUnit, RecordRef, SemanticLink
from tianshu.runtime.serialization import wire
from tianshu.types import ContextContribution, Coverage, OriginKind, RequestBudget, SourceRef, TrustedContext


class Authority:
    def __init__(self, revoke_on_final=False):
        self.checks = 0
        self.revoke_on_final = revoke_on_final

    async def check(self, *args):
        pass

    async def check_many(self, context, groups):
        self.checks += 1
        return tuple(not (self.revoke_on_final and self.checks > 1 and any(ref.source_id == "opposite" for ref in refs)) for refs in groups)


def context():
    return TrustedContext("principal", "actor", "person", None, ("scope",), ("scope",), 1, OriginKind.HUMAN)


def unit(identifier="fact", *, opposite_text="我周末不喝咖啡", hypothesis=False):
    now = datetime.now(UTC)
    own, other = SourceRef("own", 1), SourceRef("opposite", 1)
    value = MemoryEvidenceUnit(kind="hypothesis" if hypothesis else "claim", id=identifier, revision=2,
        actor_id="actor", subject_id="person", speaker_id="person", assertion="inferred" if hypothesis else "self_confirmed",
        content="咖啡", conditions={"day": "weekend"}, occurred_at=now, valid_from=now, valid_until=None,
        recorded_at=now, matter_id="weekend-meeting", current_status="current", state="active",
        source_refs=(own,), scope_ids=("scope",), coverage=Coverage(True),
        interpretation=hypothesis, head_revision=2)
    opposite = replace(value, kind="claim", id="other", revision=3, head_revision=4, speaker_id="other-person",
                       assertion="reported", interpretation=False, content=opposite_text, source_refs=(other,))
    link = SemanticLink("real-semantic-link", "actor", RecordRef(value.kind, identifier, 2),
        RecordRef("claim", "other", 3), "contradicts", None, None, now, (own, other), ("scope",),
        from_evidence=wire(value), to_evidence=wire(opposite))
    return replace(value, source_refs=(own, other), relations=(link,))


async def test_model_messages_include_conditions_both_sides_and_distinct_content_revisions():
    assembler = ContextAssembler(Authority(), None, None)
    units = (unit(), unit("guess", hypothesis=True))
    pack = await assembler.assemble(context(), "extract", "你好", RequestBudget(datetime.now(UTC)+timedelta(seconds=10)),
        contributions=contributions(units), max_input_tokens=20000, reserved_output_tokens=100)
    materials = [json.loads(item.text) for item in pack.materials]
    assert len(materials) == 2 and {item["kind"] for item in materials} == {"claim", "hypothesis"}
    assert all(item["conditions"] == {"day": "weekend"} for item in materials)
    assert materials[0]["relations"][0]["to_evidence"]["speaker_id"] == "other-person"
    assert materials[1]["interpretation"] and materials[1]["assertion"] == "inferred"
    assert {"claim:fact:2", "hypothesis:guess:2"} <= set(pack.content_revisions)
    model_material = "\n".join(part.text for message in pack.messages for part in message.parts if part.text)
    assert "我周末不喝咖啡" in model_material and "other-person" in model_material


async def test_budget_cannot_keep_the_short_side_of_an_oversized_conflict():
    assembler = ContextAssembler(Authority(), None, None)
    evidence = unit(opposite_text="必须与正面证据一起保留的条件"*1000)
    pack = await assembler.assemble(context(), "extract", "你好", RequestBudget(datetime.now(UTC)+timedelta(seconds=10)),
        contributions=contributions((evidence,)), max_input_tokens=4000, reserved_output_tokens=100)
    assert not pack.materials and "budget_omitted:memory_evidence" in pack.missing
    assert "咖啡" not in "\n".join(part.text for message in pack.messages for part in message.parts if part.text)


async def test_final_withdrawal_drops_both_sides_and_preserves_usable_current_input():
    assembler = ContextAssembler(Authority(revoke_on_final=True), None, None)
    mandatory = ContextContribution("input", "", (SourceRef("own", 1),), ("scope",), 100, True)
    named = replace(unit(), display_subjects=({"subject_id": "other-person", "display_name": "可撤回作者",
                                              "aliases": (), "kind": "person"},))
    pack = await assembler.assemble(context(), "extract", "你好", RequestBudget(datetime.now(UTC)+timedelta(seconds=10)),
        contributions=(mandatory, *contributions((named,))), max_input_tokens=20000, reserved_output_tokens=100)
    assert [item.kind for item in pack.materials] == ["input"]
    assert pack.source_refs == mandatory.source_refs and "contribution_unavailable:memory_evidence" in pack.missing
    assert not any("other-person" in (part.text or "") for message in pack.messages for part in message.parts)
    assert not any("可撤回作者" in (part.text or "") for message in pack.messages for part in message.parts)


async def test_display_mapping_uses_only_readable_subjects_and_relation_authors():
    class Identity:
        def __init__(self):
            self.requested = []

        async def resolve_memory_subjects_on(self, conn, current, ids, **kwargs):
            self.requested.extend(ids)
            return ({"subject_id": "person", "display_name": "本人", "aliases": ("授权别名",), "kind": "person"},
                    {"subject_id": "other-person", "display_name": "引用作者", "aliases": (), "kind": "person"},
                    {"subject_id": "actor:actor", "display_name": "actor", "aliases": (), "kind": "actor"},
                    {"subject_id": "directory-only", "display_name": "不相关群成员", "aliases": (), "kind": "person"})

    class Sources:
        async def check_many_on(self, conn, current, groups):
            return tuple(not any(ref.source_id == "private" for ref in refs) for refs in groups)

    identity = Identity()
    memory = SimpleNamespace(identity=identity, sources=Sources())
    private = replace(unit("private-fact"), subject_id="private-person", speaker_id="private-person",
                      relations=(), source_refs=(SourceRef("private", 1),))
    unknown = replace(unit("unknown-person-fact"), subject_id="unknown-person", speaker_id=None, relations=())
    actor = replace(unit("actor-note"), subject_id=None, speaker_id="actor:actor", relations=())
    displayed, hidden, unresolved, actor = await display_evidence_on(memory, None, context(), (unit(), private, unknown, actor))
    assert hidden is None and set(identity.requested) == {"person", "other-person", "unknown-person", "actor:actor"}
    assert not unresolved.display_subjects
    assert actor.display_subjects[0]["display_name"] is None
    people = {item["subject_id"]: item for item in displayed.display_subjects}
    assert people["person"]["aliases"] == ("授权别名",)
    assert people["other-person"]["display_name"] == "引用作者"
    assert "directory-only" not in people and "private-person" not in people
    pack = await ContextAssembler(Authority(), None, None).assemble(context(), "extract", "谁转述？",
        RequestBudget(datetime.now(UTC)+timedelta(seconds=10)), contributions=contributions((displayed,)),
        max_input_tokens=20000, reserved_output_tokens=100)
    material = json.loads(pack.materials[0].text)
    assert {item["display_name"] for item in material["display_subjects"]} == {"本人", "引用作者"}


async def test_display_aliases_are_budgeted_inside_the_whole_evidence_unit():
    named = replace(unit(), display_subjects=({"subject_id": "person", "display_name": "本人",
        "aliases": ("很长的授权别名"*1000,), "kind": "person"},))
    assembler = ContextAssembler(Authority(), None, None)
    pack = await assembler.assemble(context(), "extract", "你好", RequestBudget(datetime.now(UTC)+timedelta(seconds=10)),
        contributions=contributions((named,)), max_input_tokens=4000, reserved_output_tokens=100)
    assert not pack.materials and "budget_omitted:memory_evidence" in pack.missing
    assert not any("很长的授权别名" in (part.text or "") for message in pack.messages for part in message.parts)
