"""Rebuildable byte/time indexes; original append files remain the log authority."""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BLOCK_BYTES = 256 * 1024
MAX_LINE = 8 * 1024 * 1024
_STAMP = re.compile(r"^(\d{4}-\d\d-\d\d[Tt ]\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$")


def event_ns(value):
    match = _STAMP.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("invalid_timestamp")
    zone = "+00:00" if match.group(3) == "Z" else match.group(3)
    instant = datetime.fromisoformat(match.group(1) + zone).astimezone(UTC)
    delta = instant - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + int((match.group(2) or "").ljust(9, "0"))


def decode_record(line):
    row = json.loads(line)
    if not isinstance(row, dict):
        raise TypeError("invalid_log_record")
    stamp = event_ns(row.get("timestamp"))
    instance, event_id, sequence = row.get("instance_id"), row.get("event_id"), row.get("sequence")
    if (not isinstance(instance, str) or not instance or len(instance) > 128
            or not isinstance(event_id, str) or not event_id or len(event_id) > 128
            or type(sequence) is not int or sequence < 1):
        raise ValueError("invalid_log_record")
    return row, (stamp, instance, sequence, event_id)


class SourceChanged(ValueError):
    pass


class SourceTimeIndex:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.cache = self.directory / ".query-index"

    @staticmethod
    def _valid(value, source):
        if not isinstance(value, dict) or value.get("version") != 1:
            return False
        if (value.get("device"), value.get("inode")) != (source["device"], source["inode"]):
            return False
        indexed, observed = value.get("indexed_size"), value.get("observed_size")
        if type(indexed) is not int or type(observed) is not int or not 0 <= indexed <= observed:
            return False
        # A same-size rewrite is not an append, so its cached times cannot be reused.
        if (observed == source["size"] and source.get("modified_ns") is not None
                and source["modified_ns"] != value.get("modified_ns")):
            return False
        if not isinstance(value.get("blocks"), list):
            return False
        position = 0
        for block in value["blocks"]:
            if not isinstance(block, dict) or block.get("start") != position:
                return False
            end = block.get("end")
            if type(end) is not int or not position < end <= indexed:
                return False
            low, high = block.get("min_ns"), block.get("max_ns")
            if not (low is None and high is None or type(low) is int and type(high) is int and low <= high):
                return False
            if not isinstance(block.get("missing"), list) or any(
                flag not in {"invalid_log_record", "oversized_or_partial_record"} for flag in block["missing"]
            ):
                return False
            position = end
        return position == indexed

    def _load(self, source):
        if self.cache.is_symlink():
            return None
        path = self.cache / (source["name"] + ".json")
        if path.is_symlink():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if self._valid(value, source) else None
        except (OSError, ValueError, TypeError):
            return None

    def _save(self, source, value):
        temporary = None
        try:
            if self.cache.is_symlink():
                return
            self.cache.mkdir(mode=0o700, exist_ok=True)
            temporary = self.cache / (uuid4().hex + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, separators=(",", ":"))
            os.replace(temporary, self.cache / (source["name"] + ".json"))
        except OSError:
            # A cache write failure must not make the original logs unreadable.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _append(stream, blocks, start, horizon):
        stream.seek(start)
        low = high = None
        missing = set()
        block_start = position = start
        while position < horizon:
            line_start = position
            line = stream.readline(min(horizon - position, MAX_LINE + 1))
            if not line:
                raise SourceChanged("source_changed")
            position += len(line)
            if not line.endswith(b"\n"):
                while position < horizon and not line.endswith(b"\n"):
                    line = stream.readline(min(horizon - position, MAX_LINE + 1))
                    if not line:
                        raise SourceChanged("source_changed")
                    position += len(line)
                if not line.endswith(b"\n"):
                    # Re-read the unfinished line after it is completed, rather
                    # than permanently indexing or skipping a partial tail.
                    position = line_start
                    break
                missing.add("oversized_or_partial_record")
            else:
                try:
                    _, key = decode_record(line)
                    low = min(low, key[0]) if low is not None else key[0]
                    high = max(high, key[0]) if high is not None else key[0]
                except (ValueError, TypeError, UnicodeError, OverflowError):
                    missing.add("invalid_log_record")
            if position - block_start >= BLOCK_BYTES:
                blocks.append({"start": block_start, "end": position,
                               "min_ns": low, "max_ns": high, "missing": sorted(missing)})
                block_start = position
                low = high = None
                missing = set()
        if position > block_start:
            blocks.append({"start": block_start, "end": position,
                           "min_ns": low, "max_ns": high, "missing": sorted(missing)})
        return position

    def get(self, source):
        path = self.directory / source["name"]
        if path.parent != self.directory or path.is_symlink():
            raise SourceChanged("source_changed")
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            if (stat.st_dev, stat.st_ino) != (source["device"], source["inode"]) or stat.st_size < source["size"]:
                raise SourceChanged("source_changed")
            value = self._load(source)
            if value is None or value["indexed_size"] > stat.st_size:
                value = {"version": 1, "device": source["device"], "inode": source["inode"],
                         "indexed_size": 0, "observed_size": 0, "modified_ns": None, "blocks": []}
            blocks = value["blocks"]
            indexed_bytes = 0
            if value["indexed_size"] < source["size"]:
                start = value["indexed_size"]
                # Reuse complete blocks and only rebuild the small final block.
                if blocks and blocks[-1]["end"] - blocks[-1]["start"] < BLOCK_BYTES:
                    start = blocks.pop()["start"]
                indexed = self._append(stream, blocks, start, source["size"])
                indexed_bytes = source["size"] - start
                value.update(indexed_size=indexed, observed_size=source["size"],
                             modified_ns=source.get("modified_ns"))
                self._save(source, value)
            missing = set()
            for block in blocks:
                if block["start"] < source["size"]:
                    missing.update(block["missing"])
            if value["indexed_size"] < source["size"]:
                missing.add("oversized_or_partial_record")
            elif source["size"] and value["indexed_size"] > source["size"]:
                # Another request may have indexed a now-completed tail. An
                # older snapshot must still report its original partial line.
                stream.seek(source["size"] - 1)
                if stream.read(1) != b"\n":
                    missing.add("oversized_or_partial_record")
            return blocks, missing, indexed_bytes
