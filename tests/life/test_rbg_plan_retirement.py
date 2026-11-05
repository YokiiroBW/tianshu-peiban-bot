"""Obsolete plan pointers retire without rewriting unknown model evidence."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from unittest.mock import AsyncMock

import pytest

from test_rbf_planning_recovery import make_life, run_plan
from test_planner_execution import mark
from tianshu.conversation.generation import attempt_for
from tianshu.models.codec import request_from_wire
from tianshu.models.timing import waiting_budget
from tianshu.models.types import ModelReceipt
from tianshu.runtime.errors import AppError
from tianshu.life.planning_engine import PlanningEngine
from tianshu.life.plan_recovery import finished_plan_unknown
from tianshu.life.rbg_plan_operations import RBGPlanOperationsRecovery
from tianshu.runtime.serialization import wire


async def unacknowledged_original(value):
    original_submit = value.broker.submit
    value.broker.submit = AsyncMock(side_effect=TimeoutError("synthetic admission interruption"))
    with pytest.raises(TimeoutError):
        await run_plan(value)
    value.broker.submit = original_submit
    return await mark(value.worker.life.database)


async def test_terminal_null_pointer_is_retired_then_only_real_new_material_advances(database):
    value = await make_life(database)
    old = await unacknowledged_original(value)
    attempt = attempt_for(old["generation_key"])
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET state='superseded',recovery_state='cancelled',"
            "submission_confirmed=NULL,admission_started=NULL WHERE generation_key=%s",(old["generation_key"],))
        await connection.execute("UPDATE runtime.recovery_lineages SET cancelled=true WHERE lineage_id="
            "(SELECT recovery_lineage_id FROM life.plan_generations WHERE generation_key=%s)",(old["generation_key"],))
    await run_plan(value)
    retired = await mark(database)
    async with database.transaction(read_only=True) as connection:
        generation = await (await connection.execute(
            "SELECT * FROM life.plan_generations WHERE generation_key=%s",(old["generation_key"],))).fetchone()
        request = await (await connection.execute(
            "SELECT * FROM conversation.generation_requests WHERE material_key=%s",(old["generation_key"],))).fetchone()
    assert retired["generation_consumed"] and retired["generation_key"] == old["generation_key"]
    assert generation["retirement_model_state"] == "sealed_absent" and generation["retired_at"]
    assert generation["submission_confirmed"] is None and generation["admission_started"] is None
    assert request["state"] == "claimed" and not value.broker.requests
    await run_plan(value)
    assert not value.broker.requests and (await mark(database))["generation_key"] == old["generation_key"]
    await value.worker.life.signal(value.context,"reconsider:rbg",change={"reason":"a genuinely new request"})
    await run_plan(value)
    current = await mark(database)
    assert current["generation_key"] != old["generation_key"] and attempt not in value.broker.requests
    assert len(value.broker.requests) == 1


async def test_source_invalidated_original_can_be_sealed_without_reading_its_body(database):
    value = await make_life(database)
    old = await unacknowledged_original(value)
    async with database.transaction() as connection:
        await connection.execute("UPDATE conversation.generation_requests SET state='invalidated',request=NULL "
            "WHERE material_key=%s",(old["generation_key"],))
    value.worker.generations.observe_request = AsyncMock(side_effect=AssertionError("withdrawn body read"))
    await run_plan(value)
    assert (await mark(database))["generation_consumed"]
    async with database.transaction(read_only=True) as connection:
        saved = await (await connection.execute("SELECT retirement_reason,retirement_model_state FROM life.plan_generations "
            "WHERE generation_key=%s",(old["generation_key"],))).fetchone()
    assert saved == {"retirement_reason":"source_withdrawn","retirement_model_state":"sealed_absent"}
    value.worker.generations.observe_request.assert_not_awaited()


async def test_current_manager_retires_old_plan_when_old_routine_context_cannot_refresh(database):
    value = await make_life(database)
    old = await unacknowledged_original(value)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET state='superseded' WHERE generation_key=%s",
            (old["generation_key"],))
    current = replace(value.context,access_epoch=2)
    await value.worker.life.signal(current,"reconsider:current-manager",change={"reason":"current authority"})
    async def check(context,*_refs):
        if context.access_epoch != 2:
            raise AppError("access_changed",status=409)
    value.worker.life.identity.check = AsyncMock(side_effect=check)
    value.worker.life.identity.refresh_scheduled_context = AsyncMock(
        side_effect=AppError("scheduled_scope_unavailable",status=403))
    await run_plan(value)
    assert (await mark(database))["generation_consumed"]
    value.worker.life.identity.refresh_scheduled_context.assert_not_awaited()


async def test_expired_queued_plan_is_retired_without_new_model_execution(database,monkeypatch):
    from tianshu.life import planning_engine

    value = await make_life(database)
    await run_plan(value)
    old = await mark(database)
    attempt = attempt_for(old["generation_key"])
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT request FROM conversation.generation_requests WHERE material_key=%s",
            (old["generation_key"],))).fetchone()
    deadline = waiting_budget(request_from_wire(row["request"])).deadline_at
    future = deadline+timedelta(seconds=1)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return future.astimezone(tz) if tz else future.replace(tzinfo=None)
    monkeypatch.setattr(planning_engine,"datetime",Clock)
    await run_plan(value)
    current = await mark(database)
    assert current["generation_key"] == old["generation_key"] and current["generation_consumed"]
    assert list(value.broker.requests) == [attempt] and value.broker.receipts[attempt].error_code == "plan_expired"


async def test_concurrent_wakes_retire_once_and_new_material_has_one_current_attempt(database):
    value = await make_life(database)
    old = await unacknowledged_original(value)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET state='superseded' WHERE generation_key=%s",
            (old["generation_key"],))
    await asyncio.gather(run_plan(value),run_plan(value))
    assert (await mark(database))["generation_consumed"]
    await value.worker.life.signal(value.context,"reconsider:concurrent",change={"reason":"new material"})
    await asyncio.gather(run_plan(value),run_plan(value))
    current = await mark(database)
    assert current["generation_key"] != old["generation_key"] and len(value.broker.requests) == 1


async def test_seal_committed_before_pointer_change_is_resumed_after_restart(database):
    value = await make_life(database)
    await run_plan(value)
    old = await mark(database)
    attempt = attempt_for(old["generation_key"])
    async with database.transaction() as connection:
        generation = await (await connection.execute("UPDATE life.plan_generations SET retirement_reason='superseded',"
            "retirement_requested_at=now() WHERE generation_key=%s RETURNING *",(old["generation_key"],))).fetchone()
    await value.broker.seal_admission(attempt,lineage_id=generation["recovery_lineage_id"],
        data_generation=value.data_generation,context=value.context,original_context=value.context,reason="superseded")
    await run_plan(value)
    current = await mark(database)
    assert current["generation_key"] == old["generation_key"] and current["generation_consumed"]
    assert list(value.broker.requests) == [attempt]
    assert value.broker.receipts[attempt].status == "cancelled"


async def test_explicit_cancel_settles_original_and_does_not_schedule_replacement(database):
    value = await make_life(database)
    await run_plan(value)
    old = await mark(database)
    attempt = attempt_for(old["generation_key"])
    await value.broker.cancel(attempt,reason="explicit_cancel")
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET state='superseded' WHERE generation_key=%s",
            (old["generation_key"],))
    await run_plan(value)
    current = await mark(database)
    assert current["generation_consumed"] and current["reason"] == "planner_cancelled"
    assert current["last_error"] == "explicit_cancel" and list(value.broker.requests) == [attempt]


@pytest.mark.parametrize("max_attempts",[1,3])
async def test_explicit_cancel_stops_recovery_without_rewriting_original_unknown(database,max_attempts):
    value = await make_life(database)
    await run_plan(value)
    original = await mark(database)
    attempt = attempt_for(original["generation_key"])
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_lineages SET max_attempts=%s",(max_attempts,))
    value.broker.receipts[attempt] = replace(value.broker.receipts[attempt],status="unknown",dispatch="unknown",
        error_code="timeout_unknown",finished_at=datetime.now(UTC))
    await run_plan(value)
    value.broker.receipts[attempt] = replace(value.broker.receipts[attempt],cancellation_reason="explicit_cancel")
    if max_attempts > 1:
        async with database.transaction() as connection:
            await connection.execute("UPDATE life.plan_watermarks SET repair_due_at=now()-interval '1 second' "
                "WHERE actor_id='actor'")
    await run_plan(value)
    async with database.transaction(read_only=True) as connection:
        outcome = await (await connection.execute(
            "SELECT a.status,l.cancelled FROM runtime.recovery_attempts a JOIN runtime.recovery_lineages l "
            "ON l.lineage_id=a.lineage_id WHERE a.attempt_id=%s",(attempt,))).fetchone()
    assert outcome == {"status":"unknown","cancelled":True}
    assert (await mark(database))["last_error"] == "explicit_cancel"
    assert value.broker.receipts[attempt].status == "unknown" and list(value.broker.requests) == [attempt]


def test_finished_plan_unknown_requires_a_timezone_offset():
    class NoOffset(tzinfo):
        def utcoffset(self, value):
            return None

    receipt = ModelReceipt("attempt","intent","unknown","unknown","route",1,"provider","model","protocol","quota",
        finished_at=datetime(2026,1,1,tzinfo=NoOffset()),data_generation="generation")
    assert not finished_plan_unknown(receipt,"attempt","generation")
    assert finished_plan_unknown(replace(receipt,finished_at=datetime.now(UTC)),"attempt","generation")


async def test_stage_timeout_only_wakes_the_same_saved_original(database):
    value = await make_life(database)
    await run_plan(value)
    before = await mark(database)
    attempt = attempt_for(before["generation_key"])
    original = value.broker.receipts[attempt]
    engine = PlanningEngine(value.worker,data_generation=value.data_generation)
    await engine.defer_timeout("actor")
    after = await mark(database)
    assert after["generation_key"] == before["generation_key"] and not after["generation_consumed"]
    assert value.broker.receipts[attempt] == original and list(value.broker.requests) == [attempt]


async def test_operations_classifies_and_enqueues_the_original_without_declaring_success(database):
    value = await make_life(database)
    old = await unacknowledged_original(value)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET state='superseded',recovery_state='cancelled',"
            "submission_confirmed=NULL,admission_started=NULL WHERE generation_key=%s",(old["generation_key"],))
    guard = AsyncMock()
    adapter = RBGPlanOperationsRecovery(value.worker,guard)
    target = {"kind":"life.plan","actor_id":"actor","id":old["generation_key"],"revision":old["revision"]}
    facts = await adapter.inspect(None,target)
    assert facts["eligible"] and facts["stage"] == "retire_original" and facts["model_observation"] == "missing"
    accepted = await adapter.invoke({"owner_token":"owner"},target,facts,"ops-attempt")
    assert accepted["state"] == "accepted"
    assert (await adapter.observe(None,target,facts,"ops-attempt"))["state"] == "pending"
    await run_plan(value)
    assert (await adapter.observe(None,target,facts,"ops-attempt"))["state"] == "verified"


async def test_retired_missing_receipt_is_observed_at_low_frequency_without_replaying(database):
    value = await make_life(database)
    old = await unacknowledged_original(value)
    attempt = attempt_for(old["generation_key"])
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET state='superseded' WHERE generation_key=%s",
            (old["generation_key"],))
    await run_plan(value)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET retirement_observe_after=now()-interval '1 second' "
            "WHERE generation_key=%s",(old["generation_key"],))
    adapter = RBGPlanOperationsRecovery(value.worker,AsyncMock())
    value.broker.status = AsyncMock(side_effect=AssertionError("historical missing receipt should use sealed inspection"))
    candidates = await adapter.candidates()
    retired = next(item for item in candidates if item["target"]["kind"] == "life.plan.retired")
    assert retired["eligible"] and not retired["business_blocked"] and retired["notify_on_change"]
    target = retired["target"]
    facts = await adapter.inspect(None,target)
    assert facts["model_observation"] == "missing" and facts["original_state"] == "unknown"
    await adapter.invoke({"owner_token":"owner"},target,facts,"ops-observe")
    async with database.transaction(read_only=True) as connection:
        observed = await (await connection.execute("SELECT retirement_original_status,retirement_observed_at,"
            "retirement_observation_count,retirement_observe_after FROM life.plan_generations WHERE generation_key=%s",
            (old["generation_key"],))).fetchone()
    assert observed["retirement_original_status"] is None and observed["retirement_observed_at"]
    assert observed["retirement_observation_count"] == 1
    assert observed["retirement_observe_after"] >= observed["retirement_observed_at"]+timedelta(minutes=19)
    assert (await adapter.observe(None,target,facts,"ops-observe"))["reason"] == "historical_observation_not_due"
    assert await adapter.receipt_changed(attempt,value.data_generation)
    value.broker.receipts[attempt] = ModelReceipt(attempt,"original-intent","succeeded","submitted",
        "route",1,"provider","model","openai.chat","quota",data_generation=value.data_generation)
    result = await adapter.observe({"owner_token":"owner"},target,facts,"ops-observe")
    assert result == {"state":"verified","attempt_id":attempt,"original_status":"succeeded"}
    assert not value.broker.requests
    value.broker.status.assert_not_awaited()


async def test_periodic_retired_observation_is_bounded_concurrent_and_retries_failures(database):
    value = await make_life(database)
    old = await unacknowledged_original(value)
    attempt = attempt_for(old["generation_key"])
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET state='superseded' WHERE generation_key=%s",
            (old["generation_key"],))
    await run_plan(value)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_generations SET retirement_observe_after=now()-interval '1 second' "
            "WHERE generation_key=%s",(old["generation_key"],))
    adapter = RBGPlanOperationsRecovery(value.worker,AsyncMock())
    inspect = value.broker.inspect_admission_retirement
    value.broker.inspect_admission_retirement = AsyncMock(wraps=inspect)
    results = await asyncio.gather(adapter.observe_due_retirements(limit=1),adapter.observe_due_retirements(limit=1))
    assert sum(result["observed"] for result in results) == 1
    assert value.broker.inspect_admission_retirement.await_count == 1
    assert (await adapter.observe_due_retirements())["observed"] == 0
    async with database.transaction() as connection:
        before = await (await connection.execute("UPDATE life.plan_generations SET "
            "retirement_observe_after=now()-interval '1 second' WHERE generation_key=%s "
            "RETURNING retirement_observed_at",(old["generation_key"],))).fetchone()
    value.broker.inspect_admission_retirement = AsyncMock(side_effect=AppError("model_unavailable",status=503))
    assert (await adapter.observe_due_retirements())["unavailable"] == 1
    async with database.transaction(read_only=True) as connection:
        failed = await (await connection.execute("SELECT retirement_original_status,retirement_observed_at,"
            "retirement_observe_after FROM life.plan_generations WHERE generation_key=%s",
            (old["generation_key"],))).fetchone()
    assert failed["retirement_original_status"] is None and failed["retirement_observed_at"] == before["retirement_observed_at"]
    assert failed["retirement_observe_after"] > datetime.now(UTC)+timedelta(minutes=19)
    value.broker.inspect_admission_retirement = inspect
    value.broker.receipts[attempt] = ModelReceipt(attempt,"original-intent","succeeded","submitted",
        "route",1,"provider","model","openai.chat","quota",data_generation=value.data_generation)
    assert await adapter.receipt_changed(attempt,value.data_generation)
    result = await adapter.observe_due_retirements()
    assert result["changes"] == [{"actor_id":"actor","generation_key":old["generation_key"],
        "attempt_id":attempt,"previous_status":None,"status":"succeeded"}]
    assert result["observed"] == 1 and not value.broker.requests


async def test_preparation_candidate_requires_due_semantic_work_and_excludes_disabled(database,monkeypatch):
    from tianshu.life import rbg_plan_operations

    midday = datetime.now(UTC).replace(hour=12,minute=0,second=0,microsecond=0)
    class Midday(datetime):
        @classmethod
        def now(cls,tz=None):
            return midday.astimezone(tz) if tz else midday.replace(tzinfo=None)
    monkeypatch.setattr(rbg_plan_operations,"datetime",Midday)
    value = await make_life(database)
    await value.worker.life.signal(value.context,"reconsider:prepare",change={"reason":"actual request"})
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_watermarks SET next_due_at=LEAST(now()-interval '1 second',%s) WHERE actor_id='actor'",
            (midday-timedelta(minutes=1),))
    adapter = RBGPlanOperationsRecovery(value.worker,AsyncMock())
    candidates = await adapter.candidates()
    assert len(candidates) == 1 and candidates[0]["target"]["kind"] == "life.plan.prepare"
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.routines SET enabled=false WHERE actor_id='actor'")
    assert await adapter.candidates() == []
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.routines SET enabled=true WHERE actor_id='actor'")
        await connection.execute("UPDATE life.plan_watermarks SET next_due_at=now()+interval '1 day' WHERE actor_id='actor'")
    assert await adapter.candidates() == []
