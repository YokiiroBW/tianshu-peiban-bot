from datetime import UTC, datetime, timedelta
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.life import planner
from tianshu.life.contact_planning import prepare as prepare_contact
from tianshu.life.clock import days_to_close
from tianshu.life.eligibility import evaluate
from tianshu.runtime.api import Page
from tianshu.types import Coverage
from test_planner_contract import choice, context, make_request, result


@pytest.mark.asyncio
async def test_complete_response_applies_valid_fragment_and_reports_bad_fragment():
    request = await make_request([choice("content.read")])
    valid = {"title": "read", "kind": "reading", "goal_id": None, "capability_id": "content.read",
        "arguments": {"url": "https://example.test/one"}, "duration_minutes": 2}
    bad = {**valid, "arguments": {}}
    decision, _ = planner.decision(result(json.dumps({"activity": valid, "arrangements": [bad],
        "goal_changes": [], "cancel_activity_ids": [], "reason": "continue", "free_time_reason": "rest"})), request)
    assert decision["activity"]["duration_minutes"] == 2
    assert decision["arrangements"] == []
    assert decision["_invalid_candidates"][0]["reason"] == "planner_arguments_invalid"


@pytest.mark.asyncio
async def test_invalid_goal_and_contact_leave_independent_activity_available():
    request = await make_request([choice("content.read")])
    good = {"title": "independent", "kind": "reading", "goal_id": None, "capability_id": "content.read",
        "arguments": {"url": "https://example.test/one"}, "duration_minutes": 2}
    dependent = {**good, "title": "depends on invalid goal", "goal_id": "new:g"}
    change = {"operation": "create", "key": "g", "id": None, "title": "goal", "motivation": "motivation",
        "priority": .5, "criteria": {"kind": "completed_activity", "capability_id": "content.read", "target_count": 1},
        "basis_ids": ["invented"], "reason": "unsourced"}
    decision, _ = planner.decision(result(json.dumps({"activity": dependent, "arrangements": [good],
        "goal_changes": [change], "cancel_activity_ids": [], "contact": {"summary": "incomplete"},
        "reason": "continue", "free_time_reason": "rest"})), request)
    assert decision["activity"]["title"] == "independent"
    assert decision["goal_changes"] == [] and decision["contact"] is None
    assert {item["candidate_key"] for item in decision["_invalid_candidates"]} == {"goal:new:g", "proposal:0", "contact"}


def test_continuation_uses_receipt_next_capability_and_completed_goal_is_ineligible():
    row = {"id": "a", "revision": 4, "state": "dormant", "waiting_reason": "continuation_available",
        "resume_condition": "continue_from_checkpoint", "action_id": "discover", "goal_id": "goal",
        "protected_until": None, "checkpoint_data": {"capability_id": "content.discover",
            "completed_action_id": "discover", "last_execution": "succeeded",
            "continuation": {"capability_id": "content.read", "arguments": {"url": "https://example.test/one"}},
            "exploration": {"next_actions": [{"candidate_key": "one", "capability_id": "content.read",
                "arguments": {"url": "https://example.test/one"}}]}}}
    available = {"content.read"}
    ok = evaluate(goal={"state": "active", "revision": 2}, activity=row, expected_revision=4,
        continuation_key="one", available=available, resume=True)
    assert ok.eligible and ok.step["capability_id"] == "content.read"
    assert evaluate(goal={"state": "completed"}, activity=row, resume=True).reason == "goal_not_active"
    assert evaluate(activity=row, expected_revision=3, resume=True).reason == "activity_revision_changed"
    assert evaluate(activity=row, continuation_key="one", available=set(), resume=True).reason == "capability_unavailable"
    rendered = planner.unfinished_candidate(row, [{"id": "goal", "state": "active"}], available)
    assert rendered["continuation_options"][0]["candidate_key"] == "activity:a:one"
    assert rendered["continuation_options"][0]["eligible"] is True


def test_night_start_and_crossed_local_days_close_without_per_tick_reclosure():
    row = {"timezone": "Asia/Shanghai", "wake_at": "08:00", "sleep_at": "23:00", "idle_seconds": 1800,
        "last_tick_at": None}
    late = datetime.fromisoformat("2026-11-02T23:30:00+08:00")
    assert [str(day) for day in days_to_close(row, late)] == ["2026-11-02"]
    early = datetime.fromisoformat("2026-11-03T01:30:00+08:00")
    assert [str(day) for day in days_to_close(row, early)] == ["2026-11-02"]
    row["last_tick_at"] = late
    assert not days_to_close(row, late + timedelta(minutes=10))
    later = datetime.fromisoformat("2026-11-05T00:30:00+08:00")
    assert [str(day) for day in days_to_close(row, later)] == ["2026-11-02", "2026-11-03", "2026-11-04"]


def test_completed_goal_is_not_offered_as_a_continuation_candidate():
    row = {"id": "a", "revision": 1, "state": "dormant", "goal_id": "done", "waiting_reason": "day_finished",
        "checkpoint_data": {"capability_id": "content.read", "arguments": {"url": "https://example.test"}},
        "action_id": None}
    material = planner.material(context(), {"affect": {"regulation": {}}, "outfit": None}, None,
        Page(({"id": "done", "state": "completed"},), None, Coverage(True)), Page((), None, Coverage(True)),
        [choice("content.read")], datetime.now(UTC).isoformat(), day={"unfinished": [row]})
    assert material["unfinished"] == []


@pytest.mark.asyncio
async def test_same_evidence_and_target_keep_contact_identity_across_replanning():
    scene = replace(context(), conversation_id="chat")
    worker = SimpleNamespace(life=SimpleNamespace(contacts=SimpleNamespace(resolve_target=AsyncMock(return_value=scene)),
        identity=None), prepare_contact=AsyncMock(side_effect=lambda candidate: candidate))
    data = {"basis": [{"id": "result:action-1", "source_refs": []}]}
    proposal = {"summary": "one wording", "reason": "share", "basis_ids": ["result:action-1"],
        "after_minutes": 1, "expires_minutes": 60}
    first = await prepare_contact(worker, scene, proposal, data, datetime.now(UTC))
    second = await prepare_contact(worker, scene, {**proposal, "summary": "different wording"}, data,
        datetime.now(UTC) + timedelta(minutes=5))
    assert first.candidate_id == second.candidate_id
