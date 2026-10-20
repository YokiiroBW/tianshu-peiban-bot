"""Immutable files; callers own authorization, references, retention and cleanup."""

import errno
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .errors import StorageError


@dataclass(frozen=True)
class FileRef:
    sha256: str
    size_bytes: int
    relative_path: str
    media_type: str = "application/octet-stream"
    format: str = "tianshu-file-v1"

    def as_dict(self):
        return {
            "format": self.format,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "relative_path": self.relative_path,
            "media_type": self.media_type,
        }

    @classmethod
    def parse(cls, value):
        if isinstance(value, cls):
            result = value
        elif isinstance(value, dict):
            try:
                result = cls(**value)
            except (TypeError, ValueError):
                raise StorageError("file_reference_invalid") from None
        else:
            raise StorageError("file_reference_invalid")
        if (
            result.format != "tianshu-file-v1"
            or not isinstance(result.sha256, str)
            or not re.fullmatch("[0-9a-f]{64}", result.sha256)
            or type(result.size_bytes) is not int
            or result.size_bytes < 0
            or result.relative_path != f"{result.sha256[:2]}/{result.sha256}"
            or not isinstance(result.media_type, str)
            or not 1 <= len(result.media_type) <= 256
        ):
            raise StorageError("file_reference_invalid")
        return result


def sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class FileStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, reference, *, create=False):
        reference = FileRef.parse(reference)
        path = self.root / reference.relative_path
        # Inspect each existing component before following it. A reference cannot escape
        # its configured module root through a replaced digest directory.
        for current in (self.root, path.parent, path):
            if current.is_symlink():
                raise StorageError("file_reference_invalid")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            path.parent.mkdir(exist_ok=True)
        if not path.resolve().is_relative_to(self.root):
            raise StorageError("file_reference_invalid")
        return path

    def put_bytes(self, payload, *, media_type="application/octet-stream"):
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("payload must be bytes")
        payload = bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        reference = FileRef(digest, len(payload), f"{digest[:2]}/{digest}", media_type)
        path = self._path(reference, create=True)
        if path.exists():
            self.read_bytes(reference)
            return reference
        pending = path.with_name(path.name + "." + uuid4().hex + ".partial")
        try:
            with pending.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            pending.chmod(0o600)
            try:
                # Publish without replacing a concurrently published immutable object.
                # Replacing an open destination also fails on Windows, even when the
                # two writers have exactly the same content hash.
                os.link(pending, path)
            except FileExistsError:
                self.read_bytes(reference)
            except OSError as exc:
                if exc.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS}:
                    raise
                # Filesystems without hard links still support the existing atomic
                # rename protocol. A concurrent completed object is simply reused.
                if path.exists():
                    self.read_bytes(reference)
                else:
                    os.replace(pending, path)
            sync_directory(path.parent)
        except OSError:
            raise StorageError("file_store_write_failed") from None
        finally:
            pending.unlink(missing_ok=True)
        return reference

    def read_bytes(self, reference):
        reference = FileRef.parse(reference)
        path = self._path(reference)
        try:
            with path.open("rb") as stream:
                if os.fstat(stream.fileno()).st_size != reference.size_bytes:
                    raise StorageError("file_content_mismatch")
                content = stream.read()
        except OSError:
            raise StorageError("file_content_unavailable") from None
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise StorageError("file_content_mismatch")
        return content

    def put_text(self, text, *, media_type="text/plain; charset=utf-8"):
        return self.put_bytes(text.encode("utf-8"), media_type=media_type)

    def read_text(self, reference):
        try:
            return self.read_bytes(reference).decode("utf-8")
        except UnicodeDecodeError:
            raise StorageError("file_content_encoding_invalid") from None
