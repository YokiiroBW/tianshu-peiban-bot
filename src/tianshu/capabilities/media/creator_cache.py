"""MID-owned avatar cache in Platform data, independent of media staging retention."""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

from ..contracts import Fault, require
from .bilibili import cover_url
from .creators import named_uploaders
from .engine import validate_image
from .image_fetch import fetch_image
from .identity import require_mid
from .organizer import library_lock
from .video_storage import sync_directory


def saved_url(record):
    """Retry intentions retain the most recent URL even without a new job source."""
    if not record:
        return None
    return cover_url(record.get("url")) or cover_url((record.get("previous") or {}).get("url"))


class CreatorCache:
    def __init__(self, media, directory):
        self.media, self.directory = media, Path(directory)

    def read(self, record):
        path = Path(record["file"])
        require(path.is_relative_to(self.directory), "creator_avatar_changed", 409)
        require(
            not any(value.is_symlink() or value.is_junction() for value in (path, *path.parents)),
            "creator_avatar_changed",
            409,
        )
        require(path.stat().st_size <= 32 * 1024**2, "creator_avatar_changed", 409)
        raw = path.read_bytes()
        require(
            len(raw) == record["size_bytes"]
            and hashlib.sha256(raw).hexdigest() == record["sha256"]
            and validate_image(raw) == record["extension"],
            "creator_avatar_changed",
            409,
        )
        return raw

    def save(self, root, raw, extension):
        hashed = hashlib.sha256(raw).hexdigest()
        path = root / (hashed + "." + extension)
        temporary = root / (".avatar-" + uuid.uuid4().hex)
        try:
            with temporary.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            sync_directory(root)
        finally:
            temporary.unlink(missing_ok=True)
        return {"file": str(path), "sha256": hashed, "size_bytes": len(raw), "extension": extension}

    async def obtain(self, mid, url):
        mid = require_mid(mid)
        lock = library_lock(self.directory / mid)
        tasks = set()

        async def local(operation, *args):
            task = asyncio.create_task(self.media.local(operation, *args))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return await asyncio.shield(task)

        acquisition = asyncio.create_task(self.media.local(lock.__enter__))
        try:
            root, _ = await asyncio.shield(acquisition)
        except asyncio.CancelledError:

            def release_acquired(task):
                try:
                    task.result()
                except BaseException:
                    return
                lock.__exit__(None, None, None)

            acquisition.add_done_callback(release_acquired)
            raise
        try:
            previous = await local(self.media.repo.get, "creator_avatar", mid)
            url = cover_url(url)
            if url is None:
                url = saved_url(previous)
            if (
                previous
                and previous["state"] == "ready"
                and previous["url"] == url
                and self.media.repo.clock() - previous.get("fetched_at", 0) < 86400
            ):
                try:
                    await local(self.read, previous)
                    return previous
                except (Fault, OSError):
                    pass
            intent = {"mid": mid, "url": url, "state": "retry", "code": "creator_avatar_pending"}
            if previous:
                intent["previous"] = (
                    previous if previous["state"] == "ready" else previous.get("previous")
                )
            await local(self.media.repo.put, "creator_avatar", mid, intent)
            try:
                require(url is not None, "creator_avatar_source_missing", 503)
                raw = await fetch_image(self.media.connector, url)
                extension = await local(validate_image, raw)
                observed = await local(self.save, root, raw, extension)
                record = {
                    "mid": mid,
                    "url": url,
                    "state": "ready",
                    "code": "creator_avatar_ready",
                    "fetched_at": self.media.repo.clock(),
                    **observed,
                }
            except (Fault, OSError) as error:
                code = error.code if isinstance(error, Fault) else "creator_avatar_unavailable"
                record = {
                    **intent,
                    "code": "creator_avatar_invalid"
                    if code == "cover_invalid"
                    else "creator_avatar_source_missing"
                    if code == "creator_avatar_source_missing"
                    else "creator_avatar_unavailable",
                }
            return await local(self.media.repo.put, "creator_avatar", mid, record)
        finally:
            remaining = set(tasks)
            if remaining:

                def finished(task):
                    try:
                        task.result()
                    except BaseException:
                        pass
                    remaining.discard(task)
                    if not remaining:
                        lock.__exit__(None, None, None)

                for task in tuple(remaining):
                    task.add_done_callback(finished)
            else:
                lock.__exit__(None, None, None)

    async def prepare(self, job):
        sources = {entry["mid"]: entry.get("url") for entry in job.get("creator_images", [])}
        results = []
        for creator in named_uploaders(job["snapshot"]):
            try:
                result = await self.obtain(creator["mid"], sources.get(creator["mid"]))
            except Fault as error:
                result = {
                    "state": "retry",
                    "code": "creator_avatar_pending" if error.code == "busy" else error.code,
                }
            results.append(
                {
                    **result,
                    "mid": creator["mid"],
                    "name": creator["name"],
                    "director_name": creator["director_name"],
                }
            )
        return results
