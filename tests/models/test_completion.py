"""Physical settlement and usable content termination have separate meanings."""

import pytest

from tianshu.models.codec import result_from_wire
from tianshu.models.completion import completion_from_native
from tianshu.models.protocols import ResponseCollector


@pytest.mark.parametrize("protocol,native,reason", [
    ("openai.chat", {"choices": [{"finish_reason": "length"}]}, "length"),
    ("gemini.generate", {"candidates": [{"finishReason": "MAX_TOKENS"}]}, "MAX_TOKENS"),
    ("anthropic.messages", {"stop_reason": "max_tokens"}, "max_tokens"),
    ("openai.responses", {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "max_output_tokens"),
])
def test_native_incomplete_content_is_not_unknown(protocol, native, reason):
    summary = completion_from_native(protocol, native)
    assert summary.state == "incomplete"
    assert summary.stop_reasons == (reason,)


def test_stream_usage_trailer_preserves_stop_and_partial_tool_is_not_executable():
    collector = ResponseCollector("openai.chat")
    collector.feed({"choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "id": "call", "type": "function", "function": {"name": "lookup", "arguments": '{"q":'}}
    ]}, "finish_reason": "length"}]})
    collector.feed({"choices": [], "usage": {"completion_tokens": 4096}})
    assert collector.completed
    assert collector.completion.state == "incomplete"
    assert collector.completion.stop_reasons == ("length",)
    assert collector.tool_parts() == ()


def test_terminal_responses_incomplete_is_a_finished_physical_response():
    collector = ResponseCollector("openai.responses")
    collector.feed({"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        "usage": {"input_tokens": 100, "output_tokens": 4096},
    }})
    assert collector.completed
    assert collector.completion.state == "incomplete"
    assert collector.raw_usage["output_tokens"] == 4096


@pytest.mark.parametrize("streaming", [False, True])
def test_responses_partial_tool_arguments_wait_for_terminal_completion(streaming):
    collector = ResponseCollector("openai.responses")
    item = {"type": "function_call", "id": "item", "call_id": "call", "name": "lookup", "arguments": '{"q":'}
    terminal = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    if streaming:
        collector.feed({"type": "response.output_item.done", "item": item})
        collector.feed({"type": "response.incomplete", "response": terminal})
    else:
        collector.feed({**terminal, "output": [item]})
    assert collector.completed
    assert collector.completion.state == "incomplete"
    assert collector.tool_parts() == ()


def test_completed_responses_tool_keeps_original_native_item():
    collector = ResponseCollector("openai.responses")
    item = {"type": "function_call", "id": "item", "call_id": "call", "name": "lookup", "arguments": '{"q":"value"}'}
    collector.feed({"status": "completed", "output": [item]})
    (part,) = collector.tool_parts()
    assert part.arguments == {"q": "value"}
    assert part.native == item


def test_explicit_provider_failure_remains_known_failure_metadata():
    collector = ResponseCollector("openai.responses")
    collector.feed({"type": "response.failed", "response": {
        "status": "failed", "error": {"code": "server_error"}, "usage": {"output_tokens": 30},
    }})
    assert collector.completed
    assert collector.completion.provider_status == "failed"
    assert collector.completion.state == "incomplete"
    assert collector.completion.stop_reasons == ("server_error",)


def test_old_saved_result_recovers_completion_without_changing_receipt():
    result = result_from_wire({"parts": [], "native_response": {"choices": [{"finish_reason": "length"}]},
        "receipt": {"attempt_id": "a", "intent_id": "i", "status": "succeeded", "dispatch": "submitted",
                    "route_id": "r", "route_revision": 1, "provider_id": "p", "model": "m",
                    "protocol": "openai.chat", "quota_group": "q"}})
    assert result.receipt.status == "succeeded"
    assert result.completion.state == "incomplete"
