"""Actual ffprobe observations reused by inventory and package metadata."""


def media_details(info):
    try:
        duration = float(info.get("format", {}).get("duration", 0))
    except (ValueError, TypeError):
        duration = None
    fields = {
        "video": (
            "index",
            "codec_name",
            "profile",
            "width",
            "height",
            "pix_fmt",
            "r_frame_rate",
            "avg_frame_rate",
            "display_aspect_ratio",
            "sample_aspect_ratio",
            "color_range",
            "color_space",
            "color_transfer",
            "color_primaries",
            "side_data_list",
        ),
        "audio": (
            "index",
            "codec_name",
            "profile",
            "sample_rate",
            "channels",
            "channel_layout",
            "bits_per_raw_sample",
            "tags",
            "disposition",
        ),
        "subtitle": ("index", "codec_name", "tags", "disposition"),
    }
    result = {"duration_seconds": duration, "rawffprobe_streams": info["streams"]}
    for kind, name in (
        ("video", "video_streams"),
        ("audio", "audio_tracks"),
        ("subtitle", "subtitle_tracks"),
    ):
        result[name] = [
            {key: stream.get(key) for key in fields[kind]}
            for stream in info["streams"]
            if stream.get("codec_type") == kind
        ]
    return result
