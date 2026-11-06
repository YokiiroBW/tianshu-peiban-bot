"""Life consumes Runtime's connection budget without replacing original receipts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from historical_support import migrations
from test_planner_execution import mark
from test_pure_unknown_recovery import setup_generation
from test_rbf_planning_recovery import make_life, run_plan
from test_rbi_models_fixture import record_outcome
from tianshu.conversation.generation import attempt_for
from tianshu.life.rbg_plan_operations import RBGPlanOperationsRecovery
from tianshu.life.planning_engine import PlanningEngine
from tianshu.models.ledger import receipt as model_receipt
from tianshu.runtime.errors import AppError


async def not_sent(database, broker, attempt_id, *, headers_sent=None):
    return await record_outcome(database,broker,attempt_id,status="failed",
        error_code="transport_not_sent",dispatch="not_started",headers_sent=headers_sent)


def capture_reconciliation(monkeypatch):
    """Report safe domain reasons if a restored original is not scheduled."""
    from tianshu.life import rbh_transport_recovery

    original = rbh_transport_recovery._reconcile_one
    observed = []
    async def capture(worker,candidate):
        try:
            scheduled,reason = await original(worker,candidate)
        except AppError as error:
            observed.append((candidate["kind"],error.code))
            raise
        observed.append((candidate["kind"],reason))
        return scheduled,reason
    monkeypatch.setattr(rbh_transport_recovery,"_reconcile_one",capture)
    return observed


async def advance_retry(database, table, identifier):
    past = datetime.now(UTC)-timedelta(seconds=1)
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s",(past,))
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=%s,probe_token=NULL,"
            "probe_expires_at=NULL",(past,))
        if table == "life.plan_watermarks":
            await connection.execute("UPDATE life.plan_watermarks SET repair_due_at=%s WHERE actor_id=%s",(past,identifier))
        else:
            await connection.execute(f"UPDATE {table} SET generation_retry_due_at=%s WHERE id=%s",(past,identifier))


async def budget_for(database, policy, attempt_id):
    async with database.transaction() as connection:
        lineage = await (await connection.execute("SELECT l.lineage_id,l.owner,l.current_version FROM "
            "runtime.recovery_attempts a JOIN runtime.recovery_lineages l ON l.lineage_id=a.lineage_id "
            "WHERE a.attempt_id=%s",(attempt_id,))).fetchone()
        return await policy.computation_budget_on(connection,lineage["lineage_id"],owner=lineage["owner"],
            expected_version=lineage["current_version"])


async def test_four_confirmed_plan_connect_failures_keep_the_same_three_computation_budget(database,monkeypatch):
    from tianshu.life import clock, planning_engine
    monkeypatch.setattr(planning_engine,"awake_window",lambda _routine,now:(now,now+timedelta(hours=6)))
    monkeypatch.setattr(clock,"awake_and_due",lambda _routine,now:(True,now+timedelta(hours=6)))
    value = await make_life(database)
    originals = []
    for index in range(4):
        await run_plan(value)
        current = await mark(database)
        attempt = attempt_for(current["generation_key"])
        originals.append(attempt)
        await not_sent(database,value.broker,attempt)
        await run_plan(value)
        current = await mark(database)
        assert current["reason"] == "planner_retry_wait" and current["repair_due_at"] is not None, {
            "index":index,"reason":current["reason"],"last_error":current["last_error"],
            "budget":await budget_for(database,value.worker.life.recovery_policy,attempt)}
        if index < 3:
            await advance_retry(database,"life.plan_watermarks","actor")
    budget = await budget_for(database,value.worker.life.recovery_policy,originals[-1])
    assert budget["total_attempts"] == budget["connection_attempts"] == 4
    assert budget["computation_attempts"] == 0 and budget["max_computation_attempts"] == 3
    assert budget["can_retry"] and len(set(originals)) == len(value.broker.requests) == 4
    assert all(value.broker.receipts[attempt].status == "failed" for attempt in originals)


@pytest.mark.parametrize("kind",["diary","chapter"])
async def test_authored_connect_failures_keep_original_recipe_and_budget(database,kind):
    value,attempt,generate,current = await setup_generation(database,kind)
    table = "life.diaries" if kind == "diary" else "life.chapters"
    originals = []
    frozen_messages = None
    for index in range(4):
        await generate(attempt)
        originals.append(attempt)
        request = value.models.requests[attempt]
        frozen_messages = frozen_messages or request.messages
        assert request.messages == frozen_messages
        await not_sent(database,value.models,attempt)
        await generate(attempt)
        row = await current()
        assert row["generation_id"] == attempt and row["generation_retry_due_at"] is not None, {
            "kind":kind,"index":index,"state":row["state"],"reason":row["reason"],
            "budget":await budget_for(database,value.diaries.recovery.policy,attempt)}
        if index < 3:
            await advance_retry(database,table,row["id"])
            await generate(attempt)
            next_id = (await current())["generation_id"]
            assert next_id != attempt
            attempt = next_id
    budget = await budget_for(database,value.diaries.recovery.policy,originals[-1])
    assert budget["total_attempts"] == budget["connection_attempts"] == 4
    assert budget["computation_attempts"] == 0 and budget["max_computation_attempts"] == 3
    assert all(value.models.receipts[attempt].status == "failed" for attempt in originals)


async def test_mismatched_original_intent_cannot_be_classified(database):
    value = await make_life(database)
    await run_plan(value)
    attempt = attempt_for((await mark(database))["generation_key"])
    _, original = await not_sent(database,value.broker,attempt)
    value.broker.receipts[attempt] = replace(original,intent_id="wrong-intent")
    with pytest.raises(AppError,match="recovery_model_receipt_mismatch"):
        await run_plan(value)
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s",
            (attempt,))).fetchone()
    assert row["status"] == "reserved" and len(value.broker.requests) == 1


@pytest.mark.parametrize("kind",["diary","chapter"])
async def test_withdrawn_authored_sources_preserve_failure_without_classifying_or_retrying(database,kind):
    value,attempt,generate,_current = await setup_generation(database,kind)
    await generate(attempt)
    _, receipt = await not_sent(database,value.models,attempt)
    policy = value.diaries.recovery.policy
    policy.note_not_sent_on = AsyncMock(wraps=policy.note_not_sent_on)
    value.life.sources.validate_persistence = AsyncMock(return_value=False)
    async with database.transaction() as connection:
        state,due = await value.diaries.recovery.observe_on(connection,attempt,"failed",receipt.error_code,
            model_receipt=receipt)
    assert state == "ineligible" and due is None
    policy.note_not_sent_on.assert_not_awaited()
    async with database.transaction(read_only=True) as connection:
        original = await (await connection.execute(
            "SELECT a.status,a.error_code,l.due_at FROM runtime.recovery_attempts a "
            "JOIN runtime.recovery_lineages l ON l.lineage_id=a.lineage_id WHERE a.attempt_id=%s",
            (attempt,))).fetchone()
    assert original == {"status":"failed","error_code":"transport_not_sent","due_at":None}
    assert value.models.receipts[attempt] == receipt
    assert list(value.models.requests) == [attempt]


async def test_withdrawn_plan_source_keeps_physical_failure_and_stops_lineage(database):
    value = await make_life(database)
    await run_plan(value)
    saved = await mark(database)
    attempt = attempt_for(saved["generation_key"])
    frozen = await value.worker.generations.observe_request(attempt,observer=value.context)
    await not_sent(database,value.broker,attempt)
    value.worker.life.sources.validate_persistence = AsyncMock(return_value=False)
    engine = PlanningEngine(value.worker,data_generation=value.data_generation)
    await engine.computation_failure(value.context,saved,saved["generation_material"],datetime.now(UTC),frozen=frozen)
    async with database.transaction(read_only=True) as connection:
        original = await (await connection.execute("SELECT a.status,l.cancelled FROM runtime.recovery_attempts a "
            "JOIN runtime.recovery_lineages l ON l.lineage_id=a.lineage_id WHERE a.attempt_id=%s",(attempt,))).fetchone()
    assert original == {"status":"failed","cancelled":True}
    current = await mark(database)
    assert current["generation_consumed"] and current["last_error"] == "source_unavailable"
    assert current["repair_due_at"] is None and list(value.broker.requests) == [attempt]


async def test_contradictory_send_evidence_still_exhausts_real_computation_budget(database,monkeypatch):
    from tianshu.life import clock, planning_engine
    monkeypatch.setattr(planning_engine,"awake_window",lambda _routine,now:(now,now+timedelta(hours=6)))
    monkeypatch.setattr(clock,"awake_and_due",lambda _routine,now:(True,now+timedelta(hours=6)))
    value = await make_life(database)
    originals = []
    for index in range(3):
        await run_plan(value)
        attempt = attempt_for((await mark(database))["generation_key"])
        originals.append(attempt)
        await not_sent(database,value.broker,attempt,headers_sent=2.0)
        await run_plan(value)
        if index < 2:
            await advance_retry(database,"life.plan_watermarks","actor")
    budget = await budget_for(database,value.worker.life.recovery_policy,originals[-1])
    assert budget["total_attempts"] == budget["computation_attempts"] == 3
    assert budget["connection_attempts"] == 0 and not budget["can_retry"]
    assert (await mark(database))["reason"] == "planner_attempts_exhausted"
    assert len(value.broker.requests) == 3


@pytest.mark.parametrize("cancel_latest",[False,True])
async def test_confirmed_history_reopens_only_the_current_original_and_cancel_stays_stopped(database,monkeypatch,cancel_latest):
    from tianshu.life import clock, planning_engine
    monkeypatch.setattr(planning_engine,"awake_window",lambda _routine,now:(now,now+timedelta(hours=6)))
    monkeypatch.setattr(clock,"awake_and_due",lambda _routine,now:(True,now+timedelta(hours=6)))
    value = await make_life(database)
    # The reconciler scans plan, diary and chapter originals in one query.
    # Install the same authored-generation columns that the product includes.
    await migrations(database,diary=True)
    observations = capture_reconciliation(monkeypatch)
    policy = value.worker.life.recovery_policy
    note = policy.note_not_sent_on
    async def old_budget(connection,attempt_id,*,owner,expected_version,model_receipt):
        row = await (await connection.execute("SELECT g.recovery_lineage_id FROM life.plan_generations g "
            "JOIN conversation.generation_requests r ON r.material_key=g.generation_key "
            "WHERE r.attempt_id=%s",(attempt_id,))).fetchone()
        return await policy.computation_budget_on(connection,row["recovery_lineage_id"],
            owner=owner,expected_version=expected_version)
    monkeypatch.setattr(policy,"note_not_sent_on",old_budget)
    attempts, ledgers = [], []
    for index in range(3):
        await run_plan(value)
        attempt = attempt_for((await mark(database))["generation_key"])
        attempts.append(attempt)
        ledger, _ = await not_sent(database,value.broker,attempt)
        ledgers.append(ledger)
        await run_plan(value)
        if index < 2:
            await advance_retry(database,"life.plan_watermarks","actor")
    exhausted = await mark(database)
    assert exhausted["reason"] == "planner_attempts_exhausted" and exhausted["repair_due_at"] is None
    monkeypatch.setattr(policy,"note_not_sent_on",note)
    adapter = RBGPlanOperationsRecovery(value.worker,None)
    if cancel_latest:
        await ledgers[-1].request_cancel(attempts[-1],reason="explicit_cancel")
        value.broker.receipts[attempts[-1]] = model_receipt(await ledgers[-1].get(attempts[-1]))
        stopped = await adapter.reconcile_transport_waits(limit=10)
        assert stopped["scheduled"] == 0 and (await mark(database))["repair_due_at"] is None, (stopped,observations)
        assert ("plan","original_not_retryable") in observations, (stopped,observations)
        assert (await ledgers[-1].get(attempts[-1]))["cancellation_reason"] == "explicit_cancel"
        return
    observations.clear()
    result = await adapter.reconcile_transport_waits(limit=10,force=True)
    restored = await mark(database)
    assert result["scheduled"] == 1 and restored["generation_key"] == exhausted["generation_key"], (result,observations)
    assert restored["repair_due_at"] is not None and list(value.broker.requests) == attempts
    budget = await budget_for(database,policy,attempts[-1])
    assert budget["total_attempts"] == budget["connection_attempts"] == 3
    assert all(value.broker.receipts[attempt].status == "failed" for attempt in attempts)


async def test_history_rebinds_original_diary_only_when_closed_day_is_unchanged(database,monkeypatch):
    value,attempt,generate,current = await setup_generation(database,"diary")
    observations = capture_reconciliation(monkeypatch)
    policy = value.diaries.recovery.policy
    note = policy.note_not_sent_on
    async def old_budget(connection,attempt_id,*,owner,expected_version,model_receipt):
        row = await (await connection.execute("SELECT recovery_lineage_id FROM life.generation_recipes "
            "WHERE generation_id=%s",(attempt_id,))).fetchone()
        return await policy.computation_budget_on(connection,row["recovery_lineage_id"],
            owner=owner,expected_version=expected_version)
    monkeypatch.setattr(policy,"note_not_sent_on",old_budget)
    attempts = []
    for index in range(3):
        await generate(attempt)
        attempts.append(attempt)
        await not_sent(database,value.models,attempt)
        await generate(attempt)
        row = await current()
        if index < 2:
            await advance_retry(database,"life.diaries",row["id"])
            await generate(attempt)
            attempt = (await current())["generation_id"]
    assert row["state"] == "failed" and row["generation_id"] == attempts[-1]
    monkeypatch.setattr(policy,"note_not_sent_on",note)
    value.life.bind_recovery_policy(policy)
    worker = SimpleNamespace(life=value.life,models=value.models,generations=value.diaries.generations,writing=None)
    adapter = RBGPlanOperationsRecovery(worker,None)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diary_days SET revision=revision+1 WHERE actor_id='actor'")
    stopped = await adapter.reconcile_transport_waits(limit=16)
    assert stopped["scheduled"] == 0 and (await current())["state"] == "failed", (stopped,observations)
    assert ("diary","diary_material_changed") in observations, (stopped,observations)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diary_days SET revision=revision-1 WHERE actor_id='actor'")
    observations.clear()
    result = await adapter.reconcile_transport_waits(limit=16,force=True)
    resumed = await current()
    async with database.transaction(read_only=True) as connection:
        day = await (await connection.execute("SELECT generation_id,generation_revision,revision FROM life.diary_days "
            "WHERE actor_id='actor'")).fetchone()
    assert result["scheduled"] == 1 and resumed["generation_id"] == attempts[-1], (result,observations)
    assert resumed["state"] == "unknown" and resumed["generation_retry_due_at"] is not None
    assert day["generation_id"] == attempts[-1] and day["generation_revision"] == day["revision"]
    assert list(value.models.requests) == attempts
