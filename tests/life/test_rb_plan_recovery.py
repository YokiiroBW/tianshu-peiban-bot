from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.life import plan_recovery
from tianshu.models.types import ModelReceipt, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext
from test_rbi_models_fixture import record_outcome


@dataclass(frozen=True)
class Request:
    attempt_id: str
    route_id: str
    data_generation: str = "installation-generation"


def policy():
    return SimpleNamespace(
        register_on=AsyncMock(return_value={"lineage_id": "lineage"}),
        reserve_attempt_on=AsyncMock(return_value=SimpleNamespace(number=2, probe_token="probe")),
        claim_lineage_due_on=AsyncMock(return_value={"lineage_id": "lineage", "current_version": "material", "claim_token": "claim"}),
        claim_continuation_on=AsyncMock(return_value={"claim_token": "correction"}),
        computation_budget_on=AsyncMock(return_value={"total_attempts":1,"computation_attempts":1,
            "connection_attempts":0,"max_computation_attempts":3,"max_connection_attempts":16,
            "can_retry":True,"retry_at":None,"dependency":"models:quota:quota","reason":"not_confirmed_not_sent"}),
        inspect_wait_on=AsyncMock(return_value=None),
        update_dependency_on=AsyncMock(),
    )


def previous(state, *, attempts=1, dependency="models:quota:quota"):
    return {"generation_key": "first", "recovery_lineage_id": "lineage", "recovery_owner":
        "life.plan:" + plan_recovery.signature({"actor_id": "actor"}), "recovery_version": "material",
        "recovery_attempt_number": attempts, "recovery_state": state, "recovery_dependency": dependency,
        "reason": "planner_output_invalid"}


async def reserve(p, old=None, *, quota="quota", continuation=True, model_receipt=None,
                  original_request=None, connection=None):
    connection = connection or SimpleNamespace(execute=AsyncMock())
    generation = original_request.data_generation if original_request else "installation-generation"
    return await plan_recovery.reserve_on(SimpleNamespace(recovery_policy=p), connection,
        context=SimpleNamespace(actor_id="actor"), scene={"actor_id": "actor"}, material="material", key="next",
        frozen=Request("second-attempt", "route",generation), profile={"quota_group": quota}, previous=old,
        continuation=continuation, model_receipt=model_receipt,original_request=original_request)


async def original_in_models(database, *, status="failed"):
    await migrate(database,(Migration("0031_life",files("tianshu.life").joinpath("schema.sql").read_text()),
        Migration("0040_life_execution",files("tianshu.life").joinpath("rb_life_execution.sql").read_text())))
    async with database.transaction() as connection:
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton")).fetchone()
        await connection.execute("INSERT INTO life.plan_generations"
            "(generation_key,actor_id,day,material_fingerprint,base_revision,context) "
            "VALUES('first','actor',current_date,'material',1,'{}')")
    generation = str(generation["id"])
    context = TrustedContext("author","actor",None,None,("scope",),("scope",),1,OriginKind.ACTOR)
    attempt = plan_recovery.attempt_for("first")
    request = ModelRequest("intent",attempt,"reflect",ServiceClass.ELASTIC,context,
        RequestBudget(datetime.now(UTC)+timedelta(minutes=2)),(),route_id="route",route_revision=1,
        execution_timeout_seconds=60,data_generation=generation)
    queued = ModelReceipt(attempt,"intent","queued","not_started","route",1,"provider","model","native","quota")
    broker = SimpleNamespace(requests={attempt:request},receipts={attempt:queued})
    error = "transport_unknown" if status == "unknown" else "upstream_http_429"
    _, observed = await record_outcome(database,broker,attempt,status=status,error_code=error)
    return request, observed


