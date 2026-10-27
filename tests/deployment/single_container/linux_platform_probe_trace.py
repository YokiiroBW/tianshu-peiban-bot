"""Temporary TS371 host launcher: trace original Probe calls, never their inputs.

Run in the candidate container instead of ``-m deploy.single_container.host`` and
pass the same host arguments. Only a new private /tmp directory is written.
"""

import faulthandler
import functools
import json
import os
import sys
import threading
import time
from pathlib import Path


PHASES = (
    "evaluate",
    "_contract",
    "_store",
    "_sidecars",
    "_static",
    "_tls",
    "_credentials",
    "_logging",
    "_runtime",
    "_settle",
)


class Trace:
    """Wrap the original methods; do not replace checks, clocks or callbacks."""

    def __init__(self, probe_class, directory):
        self.probe_class = probe_class
        self.originals = {}
        directory = Path(directory)
        directory.mkdir(mode=0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
        self.fd = os.open(directory / "phases.jsonl", flags, 0o600)
        try:
            self.stack_fd = os.open(directory / "stacks.txt", flags, 0o600)
        except BaseException:
            os.close(self.fd)
            raise

    def emit(self, phase, event, probe=None, **fields):
        record = {
            "phase": phase,
            "event": event,
            "monotonic_ns": time.monotonic_ns(),
            "thread": threading.get_ident(),
            "pid": os.getpid(),
            **fields,
        }
        if probe is not None:
            record["probe"] = id(probe)
        try:
            # A single append; no logger, sink lock, configuration or exception text.
            os.write(
                self.fd, (json.dumps(record, separators=(",", ":")) + "\n").encode()
            )
        except OSError:
            pass  # A diagnostic write must not replace a real check's outcome.

    def _sync(self, phase, original):
        @functools.wraps(original)
        def wrapped(probe, *args, **kwargs):
            started = time.monotonic_ns()
            self.emit(phase, "begin", probe)
            error = None
            try:
                return original(probe, *args, **kwargs)
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                self.emit(
                    phase,
                    "end",
                    probe,
                    elapsed_ms=(time.monotonic_ns() - started) / 1_000_000,
                    error=error,
                )

        return wrapped

    def install(self):
        for phase in PHASES:
            original = getattr(self.probe_class, phase)
            self.originals[phase] = original
            setattr(self.probe_class, phase, self._sync(phase, original))
        original_ready = self.probe_class.ready
        self.originals["ready"] = original_ready

        @functools.wraps(original_ready)
        async def ready(probe, *args, **kwargs):
            started = time.monotonic_ns()
            self.emit("ready", "begin", probe, inflight=probe._inflight)
            error = None
            try:
                return await original_ready(probe, *args, **kwargs)
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                self.emit(
                    "ready",
                    "end",
                    probe,
                    elapsed_ms=(time.monotonic_ns() - started) / 1_000_000,
                    inflight=probe._inflight,
                    error=error,
                )

        self.probe_class.ready = ready
        self.emit("trace", "installed")

    def close(self):
        for name, original in self.originals.items():
            setattr(self.probe_class, name, original)
        self.originals.clear()
        os.close(self.stack_fd)
        os.close(self.fd)


def main(argv=None):
    from services.platform.runtime_health import Probe

    directory = Path("/tmp") / f"tianshu-ts371-{os.getpid()}"
    trace = Trace(Probe, directory)
    try:
        trace.install()
        faulthandler.dump_traceback_later(15, repeat=True, file=trace.stack_fd)
        try:
            from deploy.single_container.host import main as host_main

            return host_main(sys.argv[1:] if argv is None else argv)
        finally:
            faulthandler.cancel_dump_traceback_later()
    finally:
        trace.close()


if __name__ == "__main__":
    raise SystemExit(main())
