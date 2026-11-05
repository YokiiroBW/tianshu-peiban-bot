"""A retired plan cannot gain a late Models admission or rewrite its receipt."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from aiohttp import web

from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import RoutePlan
from tianshu.models.client import ModelClient
from tianshu.models.http import create_broker_app
from tianshu.models.rbg_admission import plan_owner
from tianshu.models.types import ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryPolicy
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


@pytest_asyncio.fixture
async def scenario(database):
    await migrate(database,(Migration("rbg_model_admission_test",
        files("tianshu.models").joinpath("rbg_admission_retirement.sql").read_text()),))
    context = TrustedContext("manager","actor",None,None,("actor-scope",),("actor-scope",),1,
        OriginKind.ACTOR,frozenset({"life.manage"}))
    async with database.transaction(read_only=True) as connection:
        generation = (await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true")).fetchone())["id"]
    request = ModelRequest("plan-intent","plan-attempt","life.plan",ServiceClass.ELASTIC,context,
        RequestBudget(datetime.now(UTC)+timedelta(minutes=10)),(),
        data_generation=generation,execution_timeout_seconds=30)
    plan = RoutePlan("route",1,"provider","model","openai.chat","","","quota",frozenset({"text"}),{})
    policy = RecoveryPolicy()
    owner = plan_owner(context)
    async with database.transaction() as connection:
        lineage = await policy.register_on(connection,domain="life",subject_id="actor",owner=owner,
            material_id="life-plan:fixture",material_digest="material",dependency="models:quota:quota",
            current_version="version",max_attempts=3,data_generation=generation,recovery_kind="pure_computation")
        await policy.reserve_attempt_on(connection,lineage["lineage_id"],owner=owner,
            expected_version="version",attempt_id=request.attempt_id,request_digest="digest",
            route="route",original_state="none")
    authority = AsyncMock()
    broker = ModelBroker(database,None,None,validate_submission=authority)
    return {"database":database,"broker":broker,"request":request,"context":context,
        "generation":generation,"lineage":lineage["lineage_id"],"plan":plan,"authority":authority}


async def seal(value, *, context=None, reason="superseded"):
    return await value["broker"].seal_admission(value["request"].attempt_id,lineage_id=value["lineage"],
        data_generation=value["generation"],context=context or value["context"],original_context=value["context"],reason=reason)


async def test_absent_original_is_sealed_without_inventing_a_receipt_and_late_create_is_blocked(scenario):
    value = scenario
    result = await seal(value)
    assert result["state"] == "sealed_absent" and result["status"] is None
    value["authority"].assert_awaited_once_with(value["context"],())
    with pytest.raises(AppError,match="model_admission_retired"):
        await value["broker"].ledger.create(value["request"],value["plan"],{"tokens":2},frozen=value["request"])
    assert await value["broker"].status(value["request"].attempt_id,phase="pre_submit") is None
    assert (await seal(value))["state"] == "sealed_absent"


async def test_queued_original_is_stopped_before_start_without_explicit_cancel(scenario):
    value = scenario
    ledger = value["broker"].ledger
    await ledger.create(value["request"],value["plan"],{"tokens":2},frozen=value["request"])
    result = await seal(value)
    original = await ledger.get(value["request"].attempt_id)
    assert result["state"] == "sealed_queued" and original["status"] == "cancelled"
    assert original["dispatch"] == "not_started" and original["cancellation_reason"] is None
    with pytest.raises(AppError,match="attempt_cancelled_before_start"):
        await ledger.started(value["request"].attempt_id,None,deadline_at=datetime.now(UTC)+timedelta(seconds=30))
    observed = await value["broker"].inspect_admission_retirement(value["request"].attempt_id,
        lineage_id=value["lineage"],data_generation=value["generation"],context=value["context"],
        original_context=value["context"])
    assert observed["state"] == "sealed" and observed["status"] == "cancelled"


@pytest.mark.parametrize("status",["running","unknown"])
async def test_already_submitted_or_unknown_receipt_is_never_relabelled(scenario,status):
    value = scenario
    ledger = value["broker"].ledger
    await ledger.create(value["request"],value["plan"],{"tokens":2},frozen=value["request"])
    await ledger.started(value["request"].attempt_id,None,deadline_at=datetime.now(UTC)+timedelta(seconds=30))
    if status == "unknown":
        await ledger.finish(value["request"].attempt_id,"unknown",error_code="transport_unknown")
    before = await ledger.get(value["request"].attempt_id)
    assert (await seal(value))["state"] == "sealed_existing"
    after = await ledger.get(value["request"].attempt_id)
    assert {key:after[key] for key in ("status","dispatch","result","error_code","revision")} == {
        key:before[key] for key in ("status","dispatch","result","error_code","revision")}


async def test_concurrent_retirement_and_create_leave_no_dispatchable_original(scenario):
    value = scenario
    async def create():
        try:
            return await value["broker"].ledger.create(value["request"],value["plan"],{"tokens":2},
                frozen=value["request"])
        except AppError as error:
            assert error.code == "model_admission_retired"
            return None
    result,_ = await asyncio.gather(create(),seal(value))
    row = await value["broker"].ledger.get(value["request"].attempt_id)
    assert row is None or (row["status"] == "cancelled" and row["dispatch"] == "not_started")
    assert result is None or result[1] is True
    with pytest.raises(AppError,match="attempt_cancelled_before_start"):
        await value["broker"].ledger.started(value["request"].attempt_id,None,
            deadline_at=datetime.now(UTC)+timedelta(seconds=30))


async def test_changed_observation_keeps_an_original_that_started_before_conditional_seal(scenario):
    value = scenario
    ledger = value["broker"].ledger
    await ledger.create(value["request"],value["plan"],{"tokens":2},frozen=value["request"])
    await ledger.started(value["request"].attempt_id,None,deadline_at=datetime.now(UTC)+timedelta(seconds=30))
    result = await value["broker"].seal_admission(value["request"].attempt_id,lineage_id=value["lineage"],
        data_generation=value["generation"],context=value["context"],original_context=value["context"],
        reason="superseded",expected_status="absent")
    assert result["state"] == "observation_changed" and result["status"] == "running"
    observed = await value["broker"].inspect_admission_retirement(value["request"].attempt_id,
        lineage_id=value["lineage"],data_generation=value["generation"],context=value["context"],
        original_context=value["context"])
    assert observed["state"] == "unsealed" and observed["status"] == "running"


async def test_other_actor_old_generation_and_explicit_cancel_are_not_reinterpreted(scenario):
    value = scenario
    with pytest.raises(AppError,match="forbidden"):
        await seal(value,context=replace(value["context"],actor_id="another-actor"))
    with pytest.raises(AppError,match="forbidden"):
        await seal(value,context=replace(value["context"],principal_id="another-principal"))
    with pytest.raises(AppError,match="model_admission_owner_unproven"):
        await value["broker"].seal_admission(value["request"].attempt_id,lineage_id=value["lineage"],
            data_generation=value["generation"],context=value["context"],
            original_context=replace(value["context"],scope_ids=("other-scope",)),reason="superseded")
    with pytest.raises(AppError,match="data_generation_expired"):
        await value["broker"].seal_admission(value["request"].attempt_id,lineage_id=value["lineage"],
            data_generation=str(uuid4()),context=value["context"],original_context=value["context"],reason="superseded")
    await value["broker"].ledger.request_cancel(value["request"].attempt_id,reason="explicit_cancel")
    result = await seal(value)
    assert result["state"] == "explicit_cancelled" and result["explicit_cancel"] is True


async def test_http_round_trip_validates_current_actor_without_old_sources(scenario):
    value = scenario
    app = create_broker_app(value["broker"],"synthetic-token")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner,"127.0.0.1",0)
    await site.start()
    client = ModelClient(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}","synthetic-token")
    await client.open()
    try:
        before = await client.inspect_admission_retirement(value["request"].attempt_id,lineage_id=value["lineage"],
            data_generation=value["generation"],context=value["context"],original_context=value["context"])
        assert before["state"] == "unsealed"
        result = await client.seal_admission(value["request"].attempt_id,lineage_id=value["lineage"],
            data_generation=value["generation"],context=value["context"],original_context=value["context"],reason="superseded")
        assert result["state"] == "sealed_absent"
        value["authority"].assert_awaited_with(value["context"],())
    finally:
        await client.close()
        await runner.cleanup()
