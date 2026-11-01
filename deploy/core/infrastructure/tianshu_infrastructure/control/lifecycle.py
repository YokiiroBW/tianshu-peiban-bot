"""Verifiable launcher identities and container-side Compose completion leases."""

from __future__ import annotations

import ctypes
import os
import uuid
from pathlib import Path

from ..protocol import Failure, decode_json
from ..transport import Deadline
from .files import no_symlinks


def process_identity(pid):
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None if ctypes.get_last_error() == 5 else False
        try:
            creation, exit_time, kernel_time, user_time = (ctypes.c_uint64() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            return {"pid": pid, "created": str(creation.value)} if not exit_time.value else False
        finally:
            kernel.CloseHandle(handle)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return False
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return {"pid": pid, "created": boot + ":" + fields[19]}
    except FileNotFoundError:
        return False
    except (OSError, IndexError):
        return None


def launcher_active(record):
    if not record or record.get("state") == "preparing":
        return None
    expected = record.get("identity")
    if expected is False:
        return False
    if not isinstance(expected, dict):
        return None
    observed = process_identity(expected["pid"])
    return None if observed is None else observed == expected


# Dockge already runs Node. The fixed wrapper consumes stdin for Compose,
# contains its descendants in a separate process group, and writes only a small
# private completion marker under the existing stacks bind. Closing docker exec
# output does not terminate it or turn an incomplete command into a success.
WRAPPER = Path(__file__).with_name("compose-wrapper.js").read_text(encoding="utf-8")

PROBE = Path(__file__).with_name("compose-probe.js").read_text(encoding="utf-8")


def exec_prefix(command):
    # This is the deployment registration shape, never a request-supplied argv.
    if "exec" not in command:
        return None
    index = command.index("exec")
    if len(command) < index + 4 or command[index + 1] != "-i":
        raise Failure("compose_exec_registration_invalid")
    return list(command[:index + 3]), list(command[index + 3:])


def prepare_lease(files, directory, command, container_id):
    prefix = exec_prefix(command)
    if prefix is None:
        return None
    root = files.config.stacks_root / ".tianshu-control-execution"
    no_symlinks(root, allow_missing=True)
    root.mkdir(mode=0o700, exist_ok=True)
    no_symlinks(root)
    if os.name != "nt" and (root.stat().st_mode & 0o077 or root.stat().st_uid != os.geteuid()):
        raise Failure("control_execution_state_mode_invalid")
    name = uuid.uuid4().hex + ".json"
    context_root = files.config.compose_path_root or files.config.stacks_root
    fixed_prefix = [*prefix[0][:-1], container_id]
    return {"host_path": str(root / name), "context_path": str(context_root / root.name / name), "prefix": fixed_prefix, "container_id": container_id}


def lease_active(lease, transport):
    path = Path(lease["host_path"])
    try:
        no_symlinks(path, allow_missing=True)
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return None
        value = decode_json(raw)
        if value.get("state") == "complete" and type(value.get("exit_code")) is int and type(value.get("timed_out")) is bool:
            return False
        if value.get("state") != "running" or type(value.get("pid")) is not int or not isinstance(value.get("created"), str):
            return None
        result = transport.command([*lease["prefix"], "node", "-e", PROBE, str(value["pid"]), value["created"]], max_output=4096, deadline=Deadline(5))
        if result.reason:
            if b"no such container" in result.stderr.lower() or b"is not running" in result.stderr.lower():
                # The prefix addresses the original full64 container ID. A
                # removed/stopped container cannot still run its old exec.
                return False
            return None
        active = decode_json(result.stdout).get("active")
        return active if type(active) is bool else None
    except (OSError, ValueError, TypeError, Failure):
        return None
