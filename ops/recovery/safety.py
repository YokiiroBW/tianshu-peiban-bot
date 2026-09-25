"""Local path, bounded IO, and cooperative deployment ownership boundaries."""

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path


class RecoveryError(Exception):
    """Only fixed reason codes reach the public CLI."""


class DrillDiagnosticError(RecoveryError):
    """Fixed assertion failure facts without response bodies or credentials."""

    def __init__(self, code, *, stage, actual_status=None, response_structure=None):
        super().__init__(code)
        self.stage = stage
        self.actual_status = actual_status
        self.response_structure = response_structure

    def detail(self):
        value = {"code": self.args[0], "stage": self.stage}
        if self.actual_status is not None:
            value["actual_http_status"] = self.actual_status
        if self.response_structure is not None:
            value["response_structure"] = self.response_structure
        return value


def require(condition, code):
    if not condition:
        raise RecoveryError(code)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def read_json(path):
    data = read_bytes(path, limit=4 * 1024 * 1024)
    try:
        return json.loads(
            data, object_pairs_hook=pairs, parse_constant=lambda _: fail("invalid_json")
        )
    except (ValueError, UnicodeError):
        raise RecoveryError("invalid_json") from None


def fail(code):
    raise RecoveryError(code)


def relative(value):
    require(isinstance(value, str) and 0 < len(value) <= 512, "invalid_relative_path")
    require(
        not value.startswith("/") and "\\" not in value and ":" not in value,
        "path_escape",
    )
    parts = value.split("/")
    for part in parts:
        require(part not in ("", ".", ".."), "path_escape")
        # Loki filesystem chunks use base64 padding. These are ordinary filename
        # characters on both hosts; path escapes and drive syntax remain refused.
        require(
            re.fullmatch(r"[A-Za-z0-9_.+=-]+", part) is not None, "invalid_path_name"
        )
        require(not part.endswith((".", " ")), "invalid_path_name")
        require(
            part.split(".")[0].upper()
            not in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *[f"COM{i}" for i in range(10)],
                *[f"LPT{i}" for i in range(10)],
            },
            "invalid_path_name",
        )
    return value


def safe_path(value, *, exists=True):
    path = Path(value)
    require(
        path.is_absolute() and not str(path).startswith(("\\\\", "//")),
        "local_absolute_path_required",
    )
    require(".." not in path.parts, "path_escape")
    # Reject aliases before resolving; Path.resolve alone would hide junctions/symlinks.
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode), "linked_path")
        require(not getattr(info, "st_file_attributes", 0) & 0x400, "linked_path")
        require(
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), "special_file"
        )
        if stat.S_ISREG(info.st_mode):
            require(info.st_nlink == 1, "hardlinked_file")
    require(not exists or path.exists(), "path_missing")
    return path.resolve(strict=exists)


def child(root, name, *, exists=True):
    relative(name)
    root = safe_path(root)
    path = safe_path(root / name, exists=exists)
    require(path != root and path.is_relative_to(root), "path_escape")
    return path


def separate(*paths):
    for i, left in enumerate(paths):
        for right in paths[i + 1 :]:
            require(
                not left.is_relative_to(right) and not right.is_relative_to(left),
                "overlapping_roots",
            )


def read_bytes(path, *, limit):
    path = safe_path(path)
    require(path.is_file() and path.stat().st_size <= limit, "file_size_limit")
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit, "file_size_limit")
    return value


def file_hash(path):
    path = safe_path(path)
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def sync_dir(path):
    # Windows has no stdlib equivalent of fsync(directory); report this limitation.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def walk_tree(root, *, topdown=True):
    """Never interpret failed readdir or entry classification as an empty subtree.

    os.walk also suppresses DirEntry.is_dir errors even when onerror is supplied,
    so enumerate explicitly. Close each iterator before yielding; use an explicit
    stack to retain the existing top-down/bottom-up behavior without recursion.
    """
    pending = [(safe_path(root), None, None)]
    while pending:
        directory, dirs, names = pending.pop()
        if dirs is not None:
            yield str(directory), dirs, names
            continue
        dirs, names = [], []
        try:
            with os.scandir(safe_path(directory)) as entries:
                for entry in entries:
                    safe_path(directory / entry.name)
                    collection = dirs if entry.is_dir(follow_symlinks=False) else names
                    collection.append(entry.name)
        except OSError:
            raise RecoveryError("directory_enumeration_failed") from None
        if topdown:
            yield str(directory), dirs, names
        else:
            pending.append((directory, dirs, names))
        pending.extend((directory / name, None, None) for name in reversed(dirs))


def sync_tree(root):
    for directory, dirs, _ in walk_tree(root, topdown=False):
        for name in dirs:
            safe_path(Path(directory) / name)
        sync_dir(safe_path(directory))


def write_new(path, data):
    path = safe_path(path, exists=False)
    require(not path.exists(), "destination_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def copy_new(source, target, *, limit, check_cancel):
    source = safe_path(source)
    target = safe_path(target, exists=False)
    require(source.stat().st_size <= limit, "file_size_limit")
    require(not target.exists(), "destination_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_path(target.parent)
    total = 0
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        while True:
            check_cancel()
            chunk = incoming.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            require(total <= limit, "file_size_limit")
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    sync_dir(target.parent)


def files(root, *, max_files=10000):
    root = safe_path(root)
    result = []
    folded = set()
    for directory, dirs, names in walk_tree(root):
        for name in sorted(dirs + names):
            path = safe_path(Path(directory) / name)
            rel = path.relative_to(root).as_posix()
            relative(rel)
            require(rel.casefold() not in folded, "case_alias")
            folded.add(rel.casefold())
            if path.is_file():
                result.append(rel)
                require(len(result) <= max_files, "file_count_limit")
    return sorted(result)


@contextmanager
def lease(path):
    """Never create/delete a lock: runtime and maintenance must share the same inode."""
    path = safe_path(path)
    require(path.stat().st_size == 1, "invalid_owner_lock")
    stream = path.open("r+b")
    acquired = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise RecoveryError("deployment_busy") from None
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()
