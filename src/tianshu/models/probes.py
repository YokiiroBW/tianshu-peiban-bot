"""Actual capability probes and their evidence, using the normal Models port."""

import asyncio
import base64
import hashlib
import json
import math
from dataclasses import replace
from datetime import UTC, datetime

from tianshu.runtime.errors import AppError

from .completion import content_completion
from .continuation import assistant_message
from .probe_media import data_uri, sample
from .protocols import chat_video_part
from .types import ModelMessage, ModelPart, ToolSpec

GENERATION_PROBES = ("text", "stream", "tools", "vision", "audio", "video", "json")


def capabilities(protocol):
    if protocol == "openai.embedding":
        return ("embed",)
    if protocol == "cohere.rerank":
        return ("rerank",)
    return tuple(
        value
        for value in GENERATION_PROBES
        if not (
            protocol == "anthropic.messages"
            and value in {"audio", "video", "json"}
            or protocol == "openai.responses"
            and value in {"audio", "video"}
        )
    )


def public_text(result):
    return "".join(
        part.text or ""
        for part in result.parts
        if part.kind == "text" and not part.native.get("thought")
    )


def complete(result):
    return bool(
        result
        and result.receipt.status == "succeeded"
        and content_completion(result).state == "complete"
    )


def prepare(request, capability):
    """Freeze a deterministic challenge and native synthetic media in the request."""
    request = replace(request, required_capabilities=frozenset({capability}))
    seed = hashlib.sha256(request.attempt_id.encode()).hexdigest()
    expected = "OK"
    parameters = dict(request.parameters)
    tools, native = (), None
    prompt = "Reply with OK."
    if capability == "tools":
        expected = seed[:12]
        tools = (
            ToolSpec(
                "tianshu_probe_echo",
                "Return the proof for the supplied value.",
                {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            ),
        )
        prompt = f'Call tianshu_probe_echo with value "{expected}". Then reply with only the proof returned by the tool.'
    elif capability == "json":
        expected = seed[:12]
        prompt = f'Return a JSON object with exactly one key, "proof", whose value is "{expected}".'
        if request.protocol == "gemini.generate":
            parameters["generationConfig"] = {"responseMimeType": "application/json"}
        elif request.protocol == "openai.responses":
            parameters["text"] = {"format": {"type": "json_object"}}
        else:
            parameters["response_format"] = {"type": "json_object"}
    elif capability in {"vision", "audio", "video"}:
        mime, data, expected = sample(capability, seed)
        prompt = {
            "vision": "Read the four digits in this image. Reply with only those digits.",
            "video": "Read the four-digit numbers shown in this video, in playback order. Reply only with the two numbers separated by a comma.",
            "audio": "This audio has three groups of beeps separated by long silences. Count the beeps in each group. Reply only with three numbers separated by commas.",
        }[capability]
        encoded = base64.b64encode(data).decode("ascii")
        if request.protocol == "gemini.generate":
            native = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": prompt},
                            {"inlineData": {"mimeType": mime, "data": encoded}},
                        ],
                    }
                ]
            }
        elif request.protocol == "anthropic.messages":
            native = {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": mime, "data": encoded},
                            },
                        ],
                    }
                ]
            }
        elif request.protocol == "openai.responses":
            native = {
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": data_uri(mime, data)},
                        ],
                    }
                ]
            }
        else:
            block = (
                {"type": "image_url", "image_url": {"url": data_uri(mime, data)}}
                if capability == "vision"
                else chat_video_part(data_uri(mime, data))
                if capability == "video"
                else {"type": "input_audio", "input_audio": {"format": "wav", "data": encoded}}
            )
            native = {
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, block]}]
            }
    elif capability == "embed":
        return replace(
            request,
            max_output_tokens=0,
            parameters={},
            messages=(
                ModelMessage(
                    "user",
                    (
                        ModelPart("text", text="A red apple."),
                        ModelPart("text", text="A blue bicycle."),
                    ),
                ),
            ),
        ), expected
    elif capability == "rerank":
        return replace(
            request,
            max_output_tokens=0,
            parameters={},
            system="Which item is a fruit?",
            messages=(
                ModelMessage(
                    "user",
                    (
                        ModelPart("text", text="A red apple."),
                        ModelPart("text", text="A blue bicycle."),
                    ),
                ),
            ),
        ), expected
    if native is not None:
        native.update(parameters)
    return replace(
        request,
        messages=(ModelMessage("user", (ModelPart("text", text=prompt),)),),
        tools=tools,
        native_payload=native,
        parameters=parameters,
    ), expected


