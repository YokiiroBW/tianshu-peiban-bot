"""Bilibili stream ordering and constraints, shared by selection and byte reuse.

Only extractor observations and ffprobe results belong in evidence. A requested
policy is never evidence that an existing file satisfies that policy.
"""

import re

from ..contracts import require

VIDEO_QUALITIES = ("16", "32", "64", "74", "80", "112", "116", "120", "125", "126", "127")
AUDIO_QUALITIES = ("30216", "30232", "30280", "30250", "30251")
DEFAULT_OPTIONS = {
    "video_min_quality": None,
    "video_max_quality": None,
    "audio_min_quality": None,
    "audio_max_quality": None,
    "codecs": [],
    "allow_hdr": True,
    "allow_dolby_vision": True,
    "allow_dolby_audio": True,
    "allow_hires": True,
}


def normalize_options(value):
    require(isinstance(value, dict) and set(value) <= set(DEFAULT_OPTIONS), "invalid_input", 400)
    result = {**DEFAULT_OPTIONS, **value}
    for kind, order in (("video", VIDEO_QUALITIES), ("audio", AUDIO_QUALITIES)):
        low, high = (result[f"{kind}_{bound}_quality"] for bound in ("min", "max"))
        require(low is None or isinstance(low, str) and low in order, "invalid_input", 400)
        require(high is None or isinstance(high, str) and high in order, "invalid_input", 400)
        require(
            low is None or high is None or order.index(low) <= order.index(high),
            "invalid_input",
            400,
        )
    codecs = result["codecs"]
    require(
        isinstance(codecs, list)
        and all(isinstance(codec, str) and codec in {"av1", "hevc", "avc"} for codec in codecs)
        and len(codecs) == len(set(codecs)),
        "invalid_input",
        400,
    )
    result["codecs"] = list(codecs)
    for key in DEFAULT_OPTIONS:
        if key.startswith("allow_"):
            require(type(result[key]) is bool, "invalid_input", 400)
    return result


def options(requested):
    return {**DEFAULT_OPTIONS, **requested.get("stream_options", {})}


def constrained(requested):
    return options(requested) != DEFAULT_OPTIONS


def codec_name(value):
    value = str(value or "").lower()
    for name, prefixes in (
        ("av1", ("av01", "av1")),
        ("hevc", ("hev", "hvc", "hevc")),
        ("avc", ("avc", "h264")),
    ):
        if value.startswith(prefixes):
            return name
    return None


def _audio_codec(value):
    value = str(value or "").lower()
    if value.startswith(("mp4a", "aac")):
        return "aac"
    return {"ec-3": "eac3", "ac-3": "ac3"}.get(value, value or None)


def quality_id(value):
    if type(value) in (int, float) and float(value).is_integer():
        return str(int(value))
    return None


def _within(value, kind, policy):
    low, high = (policy[f"{kind}_{bound}_quality"] for bound in ("min", "max"))
    if low is None and high is None:
        return True
    order = VIDEO_QUALITIES if kind == "video" else AUDIO_QUALITIES
    if value not in order:
        return False
    return (low is None or order.index(value) >= order.index(low)) and (
        high is None or order.index(value) <= order.index(high)
    )


def video_evidence(value):
    qn = quality_id(value.get("quality"))
    dynamic = str(value.get("dynamic_range") or "").upper()
    known = qn in VIDEO_QUALITIES
    hdr = False if known or dynamic == "SDR" else None
    dolby_vision = False if known or dynamic == "SDR" else None
    if qn == "125" or dynamic.startswith("HDR"):
        hdr = True
    if qn == "126" or dynamic.startswith("DV"):
        dolby_vision, hdr = True, False
    return {
        "video_quality": qn,
        "video_codec": codec_name(value.get("vcodec")),
        "hdr": hdr,
        "dolby_vision": dolby_vision,
    }


def audio_evidence(value):
    # The pinned extractor puts Bilibili's audio ID in format_id, not quality.
    identity = str(value.get("format_id", ""))
    qn = identity if identity in AUDIO_QUALITIES else None
    codec = str(value.get("acodec") or "").lower()
    dolby_audio = False if qn else None
    hires = False if qn else None
    if qn == "30250" or codec in {"eac3", "ec-3", "ac3", "ac-3"}:
        dolby_audio = True
    if qn == "30251" or codec == "flac":
        hires = True
    return {
        "audio_quality": qn,
        "dolby_audio": dolby_audio,
        "hires": hires,
    }


def satisfies(evidence, requested):
    policy = options(requested)
    if not _within(evidence.get("video_quality"), "video", policy) or not _within(
        evidence.get("audio_quality"), "audio", policy
    ):
        return False
    if policy["codecs"] and not all(
        codec in policy["codecs"]
        for codec in evidence.get("video_codecs", [evidence.get("video_codec")])
    ):
        return False
    for field, allowed in (
        ("hdr", "allow_hdr"),
        ("dolby_vision", "allow_dolby_vision"),
        ("dolby_audio", "allow_dolby_audio"),
        ("hires", "allow_hires"),
    ):
        if not policy[allowed] and evidence.get(field) is not False:
            return False
    return True


