"""Prepare a verified local tag replacement for Navidrome's native web player."""

import asyncio
import shutil
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from ...contracts import Fault, require
from ..destination import safe_path
from ..video_storage import copy_file, sync_file
from .audio import embedded_lyrics, set_lyrics, record, probe
from .lyrics_text import clean


def prepare(artifact, text, control, engine):
    if not text:
        return None
    item = next(f for f in artifact["files"] if f["kind"] == "audio")
    source = safe_path(Path(artifact["directory"]), item["path"])
    audio = mutagen.File(source)
    require(
        isinstance(audio, (FLAC, MP3, MP4, OggOpus, OggVorbis)), "music_format_unsupported", 409
    )
    if clean(embedded_lyrics(audio)) == clean(text):
        return None
    expected = {k: item[k] for k in ("sha256", "size_bytes")}
    require(record(source) == expected, "music_source_changed", 409)
    temporary = safe_path(control, f"lyrics-{artifact['recording_id']}-audio{source.suffix}")
    temporary.unlink(missing_ok=True)
    copy_file(source, temporary)
    try:
        shutil.copystat(source, temporary)
        copied = mutagen.File(temporary)
        set_lyrics(copied, text)
        copied.save()
        sync_file(temporary)
        # The same decoder/PCM identity used by audio publication proves that
        # changing the container's lyrics tags did not alter the music.
        before = (
            artifact.get("content_id") or asyncio.run(probe(source, None, engine))["content_id"]
        )
        observed = asyncio.run(probe(temporary, None, engine))
        require(observed["content_id"] == before, "music_audio_changed", 409)
        require(record(source) == expected, "music_source_changed", 409)
        return {
            "path": item["path"],
            "staging": temporary.name,
            "before": expected,
            "after": record(temporary),
            "content_id": before,
        }
    except (mutagen.MutagenError, ValueError):
        temporary.unlink(missing_ok=True)
        raise Fault("music_tag_failed", 409) from None
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
