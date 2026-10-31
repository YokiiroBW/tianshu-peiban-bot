"""One Linux invocation's child owner, including descendants that create new sessions.

Only this short-lived process becomes a subreaper; the platform and other modules do not.
Stdout/stderr belong to the real executor. No protocol, SSH configuration or credentials change.
"""

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def adopt_descendants(parent_pid):
    # Set both attributes before spawning, checking the parent on both sides of the
    # PDEATHSIG startup window. PR_SET_CHILD_SUBREAPER is not inherited across fork.
    if os.getppid() != parent_pid:
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
    libc.prctl.restype = ctypes.c_int
    for option, value in ((36, 1), (1, signal.SIGTERM)):
        if libc.prctl(option, value, 0, 0, 0) != 0:
            return False
    return os.getppid() == parent_pid


def retire_children(executor):
    # Killing a direct child reparents its descendants here, regardless of setsid.
    # Repeat kill+wait until ECHILD, rather than relying on a snapshot of a running tree.
    owner = os.getpid()
    while True:
        # DSM kernels may omit /proc/<pid>/task/<tid>/children. stat's PPid is
        # available there; only direct, still-unwaited children belong to us.
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == owner:
                    os.kill(int(entry.name), signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                break
            if executor is not None and pid == executor.pid:
                executor.returncode = os.waitstatus_to_exitcode(status)
        time.sleep(0.005)


def run(parent_pid, command):
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not adopt_descendants(parent_pid) or stopping:
        return 126
    executor = None
    try:
        executor = subprocess.Popen(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        while not stopping:
            if executor.poll() is not None:
                return executor.returncode
            time.sleep(0.01)
        return 128 + signal.SIGTERM
    except OSError:
        return 126
    finally:
        retire_children(executor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command and command[0] == "--":
        command = command[1:]
    if sys.platform != "linux" or not command:
        return 126
    return run(arguments.parent_pid, command)


if __name__ == "__main__":
    raise SystemExit(main())
