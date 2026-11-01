"""Whole evidence must reach the actual ModelMessage context or be omitted together."""

from datetime import UTC, datetime, timedelta
from dataclasses import replace
import json

from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.memory.retrieval import contributions
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
    pack = await assembler.assemble(context(), "extract", "你好", RequestBudget(datetime.now(UTC)+timedelta(seconds=10)),
        contributions=(mandatory, *contributions((unit(),))), max_input_tokens=20000, reserved_output_tokens=100)
    assert [item.kind for item in pack.materials] == ["input"]
    assert pack.source_refs == mandatory.source_refs and "contribution_unavailable:memory_evidence" in pack.missing
    assert not any("other-person" in (part.text or "") for message in pack.messages for part in message.parts)
