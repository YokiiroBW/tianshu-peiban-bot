"""Durable, bounded file transfers for the NAS comic writer; no media database."""

import hashlib
import json
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path

from ...contracts import require
from ..destination import safe_path, subdirectory
from ..organizer import library_lock
from ..video_storage import sync_directory
from .packages import MAX_CHAPTER_BYTES
from .publication import install, verify

PROTOCOL = "comic-publisher-v1"
PREFIX = "/comics/v1/transfers"
CHUNK_BYTES = 4 * 1024**2
MAX_FILE_BYTES = MAX_CHAPTER_BYTES + 16 * 1024**2
TARGETS = {"comic-main", "comic-adult"}


def checked_key(key):
    require(
        isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key), "invalid_input", 400
    )
    return key


def checked_intent(value):
    require(
        isinstance(value, dict)
        and set(value) == {"target_id", "relative_path", "length", "sha256"},
        "invalid_input",
        400,
    )
    relative = value["relative_path"]
    require(isinstance(relative, str) and relative == subdirectory(relative), "invalid_input", 400)
    parts = relative.split("/")
    require(
        value["target_id"] in TARGETS
        and len(parts) == 3
        and parts[-1].endswith(".cbz")
        and relative == unicodedata.normalize("NFC", relative)
        and all(len(p.encode("utf-8")) <= 200 for p in parts)
        and not any(c == "%" or ord(c) == 127 for c in relative)
        and isinstance(value["length"], str)
        and re.fullmatch(r"[1-9][0-9]{0,10}", value["length"])
        and int(value["length"]) <= MAX_FILE_BYTES
        and isinstance(value["sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]),
        "invalid_input",
        400,
    )
    return dict(value)


class PublisherStore:
    def __init__(self, targets, clock=time.time):
        require(
            isinstance(targets, dict) and targets and set(targets) <= TARGETS, "invalid_input", 400
        )
        self.targets = {}
        for key, value in targets.items():
            require(isinstance(value, str) and Path(value).is_absolute(), "invalid_input", 400)
            self.targets[key] = value
        self.clock = clock

    def directory(self, target):
        require(target in self.targets, "comic_publisher_target_missing", 404)
        return self.targets[target]

    @staticmethod
    def paths(control, key):
        checked_key(key)
        return safe_path(control, key + ".json"), safe_path(control, key + ".part")

    @staticmethod
    def read(path):
        if not path.exists():
            return None
        require(
            path.is_file() and path.stat().st_size <= 8192, "comic_publisher_state_invalid", 409
        )
        try:
            value = json.loads(path.read_bytes())
        except (ValueError, UnicodeDecodeError):
            require(False, "comic_publisher_state_invalid", 409)
        require(
            isinstance(value, dict) and value.get("protocol") == PROTOCOL,
            "comic_publisher_state_invalid",
            409,
        )
        return value

    @staticmethod
    def save(path, value):
        temporary = safe_path(path.parent, path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, target, key):
        with library_lock(self.directory(target)) as (_, control):
            path, _ = self.paths(control, key)
            value = self.read(path)
            require(value is not None, "comic_publisher_not_found", 404)
            return value

    def prepare(self, target, key, intent):
        intent = checked_intent(intent)
        require(intent["target_id"] == target, "invalid_input", 400)
        with library_lock(self.directory(target)) as (_, control):
            path, _ = self.paths(control, key)
            value = self.read(path)
            if value:
                require(value["intent"] == intent, "comic_publisher_conflict", 409)
                return value
            value = {
                "protocol": PROTOCOL,
                "key": key,
                "intent": intent,
                "state": "uploading",
                "offset": 0,
            }
            self.save(path, value)
            return value

    def append(self, target, key, offset, data, sha256):
        require(
            type(offset) is int and offset >= 0 and 0 < len(data) <= CHUNK_BYTES,
            "invalid_input",
            400,
        )
        require(hashlib.sha256(data).hexdigest() == sha256, "comic_publisher_chunk_mismatch", 409)
        with library_lock(self.directory(target)) as (_, control):
            path, temporary = self.paths(control, key)
            value = self.read(path)
            require(value is not None, "comic_publisher_not_found", 404)
            require(offset + len(data) <= int(value["intent"]["length"]), "invalid_input", 400)
            require(value["state"] == "uploading", "comic_publisher_conflict", 409)
            require(offset <= value["offset"], "comic_publisher_offset_conflict", 409)
            if offset < value["offset"]:
                require(
                    offset + len(data) <= value["offset"], "comic_publisher_offset_conflict", 409
                )
                with temporary.open("rb") as stream:
                    stream.seek(offset)
                    require(stream.read(len(data)) == data, "comic_publisher_chunk_mismatch", 409)
                return value
            with temporary.open("r+b" if temporary.exists() else "xb") as stream:
                stream.seek(0, os.SEEK_END)
                require(stream.tell() >= offset, "comic_publisher_state_invalid", 409)
                # A killed writer can leave a durable tail whose journal update did not finish.
                stream.truncate(offset)
                stream.seek(offset)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            value = {**value, "offset": offset + len(data)}
            self.save(path, value)
            return value

    def complete(self, target, key):
        with library_lock(self.directory(target)) as (root, control):
            path, temporary = self.paths(control, key)
            value = self.read(path)
            require(value is not None, "comic_publisher_not_found", 404)
            intent = value["intent"]
            artifact = {"size_bytes": int(intent["length"]), "sha256": intent["sha256"]}
            destination = safe_path(root, intent["relative_path"])
            if destination.exists():
                verify(destination, artifact)
                reused = True
            else:
                require(
                    value["offset"] == artifact["size_bytes"], "comic_publisher_incomplete", 409
                )
                verify(temporary, artifact)
                reused = install(root, temporary, intent["relative_path"], artifact)
            # Drop the staging link before AS opens a native single-link content handle.
            temporary.unlink(missing_ok=True)
            sync_directory(control)
            value = {
                **value,
                "offset": artifact["size_bytes"],
                "state": "published",
                "published_at": value.get("published_at", int(self.clock())),
                "reused": value.get("reused", reused),
            }
            self.save(path, value)
            return value
