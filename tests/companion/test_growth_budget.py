"""Whole experience selection shrinks before model admission, preserving cursor order."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.companion.growth_materials import select_batch
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.memory.types import Episode, EpisodePage
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage, OriginKind, RequestBudget, SourceRef, TrustedContext


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
