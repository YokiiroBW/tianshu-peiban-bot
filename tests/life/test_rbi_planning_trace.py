"""Planning traces legal triggers and keeps the original 429 budget."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest

from test_planner_execution import mark
from test_rbf_planning_recovery import make_life, run_plan, REST
from test_rbh_transport_recovery import advance_retry, budget_for
from test_rbi_models_fixture import record_outcome
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
    ("succeeded","recovery_model_outcome_changed"),
    ("explicit_cancel","recovery_model_cancelled"),
    ("request_digest","recovery_model_receipt_mismatch"),
])
async def test_planning_recovery_rejects_changed_original_facts(database,monkeypatch,change,code):
    from tianshu.life import rbi_model_recovery as guard
    value = await fixture(database,monkeypatch)
    await run_plan(value)
    attempt = attempt_for((await mark(database))["generation_key"])
    frozen = value.broker.requests[attempt]
    initial = "unknown" if change == "succeeded" else "failed"
    error = "transport_unknown" if initial == "unknown" else "upstream_http_429"
    ledger, observed = await record_outcome(database,value.broker,attempt,status=initial,error_code=error)
    if change == "succeeded":
        # Models may settle an earlier unknown with a late definite result.
        await ledger.finish(attempt,"succeeded",dispatch="submitted")
    elif change == "explicit_cancel":
        await ledger.request_cancel(attempt,reason="explicit_cancel")
    else:
        # Simulate changed frozen-submission evidence, without changing the
        # previously observed receipt or the Life request.
        async with database.transaction() as connection:
            await connection.execute("UPDATE models.submissions SET request_digest='different' WHERE attempt_id=%s",
                (attempt,))
    async with database.transaction() as connection:
        with pytest.raises(AppError,match=code):
            await guard.lock_original_on(connection,frozen,observed,value.data_generation)


async def test_consumer_interruption_keeps_raw_receipt_and_uses_computation_budget(database,monkeypatch):
    value = await fixture(database,monkeypatch)
    await run_plan(value)
    attempt = attempt_for((await mark(database))["generation_key"])
    _, original = await record_outcome(database,value.broker,attempt,status="cancelled",
        error_code="consumer_interrupted",cancellation_reason="consumer_interrupted")
    await run_plan(value)
    current = await mark(database)
    budget = await budget_for(database,value.worker.life.recovery_policy,attempt)
    assert current["reason"] == "planner_retry_wait" and current["repair_due_at"] is not None
    assert budget["computation_attempts"] == 1 and budget["connection_attempts"] == 0
    assert value.broker.receipts[attempt] == original


async def test_unconfirmed_planning_interruption_keeps_original_attempt(database,monkeypatch):
    value = await fixture(database,monkeypatch)
    await run_plan(value)
    attempt = attempt_for((await mark(database))["generation_key"])
    value.broker.receipts[attempt] = replace(value.broker.receipts[attempt],status="cancelled",dispatch="submitted",
        error_code="consumer_interrupted",cancellation_reason="consumer_interrupted",finished_at=datetime.now(UTC))
    await run_plan(value)
    current = await mark(database)
    assert current["reason"] == "planner_queued" and not current["generation_consumed"]
    assert current["repair_due_at"] is None and list(value.broker.requests) == [attempt]
    budget = await budget_for(database,value.worker.life.recovery_policy,attempt)
    assert budget["total_attempts"] == 1 and budget["connection_attempts"] == 0


async def test_429_exhausted_original_stays_stopped_until_real_new_intent_and_shared_cooldown(database,monkeypatch):
    value = await fixture(database,monkeypatch)
    originals = []
    for index in range(3):
        await run_plan(value)
        attempt = attempt_for((await mark(database))["generation_key"])
        originals.append(attempt)
        await record_outcome(database,value.broker,attempt,status="failed",error_code="upstream_http_429")
        await run_plan(value)
        current = await mark(database)
        async with database.transaction(read_only=True) as connection:
            timing = await (await connection.execute(
                "SELECT l.due_at,d.cooldown_until,now() AS observed_at FROM runtime.recovery_attempts a "
                "JOIN runtime.recovery_lineages l ON l.lineage_id=a.lineage_id "
                "JOIN runtime.recovery_dependencies d ON d.data_generation=l.data_generation "
                "AND d.dependency=l.dependency WHERE a.attempt_id=%s",(attempt,))).fetchone()
        assert timing["cooldown_until"] > timing["observed_at"]
        if index < 2:
            assert current["repair_due_at"] >= timing["due_at"] > timing["observed_at"]
            assert current["repair_due_at"] >= timing["cooldown_until"]
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
    # The exhausted attempt itself must leave the shared Runtime dependency
    # cooling, including when a real semantic change starts a new lineage.
    cooldown = timing["cooldown_until"]
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
