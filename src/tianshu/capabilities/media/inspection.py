"""Local observations of an actual intake, before the sole Sources registration."""

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
import json
import math

from PIL import Image, UnidentifiedImageError

from tianshu.runtime.cancellation import finish_cleanup
from ..contracts import Fault
from .engine import executable_path, run_process

IMAGE_HEADER_BYTES = 1024 * 1024
PROBE_TIMEOUT = 15
PROBE_OUTPUT_BYTES = 65536
# Uploaded containers may not open playlists, other local files, or network URLs.
CONTAINERS = (
    "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,wav,mp3,flac,ogg,aac,amr,"
    "aiff,asf,avi,mpeg,mpegts,w64,caf,au"
)


@asynccontextmanager
async def intake_path(files, reference, local):
    manager = files.inspection_path(reference)
    entering = asyncio.create_task(local(manager.__enter__))
    try:
        try:
            path = await asyncio.shield(entering)
        except asyncio.CancelledError:
            await finish_cleanup(entering)
            raise
        yield path
    finally:
        if entering.done() and not entering.cancelled() and entering.exception() is None:
            await finish_cleanup(local(manager.__exit__, None, None, None))


def image_measurements(path):
    # Header inspection never loads/decompresses pixel data or trusts a filename.
    with path.open("rb") as stream:
        prefix = stream.read(IMAGE_HEADER_BYTES)
    try:
        with Image.open(BytesIO(prefix)) as image:
            if image.width <= 0 or image.height <= 0 or image.width * image.height > 50_000_000:
                raise Fault("media_image_dimensions_invalid", 422)
            return {"width": image.width, "height": image.height}
    except UnidentifiedImageError:
        return None
    except (OSError, ValueError, Image.DecompressionBombError):
        raise Fault("media_image_metadata_unavailable", 422) from None


def seconds(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def probe_measurements(info):
    if not isinstance(info, dict) or not isinstance(info.get("streams"), list):
        raise Fault("media_probe_invalid", 422)
    streams = [stream for stream in info["streams"] if isinstance(stream, dict)]
    video = [
        stream
        for stream in streams
        if stream.get("codec_type") == "video"
        and not (stream.get("disposition") or {}).get("attached_pic")
    ]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video and not audio:
        raise Fault("media_probe_no_audio_or_video", 422)
    container = info.get("format") or {}
    if not isinstance(container, dict):
        raise Fault("media_probe_invalid", 422)
    duration = seconds(container.get("duration"))
    if duration is None:
        durations = [seconds(stream.get("duration")) for stream in (*video, *audio)]
        duration = max((value for value in durations if value is not None), default=None)
    measured = {"duration_ms": math.ceil(duration * 1000) if duration is not None else None}
    if video:
        primary = video[0]
        for key in ("width", "height"):
            value = primary.get(key)
            measured[key] = value if type(value) is int and value > 0 else None
        rotation = next(
            (
                entry.get("rotation")
                for entry in primary.get("side_data_list", [])
                if isinstance(entry, dict) and "rotation" in entry
            ),
            None,
        )
        if rotation in {-90, 90, -270, 270}:
            measured["width"], measured["height"] = measured.get("height"), measured.get("width")
    return measured, "video" if video else "audio"


async def inspect_intake(files, reference, *, local, slots, ffprobe="ffprobe"):
    """Return a real attested FileStore object plus explicit metadata coverage.

    All file hashing/header IO uses the existing bounded file executor. ffprobe
    shares those execution slots and the existing process timeout/kill cleanup.
    No database transaction, network request, byte copy or new store is involved.
    """
    declared = reference.mime_type.partition(";")[0].lower()
    kind = declared.partition("/")[0]
    measured = {}
    error = None
    method = "pillow_header"
    async with intake_path(files, reference, local) as path:
        try:
            image = await local(image_measurements, path)
            if image is not None:
                measured, kind = image, "image"
            elif kind in {"audio", "video"} or declared == "application/octet-stream":
                method = "ffprobe"
                async with slots:
                    code, output, _ = await run_process(
                        [
                            executable_path(ffprobe),
                            "-v",
                            "error",
                            "-max_alloc",
                            "67108864",
                            "-protocol_whitelist",
                            "file,pipe",
                            "-format_whitelist",
                            CONTAINERS,
                            "-probesize",
                            "1048576",
                            "-analyzeduration",
                            "5000000",
                            "-show_streams",
                            "-show_format",
                            "-of",
                            "json",
                            str(path),
                        ],
                        timeout=PROBE_TIMEOUT,
                        limit=PROBE_OUTPUT_BYTES,
                    )
                if code != 0:
                    raise Fault("media_probe_failed", 422)
                measured, kind = probe_measurements(json.loads(output))
            elif kind == "image":
                raise Fault("media_image_metadata_unavailable", 422)
            else:
                return reference, {"state": "not_applicable", "method": None, "missing": []}
        except asyncio.CancelledError:
            raise
        except (Fault, TimeoutError, ValueError, TypeError, OverflowError) as failure:
            error = (
                "media_probe_timeout"
                if isinstance(failure, TimeoutError)
                else getattr(failure, "code", "media_probe_invalid")
            )
    # Re-attestation keeps the original bytes/URI/MIME and its actual intake proof.
    if measured:
        reference = await local(files.describe_intake, reference, **measured)
    required = (
        ("width", "height")
        if kind == "image"
        else (("duration_ms", "width", "height") if kind == "video" else ("duration_ms",))
    )
    missing = [key for key in required if getattr(reference, key) is None]
    return reference, {
        "state": "known" if not missing and not error else "partial" if measured else "unavailable",
        "method": method,
        "kind": kind,
        "duration_ms": reference.duration_ms,
        "width": reference.width,
        "height": reference.height,
        "missing": missing,
        "error_code": error,
    }
