"""An input in a request is not proof that the reply obeyed it."""
import pytest

from tianshu.conversation.input_status import attach_learning_status, input_context_status


def test_original_inputs_are_matched_per_event_and_round_without_inventing_submission():
    inputs = [{"input_id": "first", "event_id": "event-a", "event_revision": 1},
              {"input_id": "second", "event_id": "event-b", "event_revision": 2},
              {"input_id": "old", "event_id": "event-c", "event_revision": 1}]
    def entry(identifier, event, revision, selected):
        return {"kind": "conversation_unit", "selected": selected, "records": [{"speaker": "user",
            "turn_id": identifier, "event_id": event, "revision": revision}]}
    selections = [
        {"attempt_id": "a", "round": 0, "model_receipt": None, "selection": [
            entry("first", "event-a", 1, True), entry("second", "event-b", 1, True),
            entry("second", "event-b", 2, False)]},
        {"attempt_id": "b", "round": 1, "model_receipt": {"dispatch": "submitted"},
         "selection": [entry("second", "event-b", 2, True)]},
    ]
    first, second, old = input_context_status(inputs, selections)
    assert first["context"] == {"selection": "selected", "attempts": [
        {"attempt_id": "a", "round": 0, "selected": True, "dispatch": "not_recorded"}]}
    assert second["context"]["selection"] == "selected"
    assert second["context"]["attempts"] == [
        {"attempt_id": "a", "round": 0, "selected": False, "dispatch": "not_recorded"},
        {"attempt_id": "b", "round": 1, "selected": True, "dispatch": "submitted"}]
    assert old["context"] == {"selection": "not_recorded", "attempts": []}
    assert {item["reply_compliance"] for item in (first, second, old)} == {"not_recorded"}


def test_learning_status_requires_the_original_event_and_revision():
    inputs = input_context_status([{"input_id": "first", "event_id": "event-a", "event_revision": 2},
                                   {"input_id": "second", "event_id": "event-b", "event_revision": 1}], [])
    attach_learning_status(inputs, [{"input_id": "first", "event_id": "event-a", "event_revision": 2,
        "state": "reviewed", "revision_state": "not_recorded"}])
    assert inputs[0]["learning"] == {"state": "reviewed", "revision_state": "not_recorded"}
    assert inputs[1]["learning"] == {"state": "not_recorded", "revision_state": "not_recorded"}
    with pytest.raises(ValueError):
        attach_learning_status(inputs, [{"input_id": "first", "event_id": "event-a", "event_revision": 1,
            "state": "reviewed", "revision_state": "applied"}])
    with pytest.raises(ValueError):
        attach_learning_status(inputs, [{"input_id": "first", "event_id": "event-a", "event_revision": 2,
            "state": "reviewed", "revision_state": "applied", "revision_evidence": []}])
    attach_learning_status(inputs, [{"input_id": "first", "event_id": "event-a", "event_revision": 2,
        "state": "reviewed", "revision_state": "applied", "revision_evidence": [{"rule_id": "rule-1"}]}])
    assert inputs[0]["learning"]["revision_evidence"] == [{"rule_id": "rule-1"}]
