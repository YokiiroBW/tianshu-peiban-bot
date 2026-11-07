from datetime import UTC, datetime, timedelta
import pytest

from tianshu.models.catalog import RoutePlan
from tianshu.models.protocols import NativeCompiler, ResponseCollector, normalize_usage, requested_capabilities
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest, ToolSpec
from tianshu.runtime.config import Secrets
from tianshu.runtime.errors import AppError
from tianshu.runtime.files import FileStore
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def request(messages=(), **kwargs):
    context = TrustedContext("p", "a", "person", "c", ("private",), ("person",), 1, OriginKind.HUMAN)
    return ModelRequest("intent", "attempt", "chat", ServiceClass.INTERACTIVE, context,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=30)), messages, **kwargs)


def route(protocol="gemini.generate"):
    return RoutePlan("main", 1, "supplier", "configured-model", protocol, "https://provider.test/v1beta",
                     "TEST_TOKEN", "shared-account", frozenset({"text", "tools", "audio", "video"}), {})


def test_gemini_media_and_tool_continuation_preserve_native_signature(tmp_path):
    files = FileStore(tmp_path)
    audio = files.put_bytes(b"synthetic audio fixture", media_type="audio/wav")
    video = files.put_bytes(b"synthetic video fixture", media_type="video/mp4")
    signature = {"functionCall": {"name": "weather", "args": {"city": "杭州"}, "id": "call-1"},
                 "thoughtSignature": "opaque-provider-signature"}
    model_request = request((
        ModelMessage("user", (ModelPart("audio", media=audio), ModelPart("video", media=video))),
        ModelMessage("assistant", (ModelPart("tool_call", name="weather", call_id="call-1",
            arguments={"city": "杭州"}, native=signature),)),
        ModelMessage("tool", (ModelPart("tool_result", name="weather", call_id="call-1",
                                      result={"temperature": 18}),)),
    ), tools=(ToolSpec("weather", "天气", {"type": "object"}),))
    call = NativeCompiler(files, Secrets({"TEST_TOKEN": "fixture-key"})).compile(
        route(), model_request, stream=False)
    assert call.body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "audio/wav"
    assert call.body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "video/mp4"
    assert call.body["contents"][1]["parts"][0]["thoughtSignature"] == signature["thoughtSignature"]
    assert call.body["contents"][2]["parts"][0]["functionResponse"]["id"] == "call-1"


def test_missing_usage_and_cache_are_not_reported_as_zero():
    unknown = normalize_usage("gemini.generate", None, estimated=123)
    assert unknown.input_tokens is None and unknown.cached_input_tokens is None
    assert unknown.estimated_input_tokens == 123 and unknown.metering == "unknown"
    partial = normalize_usage("openai.chat", {"prompt_tokens": 30, "completion_tokens": 5})
    assert partial.input_tokens == 30 and partial.cached_input_tokens is None


def test_gemini_private_thought_is_retained_without_public_text():
    collector = ResponseCollector("gemini.generate")
    parts = collector.feed({"candidates": [{"content": {"parts": [
        {"text": "private reasoning", "thought": True, "thoughtSignature": "signed"},
        {"text": "公开回答"}]}, "finishReason": "STOP"}]})
    assert "".join(part.text or "" for part in parts) == "公开回答"
    assert parts[0].native["thoughtSignature"] == "signed"


def test_responses_continuation_is_not_converted_to_chat(tmp_path):
    native = {"previous_response_id": "provider-state", "input": [{"type": "function_call_output",
              "call_id": "tool-1", "output": "done"}], "reasoning": {"effort": "high"}}
    call = NativeCompiler(FileStore(tmp_path), Secrets({"TEST_TOKEN": "fixture-key"})).compile(
        route("openai.responses"), request(native_payload=native, provider_session_scope="conversation"), stream=False)
    assert call.body["previous_response_id"] == "provider-state"
    assert call.body["input"] == native["input"]
    assert call.body["reasoning"] == {"effort": "high"}


def test_native_responses_modalities_require_actual_route_capabilities():
    native = {"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "https://example.test/image.png"},
        {"type": "input_audio", "input_audio": {"data": "fixture", "format": "wav"}}]}]}
    assert {"vision", "audio"} <= requested_capabilities(request(native_payload=native))


def test_chat_does_not_relabel_ogg_as_mp3(tmp_path):
    files = FileStore(tmp_path)
    audio = files.put_bytes(b"OggS synthetic fixture", media_type="audio/ogg")
    model_request = request((ModelMessage("user", (ModelPart("audio", media=audio),)),))
    with pytest.raises(AppError, match="native_audio_requires_transcode"):
        NativeCompiler(files, Secrets({"TEST_TOKEN": "fixture-key"})).compile(
            route("openai.chat"), model_request, stream=False)


def test_chat_video_uses_the_native_video_url_extension(tmp_path):
    from tianshu.models.probe_media import sample

    files = FileStore(tmp_path)
    mime, data, _ = sample("video", "0123456789abcdef" * 4)
    media = files.put_bytes(data, media_type=mime)
    model_request = request((ModelMessage("user", (ModelPart("video", media=media),)),))
    call = NativeCompiler(files, Secrets({"TEST_TOKEN": "fixture-key"})).compile(route("openai.chat"), model_request, stream=False)
    block = call.body["messages"][0]["content"][0]
    assert block["type"] == "video_url" and block["video_url"]["url"].startswith("data:video/mp4;base64,")
    assert "video" in requested_capabilities(request(native_payload=call.body))


def test_non_streaming_chat_reasoning_is_private_and_available_to_continuation():
    from types import SimpleNamespace
    from tianshu.models.continuation import assistant_message

    native = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "public", "reasoning_content": "private"}, "finish_reason": "stop"}]}
    collector = ResponseCollector("openai.chat")
    collector.feed(native)
    original = SimpleNamespace(parts=tuple(collector.parts), native_response=collector.native_response,
        receipt=SimpleNamespace(protocol="openai.chat"))
    message = assistant_message(original)
    assert message.native == {"reasoning_content": "private"}
    assert "".join(part.text for part in message.parts) == "public"
    # Old saved results did not have private canonical parts; their native field is enough.
    original.parts = (ModelPart("text", text="public"),)
    assert assistant_message(original).native == message.native


def test_chat_rejects_malformed_reasoning_content():
    with pytest.raises(AppError, match="invalid_native_chat_response"):
        ResponseCollector("openai.chat").feed({"choices": [{"index": 0, "message": {
            "role": "assistant", "content": "public", "reasoning_content": ["not a string"]}, "finish_reason": "stop"}]})
