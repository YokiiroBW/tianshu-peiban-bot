"""Synthetic probes earn only the capability they actually exercise."""

import base64
import io
import json
import wave
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import av
import pytest
from PIL import Image

from tianshu.models import probes
from tianshu.models.catalog import RoutePlan
from tianshu.models.protocols import NativeCompiler, ResponseCollector, requested_capabilities
from tianshu.models.types import (
    ModelCompletion,
    ModelEvent,
    ModelPart,
    ModelReceipt,
    ModelRequest,
    ModelResult,
)
from tianshu.models.upstream_error import describe
from tianshu.runtime.config import Secrets
from tianshu.runtime.errors import AppError
from tianshu.runtime.files import FileStore
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def request(protocol="openai.chat"):
    context = TrustedContext(
        "operator", None, None, None, (), (), 1, OriginKind.HUMAN, frozenset({"models.manage"})
    )
    return ModelRequest(
        "probe",
        "probe-attempt",
        "chat",
        ServiceClass.INTERACTIVE,
        context,
        RequestBudget(datetime.now(UTC) + timedelta(seconds=180)),
        (),
        protocol=protocol,
        route_id="route",
        route_revision=1,
        capability_probe=True,
    )


def result(req, text="OK", *, status="succeeded", parts=None, native=None):
    return ModelResult(
        tuple(parts) if parts is not None else (ModelPart("text", text=text),),
        ModelReceipt(
            req.attempt_id,
            req.intent_id,
            status,
            "submitted",
            "route",
            1,
            "provider",
            "model",
            req.protocol,
            "quota",
        ),
        native_response=native,
        completion=ModelCompletion("complete", ("stop",)),
    )


def plan(protocol="openai.chat"):
    return RoutePlan(
        "route",
        1,
        "provider",
        "model",
        protocol,
        "https://fixture.invalid/v1",
        "KEY",
        "quota",
        frozenset(),
        {},
    )


