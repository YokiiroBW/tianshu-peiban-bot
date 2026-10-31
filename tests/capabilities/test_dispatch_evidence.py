"""Protocol fixtures only; the integrated acceptance runner owns execution."""
import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.capabilities.tracking import TrackedBroker
from tianshu.capabilities.execution_context import CURRENT_EXECUTION
from tianshu.capabilities.external import ProviderError

class DispatchEvidence(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_rejection_releases_resource_but_unknown_does_not(self):
        for effect,operation in (("none","rejected"),("unknown","unknown")):
            permit=SimpleNamespace(submitted=AsyncMock(),finish=AsyncMock(),rejected=AsyncMock(),unknown=AsyncMock())
            @asynccontextmanager
            async def resource(request):yield permit
            broker=TrackedBroker(SimpleNamespace(resource=resource))
            with self.assertRaises(ProviderError):
                async with broker.resource(None):raise ProviderError("refused",effect=effect)
            getattr(permit,operation).assert_awaited_once_with("refused")
            getattr(permit,"unknown" if operation=="rejected" else "rejected").assert_not_awaited()

    async def test_interrupted_model_keeps_actual_submitted_identity(self):
        owner=SimpleNamespace(submitted=AsyncMock())
        receipt=SimpleNamespace(dispatch="submitted",provider_handle="native-handle")
        broker=TrackedBroker(SimpleNamespace(generate=AsyncMock(side_effect=asyncio.CancelledError()),status=AsyncMock(return_value=receipt)))
        attempt=object();state={"owner":owner,"attempt":attempt,"submitted":False}
        token=CURRENT_EXECUTION.set(state)
        try:
            with self.assertRaises(asyncio.CancelledError):await broker.generate(SimpleNamespace(attempt_id="model-attempt"))
        finally:CURRENT_EXECUTION.reset(token)
        owner.submitted.assert_awaited_once_with(attempt,"native-handle","submitted")
        self.assertTrue(state["submitted"])
