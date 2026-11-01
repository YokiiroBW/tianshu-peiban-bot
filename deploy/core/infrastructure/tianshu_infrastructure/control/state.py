"""Private durable files and OS locks for receipts, plans and saved versions."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from ..protocol import Failure, decode_json
from ..sampling import _Lock
from .protocol import encode


def checked_directory(path, *, create=False):
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved = path.resolve()
    if resolved != path.absolute() or path.is_symlink() or not path.is_dir():
        raise Failure("control_state_path_invalid")
    if os.name != "nt":
        info = path.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise Failure("control_state_owner_or_mode_invalid")
        mounts = Path("/proc/self/mountinfo")
        if mounts.is_file():
            candidates = []
            for line in mounts.read_text().splitlines():
                fields = line.split()
                separator = fields.index("-")
                mount = fields[4].replace("\\040", " ").replace("\\134", "\\")
                if path.is_relative_to(mount):
                    candidates.append((len(mount), fields[separator + 1]))
            if candidates and max(candidates)[1] in ("tmpfs", "ramfs"):
                raise Failure("control_state_must_be_persistent")
    return path


class Store:
    def __init__(self, directory):
        self.directory = checked_directory(directory, create=True)

    def path(self, kind, identity):
        token = hashlib.sha256(identity.encode()).hexdigest()
        return self.directory / f"{kind}-{token}.json"

    def read(self, kind, identity):
        path = self.path(kind, identity)
        try:
            if path.is_symlink():
                raise Failure("control_state_path_invalid")
            with path.open("rb") as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise Failure("control_state_too_large")
            value = decode_json(raw)
            if not isinstance(value, dict):
                raise ValueError("state must be an object")
            return value
        except FileNotFoundError:
            return None
        except Failure:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise Failure("control_state_invalid") from None

    def write(self, kind, identity, value):
        raw = encode(value)
        path = self.path(kind, identity)
        temporary = None
        try:
            if path.is_symlink():
                raise Failure("control_state_path_invalid")
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix="control-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                if os.name != "nt":
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            if os.name != "nt":
                fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError:
            raise Failure("control_state_unavailable") from None
        finally:
            if temporary:
                os.unlink(temporary)

    def records(self, kind):
        paths = sorted(self.directory.glob(kind + "-*.json"))
        if len(paths) > 10000:
            raise Failure("control_inventory_limit_exceeded")
        result = []
        for path in paths:
            if path.is_symlink():
                raise Failure("control_state_path_invalid")
            try:
                with path.open("rb") as stream:
                    raw = stream.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("oversized state")
                result.append(decode_json(raw))
            except (OSError, ValueError, UnicodeError, RecursionError):
                raise Failure("control_state_invalid") from None
        return result

    @contextmanager
    def lock(self, kind, identity, deadline, *, busy_code="control_busy", wait=False):
        lock = None
        try:
            path = self.path("lock-" + kind, identity).with_suffix(".lock")
            if path.is_symlink():
                raise Failure("control_state_path_invalid")
            lock = _Lock(path)
            while not lock.acquire():
                if not wait:
                    raise Failure(busy_code, "conflict")
                deadline.check()
                time.sleep(min(.02, deadline.remaining))
            yield
        except OSError:
            raise Failure("control_state_unavailable") from None
        finally:
            if lock:
                lock.close()
