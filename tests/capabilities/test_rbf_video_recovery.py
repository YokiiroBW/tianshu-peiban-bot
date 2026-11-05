from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.contracts import Fault, canonical
from tianshu.capabilities.execution_context import CURRENT_EXECUTION
from tianshu.capabilities.external import ProviderError
from tianshu.capabilities import action_checkpoint as checkpoint
from tianshu.capabilities.perception.service import Perception
from tianshu.capabilities.perception.understanding import interval
from tianshu.capabilities.perception.video import VideoParseFailure
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.serialization import wire
from tianshu.types import Action, Attempt, MediaRef, OriginKind, SourceRef, TrustedContext


def context():
    return TrustedContext("owner", "actor", None, None, ("scope",), ("scope",), 1, OriginKind.ACTOR)


def result(text):
    return ModelResult((ModelPart("text", text=text),), ModelReceipt(
        "model-attempt", "action", "succeeded", "submitted", "provider", 1, "model",
        "fixture", "gemini.generate", "fixture"), completion=ModelCompletion("complete", ("STOP",)))


def values():
    source = SourceRef("source", 1)
    media = MediaRef("source:source:1", "controlled", "audio/wav")
    action = Action("action", "intent", "video.observe", "actor", context(),
                    {"source": wire(source), "media": wire(media), "start": 0, "end": 2}, OriginKind.ACTOR)
    return action, Attempt("model-attempt", "action"), media, source


def setup_owner(broker):
    owner = Perception(broker, SimpleNamespace(), SimpleNamespace(check=AsyncMock()), None)
    owner._decode_video = AsyncMock(return_value={"coverage": {"unit": "seconds", "start": 0, "end": 2},
        "audio_ranges": [], "subtitle_ranges": [], "representations": [], "total_seconds": 2})
    owner._video_parts = AsyncMock(return_value=([ModelPart("text", text="original input"), audio_part()],
        {"modalities": ["audio"], "model_input_modalities": ["audio"], "frame_times": [],
         "audio_ranges": [{"start": 0, "end": 2}], "sampling": "sampled", "provider_sampling": "unknown", "omitted": []}))
    return owner


def audio_part():
    return ModelPart("audio", media=MediaRef("artifact:clip", "controlled", "audio/wav"))


@pytest.mark.asyncio
async def test_invalid_model_evidence_is_parse_failure_with_original_receipt(monkeypatch):
    action, attempt, media, source = values()
    bad = canonical({"summary": "description", "observations": [
        {"kind": "observed", "evidence_ids": ["not-an-input"], "detail": "unsupported"}]})
    owner = setup_owner(SimpleNamespace(generate=AsyncMock(return_value=result(bad))))
    saved = AsyncMock()
    monkeypatch.setattr(checkpoint, "load", AsyncMock(return_value={}))
    monkeypatch.setattr(checkpoint, "save", saved)
    with pytest.raises(VideoParseFailure) as failure:
        await owner._observe_segment(action, attempt, media, source, 0, 2)
    receipt = failure.value.receipt(action, attempt)
    assert receipt.execution == "failed" and receipt.effect == "none"
    assert receipt.error_code == "video_evidence_id_invalid" and receipt.result["phase"] == "parse"
    assert receipt.result["model_receipt"]["attempt_id"] == "model-attempt"
    assert receipt.result["source_refs"] == [wire(source)]
    assert saved.await_args_list[0].args[4]["segments"]["main"]["request"]["attempt_id"] == "model-attempt"


