from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.life import plan_recovery
from tianshu.models.types import ModelReceipt
from tianshu.runtime.errors import AppError


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
        update_dependency_on=AsyncMock(),
    )


def previous(state, *, attempts=1, dependency="models:quota:quota"):
    return {"generation_key": "first", "recovery_lineage_id": "lineage", "recovery_owner":
        "life.plan:" + plan_recovery.signature({"actor_id": "actor"}), "recovery_version": "material",
        "recovery_attempt_number": attempts, "recovery_state": state, "recovery_dependency": dependency,
        "reason": "planner_output_invalid"}


async def reserve(p, old=None, *, quota="quota", continuation=True, model_receipt=None):
    return await plan_recovery.reserve_on(SimpleNamespace(recovery_policy=p), SimpleNamespace(execute=AsyncMock()),
        context=SimpleNamespace(actor_id="actor"), scene={"actor_id": "actor"}, material="material", key="next",
        frozen=Request("second-attempt", "route"), profile={"quota_group": quota}, previous=old,
        continuation=continuation, model_receipt=model_receipt)


@pytest.mark.asyncio
async def test_failed_physical_attempt_and_correction_share_original_lineage():
    p = policy()
    await reserve(p, previous("failed"))
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
async def test_changed_quota_keeps_budget_but_unknown_requires_a_finished_receipt():
    p = policy()
    await reserve(p, previous("failed"), quota="new-quota")
    assert p.update_dependency_on.await_args.kwargs["new_dependency"] == "models:quota:new-quota"
    assert p.reserve_attempt_on.await_args.args[1] == "lineage"

    p.reserve_attempt_on.reset_mock()
    assert (await reserve(p, previous("reserved")))["state"] == "waiting"
    assert (await reserve(p, previous("unknown")))["state"] == "waiting"
    assert (await reserve(p, previous("failed", attempts=3)))["state"] == "exhausted"
    p.reserve_attempt_on.assert_not_awaited()
    original = ModelReceipt(plan_recovery.attempt_for("first"),"intent","unknown","unknown","route",1,
        "provider","model","native","quota",finished_at=datetime.now(UTC),data_generation="installation-generation")
    await reserve(p, previous("unknown"), model_receipt=original)
    assert p.reserve_attempt_on.await_args.kwargs["original_state"] == "unknown"
    assert p.reserve_attempt_on.await_args.kwargs["model_receipt"] is original


@pytest.mark.asyncio
async def test_changed_quota_is_remembered_while_the_claim_is_not_due():
    p = policy()
    p.claim_lineage_due_on.return_value = None
    connection = SimpleNamespace(execute=AsyncMock())
    old = previous("failed")
    result = await plan_recovery.reserve_on(SimpleNamespace(recovery_policy=p), connection,
        context=SimpleNamespace(actor_id="actor"), scene={"actor_id": "actor"}, material="material", key="next",
        frozen=Request("second-attempt", "new-route"), profile={"quota_group": "new-quota"},
        previous=old, continuation=True)
    assert result["state"] == "waiting"
    assert connection.execute.await_args.args[1] == ("models:quota:new-quota", "first")
    old["recovery_current_dependency"] = "models:quota:new-quota"
    p.update_dependency_on.reset_mock()
    await reserve(p, old, quota="new-quota")
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
async def test_unknown_receipt_is_recorded_then_resolved_only_from_original_result():
    p = policy()
    p.record_computation_unknown_on, p.resolve_unknown_on = AsyncMock(), AsyncMock()
    p.succeed_attempt_on = AsyncMock()
    life, connection = SimpleNamespace(recovery_policy=p), Connection()
    receipt = ModelReceipt(plan_recovery.attempt_for("first"),"intent","unknown","unknown","route",1,
        "provider","model","native","quota",finished_at=datetime.now(UTC))
    await plan_recovery.unknown_on(life, connection, "first", receipt)
    assert connection.row["recovery_state"] == "unknown"
    assert p.record_computation_unknown_on.await_args.kwargs["model_receipt"] is receipt
    await plan_recovery.succeed_on(life, connection, "first")
    assert connection.row["recovery_state"] == "succeeded"
    assert p.resolve_unknown_on.await_args.kwargs["state"] == "succeeded"
    assert p.resolve_unknown_on.await_args.args[1] == plan_recovery.attempt_for("first")
    p.succeed_attempt_on.assert_not_awaited()
