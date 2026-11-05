"""Rolling decisions reuse native completion, review and recovery fences."""
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.life import clock, day_plan, planning_engine, recovery, service, worker
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef
from test_planner_execution import mark
from historical_support import planner_setup as setup


NOW = datetime(2026, 11, 3, 10, tzinfo=UTC)
ROUTINE = {"timezone": "UTC", "wake_at": "08:00", "sleep_at": "14:00", "idle_seconds": 5400}


def decision(title=None, *, minutes=15):
    return {"activity": {"title": title, "kind": "reflection", "goal_id": None,
            "capability_id": None, "arguments": {}, "duration_minutes": minutes} if title else None,
        "arrangements": [], "goal_changes": [], "cancel_activity_ids": [],
        "reason": "按当前实际空闲作出下一步选择。", "free_time_reason": "自行决定活动与留白。"}


@pytest.fixture
def time_now(monkeypatch):
    value = SimpleNamespace(now=NOW)

    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return value.now.astimezone(tz) if tz else value.now.replace(tzinfo=None)

    for module in (clock, day_plan, planning_engine, recovery, service, worker):
        monkeypatch.setattr(module, "datetime", FixedTime)
    return value


async def rolling_fixture(database, output):
    value = await setup(database, output)
    value.worker.assembler = ContextAssembler(value.worker.life.identity, None, None,
        settings={"tokenizer": "o200k_base"})
    return value


async def completed_history(database, value, *, count=4):
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.day_plans(actor_id,day,timezone,reason,context,scope_ids,material_fingerprint) "
            "VALUES ('actor',%s,'UTC','history',%s,ARRAY['actor-scope'],'history')", (NOW.date(), document(value.context)))
        for index in range(count):
            identifier = f"history-{index}"
            starts = NOW-timedelta(hours=2)+timedelta(minutes=15*index)
            await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,scope_ids,context) "
                "VALUES (%s,'actor',%s,'reflection','completed',ARRAY['actor-scope'],%s)",
                (identifier, f"已经完成的不同往事 {index}", document(value.context)))
            await connection.execute("INSERT INTO life.day_slots(id,actor_id,day,kind,state,starts_at,ends_at,activity_id,reason,scope_ids) "
                "VALUES (%s,'actor',%s,'activity','completed',%s,%s,%s,'history',ARRAY['actor-scope'])",
                (identifier+'-slot', NOW.date(), starts, starts+timedelta(minutes=15), identifier))


@pytest.mark.asyncio
async def test_completed_history_does_not_limit_the_fifth_or_sixth_activity_and_completion_decides_again(database, time_now):
    value = await rolling_fixture(database, json.dumps(decision("先整理今天读到的内容")))
    await completed_history(database, value)
    value.worker.life.sources.record_event = AsyncMock(return_value=(SourceRef("actual-imagined-checkpoint", 1),))
    value.worker.life.memory = SimpleNamespace(observe_episode=AsyncMock())
    for index in range(2):
        value.models.output = json.dumps(decision(f"独立的后续活动 {index}"))
        current = await mark(database)
        await value.worker.plan("actor", current["revision"], wire(value.context), value.data_generation)
        async with database.transaction(read_only=True) as connection:
            activity = await (await connection.execute("SELECT * FROM life.activities WHERE state='planned'")).fetchone()
        assert activity and activity["id"] not in {f"history-{item}" for item in range(4)}
        await value.worker.activity_step(activity["id"], activity["revision"], wire(value.context))
        async with database.transaction(read_only=True) as connection:
            running = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s", (activity["id"],))).fetchone()
        assert running["state"] == "running"
        time_now.now = running["next_due_at"]
        await value.worker.activity_step(activity["id"], activity["revision"], wire(value.context))
        completed = await mark(database)
        assert completed["change_watermark"].startswith("activity-finished:")
        assert completed["planned_watermark"] != completed["change_watermark"]
        # Repeated completion jobs preserve the same event and actor checkpoint.
        await value.worker.activity_step(activity["id"], activity["revision"], wire(value.context))
        assert (await mark(database))["revision"] == completed["revision"]
    async with database.transaction(read_only=True) as connection:
        slots = await (await connection.execute("SELECT * FROM life.day_slots WHERE kind='activity'")).fetchall()
    assert len(slots) == 6 and all(row["state"] == "completed" for row in slots)
    assert len(value.models.calls) == 2 and value.worker.life.memory.observe_episode.await_count == 2
    assert not value.actions.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("obstacle", ["none", "ends_at_sleep", "commitment", "running", "sleep"])
