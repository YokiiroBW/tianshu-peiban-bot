"""Inspect the local daemon's host PID without requiring tools inside distroless images."""

import json
from pathlib import Path


def process_status(docker, row, proc=Path("/proc")):
    pid = row["State"].get("Pid")
    if type(pid) is not int or pid <= 0:
        raise ValueError("container_process_missing")
    before = (proc / str(pid) / "stat").read_bytes()
    status = (proc / str(pid) / "status").read_text()
    current = json.loads(docker.call(["inspect", row["Id"]]))
    if len(current) != 1:
        raise ValueError("container_process_identity_changed")
    current = current[0]
    if (
        current["Id"] != row["Id"]
        or current["Image"] != row["Image"]
        or current["State"].get("Pid") != pid
        or current["State"].get("Running") is not True
        or current["State"].get("StartedAt") != row["State"].get("StartedAt")
    ):
        raise ValueError("container_process_identity_changed")
    after = (proc / str(pid) / "stat").read_bytes()
    # starttime is field 22, after pid and parenthesized comm; CPU time may change.
    if before.rsplit(b")", 1)[1].split()[19] != after.rsplit(b")", 1)[1].split()[19]:
        raise ValueError("container_process_reused")
    return status
