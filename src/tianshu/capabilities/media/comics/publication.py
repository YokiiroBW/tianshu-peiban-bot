"""Publish immutable CBZ files under the registered, dedicated comic output root."""

import hashlib
import os
import uuid
from pathlib import Path

from ...contracts import require
from ..destination import safe_path
from ..organizer import library_lock
from ..video_storage import copy_file, sync_directory


def file_hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify(path, artifact):
    require(
        path.is_file()
        and path.stat().st_size == artifact["size_bytes"]
        and file_hash(path) == artifact["sha256"],
        "comic_file_conflict",
        409,
    )


def install(root, temporary, relative, artifact):
    """Install verified bytes while the caller owns library_lock and its job fence."""
    destination = safe_path(root, relative)
    if destination.exists():
        verify(destination, artifact)
        return True
    pending = []
    parent = destination.parent
    while not parent.exists():
        pending.append(parent)
        parent = parent.parent
    for folder in reversed(pending):
        folder.mkdir(exist_ok=True)
        sync_directory(folder.parent)
    safe_path(root, relative)
    try:
        os.link(temporary, destination)
    except FileExistsError:
        require(False, "comic_file_conflict", 409)
    sync_directory(destination.parent)
    return False


def publish(repo, job, source, owner):
    # Reuse the existing cross-process volume lock and relative-path protections.
    with library_lock(job["comic_target"]["directory"]) as (root, control):
        destination = safe_path(root, job["comic_relative_path"])
        if destination.exists():
            verify(destination, job["comic_artifact"])
            return {"reused": True, "published_at": repo.clock()}
        temporary = safe_path(control, ".comic-" + uuid.uuid4().hex)
        try:
            copy_file(source, temporary)
            verify(temporary, job["comic_artifact"])
            # Fence the final filesystem change with the same job generation as the worker.
            with repo.transaction() as db:
                row = db.execute(
                    'SELECT owner,generation,body FROM jobs WHERE id=%s', (job["job_id"],)
                ).fetchone()
                import json

                require(
                    row and row["owner"] == owner and row["generation"] == job["generation"],
                    "lease_conflict",
                    409,
                )
                require(not json.loads(row["body"])["cancel_requested"], "job_cancelled", 409)
                reused = install(root, temporary, job["comic_relative_path"], job["comic_artifact"])
            return {"reused": reused, "published_at": repo.clock()}
        finally:
            temporary.unlink(missing_ok=True)
            sync_directory(control)