async def test_long_activities_have_no_fixed_occupancy_ratio_but_obey_real_windows(database, time_now, obstacle):
    value = await rolling_fixture(database, "{}")
    data = {"unfinished": [], "commitments": [], "coverage": {}}
    now = NOW.replace(hour=14) if obstacle == "sleep" else NOW
    routine = {**ROUTINE, "sleep_at": "13:00"} if obstacle == "ends_at_sleep" else ROUTINE
    async with database.transaction() as connection:
        if obstacle == "commitment":
            due = NOW+timedelta(hours=1)
            data["commitments"] = [{"due_at": due}]
            await connection.execute("INSERT INTO life.commitments(id,actor_id,title,due_at,timezone,state,scope_ids,context) "
                "VALUES ('promise','actor','明确承诺',%s,'UTC','scheduled',ARRAY['actor-scope'],%s)", (due, document(value.context)))
        if obstacle == "running":
            await connection.execute("INSERT INTO life.day_plans(actor_id,day,timezone,reason,context,scope_ids,material_fingerprint) "
                "VALUES ('actor',%s,'UTC','current',%s,ARRAY['actor-scope'],'current')", (NOW.date(), document(value.context)))
            await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,scope_ids) "
                "VALUES ('current','actor','正在进行','reflection','running',ARRAY['actor-scope'])")
            await connection.execute("INSERT INTO life.day_slots(id,actor_id,day,kind,state,starts_at,ends_at,activity_id,reason,scope_ids) "
                "VALUES ('current-slot','actor',%s,'activity','running',%s,%s,'current','current',ARRAY['actor-scope'])",
                (NOW.date(), NOW, NOW+timedelta(minutes=60)))
        application = await value.worker.life.days.apply(connection, value.context, decision("持续三小时的独立活动", minutes=180), data,
            "long-activity", {}, (), routine, now, "material")
        slots = await (await connection.execute("SELECT * FROM life.day_slots WHERE kind='activity' AND activity_id<>'current'")).fetchall()
    if obstacle in {"none", "ends_at_sleep", "running"}:
        assert len(slots) == 1 and [item.status for item in application.items] == ["scheduled"]
        starts = NOW+timedelta(hours=1) if obstacle == "running" else NOW
        assert slots[0]["starts_at"] == starts and slots[0]["ends_at"] == starts+timedelta(hours=3)
    else:
        assert not slots and [item.status for item in application.items] == ["deferred_no_slot"]


@pytest.mark.asyncio
async def test_idle_review_keeps_its_absolute_deadline_across_harmless_ticks_and_reconsiders_once(database, time_now):
    value = await rolling_fixture(database, json.dumps(decision()))
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    first = await mark(database)
    for index, notification in enumerate(("rhythm:ordinary", "growth:style-only"), start=1):
        time_now.now = NOW+timedelta(minutes=10*index)
        await value.worker.life.signal(value.context, notification)
        await value.worker.plan("actor", (await mark(database))["revision"], wire(value.context), value.data_generation)
        current = await mark(database)
        assert current["review_after"] == current["next_due_at"] == first["review_after"]
    assert len(value.models.calls) == 1
    time_now.now = first["review_after"]
    current = await mark(database)
    await value.worker.plan("actor", current["revision"], wire(value.context), value.data_generation)
    after = await mark(database)
    await value.worker.plan("actor", after["revision"], wire(value.context), value.data_generation)
    assert len(value.models.calls) == 2 and after["generation_key"] != first["generation_key"]
    assert after["review_after"] > time_now.now and not value.actions.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("old_reason", ["day_arrangements_complete", "unchanged_decision_material"])
