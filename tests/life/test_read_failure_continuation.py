"""A failed candidate is missing coverage, not a retry or an unresolved action."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document, signature
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.conversation.types import InputPart
from tianshu.life.continuation import action_resolved, execution_window, options, resume_step
from tianshu.life.day_plan import resumable
from tianshu.life.planner import INPUT_BODY_TOKENS
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, Receipt, SourceRef
from test_activity_continuation import NOW, READ, activity, fixture as make_life, fixed_time, future_slot
from test_goal_source_transaction import source_life
from test_planner_execution import mark


FAILED = {"capability_id": "content.read", "arguments": {
    "url": "https://example.test/timed-out", "content_key": "url:failed", "mode": "research"}}
NEXT = {"capability_id": "content.read", "arguments": {
    "url": "https://example.test/remaining", "content_key": "url:remaining", "mode": "research"}}


def candidate(step, *, key=None):
    return {**deepcopy(step), "content_key": key or step["arguments"]["content_key"]}


async def successful_prefix(database):
    value = await make_life(database)
    await value.worker.activity_step("research", 1, wire(value.context))
    discovery = value.actions.calls[-1][0]
    alias = {**deepcopy(FAILED), "arguments": {**FAILED["arguments"], "content_key": "url:failed-alias"}}
    await value.worker._activity_result(value.context,
        Receipt("discovered", discovery.action_id, "discover-attempt", "succeeded", "none", "not_requested",
            result={"read_basis": "discovery_metadata", "experience_eligible": False,
                "next_actions": [candidate(READ), candidate(FAILED), candidate(alias), candidate(NEXT)],
                "continuation": deepcopy(READ)}), 1, NOW, "content.discover")
    current = await activity(database)
    await value.worker.activity_step("research", current["revision"], wire(value.context))
    read = value.actions.calls[-1][0]
    actual = SourceRef("actually-read", 1)
    await value.worker._activity_result(value.context,
        Receipt("first-body", read.action_id, "read-attempt", "succeeded", "none", "not_requested",
            coverage=Coverage(True), result={"read_basis": "actual_source_body", "content_key": READ["arguments"]["content_key"],
                "source": wire(actual), "source_refs": [wire(actual)], "content": {"text": "Actually fetched source body."}}),
        1, NOW, "content.read")
    current = await activity(database)
    await value.worker.activity_step("research", current["revision"], wire(value.context))
    failed_action = value.actions.calls[-1][0]
    assert dict(failed_action.arguments) == FAILED["arguments"]
    failure = Receipt("timeout-receipt", failed_action.action_id, "failed-attempt", "cancelled", "none", "not_requested",
        result={"source_refs": []}, error_code="capability_timeout")
    value.worker.life.directions.observe_result = AsyncMock()
    return value, read, failed_action, failure


@pytest.mark.asyncio
async def test_confirmed_timeout_skips_only_the_unread_candidate_and_retains_success_and_missing(database):
    value, successful, failed, failure = await successful_prefix(database)
    original = deepcopy(wire(failure))
    await value.worker._activity_result(value.context, failure, 1, NOW, "content.read")
    waiting = await activity(database)
    data = waiting["checkpoint_data"]
    assert waiting["state"] == "dormant" and waiting["waiting_reason"] == "continuation_available"
    assert data["completed_action_id"] == successful.action_id and data["resolved_action_id"] == failed.action_id
    assert data["last_execution"] == "cancelled" and action_resolved(waiting) and resumable(waiting, NOW)
    assert data["continuation"] == NEXT and data["execution_window"]["steps"] == 3
    assert data["coverage"]["complete"] is False and data["exploration"]["missing"] == ["candidate_body_unread:url:failed"]
    assert [item["content_key"] for item in data["exploration"]["next_actions"]] == ["url:remaining"]
    assert resume_step(waiting) == NEXT
    with pytest.raises(AppError, match="activity_continuation_invalid"):
        resume_step(waiting, "url:failed-alias")
    episode = value.worker.life.memory.observe_episode.call_args.args[1]
    assert episode["event_id"] == "action:"+failed.action_id and episode["actual_result"]["execution"] == "cancelled"
    assert episode["actual_result"]["error_code"] == "capability_timeout" and not episode["coverage"].complete
    assert episode["coverage"].missing == ("candidate_body_unread:url:failed",)
    assert "read_basis" not in episode["actual_result"] and "content" not in episode["actual_result"]
    value.worker.life.directions.observe_result.assert_not_awaited()
    await value.worker._activity_result(value.context, failure, 1, NOW, "content.read")
    await value.worker.activity_step("research", waiting["revision"], wire(value.context))
    await value.worker.activity_step("research", waiting["revision"], wire(value.context))
    assert len(value.actions.calls) == 4 and dict(value.actions.calls[-1][0].arguments) == NEXT["arguments"]
    assert sum(action.arguments.get("url") == FAILED["arguments"]["url"] for action, _ in value.actions.calls) == 1
    assert wire(failure) == original and not value.models.calls


@pytest.mark.asyncio
async def test_explicit_pause_and_resume_recognize_the_resolved_failure_without_retrying_its_url(database):
    value, _, _, failure = await successful_prefix(database)
    await value.worker._activity_result(value.context, failure, 1, NOW, "content.read")
    waiting = await activity(database)
    await value.worker.life.transition_activity(value.context, {"id": "research", "expected_revision": waiting["revision"],
        "client_request_id": "pause-after-failed-read"}, "paused")
    paused = await activity(database)
    await value.worker.life.transition_activity(value.context, {"id": "research", "expected_revision": paused["revision"],
        "client_request_id": "resume-remaining-after-failed-read"}, "running")
    resumed = await activity(database)
    assert resumed["action_id"] is None and resumed["checkpoint_data"]["arguments"] == NEXT["arguments"]
    await value.worker.activity_step("research", resumed["revision"], wire(value.context))
    assert len(value.actions.calls) == 4 and dict(value.actions.calls[-1][0].arguments) == NEXT["arguments"]


@pytest.mark.asyncio
async def test_upgrade_reconciles_an_already_stored_failure_without_rewriting_its_episode_or_receipt(database):
    value, successful, failed, failure = await successful_prefix(database)
    before = await activity(database)
    original_data = deepcopy(before["checkpoint_data"])
    # Reproduce the deployed pre-fix terminal state: original failed receipt and
    # Episode already exist, but the activity still treats this action as pending.
    await value.worker.life.memory.observe_episode(value.context, {"event_id": "action:"+failed.action_id,
        "revision": 1, "actual_result": {"execution": "cancelled", "effect": "none"}}, connection=None)
    historical = value.worker.life.memory.observe_episode.call_args
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_timeout',resume_condition='review_failure',"
            "last_result_revision=1,last_receipt_id=%s,source_refs=%s WHERE id='research'",
            (failure.receipt_id, document((SourceRef("original-failure-receipt", 1),))))
        await connection.execute("UPDATE life.day_slots SET state='failed' WHERE activity_id='research'")
    original_get = value.actions.get
    value.actions.get = AsyncMock(return_value={"action": wire(failed), "receipt": wire(failure),
        "result_revision": 1, "result_observed_at": NOW})
    await value.worker.recover()
    await value.worker.reconcile_activity("research", wire(value.context), 0)
    repaired = await activity(database)
    assert repaired["checkpoint_data"]["completed_action_id"] == successful.action_id
    assert repaired["checkpoint_data"]["resolved_action_id"] == failed.action_id
    assert repaired["checkpoint_data"]["execution_window"] == original_data["execution_window"]
    assert repaired["last_result_revision"] == 1 and repaired["last_receipt_id"] == failure.receipt_id
    assert len(value.actions.calls) == 3  # Only observation, no external replay.
    control = value.worker.life.memory.observe_episode.call_args.args[1]
    assert control["event_id"].startswith("activity-read-skip:"+failed.action_id)
    assert control["actual_result"]["derived_kind"] == "activity_read_skip"
    assert control["actual_result"]["read_basis"] == "unread_candidate" and not control["coverage"].complete
    assert SourceRef("original-failure-receipt", 1) in control["source_refs"]
    assert historical in value.worker.life.memory.observe_episode.call_args_list
    recorded = value.worker.life.memory.observe_episode.await_count
    await value.worker.reconcile_activity("research", wire(value.context), 1)
    assert value.worker.life.memory.observe_episode.await_count == recorded
    value.actions.get = original_get
    await value.worker.activity_step("research", repaired["revision"], wire(value.context))
    assert dict(value.actions.calls[-1][0].arguments) == NEXT["arguments"]


@pytest.mark.asyncio
async def test_old_failure_control_observation_has_real_sources_visible_on_its_owner_transaction(source_life):
    value = source_life
    result = Receipt("stored-failure", "failed-read", "attempt", "cancelled", "none", "not_requested",
        result={"source_refs": []}, error_code="capability_timeout")
    original = await value.sources.record_event(value.context, producer="life:action", event_id="result:failed-read",
        revision=1, occurred_at=NOW, origin_kind=OriginKind.TOOL_RESULT,
        parts=(InputPart("text", json.dumps(wire(result), ensure_ascii=False)),))
    data = execution_window({"capability_id": "content.read", "arguments": FAILED["arguments"],
        "completed_action_id": "previous-success", "last_execution": "succeeded",
        "exploration": {"next_actions": [candidate(NEXT)]}}, NOW, NOW+timedelta(minutes=30))
    data["execution_window"].update(steps=3, seen_steps=[signature(FAILED)])
    async with value.database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,resume_condition,"
            "action_id,last_result_revision,last_receipt_id,checkpoint_data,source_refs,scope_ids,context) "
            "VALUES ('old-failed-read',%s,'继续其他候选','reading','dormant','capability_timeout','review_failure',"
            "'failed-read',1,'stored-failure',%s,%s,%s,%s)",
            (value.context.actor_id, document(data), document(original), list(value.context.scope_ids), document(value.context)))
    value.life.jobs.register("life.activity_step", value.worker.activity_step)
    await value.worker._activity_result(value.context, result, 1, NOW, "content.read")
    assert len(value.episodes) == 1
    control, visible_elsewhere = value.episodes[0]
    assert visible_elsewhere is False and control["actual_result"]["derived_kind"] == "activity_read_skip"
    assert all(ref in control["source_refs"] for ref in original) and not control["coverage"].complete
    assert (await value.identity.check_many(value.context, (control["source_refs"],)))[0]


@pytest.mark.asyncio
async def test_expired_failure_replans_the_same_activity_at_full_quota_with_original_remaining_arguments(database):
    value, successful, failed, failure = await successful_prefix(database)
    # Match runtime.product's configured assembler for a full frozen checkpoint
    # and four retained arrangements; the shared minimal fixture counts bytes.
    value.worker.assembler = ContextAssembler(value.worker.life.identity, None, None,
        settings={"tokenizer": "o200k_base"})
    await future_slot(database, value)
    async with database.transaction() as connection:
        for number in (2, 3):
            identifier = "future-"+str(number)
            starts = NOW+timedelta(hours=number)
            await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,next_due_at,scope_ids,context) "
                "VALUES (%s,'actor','稍后休息','rest','planned',%s,ARRAY['actor-scope'],%s)",
                (identifier, starts, document(value.context)))
            await connection.execute("INSERT INTO life.day_slots(id,actor_id,day,kind,starts_at,ends_at,activity_id,reason,scope_ids) "
                "VALUES (%s,'actor',%s,'activity',%s,%s,%s,'fixture',ARRAY['actor-scope'])",
                (identifier+'-slot', NOW.date(), starts, starts+timedelta(minutes=20), identifier))
        await connection.execute("UPDATE life.activities SET checkpoint_data=jsonb_set(checkpoint_data,'{execution_window,ends_at}',%s) WHERE id='research'",
            (document((NOW-timedelta(seconds=1)).isoformat()),))
        await connection.execute("UPDATE life.day_slots SET starts_at=%s,ends_at=%s WHERE id='research-slot'",
            (NOW-timedelta(hours=1), NOW-timedelta(seconds=1)))
    await value.worker._activity_result(value.context, failure, 1, NOW, "content.read")
    stopped = await activity(database)
    assert stopped["checkpoint_data"]["continuation_stop_reason"] == "activity_window_ended"
    assert stopped["action_id"] == failed.action_id and stopped["next_due_at"] is None
    value.models.output = json.dumps({"goal_changes": [], "arrangements": [], "cancel_activity_ids": [],
        "free_time_reason": "继续保留其他休息安排。", "reason": "原失败候选未读，换读原列表里另一篇。",
        "activity": {"existing_activity_id": "research", "title": "读剩余的资料", "kind": "research", "goal_id": None,
            "capability_id": "content.read", "arguments": {}, "duration_minutes": 20}})
    await value.worker.plan("actor", (await mark(database))["revision"], wire(value.context))
    replanned = await activity(database)
    planning = await mark(database)
    diagnostic = {"planning": {key: planning[key] for key in ("reason", "last_error", "generation_consumed", "repair_count")},
        "activity": {key: replanned[key] for key in ("state", "waiting_reason", "resume_condition", "next_due_at", "protected_until")},
        "model_calls": len(value.models.calls), "model_observations": len(value.models.observations)}
    assert replanned["state"] == "planned" and replanned["checkpoint_data"]["arguments"] == NEXT["arguments"], diagnostic
    assert replanned["checkpoint_data"]["completed_action_id"] == successful.action_id
    assert replanned["checkpoint_data"]["execution_window"]["steps"] == 0
    await value.worker.activity_step("research", replanned["revision"], wire(value.context))
    assert len(value.models.calls) == 1 and dict(value.actions.calls[-1][0].arguments) == NEXT["arguments"]
    assert value.models.calls[0].estimated_input_tokens <= INPUT_BODY_TOKENS


@pytest.mark.asyncio
@pytest.mark.parametrize("state,execution,effect,delivery,capability_id", [
    ("dormant", "unknown", "none", "not_requested", "content.read"),
    ("dormant", "failed", "unknown", "not_requested", "content.read"),
    ("dormant", "failed", "none", "unknown", "content.read"),
    ("paused", "cancelled", "none", "not_requested", "content.read"),
    ("cancelled", "cancelled", "none", "not_requested", "content.read"),
    ("dormant", "failed", "none", "not_requested", "images.generate"),
])
async def test_uncertain_effects_user_control_and_nonreading_failures_never_take_the_skip_path(
    database, state, execution, effect, delivery, capability_id,
):
    value, _, failed, original = await successful_prefix(database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET state=%s WHERE id='research'", (state,))
    failure = replace(original, execution=execution, effect=effect, delivery=delivery)
    await value.worker._activity_result(value.context, failure, 1, NOW, capability_id)
    row = await activity(database)
    expected = state if state in {"paused", "cancelled"} else "dormant"
    assert row["state"] == expected
    assert row["checkpoint_data"].get("resolved_action_id") != failed.action_id and not action_resolved(row)
    assert len(value.actions.calls) == 3
    value.worker.life.directions.observe_result.assert_not_awaited()


def test_failed_urls_cannot_reenter_through_aliases_or_followup_options():
    from tianshu.life.continuation import failed_read_checkpoint, known_read_failure
    row = {"action_id": "failed", "checkpoint_data": {"capability_id": "content.read", "arguments": FAILED["arguments"],
        "exploration": {"next_actions": [candidate(FAILED), candidate(NEXT)], "current_item": {"follow_up": FAILED}}}}
    result = Receipt("receipt", "failed", "attempt", "failed", "none", "not_requested", error_code="content_fetch_failed")
    row["checkpoint_data"] = failed_read_checkpoint(row, result, 1, "content.read")
    assert [item["key"] for item in options(row["checkpoint_data"])] == ["url:remaining"]
    with pytest.raises(AppError, match="activity_continuation_invalid"):
        resume_step(row, "follow_up")
    assert not known_read_failure("content.read", replace(result, execution="cancelled", error_code=None))
