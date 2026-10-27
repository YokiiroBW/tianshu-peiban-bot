"""Own existing service subprocesses; business protocols stay in their products."""

from concurrent.futures import Future
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

from .health import positive_number, probe, validate_probe


NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
ENVIRONMENT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def validate_process(raw):
    fields = {"name", "argv", "env", "env_file", "cwd", "uid", "gid", "depends_on", "health",
              "startup_timeout_seconds", "stop_timeout_seconds", "stop_signal"}
    if not isinstance(raw, dict) or set(raw) - fields:
        raise ValueError("invalid process fields")
    if not isinstance(raw.get("name"), str) or not NAME.fullmatch(raw["name"]):
        raise ValueError("invalid process name")
    argv = raw.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv) or not argv[0]:
        raise ValueError("invalid process argv")
    result = dict(raw)
    result["health"] = validate_probe(raw.get("health"))
    depends = raw.get("depends_on", [])
    if not isinstance(depends, list) or not all(isinstance(name, str) for name in depends) or len(set(depends)) != len(depends):
        raise ValueError("invalid process dependencies")
    result["depends_on"] = depends
    for key in ("startup_timeout_seconds", "stop_timeout_seconds"):
        result[key] = positive_number(raw.get(key, 60 if key.startswith("startup") else 30), key)
    stop_signal = raw.get("stop_signal", "SIGTERM")
    if not isinstance(stop_signal, str) or stop_signal not in {"SIGTERM", "SIGINT", "SIGQUIT", "SIGBREAK"} or not hasattr(signal, stop_signal):
        raise ValueError("unsupported stop signal")
    result["stop_signal"] = stop_signal
    if ("uid" in raw) != ("gid" in raw):
        raise ValueError("uid and gid must be specified together")
    for key in ("uid", "gid"):
        if key in raw and (type(raw[key]) is not int or raw[key] < 0):
            raise ValueError("invalid process " + key)
    if "cwd" in raw and (not isinstance(raw["cwd"], str) or not Path(raw["cwd"]).is_absolute()):
        raise ValueError("cwd must be absolute")
    environment = raw.get("env", {})
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and ENVIRONMENT_KEY.fullmatch(key) and isinstance(value, str) and "\0" not in value
        for key, value in environment.items()
    ):
        raise ValueError("invalid process environment")
    env_files = raw.get("env_file", [])
    env_files = [env_files] if isinstance(env_files, str) else env_files
    if not isinstance(env_files, list) or not all(isinstance(path, str) and Path(path).is_absolute() for path in env_files):
        raise ValueError("env_file must contain absolute paths")
    result["env_file"] = env_files
    result["env"] = dict(environment)
    return result


def dependency_order(specifications):
    by_name = {spec["name"]: spec for spec in specifications}
    if not by_name or len(by_name) != len(specifications):
        raise ValueError("process names must be nonempty and unique")
    pending, ordered = list(by_name), []
    while pending:
        available = [name for name in pending if set(by_name[name]["depends_on"]) <= set(ordered)]
        if not available:
            raise ValueError("unknown or cyclic process dependencies")
        for name in available:
            ordered.append(name)
            pending.remove(name)
    return [by_name[name] for name in ordered]


def environment_for(spec):
    environment = os.environ.copy()
    for path in spec["env_file"]:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not ENVIRONMENT_KEY.fullmatch(key) or "\0" in value:
                raise ValueError("invalid env_file entry")
            # The native bundle writes Compose's restricted single-quoted form.
            # Unwrap that literal value without shell expansion or escape rules.
            if value.startswith("'"):
                if len(value) < 2 or not value.endswith("'") or "'" in value[1:-1] or "\\" in value[1:-1]:
                    raise ValueError("invalid quoted env_file entry")
                value = value[1:-1]
            environment[key] = value
    environment.update(spec["env"])
    return environment


