"""Append-only runtime events, buffered independently of business operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import queue
import threading
from uuid import uuid4

from .errors import AppError

EVENTS = frozenset({"runtime.starting", "runtime.started", "runtime.stopping", "runtime.stopped",
    "runtime.failed", "http.started", "http.finished", "http.failed", "model.queued",
    "model.submitted", "model.finished", "model.cancelled", "resource.queued", "resource.submitted",
    "resource.finished", "job.started", "job.finished", "job.failed", "job.routing_blocked", "config.changed"})
OUTCOMES = frozenset({"started", "succeeded", "failed", "cancelled", "unknown", "rejected", "degraded"})


def _identity(value, limit=128):
    if value is None:
        return None
    value = str(value)
    if len(value) > limit or any(ord(character) < 32 for character in value):
        return None
    return value


class EventLog:
    """A bounded, asynchronous append writer with explicit overflow and crash gaps.

    A normal emit waits neither for the writer nor for fsync. Records remain in the
    queue across recoverable I/O failures. The high-water limit is a real in-memory
    capacity limit: overflow is counted as a possible sequence gap, not reported as
    successful durable logging or propagated into an unrelated business operation.
    Vector's own checkpoint and disk buffer remain responsible for collection.
    """

    def __init__(self, directory, role, *, max_pending=4096, hard_max_pending=None,
                 hard_max_pending_bytes=64 * 1024 * 1024, prefix="", uid=None, gid=None,
                 drain_timeout=30):
        self.directory, self.role = Path(directory), role
        self.instance_id = str(uuid4())
        self.prefix, self.uid, self.gid = prefix, uid, gid
        self.soft_limit = max(1, int(max_pending))
        self.hard_limit = max(self.soft_limit, int(hard_max_pending or max(65536, self.soft_limit * 64)))
        self.hard_limit_bytes = max(1, int(hard_max_pending_bytes))
        self.pending_bytes = 0
        self.pending = queue.Queue(maxsize=self.hard_limit)
        self.sequence = 0
        self.persisted_sequence = 0
        self.persisted_bytes = 0
        self.missing_count = 0
        self.missing_first = self.missing_last = None
        self.failure = None
        self.thread = None
        self.state_lock = threading.Lock()
        self.accepting = False
        self.stop_retry = threading.Event()
        self.drain_timeout = drain_timeout
        self._closed_cleanly = False

    @property
    def path(self):
        return self.directory / (self.prefix + self.role + "-" + self.instance_id + ".jsonl")

    @property
    def state_path(self):
        return self.path.with_suffix(".state.json")

    @property
    def ready(self):
        # Readiness describes the ability to accept business events, not Loki or
        # the transient state of a background disk writer.
        return self.accepting and self.thread is not None and self.thread.is_alive()

    def status(self):
        with self.state_lock:
            return self._status_locked()

    def _status_locked(self):
        return {
            "instance_id": self.instance_id, "role": self.role, "pid": os.getpid(),
            "generated_sequence": self.sequence, "persisted_sequence": self.persisted_sequence,
            "persisted_bytes": self.persisted_bytes, "pending": self.pending.qsize(),
            "soft_limit": self.soft_limit, "hard_limit": self.hard_limit,
            "pending_bytes": self.pending_bytes, "hard_limit_bytes": self.hard_limit_bytes,
            "failure": self.failure, "clean_shutdown": self._closed_cleanly,
            "missing_count": self.missing_count,
            "possible_missing_first": self.missing_first, "possible_missing_last": self.missing_last,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    async def open(self):
        if self.thread is not None:
            raise RuntimeError("runtime_log_already_open")
        self.accepting = True
        self.thread = threading.Thread(target=self._write, name="tianshu-event-log", daemon=True)
        self.thread.start()

    def _enqueue(self, value):
        with self.state_lock:
            if not self.accepting:
                raise AppError("runtime_log_unavailable", status=503)
            self.sequence += 1
            value.update(instance_id=self.instance_id, sequence=self.sequence, event_id=str(uuid4()))
            encoded = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8", errors="backslashreplace")
            try:
                if self.pending_bytes + len(encoded) > self.hard_limit_bytes:
                    raise queue.Full
                self.pending.put_nowait((value, encoded))
                self.pending_bytes += len(encoded)
            except queue.Full:
                self.missing_count += 1
                if self.missing_first is None:
                    self.missing_first = self.sequence
                self.missing_last = self.sequence
                self.failure = "runtime_log_buffer_exhausted"
        return value

    async def emit(self, event, outcome, *, correlation_id=None, attempt_id=None,
                   duration_ms=None, error_code=None, route=None, method=None, status=None,
                   task_name=None, job_id=None, level=None, module=None, trace_id=None,
                   error_family=None, dependency_ref=None):
        if event not in EVENTS or outcome not in OUTCOMES:
            raise ValueError("unregistered_runtime_event")
        if error_code is not None:
            error_code = str(error_code)
            if len(error_code) > 96 or not all(
                character.isalnum() or character in "_.-" for character in error_code
            ):
                error_code = "unclassified_error"
        if level is None:
            level = "ERROR" if outcome == "failed" else "WARNING" if outcome in {"rejected", "degraded"} else "INFO"
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "UNCLASSIFIED"}:
            raise ValueError("invalid_runtime_log_level")
        value = {"schema_version": "2.0.0", "timestamp": datetime.now(UTC).isoformat(),
            "service": "tianshu", "role": self.role, "event": event,
            "module": _identity(module, 128) or event.split(".", 1)[0],
            "level": level, "outcome": outcome,
            "correlation_id": _identity(correlation_id), "trace_id": _identity(trace_id),
            "attempt_id": _identity(attempt_id), "duration_ms": duration_ms
            if type(duration_ms) in {int, float} and math.isfinite(duration_ms) else None,
            "error_code": error_code, "error_family": _identity(error_family or error_code, 96),
            "dependency_ref": _identity(dependency_ref),
            "route": route[:256] if isinstance(route, str) else None,
            "method": method if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"} else None,
            "status": status if type(status) is int else None,
            "task_name": _identity(task_name), "job_id": job_id if type(job_id) is int else None}
        self._enqueue(value)

    async def emit_output(self, message, secrets=()):
        """Append a supervised output line and return the safe record for stdout."""
        from .rb_logs_output import output_record

        value = output_record(self.role, self.instance_id, 0, message, secrets)
        return self._enqueue(value)

    def _checkpoint(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        data = json.dumps(self.status(), ensure_ascii=False, separators=(",", ":")).encode()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            if self.uid is not None and hasattr(os, "fchown"):
                os.fchown(fd, self.uid, self.gid)
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if fd is not None:
                os.close(fd)

    def _initialize(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            if self.uid is not None and hasattr(os, "fchown"):
                os.fchown(fd, self.uid, self.gid)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._checkpoint()

    def _append_batch(self, payload, start):
        """Resume a partial write by comparing the existing suffix, never replay it."""
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if self.uid is not None and hasattr(os, "fchown"):
                os.fchown(fd, self.uid, self.gid)
            length = os.lseek(fd, 0, os.SEEK_END)
            if length < start or length > start + len(payload):
                raise OSError("runtime_log_unexpected_file_length")
            present = length - start
            os.lseek(fd, start, os.SEEK_SET)
            existing = bytearray()
            while len(existing) < present:
                chunk = os.read(fd, min(65536, present - len(existing)))
                if not chunk:
                    raise OSError("runtime_log_partial_read")
                existing.extend(chunk)
            if existing != payload[:present]:
                raise OSError("runtime_log_unexpected_file_content")
            os.lseek(fd, length, os.SEEK_SET)
            while present < len(payload):
                written = os.write(fd, payload[present:])
                if written <= 0:
                    raise OSError("runtime_log_short_write")
                present += written
            os.fsync(fd)
        finally:
            os.close(fd)

    def _retry(self, operation):
        delay = 0.1
        while not self.stop_retry.is_set():
            try:
                operation()
                with self.state_lock:
                    self.failure = "runtime_log_buffer_exhausted" if self.missing_count else None
                return True
            except OSError:
                with self.state_lock:
                    self.failure = "runtime_log_write_failed"
                # When the event file alone failed, publish the degraded state
                # on the same sidecar used by other roles. If the whole volume
                # is unavailable, the last unclean checkpoint remains evidence.
                try:
                    self._checkpoint()
                except OSError:
                    pass
                self.stop_retry.wait(delay)
                delay = min(delay * 2, 5)
        return False

    def _write(self):
        # An unclosed sidecar indicates that a forced exit may have lost the
        # in-memory suffix, even if its precise last generated sequence is unknown.
        if not self._retry(self._initialize):
            return
        while not self.stop_retry.is_set():
            item = self.pending.get()
            if item is None:
                with self.state_lock:
                    self._closed_cleanly = True
                if not self._retry(self._checkpoint):
                    with self.state_lock:
                        self._closed_cleanly = False
                return
            batch = [item]
            while len(batch) < 128:
                try:
                    extra = self.pending.get_nowait()
                except queue.Empty:
                    break
                if extra is None:
                    self.pending.put(None)
                    break
                batch.append(extra)
            payload = b"".join(encoded for _value, encoded in batch)
            start = self.persisted_bytes
            if not self._retry(lambda: self._append_batch(payload, start)):
                return
            with self.state_lock:
                self.persisted_bytes = start + len(payload)
                self.persisted_sequence = batch[-1][0]["sequence"]
                self.pending_bytes -= len(payload)
            if not self._retry(self._checkpoint):
                return

    async def close(self):
        if self.thread is None:
            return
        with self.state_lock:
            if not self.accepting:
                return
            self.accepting = False
        # There is no further producer, so a full queue eventually makes room.
        def enqueue_stop():
            try:
                self.pending.put(None, timeout=self.drain_timeout)
                return True
            except queue.Full:
                return False

        if await asyncio.to_thread(enqueue_stop):
            await asyncio.to_thread(self.thread.join, self.drain_timeout)
        if self.thread.is_alive():
            with self.state_lock:
                self.failure = "runtime_log_drain_incomplete"
            self.stop_retry.set()
            await asyncio.to_thread(self.thread.join, 1)