def matched(result, capability, expected):
    if not result or result.receipt.status != "succeeded":
        return False
    if capability == "embed":
        rows = (result.native_response or {}).get("data") or []
        return (
            isinstance(rows, list)
            and len(rows) == 2
            and all(isinstance(r, dict) and type(r.get("index")) is int for r in rows)
            and {r.get("index") for r in rows} == {0, 1}
            and all(
                isinstance(r.get("embedding"), list)
                and len(r["embedding"]) == 1024
                and all(type(x) in {int, float} and math.isfinite(x) for x in r["embedding"])
                and any(r["embedding"])
                for r in rows
            )
        )
    if capability == "rerank":
        rows = (result.native_response or {}).get("results") or []
        return (
            isinstance(rows, list)
            and len(rows) == 2
            and all(isinstance(r, dict) and type(r.get("index")) is int for r in rows)
            and {r.get("index") for r in rows} == {0, 1}
            and rows[0]["index"] == 0
            and all(
                type(r.get("relevance_score")) in {int, float}
                and math.isfinite(r["relevance_score"])
                for r in rows
            )
            and rows[0]["relevance_score"] >= rows[1]["relevance_score"]
        )
    if not complete(result):
        return False
    text = public_text(result).strip()
    if capability == "json":
        try:
            return json.loads(text) == {"proof": expected}
        except (ValueError, TypeError):
            return False
    if capability in {"text", "stream"}:
        return bool(text)
    return text.strip('`" \n').replace("，", ",").replace(" ", "") == expected


async def _observe_or_run(models, request, *, stream=False):
    original = await models.result(request.attempt_id, context=request.context)
    if original is not None:
        _check_original(request, original)
        return original, False
    saw_stream = False
    try:
        if stream:
            async for event in models.stream(request):
                saw_stream |= event.kind == "text" and bool(event.text)
            return await models.result(request.attempt_id, context=request.context), saw_stream
        return await models.generate(request), False
    except AppError:
        original = await models.result(request.attempt_id, context=request.context)
        if original is None:
            raise
        _check_original(request, original)
        return original, saw_stream


def _check_original(request, result):
    original = result.receipt
    if (
        original.attempt_id != request.attempt_id
        or original.intent_id != request.intent_id
        or original.route_id != request.route_id
        or original.route_revision != request.route_revision
        or original.protocol != request.protocol
    ):
        raise AppError(
            "probe_original_route_changed", status=409, details={"attempt_id": original.attempt_id}
        )


async def run(models, request, capability):
    request, expected = await asyncio.to_thread(prepare, request, capability)
    result, saw_stream = await _observe_or_run(models, request, stream=capability == "stream")
    receipts = [result.receipt] if result else []
    if capability == "tools" and complete(result):
        calls = [part for part in result.parts if part.kind == "tool_call"]
        if (
            len(calls) != 1
            or calls[0].name != "tianshu_probe_echo"
            or calls[0].arguments != {"value": expected}
            or not calls[0].call_id
        ):
            return result, receipts, False, "tool_call_mismatch"
        proof = hashlib.sha256((expected + ":tool-result").encode()).hexdigest()[:12]
        followup = replace(
            request,
            attempt_id=request.attempt_id + "-result",
            messages=(
                *request.messages,
                assistant_message(result),
                ModelMessage(
                    "tool",
                    (
                        ModelPart(
                            "tool_result",
                            call_id=calls[0].call_id,
                            name=calls[0].name,
                            result={"proof": proof},
                        ),
                    ),
                ),
            ),
        )
        result, _ = await _observe_or_run(models, followup)
        if result:
            receipts.append(result.receipt)
        expected = proof
    verified = matched(result, capability, expected)
    if capability == "stream":
        verified &= saw_stream
    if verified:
        reason = None
    elif not result or result.receipt.status != "succeeded":
        reason = "model_not_succeeded"
    elif capability not in {"embed", "rerank"} and not complete(result):
        reason = "probe_output_incomplete"
    elif capability == "stream" and not saw_stream:
        reason = "stream_output_not_observed"
    else:
        reason = "probe_output_mismatch"
    return result, receipts, verified, reason


def evidence(previous, capability, receipts):
    value = dict(previous or {})
    tests = dict(value["capabilities"]) if isinstance(value.get("capabilities"), dict) else {}
    observed = datetime.now(UTC).isoformat()
    entry = {
        "source": "provider_test",
        "attempt_ids": [r.attempt_id for r in receipts],
        "observed_at": observed,
        "fixture": "tianshu.capability-probe.v1",
    }
    tests[capability] = entry
    if capability == "stream":
        tests["text"] = entry
    value.update(
        source="provider_test",
        attempt_id=receipts[-1].attempt_id,
        observed_at=observed,
        capabilities=tests,
    )
    return value
