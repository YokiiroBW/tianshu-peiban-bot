"""Receipt-to-next-action stories on the native Life queue, without external calls."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.images.service import Images
from tianshu.conversation import generation
from tianshu.conversation.codec import document, signature
from tianshu.life import day_plan, planning_engine, service, worker
from tianshu.life.continuation import automatic_step, execution_window
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, Receipt, SourceRef
from test_planner_execution import ReadPage, mark, setup


NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)
READ = {"capability_id": "content.read", "arguments": {
    "url": "https://example.test/selected", "content_key": "url:selected", "mode": "research"}}
REST = json.dumps({"activity": None, "arrangements": [], "goal_changes": [], "cancel_activity_ids": [],
    "reason": "先留一些空闲。", "free_time_reason": "保留稍后的既定安排。"})


class FixedTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    # Original-request admission must see the same business clock as Life's
    # frozen sleep boundary, even when this historical scenario runs later.
    for module in (worker, planning_engine, day_plan, service, generation):
        monkeypatch.setattr(module, "datetime", FixedTime)


async def fixture(database, *, state="planned", old=False, execution_mode=None):
    value = await setup(database, REST)
    discover = ReadPage()
    discover.definition = replace(discover.definition, id="content.discover", input_schema={
        "type": "object", "properties": {"query": {"type": "string"}, "mode": {"type": "string"}},
        "required": ["query"], "additionalProperties": False})
    value.worker.catalog.register(discover)
    reader = value.worker.catalog.handler("content.read")
    reader.definition = replace(reader.definition, input_schema={
        "type": "object", "properties": {"url": {"type": "string"}, "content_key": {"type": "string"},
        "mode": {"type": "string"}}, "required": ["url"], "additionalProperties": False})
    value.worker.life.sources.record_event = AsyncMock(return_value=(SourceRef("receipt-original", 1),))
    value.worker.life.memory = SimpleNamespace(observe_episode=AsyncMock())
    data = {"capability_id": "content.discover", "arguments": {"query": "actual topic", "mode": "research"},
        "duration_minutes": 30, "autonomous": True}
    if execution_mode is not None:
        data["execution_mode"] = execution_mode
    if not old:
        data = execution_window(data, NOW, NOW+timedelta(minutes=30))
    if state == "dormant":
        data.update(completed_action_id="discovery", last_execution="succeeded", continuation=deepcopy(READ),
            exploration={"next_actions": [{**deepcopy(READ), "content_key": "url:selected"}]})
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,next_due_at,waiting_reason,resume_condition,"
            "checkpoint_data,action_id,scope_ids,context) VALUES ('research','actor','阅读实际资料','research',%s,%s,%s,%s,%s,%s,ARRAY['actor-scope'],%s)",
            (state, NOW, "continuation_available" if state == "dormant" else "day_plan_slot",
             "continue_from_checkpoint" if state == "dormant" else None, document(data),
             "discovery" if state == "dormant" else None, document(value.context)))
        await connection.execute("INSERT INTO life.day_plans(actor_id,day,timezone,reason,context,scope_ids,material_fingerprint) "
            "VALUES ('actor',%s,'UTC','fixture',%s,ARRAY['actor-scope'],'fixture')", (NOW.date(), document(value.context)))
        await connection.execute("INSERT INTO life.day_slots(id,actor_id,day,kind,state,starts_at,ends_at,activity_id,reason,scope_ids) "
            "VALUES ('research-slot','actor',%s,'activity',%s,%s,%s,'research','fixture',ARRAY['actor-scope'])",
            (NOW.date(), "partial" if state == "dormant" else "planned",
             NOW-timedelta(hours=1) if old else NOW, NOW-timedelta(minutes=30) if old else NOW+timedelta(minutes=30)))
    return value


def register_image_capability(value):
    # Match the live image input contract and its model-tool registration.
    handler = ReadPage()
    handler.definition = replace(Images.definition, model_tool={"name": "images_generate"}, long_running=True)
    value.worker.catalog.register(handler)
    return {"prompt": "a quiet landscape", "purpose": "creation", "delivery_kind": "create_only"}


async def activity(database):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM life.activities WHERE id='research'")).fetchone()


async def future_slot(database, value, *, starts=None):
    starts = starts or NOW+timedelta(hours=1)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,next_due_at,scope_ids,context) "
            "VALUES ('later','actor','休息','rest','planned',%s,ARRAY['actor-scope'],%s)", (starts, document(value.context)))
        await connection.execute("INSERT INTO life.day_slots(id,actor_id,day,kind,starts_at,ends_at,activity_id,reason,scope_ids) "
            "VALUES ('later-slot','actor',%s,'activity',%s,%s,'later','fixture',ARRAY['actor-scope'])",
            (NOW.date(), starts, starts+timedelta(minutes=30)))


@pytest.mark.asyncio
async def test_discovery_receipt_queues_exact_read_despite_a_future_slot_and_duplicate_jobs(database):
    value = await fixture(database, execution_mode="focus")
    await future_slot(database, value)
    await value.worker.activity_step("research", 1, wire(value.context))
    original = value.actions.calls[0][0]
    assert original.capability_id == "content.discover"
    discovered = Receipt("discovery-receipt", original.action_id, "attempt", "succeeded", "none", "not_requested",
        result={"read_basis": "discovery_metadata", "experience_eligible": False,
            "candidates": [{"content_key": "url:selected", "title": "An unread result"}],
            "next_actions": [{**deepcopy(READ), "content_key": "url:selected"}], "continuation": deepcopy(READ)})
    await value.worker._activity_result(value.context, discovered, 1, NOW, "content.discover")
    waiting = await activity(database)
    assert waiting["state"] == "dormant" and waiting["next_due_at"] == NOW
    assert waiting["checkpoint_data"]["completed_action_id"] == original.action_id
    assert waiting["checkpoint_data"]["completion_basis"] == "discovery_metadata"
    async with database.transaction(read_only=True) as connection:
        queued = await (await connection.execute("SELECT args FROM runtime.job_keys k JOIN procrastinate_jobs j ON j.id=k.job_id "
            "WHERE k.key=%s", (f"activity-continuation:research:{waiting['revision']}",))).fetchone()
    assert queued["args"]["expected_revision"] == waiting["revision"]
    await value.worker._activity_result(value.context, discovered, 1, NOW, "content.discover")
    await value.worker.activity_step("research", waiting["revision"], wire(value.context))
    await value.worker.activity_step("research", waiting["revision"], wire(value.context))
    assert len(value.actions.calls) == 2
    actual_read = value.actions.calls[1][0]
    assert actual_read.action_id != original.action_id and actual_read.capability_id == "content.read"
    assert dict(actual_read.arguments) == READ["arguments"]
    assert actual_read.deadline_at == NOW+timedelta(minutes=30)
    body = {"read_basis": "actual_source_body", "content_key": "url:selected",
        "source": {"source_id": "body", "revision": 1}, "content": {"text": "The body was actually fetched."}}
    await value.worker._activity_result(value.context,
        Receipt("read-receipt", actual_read.action_id, "read-attempt", "succeeded", "none", "not_requested",
            coverage=Coverage(True), result=body), 1, NOW, "content.read")
    done = await activity(database)
    assert done["state"] == "completed" and done["checkpoint_step"] == 2
    episodes = value.worker.life.memory.observe_episode.call_args_list
    assert episodes[0].args[1]["actual_result"]["experience_eligible"] is False
    assert episodes[1].args[1]["actual_result"]["content"] == body["content"]
    assert not value.models.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_id", ["content.discover", "images.generate"])
async def test_new_action_business_deadline_is_the_accepted_slot_not_a_fixed_execution_timer(database, capability_id):
    value = await fixture(database, execution_mode="focus")
    arguments = {"query": "actual topic", "mode": "research"}
    if capability_id == "images.generate":
        arguments = register_image_capability(value)
    ends = NOW+timedelta(minutes=60)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET checkpoint_data=checkpoint_data||%s WHERE id='research'",
            (document({"capability_id": capability_id, "arguments": arguments,
                "execution_window": {"starts_at": NOW.isoformat(), "ends_at": ends.isoformat(), "steps": 0, "seen_steps": []}}),))
        await connection.execute("UPDATE life.day_slots SET ends_at=%s WHERE id='research-slot'", (ends,))
    await value.worker.activity_step("research", 1, wire(value.context))
    assert len(value.actions.calls) == 1 and value.actions.calls[0][0].deadline_at == ends
    if capability_id == "images.generate":
        assert value.actions.calls[0][0].arguments["window"]["expires_at"] == ends.isoformat()


@pytest.mark.asyncio
async def test_an_expired_nonreading_activity_cannot_start_a_new_action(database):
    value = await fixture(database, execution_mode="focus")
    arguments = register_image_capability(value)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET checkpoint_data=checkpoint_data||%s WHERE id='research'",
            (document({"capability_id": "images.generate", "arguments": arguments, "execution_window": {
                "starts_at": (NOW-timedelta(hours=1)).isoformat(), "ends_at": (NOW-timedelta(seconds=1)).isoformat(),
                "steps": 0, "seen_steps": []}}),))
    await value.worker.activity_step("research", 1, wire(value.context))
    row = await activity(database)
    assert row["state"] == "dormant" and row["waiting_reason"] == "day_finished" and not value.actions.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_delayed_imagined_start_does_not_reopen_the_accepted_activity_window(database, expired):
    value = await fixture(database)
    ends = NOW-timedelta(seconds=1) if expired else NOW+timedelta(minutes=5)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET kind='rest',checkpoint_data=%s WHERE id='research'",
            (document({"capability_id": None, "duration_minutes": 30, "execution_window": {
                "starts_at": (NOW-timedelta(minutes=10)).isoformat(), "ends_at": ends.isoformat(),
                "steps": 0, "seen_steps": []}}),))
        await connection.execute("UPDATE life.day_slots SET starts_at=%s,ends_at=%s WHERE id='research-slot'",
            (NOW-timedelta(minutes=10), ends))
    await value.worker.activity_step("research", 1, wire(value.context))
    row = await activity(database)
    assert not value.actions.calls
    if expired:
        assert row["state"] == "dormant" and row["waiting_reason"] == "day_finished" and row["next_due_at"] is None
    else:
        assert row["state"] == "running" and row["next_due_at"] == ends


@pytest.mark.parametrize("change,reason", [
    ({"execution_window": {"ends_at": (NOW-timedelta(seconds=1)).isoformat()}}, "activity_window_ended"),
    ({"execution_window": {"seen_steps": [signature(READ)]}}, "continuation_repeated_step"),
    ({"consumption": {"stop_reason": "requested_window_processed"}}, "continuation_stop_condition"),
])
def test_a_checkpoint_cannot_browse_past_its_receipt_run_boundary(change, reason):
    data = execution_window({"continuation": deepcopy(READ), "last_execution": "succeeded", "completed_action_id": "known"},
        NOW, NOW+timedelta(minutes=30))
    for key, value in change.items():
        data[key] = {**data.get(key, {}), **value}
    assert automatic_step({"state": "dormant", "waiting_reason": "continuation_available",
        "resume_condition": "continue_from_checkpoint", "action_id": "known", "checkpoint_data": data}, NOW) == (None, reason)


@pytest.mark.asyncio
async def test_recovery_rebinds_known_continuation_and_original_unknown_without_replaying_them(database):
    value = await fixture(database, state="dormant")
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,action_id,scope_ids,context) "
            "VALUES ('unknown','actor','未知原动作','reading','dormant','unresolved-action',ARRAY['actor-scope'],%s)", (document(value.context),))
    await value.worker.recover()
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT task,key FROM runtime.job_keys WHERE key LIKE 'life-recover:activities:%%'")).fetchall()
    assert {row["task"] for row in rows} == {"life.activity_step", "life.activity_reconcile"}
    assert not value.actions.calls and not value.models.calls
    row = await activity(database)
    assert row["action_id"] == row["checkpoint_data"]["completed_action_id"] == "discovery"


@pytest.mark.asyncio
async def test_old_checkpoint_goes_to_normal_planning_and_future_activity_does_not_suppress_it(database):
    value = await fixture(database, state="dormant", old=True)
    await future_slot(database, value)
    await value.worker.activity_step("research", 1, wire(value.context))
    row = await activity(database)
    assert row["checkpoint_data"]["continuation_stop_reason"] == "continuation_requires_arrangement"
    assert row["action_id"] == "discovery" and row["next_due_at"] is None
    # The earlier result slot is over; only the future appointment remains.
    current = await mark(database)
    await value.worker.plan("actor", current["revision"], wire(value.context), value.data_generation)
    after = await mark(database)
    assert after["last_error"] is None, after["reason"]
    assert len(value.models.calls) == 1 and not value.actions.calls
    assert value.models.calls[0].business_deadline_at == NOW.replace(hour=23, minute=59)
    assert after["reason"] == "no_meaningful_activity" and after["review_after"] is not None
    assert (await activity(database))["action_id"] == "discovery"  # Choosing rest is valid.
    await value.worker.life.signal(value.context, "rhythm:unchanged-after-review")
    await value.worker.plan("actor", (await mark(database))["revision"], wire(value.context), value.data_generation)
    assert len(value.models.calls) == 1


@pytest.mark.asyncio
async def test_model_approved_resume_reuses_activity_at_full_day_quota_without_replaying_discovery(database):
    value = await fixture(database, state="dormant", old=True)
    await future_slot(database, value)
    async with database.transaction() as connection:
        for number in (2, 3):
            identifier = "later-"+str(number)
            starts = NOW+timedelta(hours=number)
            await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,next_due_at,scope_ids,context) "
                "VALUES (%s,'actor','稍后休息','rest','planned',%s,ARRAY['actor-scope'],%s)",
                (identifier, starts, document(value.context)))
            await connection.execute("INSERT INTO life.day_slots(id,actor_id,day,kind,starts_at,ends_at,activity_id,reason,scope_ids) "
                "VALUES (%s,'actor',%s,'activity',%s,%s,%s,'fixture',ARRAY['actor-scope'])",
                (identifier+'-slot', NOW.date(), starts, starts+timedelta(minutes=30), identifier))
        original = await (await connection.execute("SELECT * FROM life.activities WHERE id='research'")).fetchone()
        routine = await (await connection.execute("SELECT * FROM life.routines WHERE actor_id='actor'")).fetchone()
        application = await value.worker.life.days.apply(connection, value.context,
            {"activity": {"existing_activity_id": "research", "title": original["title"], "kind": "research", "goal_id": None,
                "capability_id": "content.read", "continuation_key":"url:selected", "arguments": {}, "duration_minutes": 20},
             "arrangements": [], "cancel_activity_ids": [], "reason": "继续读取原来选中的正文。"},
            {"unfinished": [original], "commitments": []}, "accepted-next-slot", {}, (), routine, NOW, "new-window")
    resumed = await activity(database)
    assert [item.status for item in application.items] == ["scheduled"] and resumed["id"] == original["id"]
    assert resumed["action_id"] is None and resumed["checkpoint_data"]["capability_id"] == "content.read"
    await value.worker.activity_step("research", resumed["revision"], wire(value.context))
    assert len(value.actions.calls) == 1 and value.actions.calls[0][0].capability_id == "content.read"
    assert dict(value.actions.calls[0][0].arguments) == READ["arguments"]
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT count(DISTINCT activity_id) AS activities,count(*) AS slots "
            "FROM life.day_slots WHERE kind='activity'")).fetchone()
    assert row["activities"] == 4 and row["slots"] == 5


@pytest.mark.asyncio
async def test_revoked_receipt_sources_cannot_start_the_next_read(database):
    value = await fixture(database, state="dormant")
    value.worker.life.sources.validate_persistence = AsyncMock(return_value=False)
    with pytest.raises(AppError, match="source_unavailable"):
        await value.worker.activity_step("research", 1, wire(value.context))
    assert not value.actions.calls and (await activity(database))["action_id"] == "discovery"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["paused", "cancelled"])
async def test_late_success_keeps_user_pause_or_cancel_and_does_not_queue_a_read(database, target):
    value = await fixture(database)
    await value.worker.activity_step("research", 1, wire(value.context))
    pending = await activity(database)
    assert pending["state"] == "dormant" and pending["waiting_reason"] == "capability_running"
    action = value.actions.calls[0][0]
    value.jobs.register("life.activity_cancel", value.worker.cancel_activity)
    await value.worker.life.transition_activity(value.context, {"id": "research", "expected_revision": pending["revision"],
        "client_request_id": "user-"+target}, target)
    await value.worker._activity_result(value.context, Receipt("late", action.action_id, "attempt", "succeeded", "none", "not_requested",
        result={"read_basis": "discovery_metadata", "experience_eligible": False, "continuation": deepcopy(READ)}), 1, NOW, "content.discover")
    row = await activity(database)
    assert row["state"] == target
    await value.worker.activity_step("research", row["revision"], wire(value.context))
    assert len(value.actions.calls) == 1
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT key FROM runtime.job_keys WHERE key LIKE 'activity-continuation:%%'")).fetchall()
    if target == "paused":
        await value.worker.life.transition_activity(value.context, {"id": "research", "expected_revision": row["revision"],
            "client_request_id": "explicit-resume"}, "running")
        resumed = await activity(database)
        assert resumed["action_id"] is None and resumed["checkpoint_data"]["arguments"] == READ["arguments"]


@pytest.mark.asyncio
async def test_user_can_pause_a_known_continuation_and_only_explicit_resume_uses_its_original_read(database):
    value = await fixture(database, state="dormant")
    original = await activity(database)
    await value.worker.life.transition_activity(value.context, {"id": "research", "expected_revision": original["revision"],
        "client_request_id": "pause-known-continuation"}, "paused")
    paused = await activity(database)
    assert paused["state"] == "paused" and paused["waiting_reason"] == "paused_by_user" and paused["next_due_at"] is None
    assert paused["checkpoint_data"] == original["checkpoint_data"] and paused["action_id"] == original["action_id"]
    async with database.transaction(read_only=True) as connection:
        slot = await (await connection.execute("SELECT state FROM life.day_slots WHERE id='research-slot'")).fetchone()
    assert slot["state"] == "paused"
    await value.worker.activity_step("research", original["revision"], wire(value.context))
    await value.worker.activity_step("research", paused["revision"], wire(value.context))
    await value.worker.recover()
    assert not value.actions.calls
    assert (await activity(database))["revision"] == paused["revision"]
    await value.worker.life.transition_activity(value.context, {"id": "research", "expected_revision": paused["revision"],
        "client_request_id": "resume-known-continuation"}, "running")
    resumed = await activity(database)
    async with database.transaction(read_only=True) as connection:
        slot = await (await connection.execute("SELECT state FROM life.day_slots WHERE id='research-slot'")).fetchone()
    assert slot["state"] == "running"
    await value.worker.activity_step("research", resumed["revision"], wire(value.context))
    assert len(value.actions.calls) == 1 and value.actions.calls[0][0].capability_id == "content.read"
    assert dict(value.actions.calls[0][0].arguments) == READ["arguments"]


@pytest.mark.asyncio
async def test_manual_pause_keeps_an_event_wait_from_automatically_resuming(database):
    value = await fixture(database, state="dormant")
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET action_id=NULL,waiting_reason='capability_unavailable',"
            "resume_condition='capability_available',wake_condition=%s WHERE id='research'",
            (document({"kind": "capability_available", "subject_id": "content.read"}),))
    await value.worker.life.transition_activity(value.context, {"id": "research", "expected_revision": 1,
        "client_request_id": "pause-event-wait"}, "paused")
    paused = await activity(database)
    async with database.transaction() as connection:
        await value.worker.life.wake(value.context, kind="capability_available", subject_id="content.read",
            event_id="adapter-ready", connection=connection)
    current = await activity(database)
    assert current["state"] == "paused" and current["revision"] == paused["revision"]
    assert current["wake_condition"] == paused["wake_condition"] and not value.actions.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["sleep", "disabled", "appointment", "protection", "running"])
async def test_due_continuation_respects_existing_time_and_current_activity_protection(database, boundary):
    value = await fixture(database, state="dormant")
    async with database.transaction() as connection:
        # These are role-focus boundaries. Background reading has separate,
        # explicitly tested scheduling semantics.
        await connection.execute("UPDATE life.activities SET checkpoint_data=jsonb_set(checkpoint_data,'{execution_mode}',%s) WHERE id='research'",
            (document("focus"),))
        if boundary == "sleep":
            await connection.execute("UPDATE life.routines SET wake_at='13:00',sleep_at='23:00'")
        elif boundary == "disabled":
            await connection.execute("UPDATE life.routines SET enabled=false")
        elif boundary == "protection":
            await connection.execute("UPDATE life.activities SET protected_until=%s WHERE id='research'", (NOW+timedelta(minutes=5),))
        elif boundary == "running":
            await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,next_due_at,scope_ids) "
                "VALUES ('current','actor','当前活动','room','running',%s,ARRAY['actor-scope'])", (NOW+timedelta(minutes=5),))
            await connection.execute("UPDATE life.actor_state SET activity_id='current' WHERE actor_id='actor'")
    if boundary == "appointment":
        await future_slot(database, value, starts=NOW)
    await value.worker.activity_step("research", 1, wire(value.context))
    row = await activity(database)
    assert not value.actions.calls and row["action_id"] == "discovery"
    if boundary in {"running", "protection"}:
        assert row["next_due_at"] == NOW+timedelta(minutes=5)
        assert row["waiting_reason"] == "continuation_available"
    else:
        assert row["next_due_at"] is None and row["checkpoint_data"]["continuation_stop_reason"]


def test_lifecycle_fingerprint_changes_on_due_and_slot_fences_without_per_minute_churn():
    from test_planner_contract import context
    data = {"local_time": NOW.isoformat(), "routine": {}, "commitments": [], "interests": [], "goals": [], "capabilities": [],
        "day_plan": [{"id": "later", "activity_id": "activity", "state": "planned",
            "starts_at": (NOW+timedelta(minutes=10)).isoformat(), "ends_at": (NOW+timedelta(minutes=30)).isoformat()}],
        "unfinished": [{"id": "unfinished", "state": "dormant", "revision": 4, "waiting_reason": "continuation_available",
            "next_due_at": (NOW+timedelta(minutes=5)).isoformat(), "checkpoint_data": {"continuation": READ}}]}
    original = planning_engine.decision_fingerprint(context(), {}, data, NOW.date())
    data["local_time"] = (NOW+timedelta(minutes=1)).isoformat()
    assert planning_engine.decision_fingerprint(context(), {}, data, NOW.date()) == original
    data["local_time"] = (NOW+timedelta(minutes=5)).isoformat()
    due = planning_engine.decision_fingerprint(context(), {}, data, NOW.date())
    assert due != original
    data["local_time"] = (NOW+timedelta(minutes=6)).isoformat()
    assert planning_engine.decision_fingerprint(context(), {}, data, NOW.date()) == due
    data["local_time"] = (NOW+timedelta(minutes=10)).isoformat()
    assert planning_engine.decision_fingerprint(context(), {}, data, NOW.date()) != due
