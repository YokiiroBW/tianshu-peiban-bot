"""Verified video bytes shared on a target volume; no download/catalog ledger."""

import errno
import hashlib
import os
import shutil
from pathlib import Path

from ..contracts import Fault, require
from .destination import safe_path
from .engine import file_record

LINK_UNAVAILABLE = frozenset(
    {
        errno.EXDEV,
        errno.EPERM,
        errno.EACCES,
        errno.ENOSYS,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
        errno.EMLINK,
    }
)


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def copy_file(source, destination):
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024**2)
        writer.flush()
        os.fsync(writer.fileno())


def try_link(source, destination):
    try:
        os.link(source, destination)
        return True
    except OSError as error:
        if error.errno not in LINK_UNAVAILABLE:
            raise
        return False


def sync_file(path):
    # Windows _commit requires a writable descriptor; opening r+b never truncates.
    with path.open("r+b" if os.name == "nt" else "rb") as stream:
        os.fsync(stream.fileno())


def verify_video(path, entry):
    actual = file_record(path, entry["path"], "video", entry["cid"])
    require(actual == entry and actual["size_bytes"] > 0, "source_changed", 409)


class VideoStorage:
    """Call only while FileOrganizer owns the existing target root lock."""

    def __init__(self, control):
        self.control = Path(control)
        self.directory = safe_path(self.control, "videos")
        self.directory.mkdir(exist_ok=True)
        sync_directory(self.control)

    def shared_path(self, entry):
        # Deliberately no media extension: the control file is not a server item.
        return safe_path(self.directory, entry["sha256"] + "-" + str(entry["size_bytes"]))

    def source_temporary(self, entry):
        return safe_path(self.directory, ".source-" + self.shared_path(entry).name)

    def install_temporary(self, destination, entry):
        target = hashlib.sha256(os.path.normcase(str(destination)).encode()).hexdigest()
        return safe_path(self.control, ".video-" + self.shared_path(entry).name + "-" + target)

    def discard_temporary(self, path):
        if path.exists():
            require(path.is_file(), "unsafe_library_path", 409)
            path.unlink()
            sync_directory(path.parent)

    def ensure(self, source, entry):
        shared = self.shared_path(entry)
        temporary = self.source_temporary(entry)
        if shared.exists():
            try:
                verify_video(shared, entry)
                self.discard_temporary(temporary)
                return shared
            except Fault as error:
                if error.code != "source_changed":
                    raise
        try:
            if temporary.exists():
                try:
                    verify_video(temporary, entry)
                except Fault as error:
                    if error.code != "source_changed":
                        raise
                    self.discard_temporary(temporary)
            if not temporary.exists():
                verify_video(source, entry)
                if not try_link(source, temporary):
                    copy_file(source, temporary)
            verify_video(temporary, entry)
            sync_file(temporary)
            # Replacing a corrupt control file never overwrites its shared inode.
            os.replace(temporary, shared)
            sync_directory(self.directory)
        finally:
            temporary.unlink(missing_ok=True)
        return shared

    def install(self, source, destination, entry, *, preserve_copy=False):
        shared = self.ensure(source, entry)
        temporary = self.install_temporary(destination, entry)
        # The pool already holds the verified bytes, so recovering an interrupted
        # install needs only this target's exact reserved path, never a global scan.
        self.discard_temporary(temporary)
        if destination.exists() and shared.samefile(destination):
            return "hardlink"
        if preserve_copy and shared.stat().st_dev != destination.stat().st_dev:
            return "copy"
        try:
            if not try_link(shared, temporary):
                if preserve_copy:
                    # An existing valid copy already provides the safe fallback.
                    verify_video(destination, entry)
                    return "copy"
                copy_file(shared, temporary)
            verify_video(temporary, entry)
            sync_file(temporary)
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)
        return "hardlink" if shared.samefile(destination) else "copy"

    def observe(self, directory, manifest):
        files = []
        for entry in manifest["files"]:
            if entry["kind"] != "video":
                continue
            shared = self.shared_path(entry)
            installed = safe_path(directory, entry["path"])
            mode = "hardlink" if shared.is_file() and shared.samefile(installed) else "copy"
            files.append({"path": entry["path"], "mode": mode})
        modes = {entry["mode"] for entry in files}
        return {"mode": next(iter(modes)) if len(modes) == 1 else "mixed", "files": files}
