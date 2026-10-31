"""Recoverable lyric sidecars and tag updates, preserving decoded audio identity."""

import hashlib
import json
from pathlib import Path

import mutagen

from ...contracts import digest, require
from ..destination import safe_path
from ..organizer import library_lock
from ..video_storage import sync_directory, sync_file
from .lyrics_text import LIMIT, clean, sidecars, render
from .lyrics_tags import prepare as prepare_tags
from .audio import record

KIND = "music_lyrics"
WEEK = 7 * 86400
VERSION = 2


def audio_file(artifact):
    return next(f for f in artifact["files"] if f["kind"] == "audio")


def archive_key(artifact):
    audio = audio_file(artifact)
    return digest(
        {
            "directory": artifact["directory"],
            "audio": {k: audio[k] for k in ("path", "kind", "sha256", "size_bytes")},
        }
    )


def schedule(repo, identity, artifact, db):
    current = repo._document(db, KIND, identity) or {}
    key = archive_key(artifact)
    same = current.get("archive_key") == key
    if not same or current.get("version") != VERSION:
        repo.put(
            KIND,
            identity,
            {
                **(current if same else {}),
                "version": VERSION,
                "network_after": current.get("next_check_at", 0) if same else 0,
                "recording_id": identity,
                "archive_key": key,
                "state": "queued",
                "next_check_at": 0,
                "attempts": current.get("attempts", 0) if same else 0,
            },
            db=db,
        )


def read_local(artifact):
    root, audio = Path(artifact["directory"]), audio_file(artifact)
    path = safe_path(root, audio["path"])
    require(
        path.is_file() and path.stat().st_size == audio["size_bytes"], "music_source_changed", 409
    )
    base = Path(audio["path"]).with_suffix("").as_posix()

    def read(suffix):
        candidate = safe_path(root, base + suffix)
        if candidate.is_file() and candidate.stat().st_size <= LIMIT * 4:
            return clean(candidate.read_text(encoding="utf-8-sig", errors="replace"))
        return ""

    result = {
        "original": read(".original.lrc") or read(".lrc"),
        "translated": read(".zh.lrc"),
        "romanized": read(".rom.lrc"),
    }
    if not result["original"]:
        try:
            tags = mutagen.File(path)
            if tags is not None:
                for key in ("lyrics", "LYRICS", "\u00a9lyr"):
                    values = tags.get(key)
                    if values:
                        result["original"] = clean(values[0])
                        break
                if (
                    not result["original"]
                    and tags.tags is not None
                    and hasattr(tags.tags, "getall")
                ):
                    values = tags.tags.getall("USLT")
                    if values:
                        result["original"] = clean(values[0].text)
        except (mutagen.MutagenError, ValueError):
            pass
    return result


