"""Pinned yt-dlp subprocesses and local ffprobe/image validation.

Only an isolated job directory is writable. Process output is bounded and never logged.
"""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

from ..contracts import Fault, require
from .config import ENGINE_VERSION
from .probe_details import media_details
from .stream_policy import (
    constrained,
    eligible_pairs,
    output_evidence,
    select_formats,
    validate_policy,
)


def private_environment():
    names = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
    return {key: value for key, value in os.environ.items() if key.upper() in names}


def executable_path(command):
    path = shutil.which(command)
    require(path is not None, "engine_unavailable", 503)
    return str(Path(path).resolve())


def write_cookie(path, cookie):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        os.chmod(path, 0o600)
        handle.write("# Netscape HTTP Cookie File\n")
        for token in cookie.split("; "):
            name, _, value = token.partition("=")
            handle.write(f".bilibili.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")


async def run_process(command, *, timeout=30, limit=65536, cwd=None):
    options = (
        {"start_new_session": True}
        if os.name != "nt"
        else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=private_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **options,
        )
    except OSError:
        raise Fault("engine_unavailable", 503) from None

    async def consume(stream):
        result = bytearray()
        while chunk := await stream.read(8192):
            if len(result) < limit:
                result.extend(chunk[: limit - len(result)])
        return bytes(result)

    readers = [
        asyncio.create_task(consume(process.stdout)),
        asyncio.create_task(consume(process.stderr)),
    ]
    try:
        async with asyncio.timeout(timeout):
            await process.wait()
            output = await asyncio.gather(*readers)
            return process.returncode, output[0], output[1]
    finally:
        if process.returncode is None:
            if os.name == "nt":
                # Windows has no POSIX process group kill; taskkill's /T includes ffmpeg children.
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


