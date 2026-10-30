"""Durable operator clear watermark; no per-event state or source contents."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode


class ClearControl:
    def __init__(self, directory):
        self.path = Path(directory) / "clear-control.json"
        self.cutoff_ms = 0
        self.last_delete = 0
        try:
            value = json.loads(self.path.read_bytes())
        except FileNotFoundError:
            return
        if (value.get("version") != 1 or type(value.get("cutoff_ms")) is not int
                or value["cutoff_ms"] < 0 or type(value.get("last_delete")) is not int
                or value["last_delete"] < 0):
            raise ValueError("clear_control_invalid")
        self.cutoff_ms, self.last_delete = value["cutoff_ms"], value["last_delete"]

    def _write(self, cutoff_ms, last_delete):
        payload = json.dumps({"version": 1, "cutoff_ms": cutoff_ms,
                              "last_delete": last_delete}, separators=(",", ":"))
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, self.path)
        if os.name == "posix":
            fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.cutoff_ms, self.last_delete = cutoff_ms, last_delete

    def clear(self, now=None):
        now = time.time() if now is None else now
        cutoff = max(self.cutoff_ms, int(now * 1000))
        self._write(cutoff, 0)
        return cutoff

    def filter(self):
        if not self.cutoff_ms:
            return ""
        n = self.cutoff_ms
        return (' | json __ts_clear_timestamp="timestamp" '
                '| label_format __ts_clear_event=`{{ unixEpochMillis (toDateInZone "2006-01-02T15:04:05.999999999Z07:00" "UTC" .__ts_clear_timestamp) }}`,'
                '__ts_clear_collect=`{{ unixEpochMillis __timestamp__ }}` '
                f'| __ts_clear_event > {n} | __ts_clear_collect > {n} '
                '| drop __ts_clear_timestamp,__ts_clear_event,__ts_clear_collect ')

    def submit(self, backend, lock, retention_days=30, now=None):
        """Delete already collected data asynchronously.

        Query filtering provides immediate and persistent logical clear.  Physical
        cleanup is best effort and never targets Vector's buffer or source files.
        """
        now = int(time.time() if now is None else now)
        with lock:
            cutoff_ms = self.cutoff_ms
            if not cutoff_ms or self.last_delete:
                return
        # A conservative endpoint never deletes a post-clear event even if the
        # backend interprets the end of its interval as inclusive.
        end = datetime.fromtimestamp((cutoff_ms - 1) / 1000, timezone.utc).isoformat()
        start = max(0, cutoff_ms // 1000 - (retention_days + 1) * 86400)
        path = "/loki/api/v1/delete?" + urlencode(
            {"query": '{stack="tianshu"}', "start": start, "end": end})
        status, _, _ = backend.request(path, "POST", body=b"")
        if status != 204:
            raise RuntimeError("loki_clear_delete_failed")
        with lock:
            # A newer clear may have completed while Loki was handling the old
            # request; don't mark that newer action as already submitted.
            if self.cutoff_ms == cutoff_ms and not self.last_delete:
                self._write(cutoff_ms, now)
