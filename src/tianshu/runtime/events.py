"""Complete safe runtime events to immutable files, for Vector's persistent disk pipeline."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
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


class EventLog:
    """No sampling, no log database, no deletion of uncollected segments.

    The bounded in-memory buffer is drained to an append-only, fsynced process file.
    Vector reads these files and owns collector checkpoints, disk buffering and Loki retry.
    The existing deployment capacity guard owns disk exhaustion policy.
    """

    def __init__(self, directory, role, *, max_pending=4096):
        self.directory, self.role = Path(directory), role
        self.instance_id = str(uuid4())
        self.pending = queue.Queue(maxsize=max_pending)
        self.sequence = 0
        self.failure = None
        self.thread = None
        self.state_lock = threading.Lock()

    @property
    def ready(self):
        return self.failure is None and self.thread is not None and self.thread.is_alive()

    async def open(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._write, name="tianshu-event-log", daemon=True)
        self.thread.start()

    async def emit(self, event, outcome, *, correlation_id=None, attempt_id=None,
                   duration_ms=None, error_code=None, route=None, method=None, status=None,
                   task_name=None, job_id=None):
        if event not in EVENTS or outcome not in OUTCOMES:
            raise ValueError("unregistered_runtime_event")
        if error_code is not None and (len(error_code) > 96 or not all(
            character.isalnum() or character in "_.-" for character in error_code)):
            error_code = "unclassified_error"
        self.sequence += 1
        value = {"schema_version": "2.0.0", "timestamp": datetime.now(UTC).isoformat(),
            "service": "tianshu", "role": self.role, "instance_id": self.instance_id,
            "sequence": self.sequence, "event_id": str(uuid4()), "event": event,
            "level": "ERROR" if outcome == "failed" else "INFO", "outcome": outcome,
            "correlation_id": correlation_id, "attempt_id": attempt_id,
            "duration_ms": duration_ms, "error_code": error_code,
            "route": route[:256] if isinstance(route, str) else None,
            "method": method if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"} else None,
            "status": status if type(status) is int else None,
            "task_name": task_name[:128] if isinstance(task_name, str) else None,
            "job_id": job_id if type(job_id) is int else None}
        # Correlation values are stable generated identities; never log input bodies/URLs.
        for name in ("correlation_id", "attempt_id"):
            if value[name] is not None and (len(str(value[name])) > 128 or any(
                ord(character) < 32 for character in str(value[name]))):
                value[name] = None
        future = asyncio.get_running_loop().create_future()
        with self.state_lock:
            if not self.ready:
                raise AppError("runtime_log_unavailable", status=503)
            try:
                self.pending.put_nowait((value, asyncio.get_running_loop(), future))
            except queue.Full:
                raise AppError("runtime_log_buffer_full", status=503) from None
        await asyncio.shield(future)

    @staticmethod
    def _settle(future, error):
        if not future.done():
            if error:
                future.set_exception(AppError("runtime_log_write_failed", status=503))
            else:
                future.set_result(None)

    def _write(self):
        batch = []
        try:
            path = self.directory / (self.role+"-"+self.instance_id+".jsonl")
            with path.open("xb") as stream:
                path.chmod(0o600)
                while True:
                    item = self.pending.get()
                    if item is None:
                        break
                    batch = [item]
                    while len(batch) < 64:
                        try:
                            extra = self.pending.get_nowait()
                        except queue.Empty:
                            break
                        if extra is None:
                            self.pending.put_nowait(None)
                            break
                        batch.append(extra)
                    for value, _loop, _future in batch:
                        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                                     .encode()+b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                    for _value, loop, future in batch:
                        loop.call_soon_threadsafe(self._settle, future, None)
                    batch = []
        except Exception:
            with self.state_lock:
                self.failure = "runtime_log_write_failed"
                for _value, loop, future in batch:
                    loop.call_soon_threadsafe(self._settle, future, self.failure)
                while True:
                    try:
                        item = self.pending.get_nowait()
                    except queue.Empty:
                        break
                    if item:
                        _value, loop, future = item
                        loop.call_soon_threadsafe(self._settle, future, self.failure)

    async def close(self):
        if self.thread and self.thread.is_alive():
            await asyncio.to_thread(self.pending.put, None)
            await asyncio.to_thread(self.thread.join)
