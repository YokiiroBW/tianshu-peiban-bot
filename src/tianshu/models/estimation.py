"""Heuristic media reservations, separate from transport bytes and reported usage."""
import base64
import binascii
import json
import math


def media_tokens(kind, duration_ms=None, settings=None):
    """Existing context estimates; these are reservations, not provider token counts."""
    settings = settings or {}
    duration = duration_ms / 1000 if duration_ms is not None else None
    return (int(settings.get("image_tokens", 2048)) if kind == "image"
            else math.ceil(duration * settings.get("audio_tokens_per_second", 50)) if kind == "audio" and duration
            else math.ceil(duration * settings.get("video_tokens_per_second", 256)) if kind == "video" and duration
            else int(settings.get("unknown_tokens", 8192)))


def _image_data_uri(value):
    if not isinstance(value, str):
        return None
    header, separator, data = value.partition(",")
    return data if separator and header.lower().startswith("data:image/") and header.lower().endswith(";base64") else None


def _inline_images(protocol, body):
    """Inspect only protocol-defined media positions, never tool JSON or prose."""
    if not isinstance(body, dict):
        return
    if protocol == "gemini.generate":
        contents = body.get("contents")
        for content in contents if isinstance(contents, list) else ():
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            for part in parts if isinstance(parts, list) else ():
                inline = part.get("inlineData") if isinstance(part, dict) else None
                if isinstance(inline, dict) and str(inline.get("mimeType", "")).lower().startswith("image/"):
                    yield inline.get("data")
    elif protocol in {"openai.chat", "openai.responses", "anthropic.messages"}:
        messages = body.get("input" if protocol == "openai.responses" else "messages", ())
        for message in messages if isinstance(messages, list) else ():
            if not isinstance(message, dict):
                continue
            content = message.get("content", ())
            for part in content if isinstance(content, list) else ():
                if not isinstance(part, dict):
                    continue
                if protocol == "openai.chat" and part.get("type") == "image_url":
                    image = part.get("image_url")
                    yield _image_data_uri(image.get("url") if isinstance(image, dict) else image)
                elif protocol == "openai.responses" and part.get("type") == "input_image":
                    yield _image_data_uri(part.get("image_url"))
                elif protocol == "anthropic.messages" and part.get("type") == "image":
                    source = part.get("source") or {}
                    if isinstance(source, dict) and source.get("type") == "base64" and str(source.get("media_type", "")).lower().startswith("image/"):
                        yield source.get("data")


def input_tokens(protocol, body):
    """Preserve the byte fallback except for recognized inline image encoding.

    Unknown image geometry/provider rules use the existing 2048-token image
    reservation. Actual provider usage can only be known from its report.
    The original body and every network/file byte limit remain unchanged.
    """
    estimate = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    for encoded in _inline_images(protocol, body):
        if not isinstance(encoded, str) or not encoded:
            continue
        try:
            base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            # An unrecognized/invalid payload retains the conservative old fallback.
            continue
        estimate += media_tokens("image") - len(encoded)
    return estimate
