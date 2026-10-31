"""Immutable audio files and manifests published before the durable completion record."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from mutagen.mp3 import MP3

from ....contracts import Fault, require
from ...destination import safe_path
from ..organization import book_relative_path
from .segments import cues


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def duration(raw):
    try:
        info = MP3(io.BytesIO(raw)).info
        require(
            0 < info.length <= 3600 and info.sample_rate > 0, "book_audio_response_invalid", 502
        )
        return info.length
    except Exception as error:
        if isinstance(error, Fault):
            raise
        raise Fault("book_audio_response_invalid", 502) from None


class AudioFiles:
    def __init__(self, books):
        self.books = books
        self.ffmpeg = (books.media.config or {}).get("engine", {}).get("ffmpeg", "ffmpeg")

    def root(self):
        directory = self.books.catalog.settings()["directory"]
        require(directory and Path(directory).is_dir(), "book_directory_unavailable", 409)
        return Path(directory)

    def cache(self, root, key):
        return safe_path(Path(root), ".tianshu-audio-cache/" + key)

    def read(self, root, key):
        folder = self.cache(root, key)
        try:
            manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
            path = folder / "audio.mp3"
            raw = path.read_bytes()
            if (
                manifest["key"] != key
                or len(raw) != manifest["bytes"]
                or hashlib.sha256(raw).hexdigest() != manifest["sha256"]
            ):
                return None
            return manifest
        except (OSError, ValueError, KeyError):
            return None

    def publish(self, root, key, text, raw, boundaries):
        length = duration(raw)
        alignment, mode = cues(text, boundaries, length)
        folder = self.cache(root, key)
        manifest = {
            "key": key,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "duration": length,
            "cues": alignment,
            "sync": mode,
        }
        atomic(folder / "audio.mp3", raw)
        atomic(folder / "manifest.json", json.dumps(manifest, ensure_ascii=False).encode())
        return manifest

    async def assemble(self, job, pieces):
        root, key = job["root"], job["key"]
        local=self.books.media.local
        existing = await local(self.read,root,key)
        if existing:
            return await local(self.link,job,existing)
        folder = self.cache(root, key)
        folder.mkdir(parents=True, exist_ok=True)
        join = folder / (uuid.uuid4().hex + ".partial.mp3")
        listing = folder / (uuid.uuid4().hex + ".concat")
        combined, offset, modes = [], 0.0, set()
        try:
            listing.write_text(
                "".join("file '../" + part["key"] + "/audio.mp3'\n" for part, _ in pieces),
                encoding="utf-8",
            )
            from ...engine import run_process
            code,_,_=await run_process([self.ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-f","concat","-safe","0","-i",str(listing),"-map","0:a:0","-c","copy","-y",str(join)],timeout=90,limit=65536)
            require(code==0 and join.is_file(),"book_audio_assembly_failed",502)
            raw = await local(join.read_bytes)
            length = await local(duration,raw)
            for part, segment in pieces:
                for cue in part["cues"]:
                    combined.append(
                        {
                            "start": cue["start"] + offset,
                            "end": cue["end"] + offset,
                            "text_start": cue["text_start"] + segment["start"],
                            "text_end": cue["text_end"] + segment["start"],
                        }
                    )
                offset += part["duration"]
                modes.add(part["sync"])
            manifest = {
                "key": key,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "duration": length,
                "cues": combined,
                "sync": "sentence" if modes == {"sentence"} else "segment",
                "text": job["text"],
                "content_hash": job["content_hash"],
                "profile": job["profile"],
                "chapter_id": job["chapter_id"],
            }
            await local(atomic,folder / "audio.mp3",raw)
            await local(atomic,folder / "manifest.json",json.dumps(manifest,ensure_ascii=False).encode())
            return await local(self.link,job,manifest)
        except (OSError, subprocess.TimeoutExpired):
            raise Fault("book_audio_assembly_failed", 502) from None
        finally:
            join.unlink(missing_ok=True)
            listing.unlink(missing_ok=True)

    def link(self, job, manifest):
        book = self.books.catalog.book(job["book_id"])
        relative = (
            Path(book_relative_path(book)).parent
            / "audio"
            / job["profile_id"]
            / (job["chapter_id"] + ".mp3")
        ).as_posix()
        target = safe_path(Path(job["root"]), relative)
        source = self.cache(job["root"], job["key"]) / "audio.mp3"
        target.parent.mkdir(parents=True, exist_ok=True)
        if (
            not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != manifest["sha256"]
        ):
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".partial")
            try:
                try:
                    os.link(source, temporary)
                except OSError:
                    shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        atomic(
            target.with_suffix(".json"),
            json.dumps({**manifest, "title": job["title"]}, ensure_ascii=False).encode(),
        )
        return {
            k: v
            for k, v in {**manifest, "relative_path": relative}.items()
            if k not in {"text", "cues"}
        }
