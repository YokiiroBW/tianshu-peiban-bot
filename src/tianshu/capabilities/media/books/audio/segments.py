"""Canonical text offsets and bounded speech segments without fabricated word timing."""

import re


def segments(text, limit=900):
    result, start = [], 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundaries = list(re.finditer(r"[\n。！？!?；;]", text[start:end]))
            if boundaries and boundaries[-1].end() >= limit // 3:
                end = start + boundaries[-1].end()
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            result.append({"start": left, "end": right, "text": text[left:right]})
        start = end
    return result


def cues(text, boundaries, duration):
    """Use only exact, forward text matches; retain a segment fallback otherwise."""
    result, position, last = [], 0, 0.0
    for item in boundaries:
        value = item.get("text", "").strip()
        at = text.find(value, position) if value else -1
        begin, end = item.get("start", -1), item.get("end", -1)
        if at < 0 or not (last <= begin < end <= duration + 0.15):
            continue
        result.append(
            {
                "start": round(begin, 4),
                "end": round(min(end, duration), 4),
                "text_start": at,
                "text_end": at + len(value),
            }
        )
        position, last = at + len(value), end
    if not result:
        return [{"start": 0, "end": duration, "text_start": 0, "text_end": len(text)}], "segment"
    return result, "sentence"
