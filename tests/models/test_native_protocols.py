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