@pytest.mark.asyncio
async def test_failed_physical_attempt_and_correction_share_original_lineage(database):
    p = policy()
    original, observed = await original_in_models(database)
    async with database.transaction() as connection:
        await reserve(p, previous("failed"),connection=connection,original_request=original,model_receipt=observed)
    assert p.reserve_attempt_on.await_args.args[1] == "lineage"
    assert p.reserve_attempt_on.await_args.kwargs["original_state"] == "failed"
    assert p.reserve_attempt_on.await_args.kwargs["claim_token"] == "claim"
    p.register_on.assert_not_awaited()

    await reserve(p, previous("succeeded"))
    assert p.claim_continuation_on.await_args.kwargs["prior_attempt_id"] == plan_recovery.attempt_for("first")
    assert p.reserve_attempt_on.await_args.kwargs["original_state"] == "succeeded"
    assert p.reserve_attempt_on.await_args.kwargs["claim_token"] == "correction"
    p.register_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_quota_keeps_budget_but_unknown_requires_a_finished_receipt(database):
    p = policy()
    original, observed = await original_in_models(database)
    async with database.transaction() as connection:
        options = {"connection":connection,"original_request":original,"model_receipt":observed}
        await reserve(p, previous("failed"), quota="new-quota",**options)
        assert p.update_dependency_on.await_args.kwargs["new_dependency"] == "models:quota:new-quota"
        assert p.reserve_attempt_on.await_args.args[1] == "lineage"

        p.reserve_attempt_on.reset_mock()
        assert (await reserve(p, previous("reserved")))["state"] == "waiting"
        with pytest.raises(AppError,match="recovery_model_receipt_mismatch"):
            await reserve(p,previous("unknown"),connection=connection,original_request=original)
        p.computation_budget_on.return_value = {"can_retry":False,"reason":"recovery_budget_exhausted"}
        assert (await reserve(p, previous("failed", attempts=3),**options))["state"] == "exhausted"
        p.reserve_attempt_on.assert_not_awaited()
        p.computation_budget_on.return_value = {"can_retry":True,"reason":"confirmed_not_sent"}
        await reserve(p, previous("failed", attempts=4),**options)
        assert p.reserve_attempt_on.await_args.kwargs["original_state"] == "failed"


@pytest.mark.asyncio
async def test_finished_unknown_uses_its_original_models_ledger(database):
    p = policy()
    original, observed = await original_in_models(database,status="unknown")
    async with database.transaction() as connection:
        await reserve(p,previous("unknown"),connection=connection,original_request=original,model_receipt=observed)
    assert p.reserve_attempt_on.await_args.kwargs["original_state"] == "unknown"
    assert p.reserve_attempt_on.await_args.kwargs["model_receipt"] is observed


@pytest.mark.asyncio
async def test_changed_quota_is_remembered_while_the_claim_is_not_due(database):
    p = policy()
    p.claim_lineage_due_on.return_value = None
    original, observed = await original_in_models(database)
    old = previous("failed")
    async with database.transaction() as connection:
        options = {"connection":connection,"original_request":original,"model_receipt":observed}
        result = await reserve(p,old,quota="new-quota",**options)
        assert result["state"] == "waiting"
        saved = await (await connection.execute("SELECT recovery_current_dependency FROM life.plan_generations "
            "WHERE generation_key='first'")).fetchone()
        assert saved["recovery_current_dependency"] == "models:quota:new-quota"
        old["recovery_current_dependency"] = "models:quota:new-quota"
        p.update_dependency_on.reset_mock()
        await reserve(p, old, quota="new-quota",**options)
        p.update_dependency_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_attempt_uses_real_quota_and_missing_quota_is_not_guessed():
    p = policy()
    result = await reserve(p, continuation=False)
    assert result["lineage_id"] == "lineage"
    assert p.register_on.await_args.kwargs["dependency"] == "models:quota:quota"
    assert p.register_on.await_args.kwargs["data_generation"] == "installation-generation"
    assert p.register_on.await_args.kwargs["max_attempts"] == 3
    assert p.reserve_attempt_on.await_args.kwargs["original_state"] == "none"
    with pytest.raises(AppError, match="planner_recovery_quota_unavailable"):
        await reserve(p, quota=None, continuation=False)


class Connection:
    def __init__(self):
        self.row = {**previous("reserved"), "recovery_probe_token": "probe"}

    async def execute(self, query, parameters=()):
        if "SET recovery_state='unknown'" in query:
            self.row["recovery_state"] = "unknown"
        elif "SET recovery_state='succeeded'" in query:
            self.row["recovery_state"] = "succeeded"
        return SimpleNamespace(fetchone=AsyncMock(return_value=dict(self.row)))


@pytest.mark.asyncio
async def test_unknown_receipt_is_recorded_then_resolved_only_from_original_result(database):
    p = policy()
    p.record_computation_unknown_on, p.resolve_unknown_on = AsyncMock(), AsyncMock()
    p.succeed_attempt_on = AsyncMock()
    life, connection = SimpleNamespace(recovery_policy=p), Connection()
    _, receipt = await original_in_models(database,status="unknown")
    await plan_recovery.unknown_on(life, connection, "first", receipt)
    assert connection.row["recovery_state"] == "unknown"
    assert p.record_computation_unknown_on.await_args.kwargs["model_receipt"] is receipt
    await plan_recovery.succeed_on(life, connection, "first")
    assert connection.row["recovery_state"] == "succeeded"
    assert p.resolve_unknown_on.await_args.kwargs["state"] == "succeeded"
    assert p.resolve_unknown_on.await_args.args[1] == plan_recovery.attempt_for("first")
    p.succeed_attempt_on.assert_not_awaited()