def file_digest(path):
    if not path.is_file():
        return None
    require(path.stat().st_size <= LIMIT * 4, "music_lyrics_changed", 409)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LyricStorage:
    def __init__(self, music):
        self.music, self.repo = music, music.media.repo

    def current(self, artifact, owner, generation, db):
        self.repo.require_scan(db, "music_lyrics", owner, generation)
        catalog = self.music.library.catalog
        current = catalog.file(artifact["recording_id"], db=db)
        settings = self.repo._document(db, "music_settings", "library") or {}
        require(
            current
            and current["recording_id"] == artifact["recording_id"]
            and archive_key(current) == archive_key(artifact)
            and settings.get("directory") == artifact["directory"],
            "scope_changed",
            409,
        )
        return current

    def publish(self, artifact, value, result, owner, generation):
        identity, root = artifact["recording_id"], Path(artifact["directory"])
        base = Path(audio_file(artifact)["path"]).with_suffix("").as_posix()
        with library_lock(str(root)) as (_, control):
            with self.repo.transaction() as db:
                self.current(artifact, owner, generation, db)
                state = self.repo._document(db, KIND, identity) or {}
            pending = state.get("pending")
            if not pending:
                writes = []
                for suffix, text in sidecars(value).items():
                    relative = base + suffix
                    content = (text + "\n").encode("utf8")
                    writes.append(
                        {
                            "path": relative,
                            "content": content.decode("utf8"),
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "size_bytes": len(content),
                            "kind": "lyrics",
                            "before": file_digest(safe_path(root, relative)),
                        }
                    )
                pending = {"writes": writes, "result": result}
            if "audio" not in pending:
                # Copies, tagging and PCM validation hold the filesystem lock,
                # never the database transaction or the event loop.
                text = next(
                    (w["content"] for w in pending["writes"] if w["path"] == base + ".lrc"),
                    render(value),
                )
                pending["audio"] = prepare_tags(
                    artifact, text, control, self.music.media.config["engine"]
                )
                with self.repo.transaction() as db:
                    self.current(artifact, owner, generation, db)
                    self.repo.put(
                        KIND,
                        identity,
                        {
                            **state,
                            "recording_id": identity,
                            "archive_key": archive_key(artifact),
                            "state": "publishing",
                            "next_check_at": 0,
                            "pending": pending,
                        },
                        db=db,
                    )
            # The persisted intent makes interrupted replacements idempotent.
            changed = False
            audio = pending.get("audio")
            if audio:
                target = safe_path(root, audio["path"])
                temporary = safe_path(control, audio["staging"])
                observed = record(target)
                require(observed in (audio["before"], audio["after"]), "music_source_changed", 409)
                if observed != audio["after"]:
                    require(record(temporary) == audio["after"], "music_staging_changed", 409)
                    temporary.replace(target)
                    sync_directory(target.parent)
                    changed = True
            for index, item in enumerate(pending["writes"]):
                target = safe_path(root, item["path"])
                actual = file_digest(target)
                require(actual in {item["before"], item["sha256"]}, "music_lyrics_changed", 409)
                if actual == item["sha256"]:
                    continue
                temporary = safe_path(control, f"lyrics-{identity}-{index}.tmp")
                temporary.write_bytes(item["content"].encode("utf8"))
                sync_file(temporary)
                temporary.replace(target)
                sync_directory(target.parent)
                changed = True
            with self.repo.transaction() as db:
                current = self.current(artifact, owner, generation, db)
                items = [
                    {k: item[k] for k in ("path", "sha256", "size_bytes", "kind")}
                    for item in pending["writes"]
                ]
                if audio:
                    items.insert(0, {"kind": "audio", "path": audio["path"], **audio["after"]})
                paths = {item["path"] for item in items}
                result = {
                    **pending["result"],
                    "audio_tags_updated": bool(audio),
                    "audio_content_unchanged": True,
                }
                updated = {
                    **current,
                    "files": [f for f in current["files"] if f["path"] not in paths] + items,
                    "lyrics": result,
                }
                if audio:
                    updated["content_id"] = audio["content_id"]
                if result["coverage"]["original"]:
                    updated["warnings"] = [
                        w for w in updated.get("warnings", []) if w != "music_lyrics_unavailable"
                    ]
                # This is metadata maintenance, never a new/retired audio artifact.
                self.repo.put("music_library_file", identity, updated, db=db)
                for raw in db.execute(
                    "SELECT kind,id,body FROM documents WHERE kind IN ('music_artifact','music_publication')"
                ).fetchall():
                    other = json.loads(raw["body"])
                    if (
                        other.get("files")
                        and other.get("state") != "prepared"
                        and archive_key(other) == archive_key(artifact)
                    ):
                        self.repo.put(
                            raw["kind"],
                            raw["id"],
                            {
                                **other,
                                "files": [
                                    {
                                        **next(
                                            (
                                                f
                                                for f in other["files"]
                                                if f["path"] == item["path"]
                                            ),
                                            {},
                                        ),
                                        **item,
                                    }
                                    for item in updated["files"]
                                ],
                                "warnings": updated.get("warnings", []),
                                "lyrics": result,
                            },
                            db=db,
                        )
                # Update historical receipts without requeuing jobs or changing leases.
                db.execute(
                    "UPDATE jobs SET body=jsonb_set(body::jsonb,'{audio_receipt}',%s::jsonb)::text WHERE (body::jsonb #>> '{recording_id}')=%s AND (body::jsonb #>> '{audio_receipt,directory}')=%s",
                    (json.dumps(updated, ensure_ascii=False), identity, artifact["directory"]),
                )
                self.repo.put(
                    KIND,
                    identity,
                    {
                        "recording_id": identity,
                        "archive_key": archive_key(updated),
                        "version": VERSION,
                        **result,
                        "changed": changed,
                    },
                    db=db,
                )
                if audio:
                    self.music.navidrome.state.recording_changed(identity, db)
            return {**result, "changed": changed}