async def test_restart_recovers_legacy_idle_deadline_without_rewriting_original_requests(database, time_now, old_reason):
    value = await rolling_fixture(database, json.dumps(decision()))
    await completed_history(database, value)
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    first = await mark(database)
    deadline = NOW+timedelta(minutes=90)
    original = value.models.calls[0]
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_watermarks SET review_after=NULL,next_due_at=%s,reason=%s WHERE actor_id='actor'",
            (deadline, old_reason))
    time_now.now = NOW+timedelta(minutes=10)
    await value.worker.life.signal(value.context, "growth:ordinary")
    await value.worker.plan("actor", (await mark(database))["revision"], wire(value.context), value.data_generation)
    current = await mark(database)
    assert current["next_due_at"] == deadline and current["review_after"] is None and len(value.models.calls) == 1
    value.jobs.enqueue = AsyncMock(wraps=value.jobs.enqueue)
    await value.worker.recover()
    await value.worker.recover()
    jobs = [call.args[1] for call in value.jobs.enqueue.await_args_list if call.args[1].task == "life.plan"]
    assert len(jobs) == 2 and all(row.scheduled_at == deadline for row in jobs)
    assert not value.actions.calls and value.models.calls == [original]
    time_now.now = deadline
    for row in jobs:
        await value.worker.plan(**row.payload)
    after = await mark(database)
    assert len(value.models.calls) == 2 and after["generation_key"] != first["generation_key"]
    assert after["review_after"] > deadline and value.models.calls[0] == original
    async with database.transaction(read_only=True) as connection:
        previous = await (await connection.execute("SELECT state FROM life.plan_generations WHERE generation_key=%s", (first["generation_key"],))).fetchone()
        history = await (await connection.execute("SELECT state FROM life.activities WHERE id LIKE 'history-%%'")).fetchall()
    assert previous["state"] == "applied" and all(row["state"] == "completed" for row in history)


@pytest.mark.asyncio
@pytest.mark.parametrize("notification", ["rhythm:ordinary", "growth:style-only"])
async def test_due_legacy_review_preserves_running_and_unknown_activities(database, time_now, notification):
    value = await rolling_fixture(database, json.dumps(decision()))
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_watermarks SET review_after=NULL,next_due_at=%s,reason='unchanged_decision_material' WHERE actor_id='actor'",
            (NOW-timedelta(seconds=1),))
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,next_due_at,action_id,waiting_reason,scope_ids,context) "
            "VALUES ('current','actor','正在活动','reflection','running',%s,NULL,'fictional_activity',ARRAY['actor-scope'],%s),"
            "('unknown','actor','原未知动作','reading','dormant',NULL,'original-unknown','outcome_unknown',ARRAY['actor-scope'],%s)",
            (NOW+timedelta(minutes=30), document(value.context), document(value.context)))
    await value.worker.life.signal(value.context, notification)
    await value.worker.recover()
    await value.worker.plan("actor", (await mark(database))["revision"], wire(value.context), value.data_generation)
    await value.worker.activity_step("unknown", 1, wire(value.context))
    async with database.transaction(read_only=True) as connection:
        unknown = await (await connection.execute("SELECT * FROM life.activities WHERE id='unknown'")).fetchone()
        active = await (await connection.execute("SELECT * FROM life.activities WHERE id='current'")).fetchone()
        tasks = await (await connection.execute("SELECT task FROM runtime.job_keys WHERE key LIKE 'life-recover:activities:%%'")).fetchall()
    assert (await mark(database))["reason"] == "existing_activity_continues" and len(value.models.calls) == 1
    assert active["state"] == "running" and unknown["state"] == "dormant" and unknown["revision"] == 1
    assert unknown["action_id"] == "original-unknown" and unknown["waiting_reason"] == "outcome_unknown"
    assert {row["task"] for row in tasks} == {"life.activity_step", "life.activity_reconcile"}
    assert not value.actions.calls


@pytest.mark.asyncio
async def test_legacy_review_during_sleep_waits_for_the_original_waking_boundary(database, time_now):
    value = await rolling_fixture(database, json.dumps(decision()))
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    first = await mark(database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.routines SET wake_at='08:00',sleep_at='09:00' WHERE actor_id='actor'")
        await connection.execute("UPDATE life.plan_watermarks SET review_after=NULL,next_due_at=%s,reason='day_arrangements_complete' WHERE actor_id='actor'",
            (NOW-timedelta(seconds=1),))
    await value.worker.recover()
    await value.worker.plan("actor", first["revision"], wire(value.context), value.data_generation)
    after = await mark(database)
    assert after["reason"] == "actor_resting" and after["next_due_at"] == NOW.replace(hour=8)+timedelta(days=1)
    assert after["generation_key"] == first["generation_key"] and len(value.models.calls) == 1
    assert not value.actions.calls