class Engine:
    def __init__(self, config):
        self.config = config
        self.status = {
            "state": "not_verified",
            "version": ENGINE_VERSION,
            "code": "engine_not_checked",
        }

    async def check(self):
        try:
            code, output, _ = await run_process(
                [self.config["python"], "-m", "yt_dlp", "--version"]
            )
            require(
                code == 0 and output.decode().strip() == ENGINE_VERSION,
                "engine_version_mismatch",
                503,
            )
            for tool in ("ffmpeg", "ffprobe"):
                executable_path(self.config[tool])
            self.status = {"state": "ready", "version": ENGINE_VERSION, "code": "ready"}
        except (Fault, TimeoutError, UnicodeDecodeError) as error:
            self.status = {
                "state": "unavailable",
                "version": ENGINE_VERSION,
                "code": error.code if isinstance(error, Fault) else "engine_unavailable",
            }
        return dict(self.status)

    async def format_info(self, bvid, part, cookie, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        cookie_file = directory / ".session.txt"
        try:
            if cookie:
                write_cookie(cookie_file, cookie)
            command = [
                self.config["python"],
                "-m",
                "yt_dlp",
                "--ignore-config",
                "--no-cache-dir",
                "--no-playlist",
                "--no-warnings",
                "--proxy",
                "",
                "--socket-timeout",
                "15",
                "--retries",
                "1",
                "--simulate",
                "--dump-single-json",
            ]
            if cookie:
                command += ["--cookies", str(cookie_file)]
            command += [f"https://www.bilibili.com/video/{bvid}?p={part['index']}"]
            code, output, _ = await run_process(
                command, timeout=90, limit=4 * 1024**2, cwd=directory
            )
            require(code == 0, "quality_probe_unavailable", 503)
            info = json.loads(output)
            require(
                str(info.get("id")) in {bvid, f"{bvid}_p{part['index']}"},
                "source_identity_changed",
                409,
            )
            require(isinstance(info.get("formats"), list), "quality_probe_unavailable", 503)
            return info["formats"]
        except (ValueError, TypeError, OSError, TimeoutError):
            raise Fault("quality_probe_unavailable", 503) from None
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    async def formats(self, bvid, part, cookie, directory, requested=None):
        formats = await self.format_info(bvid, part, cookie, directory)
        if requested and constrained(requested):
            pairs = eligible_pairs(formats, requested)
            available = {
                pair[2]["video_quality"]: pair[0].get("format_note")
                or pair[0].get("format")
                or pair[2]["video_quality"]
                for pair in pairs
            }
        else:
            has_audio = any(value.get("acodec") not in {None, "none"} for value in formats)
            available = {
                str(value["quality"]): value.get("format_note", str(value["quality"]))
                for value in formats
                if has_audio
                and value.get("vcodec") not in {None, "none"}
                and type(value.get("quality")) in (int, float)
                and float(value["quality"]).is_integer()
            }
        require(available, "quality_unavailable", 503)
        return [
            {"quality_id": number, "label": str(available[number])}
            for number in sorted(available, key=int, reverse=True)
        ]

    async def download(self, job, part, cookie, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        require(not directory.is_symlink(), "unsafe_staging", 503)
        ffmpeg = executable_path(self.config["ffmpeg"])
        cookie_file = directory / ".session.txt"
        requested = job["quality"]
        selector = "bestvideo+bestaudio/best"
        if requested["mode"] == "exact":
            number = requested["quality_id"]
            selector = f"bestvideo[quality={number}]+bestaudio/best[quality={number}]"
            if requested["allow_fallback"]:
                selector += "/bestvideo+bestaudio/best"
        if constrained(requested):
            formats = await self.format_info(job["bvid"], part, cookie, directory / ".selection")
            selector, _ = select_formats(formats, requested)
        if cookie:
            write_cookie(cookie_file, cookie)
        command = [
            self.config["python"],
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--no-playlist",
            "--no-progress",
            "--no-warnings",
            "--proxy",
            "",
            "--socket-timeout",
            "15",
            "--retries",
            "2",
            "--fragment-retries",
            "2",
            "--max-filesize",
            str(self.config["max_bytes"]),
            "--write-info-json",
            # yt-dlp's default cleaning removes the selected requested_formats.
            "--no-clean-info-json",
            "--merge-output-format",
            # MP4 muxing rejects FLAC in the pinned toolchain. yt-dlp chooses
            # MP4 where compatible, otherwise Matroska, with stream copy only.
            "mp4/mkv",
            "--ffmpeg-location",
            ffmpeg,
            "--format",
            selector,
            "--output",
            "download.%(ext)s",
            "--paths",
            str(directory),
            "--no-overwrites",
            "--continue",
        ]
        if cookie:
            command += ["--cookies", str(cookie_file)]
        command += [f"https://www.bilibili.com/video/{job['bvid']}?p={part['index']}"]
        try:
            code, _, stderr = await run_process(
                command, timeout=self.config["timeout_seconds"], cwd=directory
            )
            if code:
                lower = stderr.lower()
                if b"login" in lower or b"sign in" in lower or b"premium" in lower:
                    raise Fault("auth_required", 503)
                if b"requested format" in lower:
                    raise Fault("quality_unavailable", 503)
                raise Fault("download_failed", 503)
            files = [
                path
                for path in directory.iterdir()
                if path.name in {"download.mp4", "download.mkv"}
            ]
            require(
                len(files) == 1 and files[0].stat().st_size <= self.config["max_bytes"],
                "download_invalid_output",
                503,
            )
            infos = list(directory.glob("*.info.json"))
            require(
                len(infos) == 1 and infos[0].stat().st_size <= 4 * 1024**2,
                "download_invalid_output",
                503,
            )
            value = json.loads(infos[0].read_bytes())
            require(isinstance(value, dict), "download_invalid_output", 503)
            require(
                value.get("id") == f"{job['bvid']}_p{part['index']}",
                "source_identity_changed",
                409,
            )
            formats = value.get("requested_formats") or [value]
            require(
                isinstance(formats, list) and all(isinstance(item, dict) for item in formats),
                "quality_unverified",
                503,
            )
            actual = next(
                (
                    str(int(item["quality"]))
                    for item in formats
                    if item.get("vcodec") not in {None, "none"}
                    and type(item.get("quality")) in (int, float)
                    and float(item["quality"]).is_integer()
                ),
                None,
            )
            require(actual is not None, "quality_unverified", 503)
            require(
                requested["mode"] != "exact"
                or requested["allow_fallback"]
                or actual == requested["quality_id"],
                "quality_mismatch",
                503,
            )
            observed = await self.verify(files[0], part.get("duration_seconds"))
            details = media_details(observed)
            return validate_policy(
                {
                    "file": str(files[0]),
                    "actual_quality": actual,
                    "extension": files[0].suffix[1:],
                    "media": details,
                    "stream_evidence": output_evidence(
                        formats, details, strict=constrained(requested)
                    ),
                },
                requested,
            )
        except TimeoutError:
            raise Fault("download_timeout", 503) from None
        except (OSError, ValueError):
            raise Fault("download_invalid_output", 503) from None
        finally:
            cookie_file.unlink(missing_ok=True)
            for info in directory.glob("*.info.json"):
                info.unlink(missing_ok=True)

    async def probe(self, path):
        code, output, _ = await run_process(
            [
                executable_path(self.config["ffprobe"]),
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            timeout=30,
            limit=131072,
        )
        require(code == 0, "media_invalid", 503)
        try:
            info = json.loads(output)
            require(
                isinstance(info, dict)
                and isinstance(info.get("streams"), list)
                and all(isinstance(stream, dict) for stream in info["streams"])
                and isinstance(info.get("format", {}), dict),
                "media_invalid",
                503,
            )
            return info
        except (ValueError, TypeError):
            raise Fault("media_invalid", 503) from None

    async def remux(self, download, destination, expected_duration=None):
        """Unify a mixed multipart package without rewriting the source inode."""
        destination = Path(destination)
        require(
            destination.suffix == ".mkv" and destination != Path(download["file"]),
            "unsafe_staging",
            503,
        )
        # The caller has durably reserved this exact private input path. Even
        # after a process crash its partial bytes are therefore reclaimable.
        source = await asyncio.to_thread(file_record, download["file"], "cache", "video", None)
        require(
            (source["sha256"], source["size_bytes"])
            == (download["sha256"], download["size_bytes"]),
            "download_cache_changed",
            409,
        )
        source_media = media_details(await self.verify(download["file"], expected_duration))
        subtitles = source_media["subtitle_tracks"]
        conversions = [
            {"stream": index, "from": "mov_text", "to": "ass"}
            for index, stream in enumerate(subtitles)
            if stream.get("codec_name") == "mov_text"
        ]
        subtitle_options = [
            argument for change in conversions for argument in (f"-c:s:{change['stream']}", "ass")
        ]
        destination.unlink(missing_ok=True)
        complete = False
        try:
            code, _, _ = await run_process(
                [
                    executable_path(self.config["ffmpeg"]),
                    "-v",
                    "error",
                    "-nostdin",
                    "-n",
                    "-i",
                    download["file"],
                    "-map",
                    "0",
                    "-c",
                    "copy",
                    *subtitle_options,
                    str(destination),
                ],
                timeout=self.config["timeout_seconds"],
            )
            require(
                code == 0 and destination.stat().st_size <= self.config["max_bytes"],
                "download_invalid_output",
                503,
            )
            details = media_details(await self.verify(destination, expected_duration))
            fields = (
                "codec_type",
                "codec_name",
                "profile",
                "width",
                "height",
                "color_transfer",
                "sample_rate",
                "channels",
                "bits_per_raw_sample",
            )

            def signatures(media):
                return [
                    tuple(stream.get(field) for field in fields)
                    for stream in media.get("rawffprobe_streams", [])
                    if stream.get("codec_type") in {"video", "audio"}
                ]

            require(signatures(source_media) == signatures(details), "quality_mismatch", 503)
            expected_subtitles = [
                "ass" if stream.get("codec_name") == "mov_text" else stream.get("codec_name")
                for stream in subtitles
            ]
            require(
                [stream.get("codec_name") for stream in details["subtitle_tracks"]]
                == expected_subtitles,
                "quality_mismatch",
                503,
            )
            require(
                await asyncio.to_thread(file_record, download["file"], "cache", "video", None)
                == source,
                "download_cache_changed",
                409,
            )
            record = await asyncio.to_thread(file_record, destination, "cache", "video", None)
            complete = True
            return {
                **download,
                "file": str(destination),
                "extension": "mkv",
                "media": details,
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
                "remux_source": {
                    "kind": "ffmpeg_av_stream_copy",
                    "sha256": source["sha256"],
                    "size_bytes": source["size_bytes"],
                    "extension": download["extension"],
                    "subtitle_conversions": conversions,
                },
            }
        finally:
            if not complete:
                destination.unlink(missing_ok=True)

    async def verify(self, path, expected_duration=None):
        return self.validate_probe(await self.probe(path), expected_duration)

    def validate_probe(self, info, expected_duration=None):
        try:
            streams = info.get("streams", [])
            require(
                any(item.get("codec_type") == "video" for item in streams),
                "media_video_missing",
                503,
            )
            require(
                any(item.get("codec_type") == "audio" for item in streams),
                "media_audio_missing",
                503,
            )
            duration = float(info.get("format", {}).get("duration", 0))
            require(duration > 0, "media_duration_invalid", 503)
            if expected_duration:
                require(
                    abs(duration - expected_duration) <= max(5, expected_duration * 0.05),
                    "media_duration_mismatch",
                    503,
                )
            return info
        except (ValueError, TypeError):
            raise Fault("media_invalid", 503) from None


def file_record(path, relative, kind, cid):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "unsafe_staging", 503)
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024**2), b""):
            hasher.update(chunk)
    return {
        "path": relative,
        "kind": kind,
        "cid": cid,
        "size_bytes": path.stat().st_size,
        "sha256": hasher.hexdigest(),
    }


def validate_image(raw):
    from io import BytesIO
    from PIL import Image

    try:
        with Image.open(BytesIO(raw)) as picture:
            require(
                picture.format in {"JPEG", "PNG"}
                and picture.width > 0
                and picture.height > 0
                and picture.width * picture.height <= 50_000_000,
                "cover_invalid",
                503,
            )
            extension = "jpg" if picture.format == "JPEG" else "png"
            picture.verify()
        return extension
    except (OSError, ValueError, Image.DecompressionBombError):
        raise Fault("cover_invalid", 503) from None
