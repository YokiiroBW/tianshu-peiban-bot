"""Stable, bounded-memory paging over the existing append-only log files."""

from __future__ import annotations

import base64
import heapq
import json
import os
import re
import shutil
import time
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .errors import AppError
from .rb_logs_index import MAX_LINE, SourceChanged, SourceTimeIndex, decode_record
from .rb_logs_index import event_ns as _event_ns
from .rb_logs_output import LEVELS, redact_text

MAX_PAGE_BYTES = 4 * 1024 * 1024
SNAPSHOT_SECONDS = 24 * 60 * 60
_SENSITIVE = re.compile(r"^(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
                        r"password|passwd|secret|credentials?|prompt|messages|body|payload|"
                        r"content|arguments|kwargs|source_text)$", re.I)


def _ns(value):
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        result = int(value)
    else:
        raise AppError("invalid_log_time_range")
    if not 0 <= result <= 2**63 - 1:
        raise AppError("invalid_log_time_range")
    return result


def _text(value, name, *, maximum=128):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise AppError("invalid_log_" + name)
    return value


def _sanitize(value, secrets):
    if isinstance(value, dict):
        return {str(key): "[redacted]" if _SENSITIVE.fullmatch(str(key)) else _sanitize(item, secrets)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets)
    return value


def _filters(start_ns, end_ns, role, level, module, correlation_id, trace_id,
             job_id, attempt_id, keyword, exclude_conversation):
    start, end = _ns(start_ns), _ns(end_ns)
    if end <= start or end - start > 24 * 3600 * 1_000_000_000:
        raise AppError("invalid_log_time_range")
    level = _text(level, "level")
    if level:
        level = level.upper()
        if level == "WARN":
            level = "WARNING"
        if level not in LEVELS:
            raise AppError("invalid_log_level")
    if job_id is not None:
        job_id = _text(str(job_id), "job_id")
    if type(exclude_conversation) is not bool:
        raise AppError("invalid_log_exclude_conversation")
    return {
        "start_ns": start, "end_ns": end,
        "role": _text(role, "role"), "level": level, "module": _text(module, "module"),
        "correlation_id": _text(correlation_id, "correlation_id"),
        "trace_id": _text(trace_id, "trace_id"), "job_id": job_id,
        "attempt_id": _text(attempt_id, "attempt_id"),
        "keyword": _text(keyword, "keyword", maximum=512),
        "exclude_conversation": exclude_conversation,
    }


def _matches(row, filters):
    for field in ("role", "level", "module", "correlation_id", "trace_id", "job_id", "attempt_id"):
        selected = filters[field]
        actual = row.get(field)
        if selected is not None and (actual is None or str(actual) != selected):
            return False
    if filters["exclude_conversation"]:
        values = (row.get("event"), row.get("module"), row.get("task_name"), row.get("route"))
        if any(isinstance(item, str) and (item.startswith("conversation.")
               or item.startswith("tianshu.conversation") or item.startswith("/api/v1/conversation"))
               for item in values):
            return False
    return not filters["keyword"] or filters["keyword"].casefold() in json.dumps(
        row, ensure_ascii=False, separators=(",", ":")
    ).casefold()


def _encode_cursor(snapshot_id, before):
    raw = json.dumps({"snapshot": snapshot_id, "before": before}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor):
    if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
        raise AppError("invalid_log_cursor")
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        value = json.loads(raw)
        snapshot_id = value["snapshot"]
        before = value["before"]
        if (not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{32}", snapshot_id)
                or not isinstance(before, list) or len(before) != 4
                or type(before[0]) is not int or not isinstance(before[1], str)
                or type(before[2]) is not int or not isinstance(before[3], str)):
            raise ValueError
        return snapshot_id, tuple(before)
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise AppError("invalid_log_cursor") from None


