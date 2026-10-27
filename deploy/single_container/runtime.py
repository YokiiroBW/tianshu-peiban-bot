"""PID 1 for the unified Tianshu container, using existing product entrypoints."""

import argparse
import json
import os
from pathlib import Path
import signal
import stat
import threading
import time
import uuid

from .health import positive_number
from .process import ProcessGroup, validate_process

HOST_DIRECTORY = Path("/run/tianshu/host")


def prepare_host_directory():
    """Only the fixed host-state tmpfs child is writable by the business UID."""
    if os.name != "posix" or os.geteuid() != 0 or os.getegid() != 10001:
        raise ValueError("business host IPC requires supervisor identity 0:10001")
    parent = HOST_DIRECTORY.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
        raise ValueError("unsafe business host IPC parent")
    try:
        HOST_DIRECTORY.mkdir(mode=0o770)
    except FileExistsError:
        pass
    else:
        HOST_DIRECTORY.chmod(0o770)
    entry = HOST_DIRECTORY.lstat()
    if (
        not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != 0
        or entry.st_gid != 10001
        or stat.S_IMODE(entry.st_mode) != 0o770
    ):
        raise ValueError("unsafe business host IPC directory")


def load_config(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - {
        "version",
        "state_file",
        "poll_interval_seconds",
        "processes",
        "comics",
    }:
        raise ValueError("invalid runtime fields")
    if raw.get("version") != 1 or type(raw.get("version")) is not int:
        raise ValueError("unsupported runtime version")
    if (
        not isinstance(raw.get("state_file"), str)
        or not Path(raw["state_file"]).is_absolute()
    ):
        raise ValueError("state_file must be absolute")
    if not isinstance(raw.get("processes"), list):
        raise ValueError("processes must be a list")
    result = dict(raw)
    result["processes"] = [validate_process(item) for item in raw["processes"]]
    result["poll_interval_seconds"] = positive_number(
        raw.get("poll_interval_seconds", 1), "poll interval", 5
    )
    return result


def write_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reap_orphans(group, comics):
    """PID 1 adopts orphan workers; do not steal Popen's direct-child wait status."""
    if os.name != "posix" or os.getpid() != 1:
        return
    owned = {
        item.child.pid
        for item in group.processes.values()
        if item.child and item.child.poll() is None
    }
    if comics is not None:
        owned.update(comics.live_pids())
    # Linux exposes the actual children without a racy waitpid(-1), which could
    # reap a concurrently exiting owned child before Popen captures its status.
    path = Path(f"/proc/self/task/{os.getpid()}/children")
    try:
        children = [int(value) for value in path.read_text().split()]
    except OSError:
        return
    for pid in children:
        if pid not in owned:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass


class Runtime:
    def __init__(self, config):
        self.config = config
        self.group = ProcessGroup(config["processes"])
        self.stop_event = threading.Event()
        self.instance = str(uuid.uuid4())
        self.started_at = time.time()
        self.comics = None
        if config.get("comics") is not None:
            from .comics_control import ComicsController

            self.comics = ComicsController(config["comics"])

    def publish(self, status=None):
        overall = status or self.group.status()
        if overall == "failed":
            overall = "degraded"
        comics = self.comics.status() if self.comics else None
        if overall == "ready" and comics and comics["state"] in {"starting", "failed"}:
            overall = "degraded"
        write_state(
            self.config["state_file"],
            {
                "version": 1,
                "pid": os.getpid(),
                "instance": self.instance,
                "started_at": self.started_at,
                "updated_at": time.time(),
                "status": overall,
                "processes": self.group.snapshot(),
                "comics": comics,
            },
        )

    def run(self, *, install_signals=True):
        previous = {}
        stopped = True
        if install_signals:
            for name in ("SIGTERM", "SIGINT"):
                number = getattr(signal, name)
                previous[number] = signal.signal(
                    number, lambda *_: self.stop_event.set()
                )
        try:
            # Overwrite any previous container's ready state before opening listeners.
            self.publish("starting")
            if "business-host" in self.group.processes:
                prepare_host_directory()
            if self.comics:
                self.comics.open()
            self.group.start()
            while not self.stop_event.is_set():
                self.group.tick()
                if self.comics:
                    self.comics.tick()
                reap_orphans(self.group, self.comics)
                self.publish()
                self.stop_event.wait(self.config["poll_interval_seconds"])
        finally:
            try:
                try:
                    self.publish("stopping")
                finally:
                    try:
                        # Platform's plugin shutdown still needs the owned Unix
                        # controller to stop its workers and acknowledge it.
                        stopped = self.group.stop()
                    finally:
                        if self.comics:
                            stopped = self.comics.close() and stopped
                        reap_orphans(self.group, None)
                        self.publish("stopped" if stopped else "failed")
            finally:
                for number, handler in previous.items():
                    signal.signal(number, handler)
        return 0 if stopped else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        return Runtime(load_config(args.config)).run()
    except (OSError, ValueError):
        # Private configuration can contain credentials. Print only a fixed code.
        print("tianshu_runtime_failed", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
