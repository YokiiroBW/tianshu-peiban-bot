"""Protocol fixtures only; the integrated acceptance runner owns execution."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.capabilities.tracking import TrackedBroker, TrackedPermit
from tianshu.capabilities.execution_context import CURRENT_EXECUTION
from tianshu.capabilities.external import ProviderError
from tianshu.capabilities.contracts import Fault


class DispatchEvidence(unittest.IsolatedAsyncioTestCase):
    async def test_provider_observation_keeps_the_original_clock_and_checks_its_owner(self):
        for loss in (None, "before", "after"):
            with self.subTest(loss=loss):
                changed = Fault("action_execution_owner_changed", 409)
                checks = [changed] if loss == "before" else [None, changed] if loss == "after" else [None, None]
                owner = SimpleNamespace(owners=SimpleNamespace(check_attempt=AsyncMock(side_effect=checks)),
                    submitted=AsyncMock())
                clock, attempt = object(), object()
                permit = SimpleNamespace(observe_provider=AsyncMock(return_value=clock),
                    submitted=AsyncMock(), rejected=AsyncMock(), unknown=AsyncMock())
                tracked = TrackedPermit(permit, {"owner": owner, "attempt": attempt})
                if loss:
                    with self.assertRaises(Fault):
                        await tracked.observe_provider("running", provider_handle="original-prompt")
                else:
                    self.assertIs(await tracked.observe_provider("running", provider_handle="original-prompt"), clock)
                if loss == "before":
                    permit.observe_provider.assert_not_awaited()
                else:
                    permit.observe_provider.assert_awaited_once_with("running", provider_handle="original-prompt")
                self.assertTrue(all(call.args == (attempt,) for call in owner.owners.check_attempt.await_args_list))
                owner.submitted.assert_not_awaited()
                permit.submitted.assert_not_awaited()
                permit.rejected.assert_not_awaited()
                permit.unknown.assert_not_awaited()

    async def test_explicit_rejection_releases_resource_but_unknown_does_not(self):
        for effect, operation in (("none", "rejected"), ("unknown", "unknown")):
            permit = SimpleNamespace(
                submitted=AsyncMock(), finish=AsyncMock(), rejected=AsyncMock(), unknown=AsyncMock()
            )

            @asynccontextmanager
            async def resource(request):
                yield permit

            broker = TrackedBroker(SimpleNamespace(resource=resource))
            with self.assertRaises(ProviderError):
                async with broker.resource(None):
                    raise ProviderError("refused", effect=effect)
            getattr(permit, operation).assert_awaited_once_with("refused")
            getattr(
                permit, "unknown" if operation == "rejected" else "rejected"
            ).assert_not_awaited()

    async def test_interrupted_model_keeps_actual_submitted_identity(self):
        owner = SimpleNamespace(
            submitted=AsyncMock(), owners=SimpleNamespace(check_attempt=AsyncMock())
        )
        receipt = SimpleNamespace(dispatch="submitted", provider_handle="native-handle")
        broker = TrackedBroker(
            SimpleNamespace(
                generate=AsyncMock(side_effect=asyncio.CancelledError()),
                status=AsyncMock(return_value=receipt),
            )
        )
        attempt = object()
        state = {"owner": owner, "attempt": attempt, "submitted": False}
        token = CURRENT_EXECUTION.set(state)
        try:
            with self.assertRaises(asyncio.CancelledError):
                await broker.generate(SimpleNamespace(attempt_id="model-attempt"))
        finally:
            CURRENT_EXECUTION.reset(token)
        owner.submitted.assert_awaited_once_with(attempt, "native-handle", "submitted")
        self.assertTrue(state["submitted"])
