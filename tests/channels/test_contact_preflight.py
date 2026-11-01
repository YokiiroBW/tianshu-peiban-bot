"""The last pre-submit check may stop a known-unsent expression, never replay it."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from tianshu.channels.contracts import TransportResult
from tianshu.channels.delivery import DeliveryHandler
from tianshu.channels.service import Channels
from tianshu.types import Action, Attempt, OriginKind, TrustedContext


def action():
    context = TrustedContext("owner", "actor", None, "chat", ("private",), ("private",),
        1, OriginKind.ACTOR, delivery_allowed=True)
    return Action("delivery", "intent", "channels.deliver", "actor", context,
        {"plan": {"expression_id": "contact:one:1", "actor_id": "actor", "target_ref": "chat",
            "candidate_id": "one", "origin_kind": "actor", "public_parts": [{"kind": "text", "text": "A discovery"}]}},
        OriginKind.ACTOR, expression_id="contact:one:1")


class Calls:
    def __init__(self):
        self.rows = []
        self.seals = {}

    async def calls_for(self, *_args):
        return self.rows

    async def call(self, identifier):
        return next(row for row in self.rows if row["id"] == identifier)

    async def prepare_call(self, action, attempt, connection_id, name, params, **kwargs):
        self.rows.append({"id": "call", "action_id": action.action_id, "attempt_id": attempt.attempt_id,
            "connection_id": connection_id, "api_name": name, "state": "prepared", "submitted_at": None,
            "control": False, "expected_count": 1, "payload": "sealed", "result": None})
        return "call"

    async def settle(self, identifier, result):
        row = await self.call(identifier)
        row.update(state=result.state, result={"error_code": result.error_code}, actual=result)

    def outcome(self, row, **kwargs):
        return row.get("actual") or TransportResult(row["state"])


class QueuedDeliveryPreflight(IsolatedAsyncioTestCase):
    async def test_queued_candidate_is_cancelled_before_any_channel_call(self):
        original = action()
        for adapter in ("web", "qq"):
            with self.subTest(adapter=adapter):
                callback = AsyncMock(return_value={"decision": "defer", "reason": "became_busy",
                    "due_at": "2026-11-03T08:00:00+08:00"})
                calls = Calls()
                service = SimpleNamespace(identity=SimpleNamespace(channel_target=AsyncMock(return_value={
                    "context": original.context, "adapter": adapter})), repository=calls, contact_preflight=callback,
                    web=SimpleNamespace(publish=AsyncMock()), actions=SimpleNamespace(submitted=AsyncMock()))
                receipt = await DeliveryHandler(service).execute(original, Attempt("attempt", original.action_id))
                self.assertEqual((receipt.execution, receipt.effect, receipt.delivery),
                                 ("cancelled", "none", "not_requested"))
                self.assertEqual(receipt.result["contact_preflight"]["decision"], "defer")
                callback.assert_awaited_once_with(original.context, "one", expression_id="contact:one:1")
                self.assertEqual(calls.rows, [])
                service.web.publish.assert_not_awaited()
                service.actions.submitted.assert_not_awaited()

    async def test_web_candidate_changed_after_initial_queue_check(self):
        original = action()
        calls = Calls()
        callback = AsyncMock(side_effect=[{"decision": "send", "reason": "current"},
            {"decision": "cancel", "reason": "no_longer_current"}])
        service = SimpleNamespace(identity=SimpleNamespace(channel_target=AsyncMock(return_value={
            "context": original.context, "adapter": "web"})), repository=calls, contact_preflight=callback,
            web=SimpleNamespace(publish=AsyncMock()), actions=SimpleNamespace(submitted=AsyncMock()))
        receipt = await DeliveryHandler(service).execute(original, Attempt("attempt", original.action_id))
        self.assertEqual(receipt.execution, "cancelled")
        self.assertEqual(calls.rows[0]["state"], "rejected")
        self.assertIsNone(calls.rows[0]["submitted_at"])
        service.web.publish.assert_not_awaited()
        service.actions.submitted.assert_not_awaited()

    async def test_submitted_or_unknown_original_is_never_reclassified_as_a_new_candidate(self):
        original = action()
        for state in ("sending", "unknown", "confirmed"):
            with self.subTest(state=state):
                calls = Calls()
                calls.rows = [{"id": "original-call", "state": state, "submitted_at": "known-submitted"}]
                callback = AsyncMock(return_value={"decision": "defer", "reason": "new_busy_state"})
                handler = DeliveryHandler(SimpleNamespace(repository=calls, contact_preflight=callback))
                self.assertIsNone(await handler.preflight(original, Attempt("attempt", original.action_id)))
                callback.assert_not_awaited()

    async def test_non_candidate_expression_keeps_the_original_delivery_path(self):
        original = action()
        original = Action(original.action_id, original.intent_key, original.capability_id, original.actor_id,
            original.context, {"plan": {**original.arguments["plan"], "candidate_id": None}}, original.origin_kind,
            expression_id=original.expression_id)
        callback = AsyncMock(return_value={"decision": "cancel", "reason": "ordinary_candidate_rule"})
        handler = DeliveryHandler(SimpleNamespace(contact_preflight=callback))
        self.assertIsNone(await handler.preflight(original, Attempt("attempt", original.action_id)))
        callback.assert_not_awaited()

    async def test_transport_owner_checks_after_media_preparation_before_network_submission(self):
        original = action()
        original = Action(original.action_id, original.intent_key, original.capability_id, original.actor_id,
            original.context, {"plan": {**original.arguments["plan"], "target_ref": "chat"}}, original.origin_kind,
            expression_id=original.expression_id)
        attempt = Attempt("attempt", original.action_id)
        calls = Calls()
        await calls.prepare_call(original, attempt, "connection", "sendMessage", {})
        connection = {"id": "connection", "revision": 1, "enabled": True, "configured": True,
                      "transport": "bridge", "adapter": "telegram"}
        calls.connection = AsyncMock(return_value=connection)
        calls.unseal = lambda *_args: {"text": "A discovery"}
        calls.observe = AsyncMock()

        async def query(sql, params):
            if "SET state='sending'" in sql:
                calls.rows[0]["state"] = "sending"
                return SimpleNamespace(fetchone=AsyncMock(return_value={"id": "call"}))
            return SimpleNamespace(fetchone=AsyncMock(return_value=None))

        @asynccontextmanager
        async def transaction(**_kwargs):
            yield SimpleNamespace(execute=query)

        network = AsyncMock()

        async def transport_call(_identifier, _name, _params, submitted, **_kwargs):
            # Represents media preparation already having finished at the owner.
            await submitted()
            await network()
            return TransportResult("confirmed", message_ids=("message",))

        owner = object.__new__(Channels)
        owner.runtime = SimpleNamespace(database=SimpleNamespace(transaction=transaction))
        owner.owner_id, owner.running, owner.repository = "owner", {}, calls
        owner.ensure_owner = AsyncMock()
        owner.identity = SimpleNamespace(channel_target=AsyncMock(return_value={
            "context": original.context, "connection_id": "connection", "target_ref": "chat"}))
        owner.sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True), check=AsyncMock())
        owner.actions = SimpleNamespace(load_for_execution=AsyncMock(return_value=(original, attempt, None)),
                                        submitted=AsyncMock())
        owner.contact_preflight = AsyncMock(return_value={"decision": "defer", "reason": "became_busy",
                                                        "due_at": "2026-11-03T08:00:00+08:00"})
        owner.transports = {"connection": SimpleNamespace(call=transport_call, acknowledge=AsyncMock())}
        owner.revisions = {"connection": 1}
        owner.delivery_handler = DeliveryHandler(owner)
        result = await owner.perform_call("call")
        self.assertEqual(result.state, "rejected")
        self.assertEqual(result.raw["contact_preflight"]["decision"], "defer")
        self.assertIsNone(calls.rows[0]["submitted_at"])
        network.assert_not_awaited()
        owner.actions.submitted.assert_not_awaited()
        receipt = await owner.delivery_handler.reconcile(original, attempt)
        self.assertEqual((receipt.execution, receipt.effect), ("cancelled", "none"))
        self.assertEqual(receipt.error_code, "became_busy")
