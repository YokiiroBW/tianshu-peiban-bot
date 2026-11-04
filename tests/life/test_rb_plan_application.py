from datetime import UTC, datetime, timedelta
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document
from tianshu.life.application import apply as apply_proposal
from tianshu.runtime.migrations import Migration, migrate
from test_planner_execution import setup


NOW = datetime(2026, 11, 5, 10, tzinfo=UTC)
ROUTINE = {"timezone": "UTC", "wake_at": "08:00", "sleep_at": "11:00", "idle_seconds": 1800}


async def fixture(database):
    value = await setup(database, "{}")
    await migrate(database, (Migration("0040_life_execution", files("tianshu.life").joinpath("rb_life_execution.sql").read_text()),))
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.day_plans(actor_id,day,timezone,reason,context,scope_ids,material_fingerprint) "
            "VALUES ('actor',%s,'UTC','retained',%s,ARRAY['actor-scope'],'old')", (NOW.date(), document(value.context)))
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,revision,waiting_reason,checkpoint_data,scope_ids,context) "
            "VALUES ('old','actor','old','reading','dormant',2,'day_finished',%s,ARRAY['actor-scope'],%s)",
            (document({"capability_id": "content.read", "arguments": {"url": "https://example.test/old"}}), document(value.context)))
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,scope_ids,context) "
            "VALUES ('focus','actor','meeting','room','running',ARRAY['actor-scope'],%s)", (document(value.context),))
        await connection.execute("INSERT INTO life.day_slots(id,actor_id,day,kind,state,starts_at,ends_at,activity_id,reason,scope_ids) "
            "VALUES ('focus-slot','actor',%s,'activity','running',%s,%s,'focus','existing',ARRAY['actor-scope'])",
            (NOW.date(), NOW, NOW + timedelta(hours=1)))
    return value


def proposal(title, capability=None, *, goal=None, key=None):
    return {"title": title, "kind": "reading" if capability else "rest", "goal_id": goal,
        "capability_id": capability, "arguments": {"url": "https://example.test/new"} if capability else {},
        "duration_minutes": 30, "_candidate_key": key or title}


def decision(items):
    return {"activity": items[0] if items else None, "arrangements": items[1:], "goal_changes": [],
        "cancel_activity_ids": [], "reason": "current decision", "free_time_reason": "rest"}


@pytest.mark.asyncio
async def test_application_distinguishes_no_slot_stale_revision_and_independent_background(database):
    value = await fixture(database)
    stale = {**proposal("old", "content.read", key="stale"), "existing_activity_id": "old", "arguments": {}}
    data = {"unfinished": [{"id": "old", "revision": 1}], "commitments": [], "coverage": {}}
    async with database.transaction() as connection:
        application = await value.worker.life.days.apply(connection, value.context,
            decision([proposal("no-slot"), proposal("background", "content.read"), stale]),
            data, "mixed-generation", {}, (), ROUTINE, NOW, "material")
        # Different independent running work is permitted for the same actor.
        await connection.execute("UPDATE life.activities SET state='running' WHERE id=%s",
            (next(item.activity_id for item in application.items if item.candidate_key == "background"),))
        running = await (await connection.execute("SELECT count(*) AS n FROM life.activities WHERE state='running'")).fetchone()
        stored_plan = await (await connection.execute("SELECT material_fingerprint FROM life.day_plans WHERE actor_id='actor' AND day=%s", (NOW.date(),))).fetchone()
    statuses = {item.candidate_key: item.status for item in application.items}
    assert statuses == {"no-slot": "deferred_no_slot", "background": "scheduled", "stale": "stale_state"}
    assert application.next_due_at == NOW  # Stale candidates refresh before the later free window.
    assert running["n"] == 2
    assert stored_plan["material_fingerprint"] == "old"


@pytest.mark.asyncio
async def test_goal_and_dependent_work_roll_back_together_while_other_work_survives(database):
    value = await fixture(database)
    value.worker.life.sources.record_event = AsyncMock(return_value=())
    dependent = [proposal("group-rest", goal="new:g"), proposal("group-read", "content.read", goal="new:g")]
    other = proposal("independent", "content.read")
    plan = decision([*dependent, other])
    plan["goal_changes"] = [{"operation": "create", "key": "g", "id": None, "title": "goal", "motivation": "motivation",
        "priority": .5, "criteria": {"kind": "completed_activity", "capability_id": "content.read", "target_count": 1},
        "basis_ids": ["basis"], "reason": "source"}]
    data = {"goals": [], "basis": [{"id": "basis", "source_refs": []}], "unfinished": [], "commitments": [], "coverage": {}}
    async with database.transaction() as connection:
        application = await apply_proposal(value.worker.life, connection, value.context, plan, data,
            "group-generation", (), ROUTINE, NOW, "material")
        goals = await (await connection.execute("SELECT id FROM life.goals")).fetchall()
        activities = await (await connection.execute("SELECT title FROM life.activities WHERE title IN ('group-rest','group-read','independent')")).fetchall()
    assert not goals and [item["title"] for item in activities] == ["independent"]
    statuses = {item.candidate_key: item.status for item in application.items}
    assert statuses["independent"] == "scheduled"
    assert statuses["group-rest"] == statuses["group-read"] == "deferred_no_slot"


@pytest.mark.asyncio
async def test_explicit_idle_has_its_own_result(database):
    value = await fixture(database)
    async with database.transaction() as connection:
        application = await value.worker.life.days.apply(connection, value.context, decision([]),
            {"unfinished": [], "commitments": [], "coverage": {}}, "idle-generation", {}, (), ROUTINE, NOW, "material")
    assert [(item.candidate_key, item.status) for item in application.items] == [("idle", "intentional_idle")]
