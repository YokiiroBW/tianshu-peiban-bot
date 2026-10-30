"""Native log segment closure and host liveness helpers."""

import json
import os
import re
import uuid
from pathlib import Path

from alerts import MONITOR_STALE_SECONDS

MODULES = {"platform", "gateway", "memory", "knowledge", "companion"}
UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"


def pid_alive(pid):
    # os.kill(pid, 0) terminates a process on Windows. Native tail reclamation
    # uses Linux process liveness; other hosts keep their unproved tail.
    if os.name != "posix":
        return False
    os.kill(pid, 0)
    return True


def active_instances(path, now):
    """Unknown/partial/stale host state never proves that a tail is closed."""
    if path is None:
        return {}
    try:
        state = json.loads(Path(path).read_bytes())
        diagnostic = state["diagnostics"]
        instances = diagnostic["instances"]
        age = now - state["updated_at"]
        if (
            state["version"] != 1
            or state["status"] != "ready"
            or diagnostic["complete"] is not True
            or set(instances) != MODULES
            or type(state["pid"]) is not int
            or state["pid"] <= 0
            or not 0 <= age <= MONITOR_STALE_SECONDS
        ):
            return {}
        for instance in instances.values():
            if str(uuid.UUID(instance)) != instance:
                return {}
        if not pid_alive(state["pid"]):
            return {}
        return instances
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {}


def native_segment(source, name):
    """Native production filenames only, not every arbitrary JSONL file."""
    if source == "companion":
        match = re.fullmatch(r"companion\.jsonl(?:\.(\d+))?", name)
        return ("companion", int(match[1] or 0)) if match else None
    pattern = (
        rf"({UUID})\.(\d{{6,}})\.jsonl"
        if source in {"memory", "knowledge"}
        else rf"{re.escape(source)}-({UUID})-(\d{{4,}})\.jsonl"
    )
    match = re.fullmatch(pattern, name)
    return (match[1], int(match[2])) if match else None


def stamp(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def closed(source, segment, highest, instances):
    instance, index = segment
    return (
        index < highest[instance]
        or source != "companion"
        and source in instances
        and instances[source] != instance
    )


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
