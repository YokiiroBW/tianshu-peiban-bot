"""Planning traces legal triggers and keeps the original 429 budget."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest

from test_planner_execution import mark
from test_rbf_planning_recovery import make_life, run_plan, REST
from test_rbh_transport_recovery import advance_retry, budget_for
from tianshu.conversation.generation import attempt_for
from tianshu.life import clock, planning_engine
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.errors import AppError


async def fixture(database, monkeypatch):
    monkeypatch.setattr(planning_engine,"awake_window",lambda _routine,now:(now,now+timedelta(hours=6)))
    monkeypatch.setattr(clock,"awake_and_due",lambda _routine,now:(True,now+timedelta(hours=6)))
    value = await make_life(database)
    await migrate(database,(Migration("rbi_activity_reconciliation",
        files("tianshu.life").joinpath("rbi_activity_reconciliation.sql").read_text(encoding="utf-8")),))
    return value


async def generation(database,key):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM life.plan_generations WHERE generation_key=%s",(key,))).fetchone()


async def test_notification_reuses_decision_then_due_review_records_new_trigger(database,monkeypatch):
    value = await fixture(database,monkeypatch)
    await run_plan(value)
    first = await mark(database)
    original = attempt_for(first["generation_key"])
    value.broker.complete(original,REST)
    await run_plan(value)
    applied = await mark(database)
    assert applied["review_after"] > datetime.now(UTC)
    trace = (await generation(database,first["generation_key"]))["decision_trace"]
    assert trace["trigger"] == "initial" and trace["model_request_digest"] == value.broker.receipts[original].request_digest
    await value.worker.life.signal(value.context,"capability-observation:rbi-harmless")
    await run_plan(value)
    assert list(value.broker.requests) == [original]
    assert (await mark(database))["review_after"] == applied["review_after"]
    past = datetime.now(UTC)-timedelta(seconds=1)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_watermarks SET review_after=%s,next_due_at=%s WHERE actor_id='actor'",(past,past))
    await run_plan(value)
    reviewed = await mark(database)
    assert reviewed["generation_key"] != first["generation_key"] and len(value.broker.requests) == 2
    trace = (await generation(database,reviewed["generation_key"]))["decision_trace"]
    assert trace["trigger"] == "scheduled_review" and trace["decision_review_at"] == past.isoformat()
    assert trace["semantic_fingerprint"] == trace["previous_decision_fingerprint"]


async def test_new_local_day_can_start_a_new_decision(database,monkeypatch):
    value = await fixture(database,monkeypatch)
    await run_plan(value)
    first = await mark(database)
    value.broker.complete(attempt_for(first["generation_key"]),REST)
    await run_plan(value)
    real_datetime = datetime
    class Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz)+timedelta(days=1)
    monkeypatch.setattr(planning_engine,"datetime",Tomorrow)
    await run_plan(value)
    current = await mark(database)
    assert current["generation_key"] != first["generation_key"] and len(value.broker.requests) == 2
    trace = (await generation(database,current["generation_key"]))["decision_trace"]
    assert trace["trigger"] == "local_day_changed"


@pytest.mark.parametrize("change,code",[
    ({"status":"succeeded"},"recovery_model_outcome_changed"),
    ({"cancellation_reason":"explicit_cancel"},"recovery_model_cancelled"),
    ({"request_digest":"different"},"recovery_model_receipt_mismatch"),
])
async def test_planning_recovery_rejects_changed_original_facts(database,monkeypatch,change,code):
    from tianshu.life import rbi_model_recovery as guard
    value = await fixture(database,monkeypatch)
    await run_plan(value)
    attempt = attempt_for((await mark(database))["generation_key"])
    frozen = value.broker.requests[attempt]
    receipt = replace(value.broker.receipts[attempt],status="failed",dispatch="submitted",
        error_code="upstream_http_429",finished_at=datetime.now(UTC))
    facts = {name:getattr(receipt,name) for name in ("attempt_id","intent_id","status","dispatch","error_code",
        "finished_at","data_generation","request_digest","cancellation_reason")}
    monkeypatch.setattr(guard,"lock_recovery_facts_on",AsyncMock(return_value={**facts,**change}))
    with pytest.raises(AppError,match=code):
        await guard.lock_original_on(None,frozen,receipt,value.data_generation)


async def test_consumer_interruption_keeps_raw_receipt_and_uses_computation_budget(database,monkeypatch):
    value = await fixture(database,monkeypatch)
    await run_plan(value)
    attempt = attempt_for((await mark(database))["generation_key"])
    value.broker.receipts[attempt] = replace(value.broker.receipts[attempt],status="cancelled",dispatch="submitted",
        error_code="consumer_interrupted",cancellation_reason="consumer_interrupted",finished_at=datetime.now(UTC))
    await run_plan(value)
    current = await mark(database)
    budget = await budget_for(database,value.worker.life.recovery_policy,attempt)
    assert current["reason"] == "planner_retry_wait" and current["repair_due_at"] is not None
    assert budget["computation_attempts"] == 1 and budget["connection_attempts"] == 0
    assert value.broker.receipts[attempt].status == "cancelled"
    assert value.broker.receipts[attempt].cancellation_reason == "consumer_interrupted"


async def test_429_exhausted_original_stays_stopped_until_real_new_intent_and_shared_cooldown(database,monkeypatch):
    value = await fixture(database,monkeypatch)
    originals = []
    for index in range(3):
        await run_plan(value)
        attempt = attempt_for((await mark(database))["generation_key"])
        originals.append(attempt)
        hold = datetime.now(UTC)+timedelta(minutes=3)
        value.broker.receipts[attempt] = replace(value.broker.receipts[attempt],status="failed",dispatch="submitted",
            error_code="upstream_http_429",finished_at=datetime.now(UTC),hold_until=hold)
        await run_plan(value)
        current = await mark(database)
        if index < 2:
            assert current["repair_due_at"] >= hold
            await advance_retry(database,"life.plan_watermarks","actor")
    exhausted = await mark(database)
    assert exhausted["reason"] == "planner_attempts_exhausted" and exhausted["repair_due_at"] is None
    budget = await budget_for(database,value.worker.life.recovery_policy,originals[-1])
    assert budget["total_attempts"] == budget["computation_attempts"] == 3
    assert budget["connection_attempts"] == 0 and not budget["can_retry"]
    await value.worker.life.signal(value.context,"capability-observation:rbi-429")
    await run_plan(value)
    current = await mark(database)
    assert current["reason"] == "planner_attempts_exhausted" and current["repair_due_at"] is None
    assert current["planned_watermark"] == current["change_watermark"]
    assert current["generation_key"] == exhausted["generation_key"] and list(value.broker.requests) == originals
    cooldown = datetime.now(UTC)+timedelta(minutes=4)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s,probe_token=NULL,"
            "probe_expires_at=NULL",(cooldown,))
    await value.worker.life.signal(value.context,"reconsider:rbi-new-intent",change={"reason":"new constraint"})
    await run_plan(value)
    cooling = await mark(database)
    assert cooling["reason"] == "recovery_dependency_cooling" and cooling["next_due_at"] >= cooldown
    assert list(value.broker.requests) == originals
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second'")
    await run_plan(value)
    replacement = await mark(database)
    assert replacement["generation_key"] != exhausted["generation_key"] and len(value.broker.requests) == 4
    latest = attempt_for(replacement["generation_key"])
    new_budget = await budget_for(database,value.worker.life.recovery_policy,latest)
    assert new_budget["total_attempts"] == 1 and new_budget["computation_attempts"] == 1
    assert all(value.broker.receipts[attempt].error_code == "upstream_http_429" for attempt in originals)
    trace = (await generation(database,replacement["generation_key"]))["decision_trace"]
    assert trace["trigger"] == "semantic_or_lifecycle_change"
