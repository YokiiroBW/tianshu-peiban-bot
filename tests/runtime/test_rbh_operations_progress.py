"""Existing Operations progress keeps bounded recovery and observation independent."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tianshu.runtime import product_operations


@pytest.mark.parametrize("memory_scan_fails", [False, True])
async def test_progress_reuses_domain_adapters_and_keeps_observations_after_scan_failure(
    monkeypatch, tmp_path, memory_scan_fails,
):
    result = {"inspected": 2, "scheduled": 1, "ineligible": 1, "changes": [{"id": "original"}]}
    scan = lambda: AsyncMock(return_value=dict(result))
    plan = SimpleNamespace(
        reconcile_transport_waits=scan(),
        observe_due_retirements=AsyncMock(return_value={"observed": 1, "unavailable": 0, "changes": []}),
        candidates=AsyncMock(return_value=({"domain": "plan"},)),
        registration=Mock(return_value={}),
    )
    result_recovery = SimpleNamespace(
        candidates=AsyncMock(return_value=({"domain": "result"},)),
        registration=Mock(return_value={}),
    )
    factory = Mock(return_value=plan)
    monkeypatch.setattr("tianshu.life.rbg_plan_operations.RBGPlanOperationsRecovery", factory)
    monkeypatch.setattr("tianshu.conversation.rbg_result_recovery.ResultRecovery", Mock(return_value=result_recovery))
    monkeypatch.setattr(product_operations, "build_operations", lambda *args, **kwargs: kwargs)
    memory = SimpleNamespace(
        consolidation=SimpleNamespace(reconcile_transport_waits=scan()),
        recovery_candidates=AsyncMock(return_value=({"domain": "memory"},)),
        set_operations_guard=Mock(),
        operations=SimpleNamespace(inspect=AsyncMock(), invoke=AsyncMock(), observe=AsyncMock()),
    )
    if memory_scan_fails:
        memory.consolidation.reconcile_transport_waits.side_effect = RuntimeError("unavailable")
    growth = SimpleNamespace(
        retries=SimpleNamespace(reconcile_transport_waits=scan()),
        operations=SimpleNamespace(progress=AsyncMock(return_value=({"domain": "growth"},))),
    )
    worker = SimpleNamespace(recovery_candidates=AsyncMock(return_value=({"domain": "life"},)))
    runtime = SimpleNamespace(
        database=None, jobs=None, settings={}, module_status={},
        files=SimpleNamespace(root=tmp_path / "files"),
    )
    registered = product_operations.attach_operations(
        runtime, SimpleNamespace(identity=None), None, None,
        memory=memory, growth=growth, life_worker=worker,
    )
    for _ in range(2):
        facts = await registered["progress"]()
        assert {fact["domain"] for fact in facts} == {"life", "plan", "result", "memory", "growth"}
    factory.assert_called_once()
    for callback in (
        plan.reconcile_transport_waits, memory.consolidation.reconcile_transport_waits,
        growth.retries.reconcile_transport_waits,
    ):
        assert callback.await_count == 2
        assert all(call.kwargs == {"limit": 32} for call in callback.await_args_list)
    assert plan.observe_due_retirements.await_count == 2
    assert memory.recovery_candidates.await_count == 2
    assert runtime.module_status["operations.transport.memory"]["state"] == (
        "unavailable" if memory_scan_fails else "ready"
    )
    for domain in ("life", "growth"):
        assert runtime.module_status["operations.transport." + domain] == {
            "state": "ready", "inspected": 2, "scheduled": 1, "ineligible": 1, "changes": 1,
        }
