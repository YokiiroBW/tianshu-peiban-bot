"""Chat nullable fields follow the official OpenAI generated protocol types.

https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion_message.py
https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion_chunk.py
"""

import pytest

from tianshu.models.protocols import ResponseCollector, normalize_usage
from tianshu.runtime.errors import AppError


def response(message, *, finish="stop"):
    if isinstance(message, dict):
        message = {"role": "assistant", **message}
    return {
        "id": "fixture-completion",
        "object": "chat.completion",
        "created": 1,
        "model": "fixture-model",
        "usage": None,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }


def chunk(delta, *, finish=None, index=0):
    return {
        "id": "fixture-completion",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fixture-model",
        "usage": None,
        "choices": [{"index": index, "delta": delta, "finish_reason": finish}],
    }


def test_nonstream_text_allows_null_tool_calls_without_inventing_usage():
    collector = ResponseCollector("openai.chat")
    original = response(
        {
            "role": "assistant",
            "content": '{"claims":[],"matters":[]}',
            "tool_calls": None,
            "refusal": None,
        }
    )
    parts = collector.feed(original)
    assert [part.text for part in parts] == ['{"claims":[],"matters":[]}']
    assert collector.completed and collector.tool_parts() == ()
    assert collector.native_response is original
    usage = normalize_usage("openai.chat", collector.raw_usage, estimated=123)
    assert usage.input_tokens is usage.output_tokens is usage.cached_input_tokens is None
    assert usage.metering == "unknown" and usage.estimated_input_tokens == 123


def test_stream_nullable_deltas_and_usage_trailer_preserve_real_finish():
    collector = ResponseCollector("openai.chat")
    assert collector.feed(chunk({"role": "assistant", "content": None, "tool_calls": None})) == []
    assert not collector.completed
    collector.feed(chunk({"content": "actual text", "tool_calls": None}))
    collector.feed(chunk({"content": None, "tool_calls": None}, finish="stop"))
    assert collector.completed
    usage = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    collector.feed({"choices": [], "usage": usage})
    assert collector.completed and collector.raw_usage is usage
    assert [part.text for part in collector.parts] == ["actual text"]
    assert collector.tool_parts() == ()


def test_nullable_tool_fragments_keep_original_ids_and_accumulate_by_index():
    collector = ResponseCollector("openai.chat")
    collector.feed(
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-weather",
                        "type": "function",
                        "function": {"name": "wea", "arguments": ""},
                    },
                    {
                        "index": 1,
                        "id": "call-clock",
                        "type": "function",
                        "function": {"name": "clock", "arguments": "{"},
                    },
                ]
            }
        )
    )
    collector.feed(
        chunk(
            {
                "content": None,
                "tool_calls": [
                    {"index": 0, "id": None, "type": None, "function": None},
                    {"index": 1, "id": None, "function": {"name": None, "arguments": None}},
                ],
            }
        )
    )
    assert not collector.completed
    collector.feed(
        chunk(
            {
                "tool_calls": [
                    {"index": 0, "id": None, "function": {"name": "ther", "arguments": '{"city":'}},
                    {"index": 1, "id": None, "function": {"name": None, "arguments": "}"}},
                ]
            }
        )
    )
    collector.feed(
        chunk(
            {
                "tool_calls": [
                    {"index": 0, "id": None, "function": {"name": None, "arguments": '"杭州"}'}},
                ]
            },
            finish="tool_calls",
        )
    )
    parts = collector.tool_parts()
    assert [(part.call_id, part.name, part.arguments) for part in parts] == [
        ("call-weather", "weather", {"city": "杭州"}),
        ("call-clock", "clock", {}),
    ]
    assert collector.raw_usage is None and collector.completed


def test_nonstream_tool_response_accepts_null_content_but_keeps_actual_arguments():
    collector = ResponseCollector("openai.chat")
    collector.feed(
        response(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "actual-call",
                        "type": "function",
                        "function": {"name": "weather", "arguments": "{}"},
                    },
                ],
            },
            finish="tool_calls",
        )
    )
    (part,) = collector.tool_parts()
    assert part.call_id == "actual-call" and part.name == "weather" and part.arguments == {}


def test_missing_finish_or_usage_alone_cannot_claim_a_complete_response():
    collector = ResponseCollector("openai.chat")
    collector.feed(chunk({"content": "partial text", "tool_calls": None}))
    collector.feed({"choices": [], "usage": {"completion_tokens": 3}})
    assert not collector.completed
    assert collector.parts[0].text == "partial text"
    unfinished = ResponseCollector("openai.chat")
    unfinished.feed(
        response({"role": "assistant", "content": "partial", "tool_calls": None}, finish=None)
    )
    assert not unfinished.completed


def test_one_finished_choice_does_not_complete_another_unfinished_choice():
    collector = ResponseCollector("openai.chat")
    first = chunk({"content": "first"}, finish="stop")
    first["choices"].append({"index": 1, "delta": {"content": "second"}, "finish_reason": None})
    collector.feed(first)
    assert not collector.completed
    collector.feed(chunk({"tool_calls": None}, finish="stop", index=1))
    assert collector.completed


@pytest.mark.parametrize(
    "malformed",
    [
        {"choices": None},
        {"choices": {}},
        {"choices": [None]},
        response(None),
        response({"role": None, "content": "text", "tool_calls": None}),
        chunk(None, finish="stop"),
        response({"content": [], "tool_calls": None}),
        response({"content": "text", "tool_calls": False}),
        response({"content": "text", "tool_calls": [None]}),
        response(
            {"content": None, "tool_calls": [{"id": "call", "type": "function", "function": None}]},
            finish="tool_calls",
        ),
        response(
            {
                "content": None,
                "tool_calls": [
                    {"id": "call", "type": "function", "function": {"name": "f", "arguments": None}}
                ],
            },
            finish="tool_calls",
        ),
    ],
)
def test_required_structures_and_nonstream_tool_fields_are_not_nullable(malformed):
    collector = ResponseCollector("openai.chat")
    with pytest.raises(AppError, match="invalid_native_chat_response"):
        collector.feed(malformed)
    assert not collector.completed


@pytest.mark.parametrize(
    "call",
    [
        {"index": 0, "id": None, "function": {"name": "weather", "arguments": "{}"}},
        {"index": 0, "id": "call", "function": {"name": None, "arguments": "{}"}},
        {"index": 0, "id": "call", "function": {"name": "weather", "arguments": '{"city":'}},
        {"index": 0, "id": "call", "function": {"name": "weather", "arguments": ""}},
    ],
)
def test_finished_generation_does_not_fabricate_incomplete_tool_calls(call):
    collector = ResponseCollector("openai.chat")
    collector.feed(chunk({"tool_calls": [call]}, finish="tool_calls"))
    assert collector.completed  # The provider did finish; its tool output is unusable.
    with pytest.raises(AppError, match="(incomplete|invalid)_native_tool_call"):
        collector.tool_parts()
