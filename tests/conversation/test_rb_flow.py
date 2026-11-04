"""The UI sees actual stage receipts instead of inferred completion."""
from datetime import UTC, datetime

from tianshu.conversation.flow import dialogue_flow


def test_missing_material_stages_are_not_applicable_and_delivery_is_not_inferred():
    turn = {"turn_id": "t", "event_id": "event", "event_revision": 1, "received_at": datetime.now(UTC),
            "round": 0, "state": "completed", "expression_id": None}
    selection = {"attempt_id": "a", "round": 0, "model_receipt": {"status": "succeeded", "dispatch": "submitted"},
                 "model_result": {"parts": [], "completion": {"state": "complete"}}}
    flow = dialogue_flow(turn, [selection], [], None, None)
    round_ = flow["rounds"][0]
    assert round_["accepted"]["state"] == "accepted" and round_["parsed"]["state"] == "parsed"
    assert round_["applied"]["state"] == "not_applicable"
    assert round_["expression"]["state"] == round_["delivered"]["state"] == "not_applicable"


def test_partial_delivery_retains_real_receipt_facts():
    turn = {"turn_id": "t", "event_id": "event", "event_revision": 1, "received_at": datetime.now(UTC),
            "round": 0, "state": "failed", "expression_id": "expression"}
    selection = {"attempt_id": "a", "round": 0, "model_receipt": {"status": "succeeded", "dispatch": "submitted"},
                 "model_result": {"parts": [{"kind": "text"}], "completion": {"state": "complete"}}}
    expression = {"state": "failed", "receipt": {"execution": "failed", "effect": "confirmed", "delivery": "failed",
        "error_code": "behavior_invalid", "result": {"message_ids": ["one"], "delivery_progress": "partial"}}}
    delivered = dialogue_flow(turn, [selection], [], expression, expression)["rounds"][0]["delivered"]
    assert delivered["state"] == "failed" and delivered["effect"] == "confirmed"
    assert delivered["message_ids"] == ["one"] and delivered["delivery_progress"] == "partial"
