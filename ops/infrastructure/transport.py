"""Bounded process IO and explicit OpenSSH transport, usable from Windows."""

from __future__ import annotations

import ctypes
import math
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .protocol import Failure


class Deadline:
    def __init__(self, seconds: float):
        self.expires = time.monotonic() + seconds

    @property
    def remaining(self):
        return max(0.0, self.expires - time.monotonic())

    def check(self):
        if not self.remaining:
            raise Failure("deadline_exceeded")


class _WindowsJob:
    """Kill-on-close includes descendants even if the root process exits first."""

    def __init__(self, process):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                        ("peak_job_memory", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        if handle:
            info = Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) and kernel.AssignProcessToJobObject(handle, int(process._handle)):
                self.handle = handle
                self.kernel = kernel
            else:
                kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            return True
        return False


@dataclass
class CommandResult:
    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int | None = None
    timed_out: bool = False
    truncated: bool = False
    unavailable: bool = False

    @property
    def reason(self):
        if self.unavailable:
            return "executable_unavailable"
        if self.timed_out:
            return "deadline_exceeded"
        if self.truncated:
            return "output_limit_exceeded"
        if self.exit_code == 0:
            return None
        lowered = self.stderr.lower()
        if self.exit_code == 255:
            if b"permission denied" in lowered or b"authentication failed" in lowered:
                return "ssh_auth_failed"
            if b"host key verification failed" in lowered or b"remote host identification has changed" in lowered:
                return "ssh_host_key_failed"
            return "ssh_unavailable"
        if self.exit_code == 127:
            return "command_unavailable"
        if b"permission denied" in lowered:
            return "permission_denied"
        return "command_failed"


def run_bounded(argv, payload: bytes, deadline: Deadline, *, max_output=2 * 1024 * 1024, stderr_limit=32768) -> CommandResult:
    deadline.check()
    options = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    environment = dict(os.environ)
    environment.pop("SSH_AUTH_SOCK", None)
    try:
        process = subprocess.Popen(argv, env=environment, **options)
    except OSError:
        return CommandResult(unavailable=True)
    job = _WindowsJob(process)
    chunks = [bytearray(), bytearray()]
    lock = threading.Lock()
    overflow = threading.Event()
    done = [threading.Event(), threading.Event()]

    def read_pipe(pipe, index):
        try:
            while True:
                chunk = os.read(pipe.fileno(), 8192)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, max_output - len(chunks[0]) - len(chunks[1]))
                    if index == 1:
                        remaining = min(remaining, max(0, stderr_limit - len(chunks[1])))
                    chunks[index].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
        except OSError:
            pass
        finally:
            done[index].set()

    def write_pipe():
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except (OSError, BrokenPipeError):
            pass
        finally:
            process.stdin.close()

    threads = [threading.Thread(target=read_pipe, args=(process.stdout, 0), daemon=True),
               threading.Thread(target=read_pipe, args=(process.stderr, 1), daemon=True),
               threading.Thread(target=write_pipe, daemon=True)]
    for thread in threads:
        thread.start()
    timed_out = False
    exited_at = None
    while True:
        if overflow.is_set():
            break
        if not deadline.remaining:
            timed_out = True
            break
        if process.poll() is not None:
            if all(event.is_set() for event in done):
                break
            exited_at = exited_at or time.monotonic()
            if time.monotonic() - exited_at > 0.05:
                break  # A descendant inherited a pipe; reap its process group too.
        time.sleep(min(0.01, deadline.remaining))
    if not job.close():
        try:
            if os.name == "nt":
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)
    for thread in threads:
        thread.join(timeout=0.1)
    process.stdout.close()
    process.stderr.close()
    return CommandResult(bytes(chunks[0]), bytes(chunks[1]), process.returncode, timed_out, overflow.is_set())


class SSHTransport:
    def __init__(self, config, node, deadline):
        self.config = config
        self.node = node
        self.deadline = deadline

    def _argv(self, command):
        node = self.node
        if not Path(node.identity_file).is_file() or not os.access(node.identity_file, os.R_OK):
            raise Failure("ssh_identity_unavailable")
        if not Path(node.known_hosts_file).is_file() or not os.access(node.known_hosts_file, os.R_OK):
            raise Failure("ssh_known_hosts_unavailable")
        options = (
            "BatchMode=yes", "IdentitiesOnly=yes", "IdentityAgent=none",
            "StrictHostKeyChecking=yes", f"UserKnownHostsFile={node.known_hosts_file}",
            f"GlobalKnownHostsFile={node.known_hosts_file}", "ControlMaster=no",
            "ControlPath=none", "ProxyCommand=none", "ProxyJump=none",
            "PermitLocalCommand=no", "ClearAllForwardings=yes", "RequestTTY=no",
            "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
            "ServerAliveInterval=5", "ServerAliveCountMax=1",
            f"ConnectTimeout={max(1, math.ceil(self.deadline.remaining))}",
        )
        argv = [*self.config.ssh_command, "-F", os.devnull, "-T", "-p", str(node.port), "-l", node.user, "-i", node.identity_file]
        for option in options:
            argv.extend(["-o", option])
        return [*argv, "--", node.host, command]

    def command(self, argv, payload=b"", *, max_output=2 * 1024 * 1024, stderr_limit=32768):
        return run_bounded(self._argv("LC_ALL=C " + shlex.join(argv)), payload, self.deadline, max_output=max_output, stderr_limit=stderr_limit)

    def script(self, script, *, max_output=2 * 1024 * 1024):
        # GNU and BusyBox timeout bound the remote read process where available;
        # closing/reaping OpenSSH remains the local hard deadline in every case.
        seconds = max(1, math.ceil(self.deadline.remaining))
        command = f"if command -v timeout >/dev/null 2>&1; then exec timeout -s KILL {seconds} sh -s; else exec sh -s; fi"
        return run_bounded(self._argv(command), ("export LC_ALL=C\n" + script).encode("utf-8"), self.deadline, max_output=max_output)


def section_script(commands: dict[str, str]) -> str:
    lines = []
    for name, command in commands.items():
        lines += [f"printf '\\n__TIANSHU_BEGIN_{name}__\\n'", f"( {command} )", "_tianshu_rc=$?",
                  f"printf '\\n__TIANSHU_END_{name}__:%s\\n' \"$_tianshu_rc\""]
    return "\n".join(lines) + "\n"


def parse_sections(raw: bytes) -> dict[str, tuple[int, bytes]]:
    result = {}
    name = None
    body = []
    for line in raw.splitlines(keepends=True):
        stripped = line.rstrip(b"\r\n")
        if stripped.startswith(b"__TIANSHU_BEGIN_") and stripped.endswith(b"__"):
            candidate = stripped[len(b"__TIANSHU_BEGIN_"):-2].decode("ascii", "ignore")
            if candidate.replace("_", "").isalnum():
                name, body = candidate, []
        elif name and stripped.startswith(("__TIANSHU_END_" + name + "__:").encode()):
            try:
                code = int(stripped.rsplit(b":", 1)[1])
                result[name] = (code, b"".join(body).removesuffix(b"\n"))
            except ValueError:
                pass
            name, body = None, []
        elif name:
            body.append(line)
    return result
