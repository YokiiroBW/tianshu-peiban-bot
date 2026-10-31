"""Immutable content-addressed files. Domains own references, authorization and retention."""

import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4

from tianshu.types import MediaRef
from .errors import AppError


class FileStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, reference):
        identity = reference.sha256 if isinstance(reference, MediaRef) else reference
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise AppError("invalid_file_reference")
        path = self.root / identity[:2] / identity
        if any(item.is_symlink() for item in (self.root, path.parent, path)):
            raise AppError("invalid_file_reference")
        if not path.resolve().is_relative_to(self.root):
            raise AppError("invalid_file_reference")
        return path

    def put_bytes(self, raw, *, media_type="application/octet-stream"):
        raw = bytes(raw)
        identity = hashlib.sha256(raw).hexdigest()
        reference = MediaRef(identity, "sha256:" + identity, media_type, len(raw), identity)
        path = self._path(reference)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.read_bytes(reference)
            return reference
        temporary = path.with_name(identity + "." + uuid4().hex + ".partial")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            try:
                os.link(temporary, path)
            except FileExistsError:
                self.read_bytes(reference)
            if os.name != "nt":
                descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)
        return reference

    def put_text(self, text, *, media_type="text/plain; charset=utf-8"):
        return self.put_bytes(text.encode("utf-8"), media_type=media_type)

    def read_bytes(self, reference):
        raw = self._path(reference).read_bytes()
        expected = reference.sha256 if isinstance(reference, MediaRef) else reference
        if hashlib.sha256(raw).hexdigest() != expected:
            raise AppError("file_content_mismatch", status=500)
        if isinstance(reference, MediaRef) and reference.size_bytes is not None:
            if len(raw) != reference.size_bytes:
                raise AppError("file_content_mismatch", status=500)
        return raw

    def read_text(self, reference):
        return self.read_bytes(reference).decode("utf-8")