def eligible_pairs(formats, requested):
    videos = [value for value in formats if value.get("vcodec") not in {None, "none"}]
    audios = [
        value
        for value in formats
        if value.get("acodec") not in {None, "none"} and value.get("vcodec") == "none"
    ]
    pairs = []
    for video in videos:
        choices = [video] if video.get("acodec") not in {None, "none"} else audios
        for audio in choices:
            evidence = {**video_evidence(video), **audio_evidence(audio)}
            if evidence["video_quality"] is not None and satisfies(evidence, requested):
                pairs.append((video, audio, evidence))
    return pairs


def select_formats(formats, requested):
    pairs = eligible_pairs(formats, requested)
    if requested["mode"] == "exact":
        exact = [pair for pair in pairs if pair[2]["video_quality"] == requested["quality_id"]]
        if exact or not requested["allow_fallback"]:
            pairs = exact
    require(pairs, "quality_unavailable", 503)
    codecs = options(requested)["codecs"]

    def rank(pair):
        video, audio, evidence = pair
        qn = evidence["video_quality"]
        audio_qn = evidence["audio_quality"]
        return (
            int(qn),
            -codecs.index(evidence["video_codec"]) if codecs else 0,
            AUDIO_QUALITIES.index(audio_qn) if audio_qn in AUDIO_QUALITIES else -1,
            formats.index(video),
            formats.index(audio),
        )

    video, audio, evidence = max(pairs, key=rank)
    selected = [video] if video is audio else [video, audio]
    identities = [str(value.get("format_id", "")) for value in selected]
    require(
        all(re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in identities),
        "quality_unverified",
        503,
    )
    return "+".join(identities), evidence


def observed_evidence(download):
    """Persistent actual evidence, with ffprobe observations taking precedence."""
    evidence = dict(download.get("stream_evidence") or {})
    evidence.setdefault("video_quality", download.get("actual_quality"))
    media = download.get("media") or {}
    streams = media.get("rawffprobe_streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if videos:
        evidence["video_codecs"] = [codec_name(v.get("codec_name")) for v in videos]
        evidence["video_codec"] = evidence["video_codecs"][0]
        hdrs, dvs = [], []
        for video in videos:
            side = video.get("side_data_list", [])
            dv = (len(videos) == 1 and evidence.get("dolby_vision") is True) or any(
                "dovi" in str(s.get("side_data_type", "")).lower()
                or "dolby vision" in str(s.get("side_data_type", "")).lower()
                for s in side
            )
            dvs.append(dv)
            transfer = video.get("color_transfer")
            # DV has its own independent allow switch, including PQ based DV.
            hdr = None
            if dv:
                hdr = False
            elif transfer in {"smpte2084", "arib-std-b67"}:
                hdr = True
            elif transfer and transfer not in {"unknown", "unspecified"}:
                hdr = False
            hdrs.append(hdr)
        evidence["dolby_vision"] = evidence.get("dolby_vision") is True or any(dvs)
        if any(value is True for value in hdrs):
            evidence["hdr"] = True
        elif all(value is False for value in hdrs):
            evidence.setdefault("hdr", False)
        elif len(videos) > 1:
            evidence["hdr"] = None
    if audios:
        codecs = [_audio_codec(audio.get("codec_name")) for audio in audios]
        if len(audios) > 1:
            # A selected stream's Bilibili ID cannot attest another audio track.
            evidence["audio_quality"] = None
        for field, special in (("dolby_audio", {"eac3", "ac3"}), ("hires", {"flac"})):
            if evidence.get(field) is True or any(codec in special for codec in codecs):
                evidence[field] = True
            elif all(codecs):
                evidence.setdefault(field, False)
            else:
                evidence[field] = None
    return evidence


def validate_policy(download, requested):
    if constrained(requested):
        require(satisfies(observed_evidence(download), requested), "quality_mismatch", 503)
    return download


def output_evidence(formats, media, *, strict=False):
    video = next((v for v in formats if v.get("vcodec") not in {None, "none"}), {})
    audio = next((v for v in formats if v.get("acodec") not in {None, "none"}), {})
    evidence = {**video_evidence(video), **audio_evidence(audio)}
    if strict:
        streams = media.get("rawffprobe_streams", [])
        observed_video = next((s for s in streams if s.get("codec_type") == "video"), {})
        observed_audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        require(
            codec_name(observed_video.get("codec_name")) is not None
            and _audio_codec(observed_audio.get("codec_name")) is not None,
            "quality_unverified",
            503,
        )
        require(
            evidence["video_codec"] == codec_name(observed_video.get("codec_name"))
            and _audio_codec(audio.get("acodec")) == _audio_codec(observed_audio.get("codec_name")),
            "quality_mismatch",
            503,
        )
        actual = observed_evidence({"media": media})
        for field in ("hdr", "dolby_vision", "dolby_audio", "hires"):
            if evidence[field] is True:
                require(actual.get(field) is not None, "quality_unverified", 503)
                require(actual[field] is True, "quality_mismatch", 503)
    return observed_evidence({"stream_evidence": evidence, "media": media})