class LogQuery:
    def __init__(self, directory, *, secrets=()):
        self.directory = Path(directory)
        self.snapshots = self.directory / ".query-snapshots"
        self.secrets = tuple(secrets)
        self.index = SourceTimeIndex(self.directory)

    def _capture_sources(self):
        if not self.directory.is_dir():
            raise AppError("local_log_directory_unavailable", status=503)
        sources, states, missing = [], [], set()
        try:
            paths = sorted(self.directory.glob("*.jsonl"))
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    missing.add("unsafe_source")
                    continue
                try:
                    stat = path.stat()
                    state_path = path.with_suffix(".state.json")
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if not isinstance(state, dict) or type(state.get("persisted_bytes")) is not int:
                        raise ValueError("invalid_writer_state")
                    persisted_size = state["persisted_bytes"]
                    if persisted_size < 0 or persisted_size > stat.st_size:
                        missing.add("writer_checkpoint_inconsistent")
                        persisted_size = max(0, min(persisted_size, stat.st_size))
                    states.append(state)
                    if not state.get("clean_shutdown"):
                        missing.add("unpersisted_or_unclean_writer")
                    if state.get("missing_count"):
                        missing.add("writer_buffer_exhausted")
                    if state.get("failure") and state["failure"] != "runtime_log_buffer_exhausted":
                        missing.add("writer_failure")
                    if stat.st_size > persisted_size:
                        missing.add("uncheckpointed_tail")
                except FileNotFoundError:
                    # Existing supervisor files do not yet expose a writer
                    # checkpoint; their visible bytes are queryable, but their
                    # durability and last generated sequence are not provable.
                    stat = path.stat()
                    persisted_size = None
                    missing.add("writer_checkpoint_missing")
                except (OSError, ValueError, TypeError):
                    stat = path.stat()
                    persisted_size = None
                    missing.add("writer_checkpoint_unreadable")
                sources.append({"name": path.name, "device": stat.st_dev,
                                "inode": stat.st_ino, "size": stat.st_size,
                                "modified_ns": stat.st_mtime_ns,
                                "persisted_size": persisted_size})
            if not sources:
                missing.add("writer_state_unknown")
            return sources, states, missing
        except OSError:
            raise AppError("local_log_directory_unavailable", status=503) from None

    def _create_snapshot(self, filters):
        sources, states, missing = self._capture_sources()
        now = time.time()
        snapshot_id = uuid4().hex
        manifest = {"created_at": datetime.now(UTC).isoformat(), "created_epoch": now,
                    "filters": filters, "sources": sources, "writer_states": states,
                    "missing": sorted(missing)}
        return snapshot_id, manifest

    def _save_snapshot(self, snapshot_id, manifest):
        try:
            self.snapshots.mkdir(mode=0o700, exist_ok=True)
            now = time.time()
            # Only query manifests expire; append logs and collector data are never removed.
            for old in self.snapshots.glob("*.json"):
                try:
                    if old.stat().st_mtime < now - SNAPSHOT_SECONDS:
                        old.unlink()
                except OSError:
                    pass
            path = self.snapshots / (snapshot_id + ".json")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, ensure_ascii=False, separators=(",", ":"))
        except OSError:
            raise AppError("log_snapshot_unavailable", status=503) from None

    def _load_snapshot(self, snapshot_id, filters):
        try:
            path = self.snapshots / (snapshot_id + ".json")
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AppError("log_cursor_expired", status=410) from None
        except (OSError, ValueError):
            raise AppError("log_cursor_unavailable", status=503) from None
        if not isinstance(manifest, dict) or manifest.get("filters") != filters:
            raise AppError("log_cursor_query_mismatch")
        if time.time() - manifest.get("created_epoch", 0) > SNAPSHOT_SECONDS:
            raise AppError("log_cursor_expired", status=410)
        return manifest

    def _read_source(self, source, filters, before, selection, capacity, missing, *, start=0, end=None):
        heap, keys = selection["heap"], selection["keys"]
        path = self.directory / source["name"]
        if path.parent != self.directory or path.is_symlink():
            missing.add("source_changed")
            return
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                if (stat.st_dev, stat.st_ino) != (source["device"], source["inode"]) or stat.st_size < source["size"]:
                    missing.add("source_changed")
                    return
                stream.seek(start)
                remaining = min(source["size"], end if end is not None else source["size"]) - start
                while remaining:
                    line = stream.readline(min(remaining, MAX_LINE + 1))
                    if not line:
                        missing.add("source_changed")
                        break
                    remaining -= len(line)
                    selection["read_bytes"] += len(line)
                    if not line.endswith(b"\n"):
                        missing.add("oversized_or_partial_record")
                        # Continue after an oversized line; a partial final line
                        # remains outside the proven snapshot coverage.
                        while remaining and not line.endswith(b"\n"):
                            line = stream.readline(min(remaining, MAX_LINE + 1))
                            if not line:
                                remaining = 0
                                break
                            remaining -= len(line)
                            selection["read_bytes"] += len(line)
                        continue
                    try:
                        row, key = decode_record(line)
                        stamp = key[0]
                    except (ValueError, TypeError, UnicodeError, OverflowError):
                        missing.add("invalid_log_record")
                        continue
                    if not filters["start_ns"] <= stamp < filters["end_ns"]:
                        continue
                    if before is not None and key >= before:
                        continue
                    row["persistence"] = (
                        "persisted" if source["persisted_size"] is not None
                        and stream.tell() <= source["persisted_size"] else "unverified"
                    )
                    row = _sanitize(row, self.secrets)
                    if not _matches(row, filters) or key in keys:
                        continue
                    if len(heap) >= capacity and key <= heap[0][0]:
                        selection["has_more"] = True
                        continue
                    heapq.heappush(heap, (key, row, len(line)))
                    keys.add(key)
                    selection["bytes"] += len(line)
                    while len(heap) > capacity or (selection["bytes"] > MAX_PAGE_BYTES and len(heap) > 1):
                        removed = heapq.heappop(heap)
                        keys.remove(removed[0])
                        selection["bytes"] -= removed[2]
                        selection["has_more"] = True
        except OSError:
            missing.add("source_unavailable")

    def page(self, *, start_ns, end_ns, role=None, level=None, module=None,
             correlation_id=None, trace_id=None, job_id=None, attempt_id=None,
             keyword=None, exclude_conversation=False, cursor=None, limit=100):
        filters = _filters(start_ns, end_ns, role, level, module, correlation_id, trace_id,
                           job_id, attempt_id, keyword, exclude_conversation)
        try:
            limit = max(1, min(int(limit), 1000))
        except (ValueError, TypeError, OverflowError):
            raise AppError("invalid_log_limit") from None
        if cursor:
            snapshot_id, before = _decode_cursor(cursor)
            manifest = self._load_snapshot(snapshot_id, filters)
        else:
            snapshot_id, manifest = self._create_snapshot(filters)
            before = None
        missing = set(manifest["missing"])
        selection = {"heap": [], "keys": set(), "bytes": 0, "has_more": False, "read_bytes": 0}
        candidates, indexed_bytes = [], 0
        for source in manifest["sources"]:
            try:
                blocks, gaps, indexed = self.index.get(source)
                indexed_bytes += indexed
                missing.update(gaps)
            except SourceChanged:
                missing.add("source_changed")
                continue
            except OSError:
                missing.add("source_unavailable")
                continue
            for block in blocks:
                if (block["start"] >= source["size"] or block["max_ns"] is None
                        or block["max_ns"] < filters["start_ns"] or block["min_ns"] >= filters["end_ns"]
                        or before is not None and block["min_ns"] > before[0]):
                    continue
                candidates.append((block["max_ns"], source, block))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for maximum, source, block in candidates:
            # One extra distinct match proves a next page. Older blocks cannot
            # change this page, even when timestamps inside a file are unordered.
            if len(selection["heap"]) >= limit + 1 and maximum < selection["heap"][0][0][0]:
                break
            self._read_source(source, filters, before, selection, limit + 1, missing,
                              start=block["start"], end=block["end"])
        selected = sorted(selection["heap"], key=lambda item: item[0], reverse=True)
        has_more = selection["has_more"] or len(selected) > limit
        selected = selected[:limit]
        entries = [row for _, row, _ in selected]
        if has_more and not cursor:
            self._save_snapshot(snapshot_id, manifest)
        next_cursor = _encode_cursor(snapshot_id, selected[-1][0]) if has_more and selected else None
        grouped = {}
        for key, row, _ in selected:
            labels = (row.get("role") or "unknown", row.get("level") or "UNCLASSIFIED")
            grouped.setdefault(labels, []).append([str(key[0]), json.dumps(row, ensure_ascii=False)])
        streams = [{"stream": {"role": role, "level": level}, "values": values}
                   for (role, level), values in grouped.items()]
        try:
            disk = shutil.disk_usage(self.directory)
            capacity = {"total_bytes": disk.total, "free_bytes": disk.free}
        except OSError:
            capacity = {"total_bytes": None, "free_bytes": None}
            missing.add("disk_capacity_unavailable")
        return {
            "entries": entries, "next_cursor": next_cursor,
            # Kept for old consumers, but intentionally never suggests the
            # lossy timestamp-minus-one continuation.
            "streams": streams, "next_end_ns": None,
            "coverage": {"complete": not missing and not has_more, "missing": sorted(missing),
                         "source": "local_append_files", "scope": "observed_local_sources",
                         "generated_coverage": "unknown", "collection_coverage": "unknown",
                         "snapshot_complete": not bool(
                             missing.intersection({"source_changed", "source_unavailable", "unsafe_source",
                                                   "invalid_log_record", "oversized_or_partial_record"})),
                         "has_more": has_more},
            "freshness": {"as_of": datetime.now(UTC).isoformat(),
                          "snapshot_at": manifest["created_at"],
                          "snapshot_expires_at": (datetime.fromisoformat(manifest["created_at"]) +
                                                  timedelta(seconds=SNAPSHOT_SECONDS)).isoformat(),
                          "late_arrivals": "refresh_with_new_snapshot",
                          "writers": manifest["writer_states"], "disk": capacity,
                          "scan": {"read_bytes": selection["read_bytes"], "indexed_bytes": indexed_bytes}},
        }

    def prepare_index(self):
        """Warm only derived byte/time metadata before a release or after cache loss."""
        sources, _, missing = self._capture_sources()
        indexed_bytes = 0
        for source in sources:
            try:
                _, gaps, indexed = self.index.get(source)
                indexed_bytes += indexed
                missing.update(gaps)
            except SourceChanged:
                missing.add("source_changed")
            except OSError:
                missing.add("source_unavailable")
        return {"sources": len(sources), "indexed_bytes": indexed_bytes, "missing": sorted(missing)}

    @staticmethod
    def _decode_checkpoint(checkpoint):
        if checkpoint is None:
            return {"version": 1, "sources": {}, "next_source": None, "gaps": []}
        if not isinstance(checkpoint, str) or len(checkpoint) > 2_000_000:
            raise AppError("invalid_log_checkpoint")
        try:
            packed = base64.b64decode(checkpoint + "=" * (-len(checkpoint) % 4),
                                      altchars=b"-_", validate=True)
            decompressor = zlib.decompressobj()
            raw = decompressor.decompress(packed, 8_000_001)
            if len(raw) > 8_000_000 or decompressor.unconsumed_tail or not decompressor.eof:
                raise ValueError
            state = json.loads(raw)
            if (not isinstance(state, dict) or state.get("version") != 1
                    or not isinstance(state.get("sources"), dict)
                    or not isinstance(state.get("gaps"), list)
                    or any(not isinstance(gap, str) for gap in state["gaps"])):
                raise ValueError
            for name, position in state["sources"].items():
                if (not isinstance(name, str) or Path(name).name != name or not name.endswith(".jsonl")
                        or not isinstance(position, list) or len(position) != 3
                        or any(type(number) is not int or number < 0 for number in position)):
                    raise ValueError
            if state.get("next_source") is not None and not isinstance(state["next_source"], str):
                raise ValueError
            return state
        except (ValueError, TypeError, UnicodeError, zlib.error):
            raise AppError("invalid_log_checkpoint") from None

    @staticmethod
    def _encode_checkpoint(state):
        raw = json.dumps(state, separators=(",", ":"), sort_keys=True).encode()
        if len(raw) > 8_000_000:
            raise AppError("log_checkpoint_too_large", status=503)
        return base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip("=")

    def _read_changes(self, source, position, quota, entries, missing, gaps):
        """Read complete lines from one fixed durable byte boundary."""
        path = self.directory / source["name"]
        if path.is_symlink():
            missing.add("source_changed")
            return position, 0
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                if (stat.st_dev, stat.st_ino) != (source["device"], source["inode"]) or stat.st_size < source["size"]:
                    missing.add("source_changed")
                    return position, 0
                stream.seek(position)
                remaining = source["persisted_size"] - position
                processed = 0
                while remaining and processed < quota:
                    start = stream.tell()
                    line = stream.readline(min(remaining, MAX_LINE + 1))
                    if not line:
                        missing.add("source_changed")
                        break
                    remaining -= len(line)
                    if not line.endswith(b"\n"):
                        if len(line) <= MAX_LINE and not remaining:
                            # Do not advance past a possibly still-in-flight line.
                            missing.add("partial_record")
                            return start, processed
                        while remaining and not line.endswith(b"\n"):
                            line = stream.readline(min(remaining, MAX_LINE + 1))
                            if not line:
                                break
                            remaining -= len(line)
                        if not line.endswith(b"\n"):
                            missing.add("partial_record")
                            return start, processed
                        gaps.add("oversized_log_record")
                        position = stream.tell()
                        processed += 1
                        continue
                    position = stream.tell()
                    processed += 1
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError
                        _event_ns(row.get("timestamp"))
                        if (not isinstance(row.get("instance_id"), str) or not row["instance_id"]
                                or not isinstance(row.get("event_id"), str) or not row["event_id"]
                                or type(row.get("sequence")) is not int or row["sequence"] < 1):
                            raise ValueError
                    except (ValueError, TypeError, UnicodeError, OverflowError):
                        gaps.add("invalid_log_record")
                        continue
                    # Statistics consumers get a typed metadata view, never the
                    # full message, request body, prompt or arbitrary JSON extras.
                    fields = ("timestamp", "event_id", "instance_id", "sequence", "service", "role",
                              "event", "level", "module", "outcome", "trace_id", "correlation_id",
                              "job_id", "attempt_id", "task_name", "route", "method", "status",
                              "duration_ms", "error_code", "error_family", "dependency_ref",
                              "material_id", "failure_id", "lookup_phase", "lookup_state",
                              "pool_name", "failure_phase")
                    metadata = {}
                    for name in fields:
                        if name not in row:
                            continue
                        value = row[name]
                        if value is None or isinstance(value, (str, int, float, bool)):
                            if isinstance(value, str) and len(value) > 1024:
                                gaps.add("invalid_log_metadata")
                                continue
                            metadata[name] = value
                        else:
                            gaps.add("invalid_log_metadata")
                    entries.append(_sanitize(metadata, self.secrets))
                return position, processed
        except OSError:
            missing.add("source_unavailable")
            return position, 0

    def changes(self, *, checkpoint=None, limit=500):
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError, OverflowError):
            raise AppError("invalid_log_limit") from None
        state = self._decode_checkpoint(checkpoint)
        sources, writers, missing = self._capture_sources()
        by_name = {source["name"]: source for source in sources}
        positions = state["sources"]
        gaps = set(state["gaps"])
        if set(positions) - set(by_name):
            missing.add("source_unavailable")
        names = sorted(by_name)
        for name in names:
            source = by_name[name]
            if name not in positions:
                positions[name] = [source["device"], source["inode"], 0]
        if names and state.get("next_source") in names:
            pivot = names.index(state["next_source"])
            names = names[pivot:] + names[:pivot]
        entries = []
        processed = 0
        quantum = max(1, limit // max(1, len(names)))
        next_name = names[0] if names else None
        while names and processed < limit:
            progressed = False
            for index, name in enumerate(names):
                source, position = by_name[name], positions[name]
                if source["persisted_size"] is None:
                    continue
                if position[:2] != [source["device"], source["inode"]] or position[2] > source["persisted_size"]:
                    missing.add("source_changed")
                    continue
                old = position[2]
                position[2], count = self._read_changes(source, old, min(quantum, limit - processed),
                                                         entries, missing, gaps)
                processed += count
                progressed |= position[2] > old
                next_name = names[(index + 1) % len(names)]
                if processed >= limit:
                    break
            if not progressed:
                break
        state["next_source"] = next_name
        state["gaps"] = sorted(gaps)
        missing.update(gaps)
        caught_up = all(source["persisted_size"] is not None and
                        positions[name][2] >= source["persisted_size"]
                        for name, source in by_name.items())
        return {"entries": entries, "next_checkpoint": self._encode_checkpoint(state),
                "coverage": {"complete": not missing and caught_up, "missing": sorted(missing),
                             "source": "local_append_files", "scope": "observed_local_sources",
                             "generated_coverage": "unknown", "collection_coverage": "unknown",
                             "caught_up": caught_up},
                "freshness": {"as_of": datetime.now(UTC).isoformat(), "writers": writers}}
