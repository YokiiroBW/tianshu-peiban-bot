"""Rule changes stop only unsent work; unknown and partial receipts retain evidence."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.channels.contracts import TransportResult
from tianshu.channels.delivery import DeliveryHandler
from tianshu.conversation.behaviors import behavior_status
from tianshu.types import Action, Attempt, OriginKind, TrustedContext


def context():
    return TrustedContext("p", "actor", "person", "chat", ("s",), ("s",), 1,
                          OriginKind.HUMAN, conversation_kind="private", delivery_allowed=True)


@pytest.mark.asyncio
async def test_support_only_is_compatible_but_revoked_and_unreadable_constraints_are_distinct():
    ref = {"rule_id": "r1", "semantic_revision": 2, "semantic_role": "constraint"}
    resolver = SimpleNamespace(resolve_behaviors=AsyncMock(return_value=[{"rule_id": "r1", "revision": 4,
        "semantic_revision": 2, "requested_revision": None, "requested_semantic_revision": 2,
        "state": "active", "eligible": True, "compatible": True, "reason": "support_only"}]))
    status, _ = await behavior_status(resolver, context(), (ref,))
    assert status == "valid"
    resolver.resolve_behaviors.return_value = [{"rule_id": "r1", "requested_revision": None,
        "requested_semantic_revision": 2, "state": "revoked", "eligible": False, "compatible": False}]
    assert (await behavior_status(resolver, context(), (ref,)))[0] == "invalid"
    resolver.resolve_behaviors.side_effect = TimeoutError()
    assert (await behavior_status(resolver, context(), (ref,)))[0] == "pending"
    assert (await behavior_status(resolver, context(), ({**ref, "semantic_role": "style"},)))[0] == "unavailable"


@pytest.mark.asyncio
async def test_two_versions_of_one_rule_are_checked_independently():
    refs = ({"rule_id": "r", "revision": 1, "semantic_revision": 1},
            {"rule_id": "r", "revision": 2, "semantic_revision": 2})
    resolver = SimpleNamespace(resolve_behaviors=AsyncMock(return_value=[
        {"rule_id": "r", "requested_revision": 1, "requested_semantic_revision": 1,
         "state": "active", "eligible": True, "compatible": False},
        {"rule_id": "r", "requested_revision": 2, "requested_semantic_revision": 2,
         "state": "active", "eligible": True, "compatible": True},
    ]))
    assert (await behavior_status(resolver, context(), refs))[0] == "invalid"


@pytest.mark.asyncio
async def test_partial_delivery_does_not_erase_confirmed_part_or_claim_all_sent():
    rows = [
        {"id": "first", "api_name": "sendMessage", "state": "confirmed", "submitted_at": "earlier",
         "completed_at": "later", "expected_count": 2, "result": {"state": "confirmed", "message_ids": ["m1"]}},
        {"id": "second", "api_name": "sendMessage", "state": "rejected", "submitted_at": None,
         "completed_at": "later", "expected_count": 2, "result": {"state": "rejected", "error_code": "behavior_invalid"}},
    ]
    def outcome(row):
        result = row["result"]
        return TransportResult(row["state"], error_code=result.get("error_code"), message_ids=tuple(result.get("message_ids", ())))
    repository = SimpleNamespace(calls_for=AsyncMock(return_value=rows), outcome=outcome)
    service = SimpleNamespace(repository=repository)
    action = Action("action", "intent", "channels.deliver", "actor", context(),
                    {"plan": {"behavior_refs": []}}, OriginKind.ACTOR, expression_id="expression")
    receipt = await DeliveryHandler(service).reconcile(action, Attempt("attempt", "action", dispatch="submitted"))
    assert (receipt.execution, receipt.effect, receipt.delivery) == ("failed", "confirmed", "failed")
    assert receipt.result["message_ids"] == ("m1",)
    assert receipt.result["delivery_progress"] == "partial"
    assert receipt.error_code == "behavior_invalid"
