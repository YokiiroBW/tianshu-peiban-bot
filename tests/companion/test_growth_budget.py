"""Whole experience selection shrinks before model admission, preserving cursor order."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from tianshu.companion.growth_materials import material, reflectable, select_batch
from tianshu.companion.growth_observations import episode_material, material_coverages
from tianshu.companion.reflection_output import frozen_episodes
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.memory.types import Episode, EpisodePage
from tianshu.models.types import ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


class Authority:
    async def check(self, *_args):
        pass

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)


@pytest.mark.asyncio
async def test_large_page_selects_complete_prefix_and_preserves_authored_persona():
    context = TrustedContext("p", "a", None, None, ("s",), ("s",), 1, OriginKind.ACTOR)
    first = Episode("e1", "event1", 1, "a", OriginKind.HUMAN, datetime.now(UTC),
        "明确的完整经历。" * 100, (SourceRef("s1", 1),), ("s",), None, Coverage(True), 1, "e:1")
    items = tuple(replace(first, episode_id=f"e{i}", sequence=i, cursor=f"e:{i}") for i in range(1, 33))
    page = EpisodePage(items, "e:32", Coverage(False), "e:32")
    selected, pack, cursor, more = await select_batch(ContextAssembler(Authority(), None, None), context, page,
        "保留的固定人格", RequestBudget(datetime.now(UTC) + timedelta(minutes=3)), 1)
    assert 0 < len(selected) < 32
    assert selected == items[:len(selected)]
    assert cursor == selected[-1].cursor
    assert more
    assert "保留的固定人格" in pack.system
    assert pack.token_usage.estimated_tokens + pack.token_usage.reserved_output_tokens <= pack.token_usage.limit_tokens


@pytest.mark.asyncio
async def test_single_oversize_or_source_less_item_is_explicitly_blocked():
    context = TrustedContext("p", "a", None, None, ("s",), ("s",), 1, OriginKind.ACTOR)
    item = Episode("huge", "huge", 2, "a", OriginKind.ACTOR, datetime.now(UTC),
        "超长材料" * 24000, (SourceRef("s1", 1),), ("s",), None, Coverage(True), 4, "e:4")
    for episode, code in ((item, "reflection_material_budget_exceeded"),
                          (replace(item, source_refs=()), "reflection_material_source_required")):
        with pytest.raises(AppError) as rejected:
            await select_batch(ContextAssembler(Authority(), None, None), context,
                EpisodePage((episode,), None, Coverage(True), "e:4"), "人格保持",
                RequestBudget(datetime.now(UTC) + timedelta(minutes=3)), 1)
        assert rejected.value.code == code
        assert rejected.value.details["episode_id"] == "huge"
        assert rejected.value.details["revision"] == 2


def action_episode(actual):
    return Episode("action:discover", "action:discover", 2, "a", OriginKind.TOOL_RESULT, datetime.now(UTC),
        "查找感兴趣的资料", (SourceRef("receipt-original", 1),), ("s",), actual,
        Coverage(True), 4, "e:4")


@pytest.mark.asyncio
async def test_one_huge_discovery_becomes_a_source_backed_observation_and_tail_can_fit():
    candidates = [{"candidate_key": str(index), "summary": "UNREAD_CANDIDATE_BODY" * 4000} for index in range(10)]
    actual = {"capability_id": "content.discover", "receipt_id": "original-receipt", "execution": "succeeded",
        "effect": "none", "delivery": "not_requested", "read_basis": "discovery_metadata", "experience_eligible": False,
        "candidates": candidates, "next_actions": [{"capability_id": "content.read", "arguments": {"raw": candidates}}],
        "continuation": {"arguments": {"raw": candidates}}, "discovery_continuation": {"arguments": {"raw": candidates}},
        "sources": [{"state": "ok"}], "exploration": {"query": "感兴趣的资料", "intention": "探索", "stop_reason": "checkpoint"}}
    episode = replace(action_episode(actual), source_refs=tuple(SourceRef(f"original-{i}", 1) for i in range(8)),
        coverage=Coverage(False, missing=("coverage_unknown",)))
    tail = replace(episode, episode_id="tail", actual_result={"execution": "succeeded"}, sequence=5, cursor="e:5")
    context = TrustedContext("p", "a", None, None, ("s",), ("s",), 1, OriginKind.ACTOR)
    assembler = ContextAssembler(Authority(), None, None, settings={"tokenizer": "o200k_base"})
    selected, pack, cursor, more = await select_batch(assembler, context,
        EpisodePage((episode, tail), None, Coverage(True), "e:5"), "始终保留的作者人格",
        RequestBudget(datetime.now(UTC) + timedelta(minutes=3)), 1)
    assert selected == (episode, tail) and cursor == "e:5" and not more
    assert "始终保留的作者人格" in pack.system
    assert "UNREAD_CANDIDATE_BODY" not in json.dumps(wire(pack.messages))
    assert episode.actual_result is actual and actual["candidates"] is candidates
    row = material(selected)["episodes"][0]
    assert row["source_refs"] == wire(episode.source_refs)
    assert row["actual_result"]["observation"] == {"kind": "discovery", "candidate_count": 10,
        "next_action_count": 1, "continuation_available": True, "body_read": False, "source_states": {"ok": 1},
        "query": "感兴趣的资料", "intention": "探索", "stop_reason": "checkpoint"}
    assert not row["actual_result"]["experience_eligible"]
    assert row["reflection_coverage"]["missing"] == ["action_source_coverage_partial", "coverage_unknown", "discovery_metadata_only_body_not_read"]
    assert material_coverages(selected)[episode.episode_id] == row["reflection_coverage"]
    request = ModelRequest("reflection", "attempt", "reflect", ServiceClass.ELASTIC, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=3)), tuple(pack.messages),
        system=pack.system, source_refs=tuple(pack.source_refs))
    frozen = frozen_episodes(request)
    assert set(frozen) == {episode.episode_id, tail.episode_id}
    for item in selected:
        assert frozen[item.episode_id].revision == item.revision
        assert frozen[item.episode_id].source_refs == item.source_refs


def test_action_body_is_a_whole_unit_or_explicitly_unassessed_and_original_is_unchanged():
    small = action_episode({"capability_id": "content.read", "execution": "succeeded", "read_basis": "actual_source_body",
        "experience_eligible": True, "content": {"text": "完整正文及其条件。", "posts": []}})
    row = episode_material(small)
    assert row["actual_result"]["content"] == small.actual_result["content"]
    assert row["reflection_coverage"]["complete"]
    large = replace(small, actual_result={**small.actual_result, "content": {"text": "WHOLE_UNASSESSED_BODY" * 8000}})
    partial = episode_material(large)
    assert "content" not in partial["actual_result"]
    assert not partial["actual_result"]["content_evidence_included"]
    assert partial["reflection_coverage"]["missing"] == ["action_content_not_assessed"]
    assert partial["reflection_coverage"]["whole_units"]
    assert partial["reflection_coverage"]["original"]["revision"] == large.revision
    assert "WHOLE_UNASSESSED_BODY" not in json.dumps(partial)
    assert len(large.actual_result["content"]["text"]) > 100000


def test_failed_read_keeps_receipt_error_and_unread_coverage_without_body_evidence():
    episode = replace(action_episode({"capability_id": "content.read", "execution": "cancelled", "effect": "none",
        "error_code": "capability_timeout"}), coverage=Coverage(False, missing=("candidate_body_not_read",)))
    row = episode_material(episode)
    assert row["actual_result"]["error_code"] == "capability_timeout"
    assert row["actual_result"]["execution"] == "cancelled"
    assert not row["actual_result"]["content_evidence_included"]
    assert row["reflection_coverage"]["missing"] == ["action_source_coverage_partial", "candidate_body_not_read"]


@pytest.mark.parametrize("origin", [OriginKind.HUMAN, OriginKind.ACTOR])
def test_human_and_imagined_evidence_is_not_projected_or_truncated(origin):
    episode = replace(action_episode({"experience_kind": "imagined", "text": "完整想象" * 4000}), origin_kind=origin)
    assert episode_material(episode) == wire(episode)


@pytest.mark.parametrize("kind", ["reflection", "diary"])
def test_derived_writing_never_becomes_new_growth_evidence(kind):
    assert not reflectable(action_episode({"derived_kind": kind, "content": "旧的主观材料"}))
