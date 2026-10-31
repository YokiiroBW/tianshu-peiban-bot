"""Immutable content-addressed files with bounded streaming and range readers."""

import asyncio
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import re
import threading
from uuid import uuid4

from tianshu.types import MediaRef
from .cancellation import finish_cleanup
from .errors import AppError

CHUNK_SIZE = 1024*1024


class FileStore:
    """Only byte storage; domains authorize references, ranges, retention and cleanup."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.verified = OrderedDict()
        self.cache_lock = threading.Lock()

    def _path(self, reference):
        identity = reference.sha256 if isinstance(reference, MediaRef) else reference
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise AppError("invalid_file_reference")
        path = self.root/identity[:2]/identity
        if any(item.is_symlink() for item in (self.root, path.parent, path)):
            raise AppError("invalid_file_reference")
        if not path.resolve().is_relative_to(self.root):
            raise AppError("invalid_file_reference")
        return path

    def _temporary(self):
        directory = self.root/".incoming"
        self.root.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise AppError("invalid_file_reference")
        directory.mkdir(exist_ok=True)
        path = directory/(uuid4().hex+".partial")
        stream = path.open("xb")
        path.chmod(0o600)
        return path, stream

    def _publish(self, temporary, identity, size, media_type):
        reference = MediaRef(identity, "sha256:"+identity, media_type, size, identity)
        path = self._path(reference)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(temporary, path)
        except FileExistsError:
            with self.open_reader(reference):
                pass
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return reference

    def put_stream(self, source, *, media_type="application/octet-stream", max_bytes=None):
        """Consume a binary file-like object with a fixed-size working buffer."""
        temporary, target = self._temporary()
        digest, size = hashlib.sha256(), 0
        try:
            with target:
                while chunk := source.read(CHUNK_SIZE):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise AppError("artifact_too_large", status=413)
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            return self._publish(temporary, digest.hexdigest(), size, media_type)
        finally:
            target.close()
            temporary.unlink(missing_ok=True)

    async def put_async(self, chunks, *, media_type="application/octet-stream", max_bytes=None):
        """Consume an async iterator, such as a streaming multipart upload or decoder."""
        async def io_call(operation, *args):
            task = asyncio.create_task(asyncio.to_thread(operation, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await finish_cleanup(task)
                raise

        creation = asyncio.create_task(asyncio.to_thread(self._temporary))
        try:
            temporary, target = await asyncio.shield(creation)
        except asyncio.CancelledError:
            temporary, target = await finish_cleanup(creation)
            await finish_cleanup(asyncio.to_thread(self._discard, temporary, target))
            raise
        digest, size = hashlib.sha256(), 0
        try:
            async for chunk in chunks:
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    raise AppError("artifact_too_large", status=413)
                view = memoryview(chunk)
                for position in range(0, len(view), CHUNK_SIZE):
                    piece = view[position:position+CHUNK_SIZE]
                    await io_call(target.write, piece)
                    digest.update(piece)
            await io_call(target.flush)
            await io_call(os.fsync, target.fileno())
            await io_call(target.close)
            return await io_call(self._publish, temporary, digest.hexdigest(), size, media_type)
        finally:
            await finish_cleanup(asyncio.to_thread(self._discard, temporary, target))

    @staticmethod
    def _discard(temporary, target):
        target.close()
        temporary.unlink(missing_ok=True)

    def put_bytes(self, raw, *, media_type="application/octet-stream"):
        return self.put_stream(io.BytesIO(raw), media_type=media_type)

    def put_text(self, text, *, media_type="text/plain; charset=utf-8"):
        return self.put_bytes(text.encode("utf-8"), media_type=media_type)

    @contextmanager
    def open_reader(self, reference, *, start=0, verify=True):
        if type(start) is not int or start < 0:
            raise AppError("invalid_file_range", status=416)
        path = self._path(reference)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            stat = os.fstat(stream.fileno())
            expected = reference.sha256 if isinstance(reference, MediaRef) else reference
            if isinstance(reference, MediaRef) and reference.size_bytes is not None:
                if stat.st_size != reference.size_bytes:
                    raise AppError("file_content_mismatch", status=500)
            if start > stat.st_size:
                raise AppError("invalid_file_range", status=416)
            key = (expected, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            with self.cache_lock:
                known = key in self.verified
            if verify and not known:
                digest = hashlib.sha256()
                while chunk := stream.read(CHUNK_SIZE):
                    digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise AppError("file_content_mismatch", status=500)
                with self.cache_lock:
                    self.verified[key] = True
                    while len(self.verified) > 256:
                        self.verified.popitem(last=False)
            stream.seek(start)
            yield stream

    def iter_bytes(self, reference, *, start=0, length=None, chunk_size=CHUNK_SIZE):
        if length is not None and (type(length) is not int or length < 0):
            raise AppError("invalid_file_range", status=416)
        if type(chunk_size) is not int or not 1 <= chunk_size <= CHUNK_SIZE:
            raise AppError("invalid_file_chunk_size")
        with self.open_reader(reference, start=start) as stream:
            remaining = length
            while remaining is None or remaining > 0:
                chunk = stream.read(chunk_size if remaining is None else min(chunk_size, remaining))
                if not chunk:
                    break
                yield chunk
                if remaining is not None:
                    remaining -= len(chunk)

    def read_bytes(self, reference):
        """Small-artifact convenience; large media uses iter_bytes/open_reader."""
        return b"".join(self.iter_bytes(reference))

    async def iter_async(self, reference, *, start=0, length=None, chunk_size=CHUNK_SIZE):
        iterator = self.iter_bytes(reference, start=start, length=length, chunk_size=chunk_size)
        sentinel = object()
        try:
            while True:
                task = asyncio.create_task(asyncio.to_thread(next, iterator, sentinel))
                try:
                    chunk = await asyncio.shield(task)
                except asyncio.CancelledError:
                    await finish_cleanup(task)
                    raise
                if chunk is sentinel:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(iterator.close)

    def read_text(self, reference):
        return self.read_bytes(reference).decode("utf-8")
