"""Real domain persistence around streamed planning, with no external providers."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from tianshu.life import planner
from tianshu.life.module import create_module
from tianshu.models.types import ModelCompletion
from tianshu.runtime.api import error_middleware
from tianshu.runtime.serialization import wire
from test_planner_execution import mark, chosen_activity
from historical_support import planner_setup as setup


def rest():
    return json.dumps({"activity": None, "arrangements": [], "goal_changes": [], "cancel_activity_ids": [],
                       "reason": "现在想留一会儿白。", "free_time_reason": "暂时没有新的想法。"})


async def make_due(database, field):
    assert field in {"repair_due_at", "review_after"}
    async with database.transaction() as connection:
        await connection.execute(f"UPDATE life.plan_watermarks SET {field}=now()-interval '1 second' WHERE actor_id='actor'")


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated", [False, True])
async def test_known_invalid_results_have_two_bounded_repairs_and_never_become_valid_decisions(database, truncated):
    value = await setup(database, '{"activity":')
    original_generate = value.models.generate

    async def generate(request):
        result = await original_generate(request)
        if truncated:
            value.models.actual = replace(result, completion=ModelCompletion("incomplete", ("length",)))
        return value.models.actual

    value.models.generate = generate
    keys = []
    for index in range(3):
        await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
        current = await mark(database)
        keys.append(current["generation_key"])
        assert current["decision_fingerprint"] is None and current["generation_consumed"]
        assert current["repair_count"] == index + 1
        assert current["last_error"] == ("planner_output_incomplete" if truncated else "planner_output_invalid")
        if index < 2:
            assert current["repair_due_at"] is not None
            await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
            assert len(value.models.calls) == index + 1
            await make_due(database, "repair_due_at")
    assert len(set(keys)) == 3 and len(value.models.calls) == 3
    assert (await mark(database))["repair_due_at"] is None
    await value.worker.life.signal(value.context, "rhythm:ordinary-after-final-failure")
    await value.worker.plan("actor", 2, wire(value.context), value.data_generation)
    assert len(value.models.calls) == 3
    assert (await mark(database))["reason"] == current["last_error"]
    async with database.transaction(read_only=True) as connection:
        records = await (await connection.execute("SELECT state,reason FROM life.plan_generations")).fetchall()
        activities = await (await connection.execute("SELECT id FROM life.activities")).fetchall()
    assert len(records) == 3 and all(row["state"] == "rejected" for row in records) and activities == []


@pytest.mark.asyncio
async def test_repair_applies_one_complete_result_and_keeps_the_original_rejected_record(database):
    value = await setup(database, "not JSON")
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    original = (await mark(database))["generation_key"]
    await make_due(database, "repair_due_at")
    value.models.output = chosen_activity()
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    current = await mark(database)
    assert current["generation_key"] != original and current["decision_fingerprint"]
    assert current["last_error"] is None and current["repair_count"] == 0
    async with database.transaction(read_only=True) as connection:
        old = await (await connection.execute("SELECT * FROM life.plan_generations WHERE generation_key=%s", (original,))).fetchone()
        activities = await (await connection.execute("SELECT id FROM life.activities")).fetchall()
    assert old["state"] == "rejected" and old["reason"] == "planner_output_invalid" and len(activities) == 1
    await value.worker.plan("actor", 1, wire(value.context), value.data_generation)
    assert len(value.models.calls) == 2


@pytest.mark.asyncio
async def test_verified_model_configuration_change_naturally_recovers_without_periodic_paid_retries(database):
    value = await setup(database, chosen_activity())
    ready = False
    original_profile = value.models.generation_profile

    async def profile(request):
        result = await original_profile(request)
        return {**result, "verified_capabilities": ["text", "stream"] if ready else ["text"],
                "route_revision": 2 if ready else 1}

    value.models.generation_profile = profile
    await value.worker.plan("actor", 1, wire(value.context))
    current = await mark(database)
    assert current["last_error"] == "planner_model_unavailable" and value.models.calls == []
    assert current["repair_due_at"] is None and current["generation_key"] is None
    await value.worker.life.signal(value.context, "rhythm:still-unverified")
    await value.worker.plan("actor", 2, wire(value.context))
    assert value.models.calls == []
    ready = True
    await value.worker.life.signal(value.context, "rhythm:after-capability-probe")
    await value.worker.plan("actor", 3, wire(value.context))
    current = await mark(database)
    assert current["last_error"] is None and current["decision_fingerprint"] and len(value.models.calls) == 1
    assert value.models.calls[0].route_revision == 2


@pytest.mark.asyncio
async def test_unknown_yields_to_new_material_without_replaying_its_identity(database):
    value = await setup(database, chosen_activity(), lose_response=True)
    with pytest.raises(TimeoutError):
        await value.worker.plan("actor", 1, wire(value.context))
    original = await mark(database)
    frozen=value.models.calls[0]
    value.models.lose_response=False
    await value.worker.life.signal(value.context, "reconsider:new-material", change={"reason": "A different authored idea"})
    await value.worker.plan("actor", 2, wire(value.context))
    current = await mark(database)
    assert current["generation_key"] != original["generation_key"] and current["generation_consumed"]
    assert len(value.models.calls) == 2 and value.models.calls[1].attempt_id!=frozen.attempt_id
    async with database.transaction(read_only=True) as connection:
        assert len(await (await connection.execute("SELECT id FROM life.activities")).fetchall())==1
        previous=await (await connection.execute("SELECT state FROM life.plan_generations WHERE generation_key=%s",(original["generation_key"],))).fetchone()
        request=await (await connection.execute("SELECT request FROM conversation.generation_requests WHERE attempt_id=%s",(frozen.attempt_id,))).fetchone()
    assert previous["state"]=="superseded" and request["request"]==wire(frozen)


@pytest.mark.asyncio
async def test_late_valid_result_under_revoked_sources_cannot_apply_or_create_repair(database):
    value = await setup(database, chosen_activity(), lose_response=True)
    with pytest.raises(TimeoutError):
        await value.worker.plan("actor", 1, wire(value.context))
    assert (await mark(database))["repair_due_at"] > datetime.now(UTC)
    value.models.actual = replace(value.models.actual, receipt=replace(value.models.actual.receipt, status="succeeded"))
    value.worker.life.sources.validate_persistence = AsyncMock(return_value=False)
    await make_due(database, "repair_due_at")
    await value.worker.plan("actor", 1, wire(value.context))
    current = await mark(database)
    value.worker.life.sources.validate_persistence.assert_awaited_once()
    assert current["last_error"] == "source_unavailable" and current["repair_due_at"] is None
    assert current["decision_fingerprint"] is None and len(value.models.calls) == 1
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT id FROM life.activities")).fetchall()
        generations = await (await connection.execute("SELECT state,reason FROM life.plan_generations")).fetchall()
    assert generations == [{"state": "rejected", "reason": "source_unavailable"}]


@pytest.mark.asyncio
async def test_free_time_has_a_review_window_and_ordinary_notifications_only_rebind_the_due_job(database):
    value = await setup(database, rest())
    await value.worker.plan("actor", 1, wire(value.context))
    first = await mark(database)
    assert first["review_after"] > datetime.now(UTC) and first["decision_fingerprint"]
    for index, signal in enumerate(("growth:style", "rhythm:ordinary"), start=2):
        await value.worker.life.signal(value.context, signal)
        await value.worker.plan("actor", index, wire(value.context))
    current = await mark(database)
    assert len(value.models.calls) == 1 and current["review_after"] == first["review_after"]
    await make_due(database, "review_after")
    await value.worker.plan("actor", current["revision"], wire(value.context))
    current = await mark(database)
    assert len(value.models.calls) == 2 and current["generation_key"] != first["generation_key"]
    assert current["reason"] == "no_meaningful_activity"  # The actor may still choose rest.


@pytest.mark.asyncio
async def test_only_free_slot_returns_200_through_the_registered_day_plan_http_route(database):
    value = await setup(database, rest())
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    async with database.transaction() as connection:
        await value.worker.life.days.apply(connection, value.context, json.loads(rest()),
            {"unfinished": [], "commitments": []}, "free", {}, (),
            {"timezone": "UTC", "wake_at": "07:00", "sleep_at": "23:00", "idle_seconds": 5400}, now, "free-material")
    value.worker.life.identity.for_request = AsyncMock(return_value=value.context)
    unused = MagicMock()
    module = create_module(value.worker.life, unused, unused, unused, unused, value.worker,
                           value.worker.life.identity, unused)
    app = web.Application(middlewares=[error_middleware])
    module.register_routes(app, None)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/v1/life/day-plan", json={"actor_id": "actor", "day": "2026-11-02"})
        body = await response.json()
    assert response.status == 200 and body["plan"]["day"] == "2026-11-02"
    assert len(body["slots"]) == 1 and body["slots"][0]["kind"] == "free"
    assert body["slots"][0]["activity_id"] is None and body["slots"][0]["checkpoint_data"] == {}


def test_program_calendar_and_unchanged_mood_do_not_depend_on_the_model():
    from test_planner_contract import data, context
    from tianshu.life.planning_engine import decision_fingerprint, review_time
    value = data([])
    selected = planner.material(context(), {"affect": {"regulation": {}}, "outfit": None},
        value["routine"], MagicMock(items=[], coverage={}), MagicMock(items=[], coverage={}), [],
        "2026-11-02T07:00:00+08:00")
    assert selected["local_date"] == "2026-11-02" and selected["local_weekday"] == "周一"
    expected = decision_fingerprint(context(), {}, selected, "2026-11-02")
    selected["affect_regulation"] = {"mode": "calm", "activity_bias": "passively decayed"}
    assert decision_fingerprint(context(), {}, selected, "2026-11-02") == expected
    now = datetime(2026, 11, 2, 22, 30, tzinfo=UTC)
    end = now + timedelta(minutes=30)
    assert review_time({"idle_seconds": 5400}, now, end) == end
