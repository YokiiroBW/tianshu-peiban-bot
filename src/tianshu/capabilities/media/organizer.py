"""Recoverable file transfers into an external media server directory, not a media catalog."""

import base64
import asyncio
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..contracts import Fault, require
from .engine import file_record
from .identity import multipart_directory, single_directory
from .destination import safe_path, subdirectory
from .video_storage import VideoStorage, copy_file, sync_directory

MARKER = ".tianshu-publication.json"
KIND = "file_transfer"


@contextmanager
def library_lock(directory):
    root = Path(directory)
    # Follow no configured symlink/junction, including parent components.
    for path in (root, *root.parents):
        require(not path.is_symlink() and not path.is_junction(), "unsafe_library_path", 409)
    root.mkdir(parents=True, exist_ok=True)
    control = safe_path(root, ".tianshu-transfer")
    control.mkdir(exist_ok=True)
    lock = safe_path(control, "publication.lock")
    with lock.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Fault("busy", 409) from None
        try:
            yield root, control
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class FileOrganizer:
    def __init__(self, repo, contract, staging):
        self.repo, self.contract, self.staging = repo, contract, Path(staging)

    def operation(self, target, identity):
        value = self.repo.get(KIND, identity)
        require(value is not None, "publication_not_found", 404)
        require(
            value["target_id"] == target["target_id"]
            and value["directory"] == target["directory"]
            and value["library_id"] == target["library_id"],
            "scope_changed",
            409,
        )
        return value

    def save(self, operation, **changes):
        return self.repo.put(
            KIND, operation["operation_id"], {**operation, **changes}, operation["revision"]
        )

    def verify_files(self, directory, manifest):
        require(
            directory.is_dir() and not directory.is_symlink() and not directory.is_junction(),
            "source_missing",
            409,
        )
        for entry in manifest["files"]:
            actual = file_record(
                safe_path(directory, entry["path"]), entry["path"], entry["kind"], entry["cid"]
            )
            require(actual == entry, "source_changed", 409)

    def marker(self, directory):
        path = safe_path(directory, MARKER)
        if not path.is_file():
            return None
        require(path.stat().st_size <= 4096, "target_exists", 409)
        try:
            return json.loads(path.read_bytes())
        except ValueError:
            raise Fault("target_exists", 409) from None

    def matches(self, directory, operation):
        return self.marker(directory) == {
            "operation_id": operation["operation_id"],
            "manifest_digest": operation["manifest_digest"],
        }

    def prepare(self, target, package, identity):
        raw = base64.b64decode(package["manifest_base64"], validate=True)
        require(hashlib.sha256(raw).hexdigest() == package["manifest_digest"], "hash_mismatch", 409)
        manifest = json.loads(raw)
        self.contract.check(manifest)
        require(manifest["library_id"] == target["library_id"], "scope_changed", 409)
        # The same worker command always finds its original durable intent after restart.
        request_digest = hashlib.sha256(
            json.dumps(package, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        operation_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL, "tianshu-transfer:" + target["target_id"] + ":" + identity
            )
        )
        with library_lock(target["directory"]) as (root, _):
            prior = self.repo.get(KIND, operation_id)
            if prior:
                self.operation(target, operation_id)
                require(prior["request_digest"] == request_digest, "idempotency_conflict", 409)
                return self.reconcile(root, prior)
            source = safe_path(self.staging, manifest["staging_ref"])
            paths = [entry["path"] for entry in manifest["files"]]
            require(
                len(set(p.casefold() for p in paths)) == len(paths) and MARKER not in paths,
                "invalid_media_package",
                409,
            )
            self.verify_files(source, manifest)
            relative = (
                single_directory(manifest["bvid"], manifest["selected_parts"][0]["cid"])
                if manifest["layout"] == "single"
                else multipart_directory(manifest["bvid"])
            )
            prefix = subdirectory(package.get("destination_subdirectory", ""))
            if prefix:
                relative = prefix + "/" + relative
            destination = safe_path(root, relative)
            base_id = package.get("base_operation_id")
            if base_id:
                base = self.operation(target, base_id)
                require(
                    base["version"] == package.get("base_version")
                    and base["status"] == "indexed"
                    and base["relative"] == relative,
                    "version_conflict",
                    409,
                )
                self.verify_owned(destination, base)
                old_video = {f["cid"]: f for f in base["manifest"]["files"] if f["kind"] == "video"}
                new_video = {f["cid"]: f for f in manifest["files"] if f["kind"] == "video"}
                require(set(old_video) <= set(new_video), "source_cid_removed", 409)
                replace = set(package.get("replace_video_cids", []))
                require(
                    all(
                        cid in replace or new_video[cid] == record
                        for cid, record in old_video.items()
                    ),
                    "source_changed",
                    409,
                )
            else:
                require(not destination.exists(), "target_exists", 409)
            operation = dict(
                request_digest=request_digest,
                operation_id=operation_id,
                target_id=target["target_id"],
                directory=str(root),
                library_id=target["library_id"],
                relative=relative,
                status="prepared",
                version=1,
                manifest_digest=package["manifest_digest"],
                manifest=manifest,
                base_operation_id=base_id,
                confirmation_digest=package["manifest_digest"],
                receipt=None,
            )
            return self.repo.put(KIND, operation_id, operation, 0)

    def verify_owned(self, directory, operation):
        require(self.matches(directory, operation), "target_exists", 409)
        self.verify_files(directory, operation["manifest"])
        allowed = {entry["path"] for entry in operation["manifest"]["files"]} | {MARKER}
        # Refuse to replace a directory to which somebody added files or links.
        found = set()
        for path in directory.rglob("*"):
            require(not path.is_symlink() and not path.is_junction(), "unsafe_library_path", 409)
            if path.is_file():
                found.add(path.relative_to(directory).as_posix())
        require(found == allowed, "target_exists", 409)

    def reconcile(self, root, operation):
        destination = safe_path(root, operation["relative"])
        if (
            operation["status"] == "publishing"
            and destination.exists()
            and self.matches(destination, operation)
        ):
            self.verify_owned(destination, operation)
            manifest = operation["manifest"]
            receipt = {
                "operation_id": operation["operation_id"],
                "package_id": manifest["package_id"],
                "library_id": operation["library_id"],
                "target_relative_path": operation["relative"],
                "files": manifest["files"],
                "storage": "server_directory",
                "video_storage": VideoStorage(safe_path(root, ".tianshu-transfer")).observe(
                    destination, manifest
                ),
            }
            return self.save(
                operation, status="indexed", version=operation["version"] + 1, receipt=receipt
            )
        return operation

    def read(self, target, identity):
        with library_lock(target["directory"]) as (root, _):
            return self.reconcile(root, self.operation(target, identity))

    def publish(self, target, supplied):
        with library_lock(target["directory"]) as (root, control):
            operation = self.reconcile(root, self.operation(target, supplied["operation_id"]))
            if operation["status"] == "indexed":
                return operation
            require(operation["status"] in {"prepared", "publishing"}, "version_conflict", 409)
            require(
                operation["confirmation_digest"] == supplied["confirmation_digest"],
                "version_conflict",
                409,
            )
            if operation["status"] == "prepared":
                require(operation["version"] == supplied["version"], "version_conflict", 409)
                operation = self.save(
                    operation, status="publishing", version=operation["version"] + 1
                )
            manifest = operation["manifest"]
            source = safe_path(self.staging, manifest["staging_ref"])
            destination = safe_path(root, operation["relative"])
            incoming = safe_path(control, "incoming-" + operation["operation_id"])
            previous = safe_path(control, "previous-" + operation["operation_id"])
            self.verify_files(source, manifest)
            incoming.mkdir(exist_ok=True)
            videos = VideoStorage(control)
            for entry in manifest["files"]:
                src = safe_path(source, entry["path"])
                dst = safe_path(incoming, entry["path"])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if entry["kind"] == "video":
                    videos.install(src, dst, entry)
                    continue
                # Retry never truncates a linked source inode; replace the incoming file.
                if dst.exists():
                    dst.unlink()
                copy_file(src, dst)
            self.verify_files(incoming, manifest)
            marker = safe_path(incoming, MARKER)
            with marker.open("w", encoding="utf-8") as out:
                json.dump(
                    {
                        "operation_id": operation["operation_id"],
                        "manifest_digest": operation["manifest_digest"],
                    },
                    out,
                )
                out.flush()
                os.fsync(out.fileno())
            self.verify_owned(incoming, operation)
            for directory in [p for p in incoming.rglob("*") if p.is_dir()] + [incoming]:
                sync_directory(directory)
            if operation["base_operation_id"]:
                base = self.operation(target, operation["base_operation_id"])
                if destination.exists():
                    self.verify_owned(destination, base)
                    require(not previous.exists(), "target_exists", 409)
                    destination.rename(previous)
                    sync_directory(destination.parent)
                    sync_directory(root)
                    sync_directory(control)
                else:
                    # Resume the gap between moving the old directory and installing the new one.
                    self.verify_owned(previous, base)
            else:
                require(not destination.exists(), "target_exists", 409)
            destination.parent.mkdir(parents=True, exist_ok=True)
            incoming.rename(destination)
            parent = destination.parent
            while True:
                sync_directory(parent)
                if parent == root:
                    break
                parent = parent.parent
            sync_directory(control)
            return self.reconcile(root, operation)

    def cancel(self, target, supplied):
        with library_lock(target["directory"]) as (root, _):
            operation = self.reconcile(root, self.operation(target, supplied["operation_id"]))
            if operation["status"] == "prepared":
                operation = self.save(
                    operation, status="cancelled", version=operation["version"] + 1
                )
            # Never claim a partly or fully installed publication was rolled back.
            return operation

    def deduplicate(self, target, identity):
        """Convert one current, receipted Tianshu publication; no discovery or job edits."""
        with library_lock(target["directory"]) as (root, control):
            operation = self.operation(target, identity)
            require(
                operation["status"] == "indexed" and operation.get("receipt"),
                "publication_not_indexed",
                409,
            )
            destination = safe_path(root, operation["relative"])
            self.verify_owned(destination, operation)
            videos = VideoStorage(control)
            for entry in operation["manifest"]["files"]:
                if entry["kind"] == "video":
                    path = safe_path(destination, entry["path"])
                    videos.install(path, path, entry, preserve_copy=True)
            self.verify_owned(destination, operation)
            storage = videos.observe(destination, operation["manifest"])
            if operation["receipt"].get("video_storage") != storage:
                operation = self.save(
                    operation, receipt={**operation["receipt"], "video_storage": storage}
                )
            return {
                "operation_id": identity,
                "video_storage": storage,
                "receipt": operation["receipt"],
            }


class Publications:
    """Keep existing remote operations compatible; direct transfers never call AssetLibrary."""

    def __init__(self, remote, organizer, run_local):
        self.remote, self.organizer, self.run_local = remote, organizer, run_local

    async def local(self, operation, *args):
        while True:
            try:
                return await self.run_local(operation, *args)
            except Fault as error:
                if error.code != "busy":
                    raise
                # Root-level filesystem ownership is shared by independent directories.
                # Wait cancellably while the worker heartbeat maintains its job lease.
                await asyncio.sleep(0.1)

    async def prepare(self, target, package, identity):
        if target.get("kind") == "media_server":
            return await self.local(self.organizer.prepare, target, package, identity)
        return await self.remote.prepare(target, package, identity)

    async def read(self, target, identity):
        if target.get("kind") == "media_server":
            return await self.run_local(self.organizer.read, target, identity)
        return await self.remote.read(target, identity)

    async def publish(self, target, operation):
        if target.get("kind") == "media_server":
            return await self.local(self.organizer.publish, target, operation)
        return await self.remote.publish(target, operation)

    async def cancel(self, target, operation):
        if target.get("kind") == "media_server":
            return await self.run_local(self.organizer.cancel, target, operation)
        return await self.remote.cancel(target, operation)
