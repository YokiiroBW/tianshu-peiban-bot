"""Synthetic inputs exercise the evidence boundary without an external model."""

import json

import pytest

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.perception.evidence import catalog
from tianshu.capabilities.perception.understanding import interval, observation
from tianshu.models.types import ModelPart
from tianshu.types import MediaRef, SourceRef


SOURCE = SourceRef("source", 3)
VIDEO = MediaRef("source:source:3", "controlled", "video/mp4", sha256="original")
FRAME = ModelPart("image", media=MediaRef("artifact:frame", "controlled", "image/jpeg", sha256="frame"))
AUDIO = ModelPart("audio", media=MediaRef("artifact:audio", "controlled", "audio/wav", sha256="audio"))


def sampled(*, audio=True, subtitle=False):
    parts = [ModelPart("text", text="actual subtitle text"), FRAME]
    if audio:
        parts.append(AUDIO)
    inputs = {"modalities": ["image", "audio"] if audio else ["image"],
              "model_input_modalities": ["image", "audio"] if audio else ["image"],
              "frame_times": [0.1], "audio_ranges": [{"start": 0.0689375, "end": 0.1611875}] if audio else []}
    decoded = {"source_has_audio": audio, "text": "actual subtitle text",
               "subtitle_ranges": [{"start": 0.0689375, "end": 0.1611875}] if subtitle else []}
    return catalog(SOURCE, VIDEO, parts, inputs, decoded, 0.0689375, 0.1611875)


def encoded(items):
    return json.dumps({"summary": "synthetic summary", "observations": items,
                       "unresolved_questions": []})


def test_half_microsecond_rounding_normalizes_to_actual_boundaries_only():
    assert interval({"start": 0.068937, "end": 0.161188}, 0.0689375, 0.1611875) == (0.0689375, 0.1611875)
    assert interval({"start": 0.068938, "end": 0.161187}, 0.0689375, 0.1611875) == (0.0689375, 0.1611875)
    for item in ({"start": 0.0689369, "end": 0.161188}, {"start": 0.068937, "end": 0.1611881},
                 {"start": float("nan"), "end": 0.1}, {"start": 0.1, "end": float("inf")},
                 {"start": 0.1, "end": 0.1}, {"start": 0.15, "end": 0.1}):
        with pytest.raises(Fault):
            interval(item, 0.0689375, 0.1611875)


def test_ids_are_stable_and_bound_to_actual_source_and_input_parts():
    items = sampled()
    assert items == sampled()
    assert {item["kind"] for item in items} == {"frame", "audio"}
    changed = catalog(SourceRef("source", 4), VIDEO, [FRAME],
        {"modalities": ["image"], "frame_times": [0.1], "audio_ranges": []}, {}, 0.0689375, 0.1611875)
    assert changed[0]["id"] != next(item["id"] for item in items if item["kind"] == "frame")
    with pytest.raises(Fault):
        catalog(SOURCE, VIDEO, [FRAME], {"modalities": ["image", "audio"],
            "frame_times": [0.1], "audio_ranges": []}, {}, 0.0689375, 0.1611875)


def test_native_audio_is_catalogued_only_when_the_original_contains_it():
    native = ModelPart("video", media=VIDEO, native={"videoMetadata": {"startOffset": "0.000000s", "endOffset": "2.000000s"}})
    inputs = {"modalities": ["audio", "video"], "model_input_modalities": ["video"],
              "frame_times": [], "audio_ranges": [{"start": 0, "end": 2}], "protocol": "gemini.generate"}
    items = catalog(SOURCE, VIDEO, [native], inputs, {"source_has_audio": True}, 0, 2)
    assert {item["kind"] for item in items} == {"native_video", "native_audio"}
    with pytest.raises(Fault):
        catalog(SOURCE, VIDEO, [native], inputs, {"source_has_audio": False}, 0, 2)


def test_native_rounding_never_expands_the_source_or_sent_window():
    native = ModelPart("video", media=VIDEO, native={"videoMetadata": {
        "startOffset": "0.068938s", "endOffset": "0.161187s"}})
    inputs = {"modalities": ["video"], "model_input_modalities": ["video"],
              "frame_times": [], "audio_ranges": [], "protocol": "gemini.generate"}
    item = catalog(SOURCE, VIDEO, [native], inputs, {}, 0.0689375, 0.1611875)[0]
    assert (item["start"], item["end"]) == (0.068938, 0.161187)


def test_unprovided_acoustic_cannot_be_relabelled_as_audio_and_frames_are_not_video():
    items = sampled(audio=False)
    with pytest.raises(Fault):
        observation(encoded([{"start": 0.068937, "end": 0.161188, "modality": "acoustic", "detail": "sound"}]),
                    0.0689375, 0.1611875, ["image"], evidence_items=items, legacy=True)
    legacy = observation(encoded([{"start": 0.068937, "end": 0.161188, "modality": "video", "detail": "movement"}]),
                         0.0689375, 0.1611875, ["image"], evidence_items=items, legacy=True)
    claim = legacy["observations"][0]
    assert claim["start"] == claim["end"] == 0.1
    assert claim["modality"] == "inference" and claim["basis"] == "model_inference_from_sampled_frame"
    with_audio = sampled()
    heard = observation(encoded([{"start": 0.068937, "end": 0.161188, "modality": "sound", "detail": "sound"}]),
                        0.0689375, 0.1611875, ["audio", "image"], evidence_items=with_audio, legacy=True)
    assert heard["observations"][0]["modality"] == "audio"


def test_subtitle_inference_and_unknown_remain_distinct_and_invalid_text_is_not_success():
    items = sampled(audio=False, subtitle=True)
    subtitle = next(item for item in items if item["kind"] == "subtitle")
    frame = next(item for item in items if item["kind"] == "frame")
    parsed = observation(encoded([
        {"kind": "observed", "evidence_ids": [subtitle["id"]], "detail": "subtitle"},
        {"kind": "inference", "evidence_ids": [frame["id"]], "detail": "possible action"},
        {"kind": "unknown", "evidence_ids": [], "detail": "unknown speech"},
    ]), 0.0689375, 0.1611875, ["image"], evidence_items=items)
    assert [item["modality"] for item in parsed["observations"]] == ["subtitle", "inference", "unknown"]
    assert parsed["semantic_validation"] == "model_report_not_verified"
    for raw in ("unstructured prose", encoded([{"kind": "observed", "evidence_ids": ["forged"], "detail": "x"}])):
        with pytest.raises(Fault):
            observation(raw, 0.0689375, 0.1611875, ["image"], evidence_items=items)


def test_subtitle_id_requires_actual_extracted_text_in_model_input():
    inputs = {"modalities": ["image"], "frame_times": [0.1], "audio_ranges": []}
    decoded = {"text": "not supplied", "subtitle_ranges": [{"start": 0.0689375, "end": 0.1611875}]}
    items = catalog(SOURCE, VIDEO, [ModelPart("text", text="different"), FRAME], inputs, decoded, 0.0689375, 0.1611875)
    assert {item["kind"] for item in items} == {"frame"}
