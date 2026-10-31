"""Probe/decode originals and write portable tags on private staging copies only."""

import base64
import hashlib
import io
import json
import re

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, USLT
from PIL import Image, UnidentifiedImageError

from ...contracts import Fault, digest, require
from ..engine import executable_path, run_process
from ..video_storage import sync_file


def record(path):
    checksum = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024**2):
            checksum.update(chunk)
            size += len(chunk)
    return {"sha256": checksum.hexdigest(), "size_bytes": size}


async def inspect_audio(path, engine):
    code, raw, _ = await run_process(
        [
            executable_path(engine["ffprobe"]),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    require(code == 0, "music_audio_invalid", 409)
    try:
        data = json.loads(raw)
        streams = data["streams"]
        audio = [s for s in streams if s["codec_type"] == "audio"]
        require(
            len(audio) == 1
            and not any(
                s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
                for s in streams
            ),
            "music_audio_invalid",
            409,
        )
        stream = audio[0]
        codec = stream["codec_name"]
        extension = {
            "flac": "flac",
            "mp3": "mp3",
            "aac": "m4a",
            "alac": "m4a",
            "vorbis": "ogg",
            "opus": "opus",
            "ape": "ape",
        }.get(codec)
        require(extension is not None, "music_format_unsupported", 409)
        duration = float(data["format"].get("duration", stream.get("duration", 0)))
        observed = {
            "codec": codec,
            "extension": extension,
            "duration_seconds": duration,
            "sample_rate": int(stream.get("sample_rate", 0)),
            "channels": int(stream.get("channels", 0)),
            "bits_per_sample": int(
                stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0
            ),
            "bit_rate": int(stream.get("bit_rate") or data["format"].get("bit_rate") or 0),
        }
    except (ValueError, KeyError, TypeError):
        raise Fault("music_audio_invalid", 409) from None
    return observed


async def lossless_flac(source, destination, observed, engine):
    """Convert a private lossless source while proving sample-for-sample identity."""
    destination.unlink(missing_ok=True)
    code, _, errors = await run_process(
        [
            executable_path(engine["ffmpeg"]),
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-map_metadata",
            "-1",
            "-c:a",
            "flac",
            "-f",
            "flac",
            str(destination),
        ],
        timeout=600,
    )
    require(code == 0 and not errors.strip(), "music_audio_invalid", 409)
    require(
        0 < destination.stat().st_size <= min(engine["max_bytes"], 1024**3),
        "music_file_too_large",
        409,
    )
    converted = await probe(destination, None, engine)
    require(
        all(
            converted[k] == observed[k]
            for k in ("content_id", "sample_rate", "channels", "bits_per_sample")
        ),
        "music_audio_invalid",
        409,
    )
    return converted


async def probe(path, track, engine):
    observed = await inspect_audio(path, engine)
    duration = observed["duration_seconds"]
    expected = float(track.get("duration_ms") or 0) / 1000 if track is not None else duration
    require(duration > 0 and expected > 0, "music_duration_unknown", 409)
    require(abs(duration - expected) <= max(2, expected * 0.02), "music_preview_rejected", 409)
    code, decoded, errors = await run_process(
        [
            executable_path(engine["ffmpeg"]),
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s32le",
            "-f",
            "hash",
            "-hash",
            "SHA256",
            "-",
        ],
        timeout=600,
    )
    require(code == 0 and not errors.strip(), "music_audio_invalid", 409)
    checksum = decoded.decode().strip() if isinstance(decoded, bytes) else decoded.strip()
    require(re.fullmatch(r"SHA256=[0-9a-f]{64}", checksum), "music_audio_invalid", 409)
    observed["content_id"] = digest(
        {
            "pcm_s32le": checksum,
            "sample_rate": observed["sample_rate"],
            "channels": observed["channels"],
        }
    )
    return observed


def cover(raw):
    try:
        with Image.open(io.BytesIO(raw)) as source:
            require(source.width * source.height <= 32_000_000, "cover_invalid", 409)
            source.load()
            image = source.convert("RGB")
            image.thumbnail((1600, 1600))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise Fault("cover_invalid", 409) from None


def number(value):
    found = re.match(r"^0*([1-9][0-9]{0,4})(?![0-9])", str(value or "").strip())
    return found.group(1) if found else ""


def tag_identity(track):
    return {
        "title": track.get("title") or track["source_id"],
        "artists": [a for a in track.get("artists", []) if a] or ["未知艺术家"],
        "album": track.get("album") or "未知专辑",
    }


def embedded_lyrics(audio):
    if isinstance(audio, MP3):
        values = audio.tags.getall("USLT") if audio.tags else []
        return values[0].text if values else ""
    values = audio.get("\u00a9lyr" if isinstance(audio, MP4) else "lyrics", [])
    return values[0] if values else ""


def set_lyrics(audio, lyrics):
    if audio.tags is None:
        audio.add_tags()
    if isinstance(audio, MP3):
        audio.tags.delall("USLT")
        audio.tags.add(USLT(encoding=3, lang="und", desc="", text=lyrics))
    else:
        audio["\u00a9lyr" if isinstance(audio, MP4) else "lyrics"] = [lyrics]


def write_tags(path, track, artwork, lyrics):
    identity = tag_identity(track)
    artists = identity["artists"]
    album_artists = track.get("album_artists") or artists
    title, album = identity["title"], identity["album"]
    date = track.get("date", "")
    index, disc = number(track.get("track_number")), number(track.get("disc"))
    provider = track.get("provider", "netease")
    id_tag = {"netease": "NETEASEID", "qq": "QQMUSICID", "kugou": "KUGOUHASH"}.get(
        provider, "TIANSHUSOURCEID"
    )
    source = {
        "netease": "https://music.163.com/#/song?id=",
        "qq": "https://y.qq.com/n/ryqq/songDetail/",
        "kugou": "https://www.kugou.com/song/#hash=",
    }.get(provider, "") + track["source_id"]
    try:
        audio = mutagen.File(path)
        require(
            isinstance(audio, (FLAC, MP3, MP4, OggVorbis, OggOpus)), "music_format_unsupported", 409
        )
        if audio.tags is None:
            audio.add_tags()
        if isinstance(audio, (FLAC, OggVorbis, OggOpus)):
            values = {
                "title": [title],
                "artist": artists,
                "album": [album],
                "albumartist": album_artists,
                "date": [date],
                "tracknumber": [index],
                "discnumber": [disc],
                id_tag: [track["source_id"]],
                "website": [source],
            }
            for key, value in values.items():
                if any(value):
                    audio[key] = value
            if artwork:
                picture = Picture()
                picture.type, picture.mime, picture.data = 3, "image/jpeg", artwork
                if isinstance(audio, FLAC):
                    audio.clear_pictures()
                    audio.add_picture(picture)
                else:
                    audio["metadata_block_picture"] = [
                        base64.b64encode(picture.write()).decode("ascii")
                    ]
        elif isinstance(audio, MP3):
            for key in ("TIT2", "TPE1", "TALB", "TPE2", "TDRC", "TRCK", "TPOS", "USLT", "APIC"):
                audio.tags.delall(key)
            for cls, value in (
                (TIT2, [title]),
                (TPE1, artists),
                (TALB, [album]),
                (TPE2, album_artists),
                (TDRC, [date]),
                (TRCK, [index]),
                (TPOS, [disc]),
            ):
                if any(value):
                    audio.tags.add(cls(encoding=3, text=value))
            audio.tags.add(TXXX(encoding=3, desc=id_tag, text=[track["source_id"]]))
            audio.tags.add(TXXX(encoding=3, desc="SOURCE", text=[source]))
            if artwork:
                audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, data=artwork))
        else:
            for key, value in {
                "\u00a9nam": [title],
                "\u00a9ART": artists,
                "\u00a9alb": [album],
                "aART": album_artists,
                "\u00a9day": [date],
            }.items():
                if any(value):
                    audio[key] = value
            if index:
                audio["trkn"] = [(int(index), 0)]
            if disc:
                audio["disk"] = [(int(disc), 0)]
            audio["----:com.apple.iTunes:" + id_tag] = [track["source_id"].encode()]
            if artwork:
                audio["covr"] = [MP4Cover(artwork, imageformat=MP4Cover.FORMAT_JPEG)]
        if lyrics:
            set_lyrics(audio, lyrics)
        audio.save()
        sync_file(path)
    except (mutagen.MutagenError, ValueError):
        raise Fault("music_tag_failed", 409) from None
