"""Read-only kernel process identities; never signal a PID or remove an owner lock."""

import os
from pathlib import Path

from .safety import require


class ProcessGone(Exception):
    """The kernel no longer has this PID, distinct from permission failure."""


class ProcessIdentity:
    def __init__(self, pid):
        require(type(pid) is int and pid > 0, "invalid_process_identity")
        self.pid, self.handle = pid, None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            self.api = ctypes.WinDLL("kernel32", use_last_error=True)
            self.api.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            self.api.OpenProcess.restype = wintypes.HANDLE
            self.api.CloseHandle.argtypes = [wintypes.HANDLE]
            self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
                ctypes.POINTER(wintypes.FILETIME)
            ] * 4
            self.handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
            if not self.handle and ctypes.get_last_error() == 87:
                raise ProcessGone()
            require(self.handle, "process_identity_unavailable")
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.api.GetProcessTimes(
                self.handle, *(ctypes.byref(v) for v in times)
            ):
                self.close()
                require(False, "process_identity_unavailable")
            self.birth = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        else:
            require(
                Path("/proc/sys/kernel/random/boot_id").exists(),
                "process_backend_unsupported",
            )
            try:
                self.birth = self._linux()[0]
            except FileNotFoundError:
                raise ProcessGone() from None

    def _linux(self):
        raw = Path(f"/proc/{self.pid}/stat").read_text()
        columns = raw[raw.rfind(")") + 2 :].split()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return boot + ":" + columns[19], columns[0]

    def exited(self):
        if os.name == "nt":
            status = self.api.WaitForSingleObject(self.handle, 0)
            require(status in (0, 258), "process_identity_unavailable")
            return status == 0
        try:
            birth, state = self._linux()
        except FileNotFoundError:
            return True
        return birth != self.birth or state in {"Z", "X"}

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None


def birth_of(pid):
    process = ProcessIdentity(pid)
    try:
        return process.birth
    finally:
        process.close()
