from datetime import UTC, datetime
from importlib.resources import files
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.life import model_budget, planning_engine
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from test_planner_execution import mark, setup


NOW = datetime(2026, 11, 5, 10, tzinfo=UTC)


class FixedTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_planning_submits_once_yields_worker_and_consumes_original_completion(database, monkeypatch):
    monkeypatch.setattr(planning_engine, "datetime", FixedTime)
    monkeypatch.setattr(model_budget, "datetime", FixedTime)
    output = json.dumps({"activity": None, "arrangements": [], "goal_changes": [], "cancel_activity_ids": [],
        "reason": "take a break", "free_time_reason": "free time"})
    value = await setup(database, output)
    await migrate(database, (Migration("0040_life_execution", files("tianshu.life").joinpath("rb_life_execution.sql").read_text()),))
    original_profile = value.models.generation_profile
    async def profile(request):
        return {**(await original_profile(request)), "quota_group": "quota"}
    value.models.generation_profile = profile
    policy = SimpleNamespace(register_on=AsyncMock(return_value={"lineage_id": "lineage"}),
        reserve_attempt_on=AsyncMock(return_value=SimpleNamespace(number=1, probe_token=None)),
        succeed_attempt_on=AsyncMock())
    value.worker.life.bind_recovery_policy(policy)
    async def planning(context):
        return await value.worker.catalog.discover(context)
    value.worker.catalog.planning = planning
    submitted = []
    state = {"receipt": None}
    async def submit(request, *, completion=None):
        submitted.append((request, completion))
        state["receipt"] = ModelReceipt(request.attempt_id, request.intent_id, "queued", "not_started",
            "route", 1, "provider", "model", "native", "quota")
        return state["receipt"]
    async def status(_attempt_id):
        return state["receipt"]
    value.models.submit = submit
    value.models.status = status

    await value.worker.plan("actor", 1, wire(value.context))
    first = await mark(database)
    assert first["reason"] == "planner_queued" and not first["generation_consumed"]
    assert len(submitted) == 1 and submitted[0][1].task == "life.plan"
    assert not value.models.calls and not value.models.streams
    await value.worker.plan("actor", 1, wire(value.context))
    assert len(submitted) == 1

    request = submitted[0][0]
    state["receipt"] = ModelReceipt(request.attempt_id, request.intent_id, "succeeded", "submitted",
        "route", 1, "provider", "model", "native", "quota")
    value.models.actual = ModelResult((ModelPart("text", text=output),), state["receipt"],
        completion=ModelCompletion("complete", ("stop",)))
    await value.worker.plan("actor", 1, wire(value.context))
    final = await mark(database)
    async with database.transaction(read_only=True) as connection:
        generation = await (await connection.execute("SELECT * FROM life.plan_generations WHERE generation_key=%s",
            (final["generation_key"],))).fetchone()
    assert final["reason"] == "no_meaningful_activity" and final["generation_consumed"]
    assert generation["application_results"]["items"][0]["status"] == "intentional_idle"
    assert len(submitted) == 1 and not value.models.calls
    policy.register_on.assert_awaited_once()
    policy.reserve_attempt_on.assert_awaited_once()
    policy.succeed_attempt_on.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_original_attempt_remains_observation_only():
    receipt = ModelReceipt("attempt", "intent", "unknown", "unknown", "route", 1,
        "provider", "model", "native", "quota", error_code="model_outcome_unknown")
    worker = SimpleNamespace(life=None, models=SimpleNamespace(status=AsyncMock(return_value=receipt)))
    engine = planning_engine.PlanningEngine(worker)
    engine.waiting = AsyncMock()
    engine.mark_unknown = AsyncMock()
    engine.failure = AsyncMock()
    await engine.computation_failure(SimpleNamespace(), {"generation_key": "original"}, "material", NOW,
        error=AppError("model_outcome_unknown"))
    assert engine.waiting.await_args.args[2] == "planner_unknown"
    engine.mark_unknown.assert_awaited_once()
    engine.failure.assert_not_awaited()
