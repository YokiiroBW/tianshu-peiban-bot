"""Inline image transport bytes are not a provider's visual token count."""
import base64
import json

import pytest

from tianshu.models.estimation import input_tokens, media_tokens
from test_output_budget import compiled_broker, plan, request


def native_image(protocol, encoded):
    uri = "data:image/png;base64," + encoded
    if protocol == "gemini.generate":
        return {"contents": [{"role": "user", "parts": [{"text": "Inspect the fixture image."},
            {"inlineData": {"mimeType": "image/png", "data": encoded}}]}]}
    if protocol == "openai.responses":
        return {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": uri}]}]}
    part = ({"type": "image_url", "image_url": {"url": uri}} if protocol == "openai.chat" else
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}})
    return {"messages": [{"role": "user", "content": [part]}]}


def byte_fallback(body):
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


@pytest.mark.parametrize("protocol", ["gemini.generate", "openai.chat", "openai.responses", "anthropic.messages"])
def test_inline_image_reservation_is_independent_of_base64_transport_size(protocol):
    # Synthetic encoding fixture, not a provider image-understanding acceptance.
    small = base64.b64encode(b"fixture").decode()
    large = base64.b64encode(b"x" * 1_814_148).decode()
    body = native_image(protocol, large)
    original = json.dumps(body)
    assert input_tokens(protocol, body) == byte_fallback(body) - len(large) + 2048
    assert input_tokens(protocol, body) == input_tokens(protocol, native_image(protocol, small))
    assert input_tokens(protocol, body) < 4096
    assert json.dumps(body) == original  # Encoding and network bytes remain present.


def test_only_known_modality_positions_are_discounted_not_text_or_tool_arguments():
    encoded = base64.b64encode(b"x" * 100_000).decode()
    body = native_image("gemini.generate", encoded)
    parts = body["contents"][0]["parts"]
    parts.append({"text": "data:image/png;base64," + encoded})
    parts.append({"functionCall": {"name": "keep_actual_arguments", "args": {
        "inlineData": {"mimeType": "image/png", "data": encoded}}}})
    assert input_tokens("gemini.generate", body) == byte_fallback(body) - len(encoded) + 2048
    assert input_tokens("gemini.generate", body) > 2 * len(encoded)


@pytest.mark.parametrize("body", [
    {"contents": [{"parts": [{"inlineData": {"mimeType": "image/png", "data": "invalid??"}}]}]},
    {"contents": [{"parts": [{"inlineData": None}, {"text": "actual text"}]}]},
    {"contents": None},
    {"contents": [{"parts": [{"fileData": {"mimeType": "image/png", "fileUri": "https://fixture.invalid/image.png"}}]}]},
])
def test_unrecognized_or_invalid_media_keeps_the_old_conservative_fallback(body):
    assert input_tokens("gemini.generate", body) == byte_fallback(body)


def test_shared_media_rules_keep_duration_and_unknown_semantics():
    assert media_tokens("image", settings={"image_tokens": 4096}) == 4096
    assert media_tokens("audio", 1500) == 75
    assert media_tokens("video", 1500) == 384
    assert media_tokens("audio") == media_tokens("video") == 8192


@pytest.mark.asyncio
@pytest.mark.parametrize("image,explicit", [(True, None), (True, 123), (False, None)])
async def test_broker_image_admission_is_bounded_but_reported_usage_still_settles_actual_tokens(tmp_path, image, explicit):
    encoded = base64.b64encode(b"x" * 1_814_148).decode()
    body = native_image("gemini.generate", encoded) if image else {"contents": [{"parts": [{"text": "原文本保持字节估计。"}]}]}
    native = {"candidates": [{"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}],
              "usageMetadata": {"promptTokenCount": 1460, "candidatesTokenCount": 750}}
    broker, sent = compiled_broker(tmp_path, plan("gemini.generate", {}), native)
    events = [event async for event in broker._events(request(function="vision" if image else "chat", native_payload=body,
        estimated_input_tokens=explicit, max_output_tokens=1200), stream=False)]
    result = next(event.data for event in events if event.kind == "receipt")
    estimate = explicit if explicit is not None else input_tokens("gemini.generate", sent[0])
    if image:
        assert estimate < 4096
        assert sent[0]["contents"][0]["parts"][1]["inlineData"]["data"] == encoded
    else:
        assert estimate == byte_fallback(sent[0])
    units = broker.ledger.create.call_args.args[2]
    assert units == {"tokens": estimate + 1200}
    assert broker.scheduler.acquire.call_args.args[2] == units
    assert result.receipt.usage.estimated_input_tokens == estimate
    assert result.receipt.usage.input_tokens == 1460 and result.receipt.usage.output_tokens == 750
    assert result.receipt.usage.metering == "reported"
    assert broker.scheduler.release.call_args.kwargs["units"] == {"tokens": 2210}
