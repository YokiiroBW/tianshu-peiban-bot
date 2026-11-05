from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.execution_context import CURRENT_EXECUTION
from tianshu.capabilities.tracking import TrackedBroker
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait


@pytest.mark.asyncio
async def test_missing_observation_after_possible_submission_is_unknown():
    owner = SimpleNamespace(owners=SimpleNamespace(check_attempt=AsyncMock()), submitted=AsyncMock())
    broker = SimpleNamespace(generate=AsyncMock(side_effect=OSError("lost response")), status=AsyncMock(return_value=None))
    state = {"owner": owner, "attempt": "attempt", "submitted": False}
    token = CURRENT_EXECUTION.set(state)
    try:
        with pytest.raises(OSError):
            await TrackedBroker(broker).generate(SimpleNamespace(attempt_id="model"))
    finally:
        CURRENT_EXECUTION.reset(token)
    broker.status.assert_awaited_once_with("model", phase="observe")
    owner.submitted.assert_awaited_once_with("attempt", dispatch="unknown")
    assert state["submitted"]


@pytest.mark.asyncio
async def test_explicit_not_started_and_local_preflight_stay_no_effect():
    for error, local in ((AppError("expired", status=408, details={"dispatch": "not_started"}), False),
                         (Fault("source_revoked", 410), True)):
        owner = SimpleNamespace(owners=SimpleNamespace(check_attempt=AsyncMock(side_effect=error if local else None)),
                                submitted=AsyncMock())
        broker = SimpleNamespace(generate=AsyncMock(side_effect=error), status=AsyncMock(return_value=None))
        state = {"owner": owner, "attempt": "attempt", "submitted": False}
        token = CURRENT_EXECUTION.set(state)
        try:
            with pytest.raises(type(error)):
                await TrackedBroker(broker).generate(SimpleNamespace(attempt_id="model"))
        finally:
            CURRENT_EXECUTION.reset(token)
        owner.submitted.assert_not_awaited()
        if local:
            broker.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_rollback_evidence_controls_wait_without_losing_unknown():
    for remote_confirmed in (True, False):
        wait = RecoveryWait("dependency_wait", datetime.now(UTC) + timedelta(seconds=30))
        wait.remote_rollback_confirmed = remote_confirmed
        owner = SimpleNamespace(owners=SimpleNamespace(check_attempt=AsyncMock()), submitted=AsyncMock())
        broker = SimpleNamespace(generate=AsyncMock(side_effect=wait), status=AsyncMock(return_value=None))
        state = {"owner": owner, "attempt": "attempt", "submitted": False}
        token = CURRENT_EXECUTION.set(state)
        try:
            with pytest.raises(RecoveryWait) as caught:
                await TrackedBroker(broker).generate(SimpleNamespace(attempt_id="model"))
        finally:
            CURRENT_EXECUTION.reset(token)
        assert caught.value is wait
        if remote_confirmed:
            owner.submitted.assert_not_awaited()
        else:
            owner.submitted.assert_awaited_once_with("attempt", dispatch="unknown")