class ManagedProcess:
    def __init__(self, spec):
        self.spec = spec
        self.child = None
        self.environment = None
        self.state = "pending"
        self.health = {"ok": False, "code": "not_started"}
        self.started_at = None
        self.exit_code = None
        self.forced = False
        self.next_probe_at = 0
        self.exit_seen_at = None

    def start(self):
        self.environment = environment_for(self.spec)
        options = {}
        if "uid" in self.spec:
            if os.name != "posix":
                raise ValueError("process identities require POSIX")
            options.update(user=self.spec["uid"], group=self.spec["gid"])
            # Root drops supplementary groups before executing each business service.
            if os.geteuid() == 0:
                options["extra_groups"] = []
        if os.name == "posix":
            options["start_new_session"] = True
        else:
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self.child = subprocess.Popen(
            self.spec["argv"], cwd=self.spec.get("cwd"), env=self.environment,
            stdin=subprocess.DEVNULL, close_fds=True, **options,
        )
        self.started_at = time.monotonic()
        self.state = "starting"

    def signal(self, number):
        if self.child is None:
            return
        try:
            if os.name == "posix":
                os.killpg(self.child.pid, number)
            elif self.child.poll() is None:
                self.child.send_signal(number)
        except ProcessLookupError:
            pass

    def group_running(self):
        if self.child is None:
            return False
        if os.name != "posix":
            return self.child.poll() is None
        if Path("/proc").is_dir():
            for path in Path("/proc").glob("[0-9]*/stat"):
                try:
                    raw = path.read_text()
                    fields = raw[raw.rfind(")") + 2:].split()
                    if int(fields[2]) == self.child.pid and fields[0] not in {"Z", "X"}:
                        return True
                except (OSError, ValueError, IndexError):
                    continue
            return False
        try:
            os.killpg(self.child.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def stop(self):
        if self.child is None:
            self.state = "stopped"
            self.health = {"ok": False, "code": "stopped"}
            return
        self.state = "stopping"
        self.signal(getattr(signal, self.spec["stop_signal"]))
        # A child may have spawned workers and exited ahead of them. The whole
        # owned process group must be gone before releasing its plugin owner.
        deadline = time.monotonic() + self.spec["stop_timeout_seconds"]
        while self.group_running() and time.monotonic() < deadline:
            self.child.poll()
            time.sleep(0.02)
        if self.group_running():
            self.forced = True
            if os.name == "posix":
                self.signal(signal.SIGKILL)
            else:
                self.child.kill()
            deadline = time.monotonic() + 1
            while self.group_running() and time.monotonic() < deadline:
                self.child.poll()
                time.sleep(0.01)
        self.exit_code = self.child.wait(timeout=5)
        if self.group_running():
            self.state = "failed"
            self.health = {"ok": False, "code": "stop_unconfirmed"}
            return False
        self.state = "stopped"
        self.health = {"ok": False, "code": "stopped"}
        return True

    def snapshot(self):
        return {"pid": self.child.pid if self.child else None, "state": self.state,
                "health": dict(self.health), "exit_code": self.exit_code, "forced_stop": self.forced}


class ProcessGroup:
    """Dependency start and bounded probes; failures keep other services available."""

    def __init__(self, specifications):
        self.ordered = dependency_order(specifications)
        self.processes = {spec["name"]: ManagedProcess(spec) for spec in self.ordered}
        self.probes = {}
        self.enabled = False

    def start(self):
        self.enabled = True

    def tick(self):
        if not self.enabled:
            return
        for name, item in self.processes.items():
            if item.state == "pending":
                if all(self.processes[dep].state == "ready" for dep in item.spec["depends_on"]):
                    try:
                        item.start()
                    except (OSError, ValueError, subprocess.SubprocessError):
                        item.state = "failed"
                        item.health = {"ok": False, "code": "start_failed"}
                elif any(self.processes[dep].state in {"failed", "blocked"} for dep in item.spec["depends_on"]):
                    item.state = "blocked"
                    item.health = {"ok": False, "code": "dependency_failed"}
            if item.child is None:
                continue
            item.exit_code = item.child.poll()
            if item.exit_code is not None:
                item.state = "failed"
                item.health = {"ok": False, "code": "exited"}
                # Reap direct children promptly. Also ask surviving descendants
                # to stop, without killing healthy diagnostic services.
                if item.exit_seen_at is None:
                    item.exit_seen_at = time.monotonic()
                    item.signal(signal.SIGTERM)
                elif os.name == "posix" and time.monotonic() - item.exit_seen_at >= item.spec["stop_timeout_seconds"]:
                    item.signal(signal.SIGKILL)
                continue
            entry = self.probes.get(name)
            future, began = entry if entry is not None else (None, None)
            if future is not None and future.done():
                try:
                    item.health, finished = future.result()
                    if finished - began > item.spec["health"]["timeout_seconds"]:
                        item.health = {"ok": False, "code": "probe_timeout"}
                except Exception:
                    item.health = {"ok": False, "code": "probe_failed"}
                del self.probes[name]
                item.next_probe_at = time.monotonic() + (item.spec["health"]["interval_seconds"] if item.health["ok"] else 0)
                if item.state != "failed":
                    item.state = "ready" if item.health["ok"] else "starting" if item.state == "starting" else "unhealthy"
            elif future is not None and time.monotonic() - began > item.spec["health"]["timeout_seconds"]:
                item.health = {"ok": False, "code": "probe_timeout"}
                if item.state == "ready":
                    item.state = "unhealthy"
            if item.state == "starting" and time.monotonic() - item.started_at >= item.spec["startup_timeout_seconds"]:
                item.state = "failed"
                item.health = {"ok": False, "code": "startup_timeout"}
            if name not in self.probes and item.state != "failed" and time.monotonic() >= item.next_probe_at:
                began = time.monotonic()
                future = Future()
                # At most one read-only probe per configured service. A stuck
                # resolver keeps that slot, and cannot block PID 1 shutdown or
                # create an unbounded succession of replacement threads.
                worker = threading.Thread(target=run_probe, args=(future, item.spec["health"], item.environment), daemon=True, name="tianshu-health-" + name)
                worker.start()
                self.probes[name] = future, began

    def status(self):
        states = [item.state for item in self.processes.values()]
        if not self.enabled:
            return "failed" if "failed" in states else "stopped"
        if all(state == "ready" for state in states):
            return "ready"
        if any(state in {"failed", "blocked", "unhealthy"} for state in states):
            return "failed"
        return "starting"

    def snapshot(self):
        return {name: item.snapshot() for name, item in self.processes.items()}

    def stop(self):
        self.enabled = False
        stopped = True
        for spec in reversed(self.ordered):
            item = self.processes[spec["name"]]
            try:
                if item.stop() is False:
                    stopped = False
            except (OSError, subprocess.SubprocessError):
                stopped = False
                item.state = "failed"
                item.health = {"ok": False, "code": "stop_unconfirmed"}
        for future, _ in self.probes.values():
            future.cancel()
        return stopped


def run_probe(future, check, environment):
    if not future.set_running_or_notify_cancel():
        return
    try:
        result = probe(check, environment)
        future.set_result((result, time.monotonic()))
    except Exception as error:
        future.set_exception(error)
