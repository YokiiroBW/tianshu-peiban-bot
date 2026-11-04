"""The final request retains whole current/history units and exact dependencies."""
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.conversation.context.assembly import ContextAssembler, ContextMessage
from tianshu.models.types import ModelMessage, ModelPart
from tianshu.runtime.errors import AppError
from tianshu.types import ContextContribution, OriginKind, RequestBudget, SourceRef, TrustedContext


class Authority:
    def __init__(self, revoked=()):
        self.revoked = set(revoked)
        self.checked = []

    async def check(self, *_args):
        pass

    async def check_many(self, _context, groups):
        self.checked.extend(groups)
        return tuple(not any(ref.source_id in self.revoked for ref in group) for group in groups)


def context():
    return TrustedContext("p", "a", "person", "chat", ("scope",), ("scope",), 1,
                          OriginKind.HUMAN, conversation_kind="private", delivery_allowed=True)


def budget():
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=20))


def message(text, source, *, required=False):
    return ContextMessage(ModelMessage("user", (ModelPart("text", text=text),)),
                          (SourceRef(source, 1),), {"event_id": source, "revision": 1}, required)


@pytest.mark.asyncio
async def test_referenced_exchange_precedes_optional_memory_and_manifest_records_omissions():
    auth = Authority()
    assembler = ContextAssembler(auth, None, None)
    units = (message("方案一或方案二", "previous", required=True),
             message("刚才第二种", "current", required=True))
    required = sum(assembler.counter.message(item.message)[0] for item in units)
    from tianshu.conversation.context.assembly import AUTHORITY
    ceiling = assembler.counter.measure(AUTHORITY + "\n\n固定人格：\n完整人格", (), ())[0] + required + 20
    material = ContextContribution("memory_evidence", "x" * 10000,
                                   (SourceRef("optional", 1),), ("scope",), content_revision="claim:long:1")
    pack = await assembler.assemble(context(), "extract", "刚才第二种", budget(), persona_text="完整人格",
        recent_units=units, query_is_present=True, contributions=(material,),
        max_input_tokens=ceiling + 100, reserved_output_tokens=100)
    assert [part.text for item in pack.messages for part in item.parts] == ["方案一或方案二", "刚才第二种"]
    assert {ref.source_id for ref in pack.source_refs} == {"previous", "current"}
    omitted = next(item for item in pack.selection if item["kind"] == "memory_evidence")
    assert omitted["selected"] is False and omitted["reason"] == "budget"
    assert "完整人格" in pack.system and pack.token_usage.used_tokens is None


@pytest.mark.asyncio
async def test_different_same_kind_records_remain_distinct_and_final_revoke_drops_only_that_unit():
    auth = Authority(revoked={"revoked"})
    assembler = ContextAssembler(auth, None, None)
    materials = (ContextContribution("learned_rule", "first", (SourceRef("good", 1),),
                     ("scope",), content_revision="2", material_id="rule-a"),
                 ContextContribution("learned_rule", "second", (SourceRef("good2", 1),),
                     ("scope",), content_revision="4", material_id="rule-b"),
                 ContextContribution("learned_rule", "removed", (SourceRef("revoked", 1),),
                     ("scope",), content_revision="7", material_id="rule-c"))
    pack = await assembler.assemble(context(), "extract", "hi", budget(), contributions=materials)
    assert pack.content_revisions == {"rule-a": "2", "rule-b": "4"}
    assert [item["material_id"] for item in pack.selection if item.get("selected") and item.get("material_id")] == ["rule-a", "rule-b"]
    assert next(item for item in pack.selection if item.get("material_id") == "rule-c")["reason"] == "source_unavailable"


@pytest.mark.asyncio
async def test_multiple_semantic_versions_of_one_identity_do_not_overwrite_manifest():
    assembler = ContextAssembler(Authority(), None, None)
    items = (ContextContribution("historical_evidence", "old", content_revision="1", material_id="fact-a"),
             ContextContribution("historical_evidence", "new", content_revision="2", material_id="fact-a"))
    pack = await assembler.assemble(context(), "extract", "history", budget(), contributions=items)
    assert pack.content_revisions == {"fact-a@1": "1", "fact-a@2": "2"}
    assert [item["content_revision"] for item in pack.selection if item.get("material_id") == "fact-a" and item["selected"]] == ["1", "2"]


@pytest.mark.asyncio
async def test_unselected_revoked_history_does_not_become_generation_dependency():
    auth = Authority(revoked={"old"})
    assembler = ContextAssembler(auth, None, None)
    old = message("很久以前" * 5000, "old")
    current = message("当前输入", "current", required=True)
    pack = await assembler.assemble(context(), "extract", "当前输入", budget(),
        recent_units=(old, current), query_is_present=True, max_input_tokens=4000, reserved_output_tokens=100)
    assert [ref.source_id for ref in pack.source_refs] == ["current"]
    assert all(not any(ref.source_id == "old" for ref in group) for group in auth.checked)
    assert next(item for item in pack.selection if item.get("records") and item["records"][0].get("event_id") == "old")["reason"] == "budget"


@pytest.mark.asyncio
async def test_mandatory_dependency_cannot_be_truncated_when_budget_is_too_small():
    assembler = ContextAssembler(Authority(), None, None)
    with pytest.raises(AppError, match="context_mandatory_budget_exceeded"):
        await assembler.assemble(context(), "extract", "", budget(),
            recent_units=(message("被引用的完整内容" * 1000, "quoted", required=True),),
            max_input_tokens=4000, reserved_output_tokens=100)


@pytest.mark.asyncio
async def test_revalidation_cannot_discard_the_rerank_derivation_closure(monkeypatch):
    from tianshu.conversation.context import assembly
    original, ranked = SourceRef("selected", 1), SourceRef("ranked-candidate", 1)
    ranked_item = ContextContribution("memory_evidence", "verified fact", (original, ranked), ("scope",),
                                      content_revision="claim:one:1")
    refreshed = ContextContribution("memory_evidence", "verified fact", (original,), ("scope",),
                                    content_revision="claim:one:1")
    class Retrieval:
        def foreground_budget(self, parent, **_kwargs):
            return parent
        async def search(self, *_args, **_kwargs):
            return (ranked_item,), ()
        async def revalidate(self, *_args):
            return (refreshed,), ()
    monkeypatch.setattr(assembly, "evidence_contributions", lambda values: tuple(values))
    pack = await ContextAssembler(Authority(), None, Retrieval()).assemble(context(), "chat", "hello", budget())
    assert {ref.source_id for ref in pack.source_refs} == {"selected", "ranked-candidate"}
