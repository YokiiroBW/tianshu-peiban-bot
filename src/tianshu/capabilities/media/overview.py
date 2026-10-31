"""Small read-only runtime samples for the subscription dashboard; no probes or workers."""

import threading
import time
from pathlib import Path


def read_counters(proc=Path("/proc")):
    memory, received, sent = None, None, None
    try:
        for line in (proc / "self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                memory = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        rows = []
        for line in (proc / "net/dev").read_text().splitlines():
            if ":" not in line:
                continue
            name, values = line.split(":", 1)
            if name.strip() != "lo":
                fields = values.split()
                rows.append((int(fields[0]), int(fields[8])))
        if rows:
            received, sent = map(sum, zip(*rows))
    except (OSError, ValueError, IndexError):
        pass
    return memory, received, sent


class RuntimeSample:
    def __init__(
        self,
        *,
        monotonic=time.monotonic,
        process_time=time.process_time,
        clock=time.time,
        counters=read_counters,
    ):
        self.monotonic, self.process_time = monotonic, process_time
        self.clock, self.counters = clock, counters
        self.started_at = monotonic()
        self.previous = None
        self.latest = None
        self.lock = threading.Lock()

    def view(self):
        with self.lock:
            now = self.monotonic()
            if self.previous and now - self.previous[0] < 1:
                return dict(self.latest)
            cpu = self.process_time()
            memory, received, sent = self.counters()
            percent, receive_rate, send_rate = None, None, None
            if self.previous:
                before, old_cpu, old_received, old_sent = self.previous
                elapsed = now - before
                percent = round(max(0, cpu - old_cpu) / elapsed * 100, 1)
                if received is not None and old_received is not None and received >= old_received:
                    receive_rate = round((received - old_received) / elapsed)
                if sent is not None and old_sent is not None and sent >= old_sent:
                    send_rate = round((sent - old_sent) / elapsed)
            self.previous = now, cpu, received, sent
            self.latest = {
                "sampled_at": self.clock(),
                "uptime_seconds": round(now - self.started_at),
                "cpu_percent": percent,
                "memory_bytes": memory,
                "receive_bytes_per_second": receive_rate,
                "send_bytes_per_second": send_rate,
            }
            return dict(self.latest)
