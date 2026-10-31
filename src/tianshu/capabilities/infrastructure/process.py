"""Bounded process-tree retirement, including Windows children after their parent exits.

The native job layout follows the fixed node executor's transport; the platform owns the
outer process lifetime and does not import the executor's internal implementation.
"""

import asyncio
import ctypes
import os
import signal
import subprocess
import sys
from pathlib import Path

CREATE_SUSPENDED = 0x00000004


class ProcessTree:
    def __init__(self, pid):
        self.pid = pid
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "read_ops",
                    "write_ops",
                    "other_ops",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", IO),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            if not handle or not process:
                raise ctypes.WinError(ctypes.get_last_error())
            info = Extended()
            info.basic.flags = 0x2000
            if not kernel.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            ) or not kernel.AssignProcessToJobObject(handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle, self.kernel = handle, kernel
            handle = None
        finally:
            if process:
                kernel.CloseHandle(process)
            if handle:
                kernel.CloseHandle(handle)

    def resume(self):
        if os.name != "nt":
            return
        # asyncio/Popen closes CreateProcess's primary-thread handle. The root is still
        # suspended, so find that root's thread and resume only after Job ownership.
        # Assigning a running venv launcher misses the base Python it already spawned.
        from ctypes import wintypes

        class Thread(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("usage", wintypes.DWORD),
                ("id", wintypes.DWORD),
                ("owner", wintypes.DWORD),
                ("base_priority", wintypes.LONG),
                ("delta_priority", wintypes.LONG),
                ("flags", wintypes.DWORD),
            ]

        kernel = self.kernel
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(Thread)]
        kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(Thread)]
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = Thread()
            entry.size = ctypes.sizeof(entry)
            present = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.owner == self.pid:
                    thread = kernel.OpenThread(0x0002, False, entry.id)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        previous = kernel.ResumeThread(thread)
                        if previous == 1:
                            return
                        if previous == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        if previous > 1:
                            raise OSError("Executor thread remains suspended")
                    finally:
                        kernel.CloseHandle(thread)
                present = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Suspended executor thread was not found")
        finally:
            kernel.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            return True
        return False


async def stop_process(process, tree=None):
    guarded = tree.close() if tree else False
    if os.name == "nt":
        if not guarded and process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                await asyncio.wait_for(killer.wait(), 2)
            except TimeoutError:
                killer.kill()
                await killer.wait()
    elif sys.platform == "linux":
        # The per-invocation subreaper must stay alive long enough to adopt and reap
        # setsid descendants, including when the real executor has already exited.
        if process.returncode is None:
            try:
                process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
        # Killing the owner before ECHILD would orphan the separate process groups
        # again. The supervisor itself SIGKILLs and reaps its children before exiting.
        await process.wait()
        return
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()
