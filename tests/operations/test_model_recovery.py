from types import SimpleNamespace
from unittest.mock import AsyncMock

from tianshu.operations.model_recovery import model_configuration_refresh
from tianshu.operations.recovery import RecoveryExecutor


async def test_model_reload_uses_and_verifies_the_original_event(operations_db, monkeypatch):
    identity = "model-config:original"
    status = {"event_id": identity, "revision": 7, "state": "pending"}
    async def read_status(_identity):
        return dict(status)
    async def reload(**kwargs):
        assert kwargs == {"configuration_event_id": identity, "expected_revision": 7}
        status["state"] = "applied"
        return {"configuration_reload": dict(status), "quota_groups": ["group"]}
    models = SimpleNamespace(configuration_reload_status=AsyncMock(side_effect=read_status),
                             reload=AsyncMock(side_effect=reload),
                             snapshot=AsyncMock(return_value={"status": "ready", "quota_groups": {"group": {}}}))
    runtime = SimpleNamespace(database=operations_db, settings={}, models=models)
    monkeypatch.setattr("tianshu.operations.model_recovery.configuration",
                        AsyncMock(return_value={"quota_groups": [{"quota_group": "group"}]}))
    monkeypatch.setattr(RecoveryExecutor, "guard_on", AsyncMock())
    action = model_configuration_refresh(runtime)
    target = {"material_id": identity, "version": "7"}
    facts = await action.inspect({}, target)
    assert facts["eligible"] is True
    receipt = await action.invoke({"owner_token": "owner"}, target, facts, "control-attempt")
    assert receipt["state"] == "accepted"
    observed = await action.observe({}, target, facts, "control-attempt")
    assert observed["state"] == "verified"
    assert identity in observed["observation_ref"]
    models.reload.assert_awaited_once()
