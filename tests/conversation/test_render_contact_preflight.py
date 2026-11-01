"""A proactive candidate is rechecked after rendering and conversation ordering."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tianshu.conversation.rendering import ExpressionRenderer
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, TrustedContext


class RenderedContactPreflight(IsolatedAsyncioTestCase):
    async def test_order_wait_finishes_before_recheck_and_known_defer_closes_old_expression(self):
        context = TrustedContext("owner", "actor", None, "chat", ("private",), ("private",),
                                 1, OriginKind.ACTOR, delivery_allowed=True)
        plan = {"expression_id": "contact:one:1", "actor_id": "actor", "target_ref": "chat",
                "public_parts": [{"kind": "text", "text": "A finding"}], "source_refs": [],
                "origin_kind": "actor", "turn_id": "turn", "artifacts": [], "candidate_id": "one"}
        row = {"expression_id": plan["expression_id"], "state": "ready", "receipt": None, "revision": 1,
               "context": wire(context), "plan": plan, "rendered": plan, "steps": [], "reason": None}
        waiting = True

        async def query(sql, params):
            value = None
            if sql.startswith("SELECT * FROM conversation.expression_plans"):
                value = dict(row)
            elif sql.startswith("SELECT sequence,state"):
                value = {"sequence": 2, "state": "waiting_expression"}
            elif sql.startswith("SELECT turn_id"):
                value = {"turn_id": "earlier"} if waiting else None
            elif "SET state='waiting_order'" in sql:
                row.update(state="waiting_order", reason="earlier_turn_pending")
                value = dict(row)
            elif "SET state='delivering'" in sql:
                row.update(state="delivering", reason=None)
            elif "SET state='cancelled'" in sql:
                row.update(state="cancelled", reason=params[0], revision=2)
                value = dict(row)
            return SimpleNamespace(fetchone=AsyncMock(return_value=value))

        @asynccontextmanager
        async def transaction(**_kwargs):
            yield SimpleNamespace(execute=query)

        delivery, jobs = SimpleNamespace(send=AsyncMock()), SimpleNamespace(enqueue=AsyncMock())
        renderer = ExpressionRenderer(SimpleNamespace(transaction=transaction), jobs,
            SimpleNamespace(check=AsyncMock()), SimpleNamespace(validate_persistence=AsyncMock(return_value=True)),
            None, None, delivery)
        callback = AsyncMock(return_value={"decision": "defer", "reason": "became_busy",
                                          "due_at": "2026-11-03T08:00:00+08:00"})
        renderer.bind_contact_preflight(callback)
        first = await renderer.release(plan["expression_id"])
        self.assertEqual(first.state, "waiting_order")
        callback.assert_not_awaited()
        waiting = False
        second = await renderer.release(plan["expression_id"])
        self.assertEqual(second.state, "cancelled")
        self.assertEqual(second.reason, "became_busy")
        callback.assert_awaited_once_with(context, "one", expression_id=plan["expression_id"])
        delivery.send.assert_not_awaited()
        jobs.enqueue.assert_awaited_once()
