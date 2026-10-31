"""Failure cases prepared for the integrated runner; no live providers."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Availability
from tianshu.capabilities.contracts import Fault
from tianshu.types import Attempt, Receipt


class ExecutionRecovery(unittest.IsolatedAsyncioTestCase):
    def owner(self):
        conn = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    fetchone=AsyncMock(return_value={"document": '{"service_class":"interactive"}'})
                )
            )
        )

        @asynccontextmanager
        async def transaction(**kwargs):
            yield conn

        service = Actions(
            SimpleNamespace(transaction=transaction),
            SimpleNamespace(enqueue=AsyncMock()),
            SimpleNamespace(availability=AsyncMock(return_value=Availability(True, True))),
            SimpleNamespace(check=AsyncMock()),
        )
        service.owners.start = AsyncMock()
        service.owners.check = AsyncMock()
        action = SimpleNamespace(
            action_id="original", context=object(), source_refs=(), deadline_at=None
        )
        attempt = Attempt("original-attempt", "original")
        pending = Receipt(
            "initial", "original", "original-attempt", "pending", "none", "not_requested"
        )
        service._load = AsyncMock(return_value=(action, attempt, pending))
        service.save = AsyncMock(side_effect=lambda receipt, attempt: receipt)
        service.handler = AsyncMock()
        return service, action, attempt

    async def test_authority_rejection_after_acceptance_finishes_original_attempt(self):
        service, action, attempt = self.owner()
        handler = SimpleNamespace(execute=AsyncMock())
        service.handler.return_value = handler
        service.authority.check.side_effect = Fault("forbidden", 403)
        receipt = await service.run(action.action_id)
        self.assertEqual(
            (receipt.action_id, receipt.attempt_id), (action.action_id, attempt.attempt_id)
        )
        self.assertEqual(
            (receipt.execution, receipt.effect, receipt.error_code), ("failed", "none", "forbidden")
        )
        handler.execute.assert_not_awaited()
        service.save.assert_awaited_once()

    async def test_absolute_deadline_cancels_original_provider_handle(self):
        service, action, attempt = self.owner()
        action.deadline_at = datetime.now(UTC) + timedelta(milliseconds=10)
        dispatched = replace(attempt, dispatch="submitted", provider_handle="original-handle")
        service._load.side_effect = [(action, attempt, None), (action, dispatched, None)]
        service._load.side_effect = None
        service._load.return_value = (
            action,
            dispatched,
            Receipt("r", "original", attempt.attempt_id, "pending", "none", "not_requested"),
        )
        cancelled = Receipt(
            "cancel", "original", attempt.attempt_id, "cancelled", "none", "not_requested"
        )

        async def execute(action, attempt):
            await asyncio.Event().wait()

        handler = SimpleNamespace(execute=execute, cancel=AsyncMock(return_value=cancelled))
        service.handler.return_value = handler
        receipt = await service.run(action.action_id)
        self.assertEqual(receipt.execution, "cancelled")
        self.assertEqual(handler.cancel.await_args.args[1].provider_handle, "original-handle")
        self.assertTrue(receipt.result["provider_cancel_attempted"])

    async def test_transport_interruption_does_not_claim_provider_cancelled(self):
        service, action, attempt = self.owner()
        dispatched = replace(attempt, dispatch="unknown", provider_handle="original-handle")
        service._load.return_value = (action, dispatched, None)
        handler = SimpleNamespace(cancel=AsyncMock())
        receipt = await service.interrupted(action, attempt, handler, "interrupted")
        self.assertEqual((receipt.execution, receipt.effect), ("unknown", "unknown"))
        handler.cancel.assert_not_awaited()

    async def test_orphan_recovery_enqueues_only_original_reconcile(self):
        service, action, attempt = self.owner()
        action.actor_id = "actor"
        attempt = replace(attempt, provider_handle="known-original", dispatch="submitted")
        service._load.return_value = (
            action,
            attempt,
            Receipt("r", "original", attempt.attempt_id, "running", "unknown", "not_requested"),
        )
        service.sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
        receipt = await service.recover_orphan(action.action_id, attempt.attempt_id, "dead-process")
        self.assertEqual((receipt.execution, receipt.attempt_id), ("unknown", attempt.attempt_id))
        job = service.jobs.enqueue.await_args.args[1]
        self.assertEqual(job.task, "capabilities.control")
        self.assertEqual(job.payload, {"action_id": action.action_id, "mode": "reconcile"})
        service.handler.assert_not_awaited()