@pytest.mark.asyncio
async def test_commit_resume_uses_original_result_without_decode_or_new_model(monkeypatch):
    action, attempt, media, source = values()
    text = canonical({"summary": "heard sound", "observations": [
        {"start": 0, "end": 2, "modality": "audio", "detail": "sound"}]})
    broker = SimpleNamespace(status=AsyncMock(return_value=SimpleNamespace(status="succeeded")),
                             reusable_result=AsyncMock(return_value=result(text)), generate=AsyncMock())
    owner = setup_owner(broker)
    request = owner._video_request(action, attempt, [ModelPart("text", text="original input"), audio_part()], (source,))
    decoded = {"coverage": {"unit": "seconds", "start": 0, "end": 2}, "subtitle_ranges": []}
    inputs = {"modalities": ["audio"], "model_input_modalities": ["audio"], "frame_times": [],
              "audio_ranges": [{"start": 0, "end": 2}], "sampling": "sampled", "provider_sampling": "unknown", "omitted": []}
    saved = {"segments": {"main": {"source": wire(source), "media": wire(media),
        "requested": {"start": 0, "end": 2}, "model_range": {"start": 0, "end": 2},
        "request": wire(request), "decoded": decoded, "input": inputs, "dispatch_started": True,
        "registration_confirmed": True}}}
    monkeypatch.setattr(checkpoint, "load", AsyncMock(return_value=saved))
    monkeypatch.setattr(checkpoint, "save", AsyncMock())
    token = CURRENT_EXECUTION.set({"data_generation": "00000000-0000-0000-0000-000000000001"})
    try:
        _, _, model, parsed = await owner._observe_segment(action, Attempt("new-attempt", "action"), media, source, 0, 2)
    finally:
        CURRENT_EXECUTION.reset(token)
    assert parsed["observations"][0]["detail"] == "sound" and model.receipt.attempt_id == "model-attempt"
    owner._decode_video.assert_not_awaited()
    broker.generate.assert_not_awaited()
    broker.status.assert_awaited_once_with("model-attempt", phase="observe")
    broker.reusable_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_receipt_after_possible_dispatch_stays_unknown(monkeypatch):
    action, attempt, media, source = values()
    broker = SimpleNamespace(status=AsyncMock(return_value=None), reusable_result=AsyncMock(), generate=AsyncMock())
    owner = setup_owner(broker)
    request = owner._video_request(action, attempt, [ModelPart("text", text="input"), audio_part()], (source,))
    saved = {"segments": {"main": {"source": wire(source), "media": wire(media),
        "requested": {"start": 0, "end": 2}, "model_range": {"start": 0, "end": 2},
        "request": wire(request), "decoded": {}, "input": {"modalities": ["audio"], "frame_times": [],
            "audio_ranges": [{"start": 0, "end": 2}]}, "dispatch_started": True,
        "registration_confirmed": True}}}
    monkeypatch.setattr(checkpoint, "load", AsyncMock(return_value=saved))
    with pytest.raises(ProviderError) as error:
        await owner._observe_segment(action, attempt, media, source, 0, 2)
    assert error.value.effect == "unknown"
    broker.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfirmed_admission_reuses_exact_frozen_request_and_attempt(monkeypatch):
    action, attempt, media, source = values()
    text = canonical({"summary": "heard sound", "observations": []})
    broker = SimpleNamespace(status=AsyncMock(return_value=None), reusable_result=AsyncMock(),
                             generate=AsyncMock(return_value=result(text)))
    owner = setup_owner(broker)
    request = owner._video_request(action, attempt, [ModelPart("text", text="original input"), audio_part()], (source,))
    saved = {"segments": {"main": {"source": wire(source), "media": wire(media),
        "requested": {"start": 0, "end": 2}, "model_range": {"start": 0, "end": 2},
        "request": wire(request), "decoded": {"subtitle_ranges": []},
        "input": {"modalities": ["audio"], "frame_times": [], "audio_ranges": [{"start": 0, "end": 2}]},
        "dispatch_started": True, "registration_confirmed": False}}}
    monkeypatch.setattr(checkpoint, "load", AsyncMock(return_value=saved))
    monkeypatch.setattr(checkpoint, "save", AsyncMock())
    await owner._observe_segment(action, Attempt("new-action-attempt", "action"), media, source, 0, 2)
    broker.status.assert_awaited_once_with("model-attempt", phase="pre_submit")
    reused = broker.generate.await_args.args[0]
    assert wire(reused) == wire(request) and reused.attempt_id == "model-attempt"


def test_out_of_range_evidence_is_never_clamped():
    with pytest.raises(Fault):
        interval({"start": -0.01, "end": 1}, 0, 2)
    with pytest.raises(Fault):
        interval({"start": 0, "end": 2.01}, 0, 2)
