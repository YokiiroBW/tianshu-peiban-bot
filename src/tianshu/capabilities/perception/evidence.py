"""Build a stable evidence catalog from the actual, source-bound model parts."""

import math
from decimal import Decimal, InvalidOperation

from tianshu.runtime.serialization import wire

from ..contracts import Fault, digest


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise Fault("video_evidence_manifest_invalid", 502)
    return float(value)


def _item(source, media, kind, start, end, *, part=None, index=0, text=None):
    key = {"source": wire(source), "media": wire(media), "kind": kind,
           "start": start, "end": end, "index": index,
           "part": wire(part.media) if part and part.media else None,
           "text_digest": digest(text) if text is not None else None}
    modality = {"frame": "image", "audio": "audio", "native_video": "video",
                "native_audio": "audio", "subtitle": "subtitle"}[kind]
    return {"id": "ev_" + digest(key), "kind": kind, "modality": modality,
            "start": start, "end": end}


def _native_bounds(part, start, end):
    """Use the actual provider request offsets, restricted to the source interval."""
    metadata = part.native.get("videoMetadata", {})
    try:
        left_text, right_text = metadata["startOffset"], metadata["endOffset"]
        if not isinstance(left_text, str) or not isinstance(right_text, str):
            raise ValueError
        if not left_text.endswith("s") or not right_text.endswith("s"):
            raise ValueError
        left, right = Decimal(left_text[:-1]), Decimal(right_text[:-1])
        if not left.is_finite() or not right.is_finite():
            raise ValueError
        lower, upper = Decimal(str(start)), Decimal(str(end))
        tolerance = Decimal("0.0000005")
        if abs(left - lower) > tolerance or abs(right - upper) > tolerance:
            raise ValueError
        left, right = max(left, lower), min(right, upper)
        if left >= right:
            raise ValueError
        return float(left), float(right)
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise Fault("video_evidence_manifest_invalid", 502) from error


def catalog(source, media, parts, inputs, decoded, start, end):
    """Only inputs actually sent to the model may become evidence IDs."""
    start, end = _number(start), _number(end)
    if start >= end:
        raise Fault("video_evidence_manifest_invalid", 502)
    parts = tuple(parts)
    frames = [part for part in parts if part.kind == "image"]
    audios = [part for part in parts if part.kind == "audio"]
    videos = [part for part in parts if part.kind == "video"]
    frame_times = inputs.get("frame_times", [])
    audio_ranges = inputs.get("audio_ranges", [])
    native_audio = bool(videos and not audios and "audio" in inputs.get("modalities", []))
    if (len(frames) != len(frame_times) or len(videos) > 1
            or len(audio_ranges) != (1 if native_audio else len(audios))):
        raise Fault("video_evidence_manifest_incomplete", 502)
    result = []
    for index, (part, value) in enumerate(zip(frames, frame_times)):
        at = _number(value)
        if not start <= at <= end or not part.media:
            raise Fault("video_evidence_manifest_invalid", 502)
        result.append(_item(source, media, "frame", at, at, part=part, index=index))
    for index, (part, value) in enumerate(zip(audios, audio_ranges)):
        left, right = _number(value.get("start")), _number(value.get("end"))
        if not start <= left < right <= end or not part.media:
            raise Fault("video_evidence_manifest_invalid", 502)
        result.append(_item(source, media, "audio", left, right, part=part, index=index))
    if videos:
        if not videos[0].media or "video" not in inputs.get("model_input_modalities", []):
            raise Fault("video_evidence_manifest_invalid", 502)
        native_start, native_end = _native_bounds(videos[0], start, end)
        result.append(_item(source, media, "native_video", native_start, native_end, part=videos[0]))
        if native_audio:
            audio_range = audio_ranges[0]
            if (not decoded.get("source_has_audio")
                    or abs(Decimal(str(_number(audio_range.get("start")))) - Decimal(str(native_start))) > Decimal("0.0000005")
                    or abs(Decimal(str(_number(audio_range.get("end")))) - Decimal(str(native_end))) > Decimal("0.0000005")):
                raise Fault("video_evidence_manifest_invalid", 502)
            result.append(_item(source, media, "native_audio", native_start, native_end, part=videos[0]))
    sent_text = decoded.get("text")
    if (not inputs.get("protocol") and sent_text
            and any(part.kind == "text" and sent_text in (part.text or "") for part in parts)):
        for index, value in enumerate(decoded.get("subtitle_ranges", [])):
            left, right = _number(value.get("start")), _number(value.get("end"))
            if not start <= left < right <= end:
                raise Fault("video_evidence_manifest_invalid", 502)
            result.append(_item(source, media, "subtitle", left, right, index=index, text=sent_text))
    supplied = {item["modality"] for item in result}
    expected = set(inputs.get("modalities", []))
    if not expected <= supplied or not result:
        raise Fault("video_evidence_manifest_incomplete", 502)
    return result
