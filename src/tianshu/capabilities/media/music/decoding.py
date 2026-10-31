"""Automatic offline unlocking of private staging files, shared by downloads and imports."""

import asyncio
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ...contracts import Fault, require
from . import tools
from .audio import cover, record

PLAIN = {".flac", ".mp3", ".m4a", ".ogg", ".opus"}
ENCRYPTED = {
    ".ncm",
    ".qmc0",
    ".qmc2",
    ".qmc3",
    ".qmc4",
    ".qmc6",
    ".qmc8",
    ".qmcflac",
    ".qmcogg",
    ".kgm",
    ".kgma",
    ".vpr",
    ".kgg",
    *{
        "." + base + suffix
        for base in ("mflac", "mgg")
        for suffix in ("", "0", "1", "a", "h", "l", "m")
    },
}
KGM_MAGIC = bytes.fromhex("7cd532eb86027f4ba8afa68e0fff9914")
VPR_MAGIC = bytes.fromhex("0528bc96e9e45a4391aabdd07af53631")


def encrypted_format(path, hints=(), provider=None):
    with path.open("rb") as stream:
        head = stream.read(64)
        stream.seek(max(0, path.stat().st_size - 8))
        tail = stream.read(8)
    # Actual plaintext takes precedence over a stale CDN filename or extension.
    if (
        head.startswith((b"fLaC", b"ID3", b"OggS", b"MAC "))
        or head[4:8] == b"ftyp"
        or len(head) >= 2
        and head[0] == 0xFF
        and head[1] & 0xE0 == 0xE0
    ):
        return None
    if head.startswith(b"CTENFDAM"):
        return ".ncm"
    if head.startswith(KGM_MAGIC):
        return ".kgm"
    if head.startswith(VPR_MAGIC):
        return ".vpr"
    for hint in (*hints, path.name):
        if isinstance(hint, str):
            suffix = Path(unquote(urlsplit(hint).path)).suffix.lower()
            if suffix in ENCRYPTED:
                return suffix
            if "." + hint.lower() in ENCRYPTED:
                return "." + hint.lower()
    if tail[-4:] in {b"QTag", b"STag"} or tail == b"musicex\x00" or provider == "qq":
        # QMC has several ciphers and no universal header; the decoder validates its result.
        return ".qmc0"
    return None


class Decoder:
    def __init__(self, media, storage):
        self.media, self.storage = media, storage

    async def prepare(self, job, source, update, *, hints=(), provider=None):
        local = self.media.local
        extension = await local(encrypted_format, source, hints, provider)
        if extension is None:
            unlock = {"state": "not_needed"}
            await update(audio_unlock=unlock)
            return source, {}, unlock, None, []
        original = await local(record, source)
        unlock = {
            "state": "decoding",
            "format": extension[1:],
            "input_sha256": original["sha256"],
            "input_size_bytes": original["size_bytes"],
        }
        await update(
            state="validating", stage="decoding", code="music_unlocking", audio_unlock=unlock
        )
        staged = await local(self.storage.stage, job, "unlock-input" + extension)
        output = await local(self.storage.stage, job, "unlocked.part")
        picture = await local(self.storage.stage, job, "unlocked.cover")
        # These paths belong to this leased generation; the caller supplies only a staging copy.
        for path in (staged, output, picture):
            if path != source:
                path.unlink(missing_ok=True)
        if source != staged:
            source.replace(staged)
        try:
            data = await tools.invoke(
                "musicdecode",
                {"input": str(staged), "output": str(output), "cover": str(picture)},
                timeout=600,
            )
            require(
                0 < output.stat().st_size <= min(1024**3, self.media.config["engine"]["max_bytes"]),
                "music_file_too_large",
                409,
            )
        except Fault as error:
            unlock.update(
                state={
                    "music_decode_key_required": "key_required",
                    "music_decode_unsupported": "unsupported",
                }.get(error.code, "failed"),
                code=error.code,
            )
            await update(audio_unlock=unlock)
            raise
        except asyncio.CancelledError:
            unlock.update(state="interrupted")
            try:
                await update(audio_unlock=unlock)
            except Fault:
                pass  # A new lease owner controls its own status.
            raise
        unlock.update(state="decoded", tool=data["tool"], version=data["version"])
        await update(state="validating", stage="validating", code="validating", audio_unlock=unlock)
        artwork, warnings = None, []
        if picture.exists():
            try:
                artwork = await local(lambda: cover(picture.read_bytes()))
            except Fault:
                warnings.append("music_cover_unavailable")
        return output, data, unlock, artwork, warnings