async def test_tool_probe_runs_both_rounds_and_preserves_reasoning(tmp_path):
    cache, requests = {}, []

    async def read(attempt, *, context):
        return cache.get(attempt)

    async def generate(req):
        requests.append(req)
        if len(requests) == 1:
            _, expected = probes.prepare(req, "tools")
            collector = ResponseCollector("openai.chat")
            collector.feed(
                {
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": "private tool reasoning",
                                "tool_calls": [
                                    {
                                        "id": "call",
                                        "type": "function",
                                        "function": {
                                            "name": "tianshu_probe_echo",
                                            "arguments": json.dumps({"value": expected}),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            )
            answer = result(
                req,
                parts=(*collector.parts, *collector.tool_parts()),
                native=collector.native_response,
            )
        else:
            call = NativeCompiler(FileStore(tmp_path), Secrets({"KEY": "fixture"})).compile(
                plan(), req, stream=False
            )
            assert call.body["messages"][1]["reasoning_content"] == "private tool reasoning"
            assert call.body["messages"][1]["content"] is None
            assert "private tool reasoning" not in json.dumps(call.body["messages"][1]["content"])
            proof = req.messages[-1].parts[0].result["proof"]
            answer = result(req, proof)
        cache[req.attempt_id] = answer
        return answer

    models = SimpleNamespace(result=read, generate=generate)
    answer, receipts, verified, reason = await probes.run(models, request(), "tools")
    assert verified and reason is None and len(receipts) == 2
    assert probes.public_text(answer) == requests[-1].messages[-1].parts[0].result["proof"]
    # An interrupted registration reuses both original receipts, not new paid attempts.
    assert (await probes.run(models, request(), "tools"))[2]
    assert len(requests) == 2


@pytest.mark.parametrize("status", ["unknown", "running", "failed", "cancelled"])
async def test_existing_unsettled_or_failed_probe_never_submits_another_attempt(status):
    req = request()
    models = SimpleNamespace(
        result=AsyncMock(return_value=result(req, status=status)), generate=AsyncMock()
    )
    assert not (await probes.run(models, req, "tools"))[2]
    models.generate.assert_not_awaited()


async def test_successful_text_is_not_a_successful_tool_probe():
    models = SimpleNamespace(
        result=AsyncMock(return_value=None), generate=AsyncMock(return_value=result(request()))
    )
    _, receipts, verified, reason = await probes.run(models, request(), "tools")
    assert not verified and reason == "tool_call_mismatch" and len(receipts) == 1


@pytest.mark.parametrize("kind", ["vision", "video", "audio"])
def test_media_probe_sends_decodable_media_and_checks_hidden_answer(kind):
    req, expected = probes.prepare(request(), kind)
    content = req.native_payload["messages"][0]["content"]
    assert expected not in content[0]["text"]
    assert kind in requested_capabilities(req)
    if kind == "audio":
        raw = base64.b64decode(content[1]["input_audio"]["data"])
        with wave.open(io.BytesIO(raw)) as audio:
            assert (
                audio.getnchannels() == 1
                and audio.getframerate() == 16000
                and audio.getnframes() > 16000
            )
    else:
        raw = base64.b64decode(
            content[1]["image_url" if kind == "vision" else "video_url"]["url"].split(",", 1)[1]
        )
        if kind == "vision":
            assert Image.open(io.BytesIO(raw)).size == (512, 160)
        else:
            with av.open(io.BytesIO(raw)) as video:
                frames = list(video.decode(video=0))
                assert (
                    len(frames) == 8
                    and frames[0].to_image().tobytes() != frames[-1].to_image().tobytes()
                )
    assert probes.matched(result(req, expected), kind, expected)
    assert not probes.matched(result(req, "OK"), kind, expected)


@pytest.mark.parametrize(
    "protocol,kind",
    [
        ("gemini.generate", "video"),
        ("gemini.generate", "audio"),
        ("anthropic.messages", "vision"),
        ("openai.responses", "vision"),
    ],
)
def test_media_probes_use_the_selected_native_protocol(protocol, kind, tmp_path):
    req, _ = probes.prepare(request(protocol), kind)
    call = NativeCompiler(FileStore(tmp_path), Secrets({"KEY": "fixture"})).compile(
        plan(protocol), req, stream=False
    )
    assert kind in requested_capabilities(req)
    assert ("contents" in call.body) is (protocol == "gemini.generate")
    assert ("input" in call.body) is (protocol == "openai.responses")


def test_capability_evidence_merges_without_erasing_window_or_other_tests():
    previous = {
        "context_window_tokens": 200000,
        "note": "operator evidence",
        "capabilities": {"vision": {"source": "fixture"}},
    }
    updated = probes.evidence(previous, "tools", [result(request()).receipt])
    assert updated["context_window_tokens"] == 200000 and updated["note"] == "operator evidence"
    assert updated["capabilities"]["vision"] == previous["capabilities"]["vision"]
    assert updated["capabilities"]["tools"]["attempt_ids"] == ["probe-attempt"]
    assert "tools" not in previous["capabilities"]


@pytest.mark.parametrize(
    "text,completion,accepted",
    [
        ('{"proof":"nonce"}', "complete", True),
        ('```json\n{"proof":"nonce"}\n```', "complete", False),
        ('{"proof":"wrong"}', "complete", False),
        ('{"proof":"nonce"}', "incomplete", False),
    ],
)
def test_structured_probe_requires_complete_valid_expected_json(text, completion, accepted):
    actual = replace(result(request(), text), completion=ModelCompletion(completion))
    assert probes.matched(actual, "json", "nonce") is accepted


def test_private_reasoning_alone_cannot_verify_public_text():
    actual = result(
        request(),
        parts=[ModelPart("text", text="", native={"thought": True, "reasoning_content": "OK"})],
    )
    assert not probes.matched(actual, "text", "OK")


def test_embedding_and_rerank_verify_native_results():
    vectors = {"data": [{"index": i, "embedding": [1.0] * 1024} for i in range(2)]}
    assert probes.matched(result(request("openai.embedding"), native=vectors), "embed", "")
    vectors["data"][1]["embedding"][0] = float("nan")
    assert not probes.matched(result(request("openai.embedding"), native=vectors), "embed", "")
    ranked = {
        "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}]
    }
    assert probes.matched(result(request("cohere.rerank"), native=ranked), "rerank", "")
    ranked["results"].reverse()
    assert not probes.matched(result(request("cohere.rerank"), native=ranked), "rerank", "")


def test_embedding_probe_uses_embedding_input_without_generation_parameters(tmp_path):
    req, _ = probes.prepare(
        replace(request("openai.embedding"), function="embed", parameters={"max_tokens": 8192}),
        "embed",
    )
    call = NativeCompiler(FileStore(tmp_path), Secrets({"KEY": "fixture"})).compile(
        plan("openai.embedding"), req, stream=False
    )
    assert (
        len(call.body["input"]) == 2
        and call.body["dimensions"] == 1024
        and "max_tokens" not in call.body
    )


async def test_stream_probe_requires_actual_public_stream_output():
    req = request()
    actual = None

    async def read(*_args, **_kwargs):
        return actual

    async def stream(submitted):
        nonlocal actual
        actual = result(submitted)
        yield ModelEvent("text", text="OK")
        yield ModelEvent("receipt", receipt=actual.receipt)

    models = SimpleNamespace(result=read, stream=stream)
    assert (await probes.run(models, req, "stream"))[2]
    # A stored success alone does not demonstrate that a stream was observed.
    assert not (await probes.run(models, req, "stream"))[2]


async def test_provider_failure_is_read_from_original_receipt():
    original = None
    req = request()

    async def read(*_args, **_kwargs):
        return original

    async def generate(submitted):
        nonlocal original
        original = result(submitted, status="failed")
        original = replace(
            original,
            receipt=replace(
                original.receipt,
                error_code="upstream_http_429",
                diagnostic={"upstream": {"category": "rate_limit", "status": 429}},
            ),
        )
        raise AppError("upstream_http_429", status=502)

    observed, receipts, verified, _ = await probes.run(
        SimpleNamespace(result=read, generate=generate), req, "text"
    )
    assert not verified and observed is original and receipts == [original.receipt]


@pytest.mark.parametrize("protocol", ["openai.chat", "openai.responses", "gemini.generate"])
def test_json_probe_requests_native_structured_output(protocol):
    prepared, _ = probes.prepare(request(protocol), "json")
    assert prepared.required_capabilities == frozenset({"json"})
    if protocol == "openai.chat":
        assert prepared.parameters["response_format"] == {"type": "json_object"}
    elif protocol == "openai.responses":
        assert prepared.parameters["text"] == {"format": {"type": "json_object"}}
    else:
        assert prepared.parameters["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize("capability,key", [("embed", "data"), ("rerank", "results")])
def test_malformed_native_results_are_not_capability_evidence(capability, key):
    for rows in ([None, None], [{"index": []}, {"index": {}}], [{"index": False}, {"index": True}]):
        assert not probes.matched(result(request(), native={key: rows}), capability, "")


@pytest.mark.parametrize(
    "code,category",
    [
        ("insufficient_quota", "quota_or_balance"),
        ("rate_limit_exceeded", "rate_limit"),
        ("quota_exceeded_error", "rate_or_quota"),
    ],
)
def test_error_diagnostic_distinguishes_known_causes_without_retaining_messages(code, category):
    raw = json.dumps(
        {
            "error": {
                "code": code,
                "message": "private prompt and secret",
                "param": "messages.1.reasoning_content",
            }
        }
    )
    value = describe(429, raw)
    assert value["category"] == category and value["param"] == "messages.1.reasoning_content"
    assert "private" not in json.dumps(value) and "message" not in value
    assert (
        describe(429, json.dumps({"error": {"code": "sk-private-key"}}))["category"]
        == "rate_or_quota"
    )
