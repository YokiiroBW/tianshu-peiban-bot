"""LRC normalization and a bilingual timeline readable by ordinary Subsonic clients."""

import re
from bisect import bisect_left

LIMIT = 512 * 1024
TIME = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
TAG = re.compile(r"^\[[a-zA-Z][^\]:]*:[^\]]*\]\s*$")


def clean(value):
    return (
        value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()[:LIMIT]
        if isinstance(value, str)
        else ""
    )


def lines(text):
    text = clean(text)
    offset = re.search(r"\[offset:([+-]?\d{1,9})\]", text, re.I)
    offset = int(offset[1]) if offset else 0
    result = []
    for line in text.splitlines():
        stamps = list(TIME.finditer(line))
        if not stamps or stamps[0].start() != 0:
            continue
        end = 0
        times = []
        for match in stamps:
            if line[end : match.start()].strip():
                break
            hour, minute, second, fraction = match.groups()
            if int(second) >= 60:
                continue
            times.append(
                max(
                    0,
                    ((int(hour or 0) * 60 + int(minute)) * 60 + int(second)) * 1000
                    + int((fraction or "0").ljust(3, "0"))
                    - offset,
                )
            )
            end = match.end()
        value = line[end:].strip()
        for stamp in times:
            result.append((stamp, value))
    return sorted(set(result), key=lambda item: item[0])


def usable(text):
    text = clean(text)
    content = [v for _, v in lines(text)] or [v for v in text.splitlines() if not TAG.match(v)]
    return any(v.strip() for v in content)


def aligned(original, translated):
    source, other = lines(original), lines(translated)
    if not source or not other:
        return {}
    paired = {}
    stamps = sorted({stamp for stamp, _ in source})
    for stamp, value in other:
        if not value:
            continue
        index = bisect_left(stamps, stamp)
        nearest = stamps[max(0, index - 1) : index + 1]
        closest = min(nearest, key=lambda item: abs(item - stamp))
        if abs(closest - stamp) <= 150:
            paired.setdefault(closest, []).append(value)
    # Do not place an unrelated translation on a guessed timeline.
    return paired if len(paired) >= max(1, len([v for _, v in other if v]) * 0.8) else {}


def normalize(value, duration_ms=None):
    result = {k: clean(value.get(k)) for k in ("original", "translated", "romanized")}
    if not usable(result["original"]):
        return {}
    timeline = lines(result["original"])
    if duration_ms and timeline and timeline[-1][0] > duration_ms + 5000:
        return {}
    return result


def coverage(value):
    original = value.get("original", "")
    return {
        "original": usable(original),
        "synced": bool(lines(original)),
        "translated": usable(value.get("translated", "")),
        "translation_synced": bool(aligned(original, value.get("translated", ""))),
        "romanized": usable(value.get("romanized", "")),
    }


def score(value):
    present = coverage(value)
    return (
        present["original"],
        present["synced"],
        present["translation_synced"],
        present["translated"],
        present["romanized"],
    )


def render(value):
    original = value.get("original", "")
    pairs = aligned(original, value.get("translated", ""))
    if not pairs:
        return original
    result = []
    for stamp, text in lines(original):
        additions = [v for v in dict.fromkeys(pairs.get(stamp, [])) if v != text]
        text = " ／ ".join([text, *additions]) if text else " ／ ".join(additions)
        minute, second = divmod(stamp, 60000)
        result.append(f"[{minute:02d}:{second // 1000:02d}.{second % 1000:03d}]{text}")
    return "\n".join(result)


def sidecars(value):
    return {
        suffix: clean(text)
        for suffix, text in {
            ".lrc": render(value),
            ".original.lrc": value.get("original", ""),
            ".zh.lrc": value.get("translated", ""),
            ".rom.lrc": value.get("romanized", ""),
        }.items()
        if text
    }
