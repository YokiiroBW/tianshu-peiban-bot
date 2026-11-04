from datetime import UTC, datetime, timedelta
from dataclasses import replace
from importlib.resources import files

import pytest

from tianshu.conversation.codec import document, signature
from tianshu.life import worker
from tianshu.life.attention import AttentionPolicy
from tianshu.life.continuation import automatic_step, discovery_progress, execution_window, failed_read_checkpoint, remaining_actions
from tianshu.life.eligibility import background_work
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, Receipt
from test_planner_execution import setup


NOW = datetime(2026, 11, 5, 16, tzinfo=UTC)
STEP = {"capability_id": "content.read", "arguments": {"url": "https://example.test/page/13"}}


def activity():
    checkpoint = execution_window({"capability_id": "content.discover", "completed_action_id": "original",
        "last_execution": "succeeded", "continuation": STEP}, NOW - timedelta(hours=1), NOW - timedelta(minutes=1))
    checkpoint["execution_window"]["steps"] = 12
    checkpoint["execution_window"]["seen_steps"] = [f"previous-{number}" for number in range(12)]
    return {"id": "a", "state": "dormant", "waiting_reason": "continuation_available",
        "resume_condition": "continue_from_checkpoint", "action_id": "original", "checkpoint_data": checkpoint}


def test_background_read_continues_after_twelve_steps_and_across_rest():
    row = activity()
    step, reason = automatic_step(row, NOW)
    assert reason is None and step == STEP and background_work(row["checkpoint_data"])


def test_repeated_step_and_true_deadline_stop_without_reissuing_action():
    row = activity()
    row["checkpoint_data"]["execution_window"]["seen_steps"].append(signature(STEP))
    assert automatic_step(row, NOW) == (None, "continuation_repeated_step")
    row = activity()
    row["checkpoint_data"]["business_deadline_at"] = NOW.isoformat()
    assert automatic_step(row, NOW) == (None, "activity_deadline_expired")


def test_rearrangement_keeps_seen_signatures():
    row = activity()
    next_window = execution_window(row["checkpoint_data"], NOW, NOW + timedelta(hours=1))
    assert next_window["execution_window"]["seen_steps"] == row["checkpoint_data"]["execution_window"]["seen_steps"]


def test_completed_content_and_duplicate_discovery_candidates_are_not_read_again():
    def candidate(key, url):
        return {"content_key": key, "capability_id": "content.read", "arguments": {"url": url, "content_key": key}}
    pending = {"completed_content_keys": ["finished"], "next_actions": [candidate("finished", "https://example.test/alternate"),
        candidate("new", "https://example.test/new"), candidate("new", "https://example.test/mirror")]}
    assert [item["content_key"] for item in remaining_actions(pending)] == ["new"]


def test_changing_cursor_with_same_empty_discovery_stops_without_losing_cursor():
    next_step = {"capability_id": "content.discover", "arguments": {"cursor": "next"}}
    first = discovery_progress({}, {"seen_keys": [], "discovery_continuation": next_step}, {"candidates": []})
    assert not first["no_progress_stop"]
    second = discovery_progress(first, {**first, "discovery_continuation": next_step}, {"candidates": []})
    assert second["no_progress_stop"] and second["discovery_continuation"] == next_step
    advanced = discovery_progress(second, dict(second), {"candidates": [{"content_key": "fresh"}]})
    assert not advanced.get("no_progress_stop")


def test_failed_final_candidate_keeps_the_discovery_page_continuation():
    next_page = {"capability_id": "content.discover", "arguments": {"cursor": "page-2"}}
    row = {"action_id": "failed", "checkpoint_data": execution_window({"capability_id": "content.read",
        "arguments": {"content_key": "bad", "url": "https://example.test/bad"},
        "exploration": {"next_actions": [], "discovery_continuation": next_page}}, NOW, NOW + timedelta(hours=1))}
    failure = Receipt("receipt", "failed", "attempt", "failed", "none", "not_requested", coverage=Coverage(False), error_code="read_failed")
    checkpoint = failed_read_checkpoint(row, failure, 1, "content.read")
    assert checkpoint["continuation"] == next_page
    assert checkpoint["exploration"]["failed_reads"][0]["content_key"] == "bad"


class FixedTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


async def background_fixture(database, monkeypatch):
    monkeypatch.setattr(worker, "datetime", FixedTime)
    value = await setup(database, "{}")
    await migrate(database, (Migration("0040_life_execution", files("tianshu.life").joinpath("rb_life_execution.sql").read_text()),))
    checkpoint = execution_window({"capability_id": "content.read", "arguments": {"url": "https://example.test/page"},
        "duration_minutes": 10, "autonomous": True}, NOW - timedelta(hours=1), NOW - timedelta(minutes=1))
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.routines SET wake_at='18:00',sleep_at='23:00' WHERE actor_id='actor'")
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,next_due_at,scope_ids) "
            "VALUES ('focus','actor','rest','rest','running',%s,ARRAY['actor-scope'])", (NOW + timedelta(hours=1),))
        await connection.execute("UPDATE life.actor_state SET activity_id='focus' WHERE actor_id='actor'")
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,next_due_at,protected_until,checkpoint_data,scope_ids,context) "
            "VALUES ('read','actor','read','reading','planned',%s,%s,%s,ARRAY['actor-scope'],%s)",
            (NOW - timedelta(minutes=1), NOW + timedelta(hours=1), document(checkpoint), document(value.context)))
    return value


@pytest.mark.asyncio
async def test_sleep_and_protected_focus_do_not_block_one_background_step(database, monkeypatch):
    value = await background_fixture(database, monkeypatch)
    await value.worker.activity_step("read", 1, wire(value.context))
    await value.worker.activity_step("read", 1, wire(value.context))
    async with database.transaction(read_only=True) as connection:
        state = await (await connection.execute("SELECT activity_id FROM life.actor_state WHERE actor_id='actor'")).fetchone()
        row = await (await connection.execute("SELECT * FROM life.activities WHERE id='read'")).fetchone()
    assert len(value.actions.calls) == 1 and value.actions.calls[0][0].deadline_at is None
    assert row["waiting_reason"] == "capability_running" and state["activity_id"] == "focus"


@pytest.mark.asyncio
async def test_selected_capability_schema_is_rechecked_before_dispatch(database, monkeypatch):
    value = await background_fixture(database, monkeypatch)
    handler = value.worker.catalog.handler("content.read")
    handler.definition = replace(handler.definition, input_schema={"type": "object", "required": ["new_config"]})
    await value.worker.activity_step("read", 1, wire(value.context))
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT * FROM life.activities WHERE id='read'")).fetchone()
    assert row["waiting_reason"] == "capability_configuration_changed" and not value.actions.calls


def test_unsolicited_contact_waits_for_actor_wake_without_extending_expiry():
    candidate = {"kind": "experience", "explicit": False, "delivery_kind": "share_candidate", "target_ref": "chat",
        "created_at": NOW, "due_at": NOW, "expires_at": NOW + timedelta(hours=4),
        "payload": {"salience": {"score": 1, "thresholds": {"engage": 0}}}}
    preferences = {"expiry_seconds": 14400, "enabled": True, "timezone": "UTC", "quiet_start": "00:00", "quiet_end": "00:00",
        "daily_quota": 4, "unanswered_limit": 2, "cooldown_seconds": 0}
    routine = {"enabled": True, "timezone": "UTC", "wake_at": "18:00", "sleep_at": "23:00", "idle_seconds": 1800}
    decision = AttentionPolicy().decide(candidate, preferences, [], now=NOW, routine=routine)
    assert decision.decision == "defer" and decision.due_at == NOW + timedelta(hours=2)
    short = {**candidate, "expires_at": NOW + timedelta(hours=1)}
    assert AttentionPolicy().decide(short, preferences, [], now=NOW, routine=routine).decision == "cancel"
