"""Per-page resumable snapshots, lossless packaging and two independent receipts."""

import asyncio
import uuid

from ...contracts import Fault, digest, require
from ..destination import safe_path
from .archive import Archive
from .packages import (
    MAX_CHAPTER_BYTES,
    extract_images,
    image_record,
    package_cbz,
)
from .publication import file_hash, publish
from .publisher_client import Publisher

RETRYABLE = {
    "comic_engine_timeout",
    "comic_source_dns_failed",
    "comic_source_timeout",
    "comic_source_server_error",
    "comic_source_image_failed",
    "comic_source_connection_refused",
    "comic_source_unavailable",
    "comic_engine_unavailable",
    "comic_source_rate_limited",
    "comic_archive_unavailable",
    "comic_archive_pending",
    "comic_publisher_unavailable",
    "comic_input_service_unavailable",
    "comic_komga_unavailable",
    "comic_reader_index_pending",
    "busy",
}
PENDING = {"comic_archive_pending", "comic_reader_index_pending"}


class Downloads:
    def __init__(self, comics):
        self.comics, self.media, self.repo = comics, comics.media, comics.repo
        self.archive = Archive(comics)
        self.publisher = Publisher()

    async def close(self):
        await self.archive.close()
        await self.publisher.close()

    def cleanup(self, job):
        if job.get("state") != "completed" or job.get("comic_cache_cleaned"):
            return
        require(
            job.get("asset_receipt")
            and any(r.get("state") == "verified" for r in job.get("library_results", [])),
            "comic_archive_pending",
            409,
        )
        require(str(uuid.UUID(job["job_id"])) == job["job_id"], "invalid_input", 400)
        folder = safe_path(self.comics.data_root, job["job_id"])
        if folder.exists():
            paths = list(folder.rglob("*"))
            for path in paths:
                safe_path(self.comics.data_root, path.relative_to(self.comics.data_root).as_posix())
            for path in paths:
                if path.is_file():
                    path.unlink()
            for path in sorted(
                (p for p in paths if p.is_dir()), key=lambda p: len(p.parts), reverse=True
            ):
                path.rmdir()
            folder.rmdir()
        self.repo.update_job(job["job_id"], {"comic_cache_cleaned": True, "comic_cache": []})

    async def clean_completed(self):
        while True:
            for job in await self.media.local(self.repo.retention_jobs):
                if job.get("media_kind") == "comic":
                    try:
                        await self.media.local(self.cleanup, job)
                    except (Fault, OSError):
                        pass
            await asyncio.sleep(60)

    async def run(self, job, owner):
        identity, generation = job["job_id"], job["generation"]
        running = asyncio.current_task()

        async def update(**changes):
            nonlocal job
            job = await self.media.local(
                self.repo.update_job, identity, changes, owner=owner, generation=generation
            )
            return job

        async def checkpoint():
            await self.media.local(self.repo.heartbeat, identity, owner, generation)
            current = await self.media.local(self.comics.settings.target, job["comic_library_kind"])
            require(current == job["comic_target"], "comic_target_changed", 409)

        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                try:
                    await self.media.local(self.repo.heartbeat, identity, owner, generation)
                except Fault:
                    running.cancel()
                    return

        pulse = asyncio.create_task(heartbeat())
        comic, health_stage = None, None
        try:
            await checkpoint()
            comic = await self.media.local(self.comics.catalog.comic, job["comic_id"])
            if not job.get("comic_artifact"):
                health_stage = "download"
                pages = await self.pages(comic, job, update, checkpoint, owner)
                health_stage = "package"
                await update(state="validating", stage="comic_package", code="comic_packaging")
                package_relative = identity + f"/{generation}/chapter.cbz"
                path = safe_path(self.comics.data_root, package_relative)
                record = await self.media.local(
                    package_cbz, pages, path, job["comic_metadata"], job["comic_chapter"]
                )
                await checkpoint()
                # Versioned filenames preserve existing bytes and make publication immutable.
                relative = (
                    job["comic_relative_path"].removesuffix(".cbz")
                    + "-"
                    + record["sha256"][:12]
                    + ".cbz"
                )
                key = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        digest(
                            {
                                "protocol": "service-imports-v1",
                                "library_id": job["comic_target"]["asset_library_id"],
                                "sha256": record["sha256"],
                                "target_id": job["comic_target"]["asset_target_id"],
                                "path": relative,
                            }
                        ),
                    )
                )
                artifact = {k: record[k] for k in ("sha256", "size_bytes", "page_count")}
                artifact.update(
                    archive_key=key,
                    package_relative=package_relative,
                    metadata_digest=digest(job["comic_metadata"]),
                )
                await update(comic_artifact=artifact, comic_relative_path=relative)
                await self.media.local(
                    self.comics.health.record,
                    comic,
                    "package",
                    "ready",
                    facts={
                        "job_id": identity,
                        "page_count": record["page_count"],
                        "library_kind": job["comic_library_kind"],
                    },
                )
            health_stage = "archive"
            archive_cached = bool(job.get("asset_receipt"))
            if not job.get("asset_receipt"):
                path = safe_path(self.comics.data_root, job["comic_artifact"]["package_relative"])
                require(
                    path.is_file()
                    and path.stat().st_size == job["comic_artifact"]["size_bytes"]
                    and await self.media.local(file_hash, path) == job["comic_artifact"]["sha256"],
                    "comic_package_changed",
                    409,
                )
                await update(state="publishing", stage="comic_archive", code="comic_archiving")
                if not job["comic_artifact"].get("published_at"):
                    from ...tracking import mark_submitted
                    await mark_submitted(identity)
                    if job["comic_target"].get("publisher_socket"):
                        publication = await self.publisher.publish(job, path, checkpoint)
                    else:
                        publication = await self.media.local(publish, self.repo, job, path, owner)
                    await update(comic_artifact={**job["comic_artifact"], **publication})
                receipt = await self.archive.observe(job, checkpoint)
                await update(
                    state="asset_indexed",
                    stage="comic_indexed",
                    code="comic_archived",
                    asset_receipt=receipt,
                )
            await self.media.local(
                self.comics.health.record,
                comic,
                "archive",
                "ready",
                facts={
                    "job_id": identity,
                    "library_kind": job["comic_library_kind"],
                    "cached": archive_cached,
                },
            )
            health_stage = "reader"
            await update(
                state="library_verifying", stage="comic_reader", code="comic_reader_index_pending"
            )
            result = await self.comics.komga.verify(job)
            await checkpoint()
            await self.media.local(
                self.comics.health.record,
                comic,
                "reader",
                "ready",
                facts={"job_id": identity, "library_kind": job["comic_library_kind"]},
            )

            def finish():
                with self.repo.transaction() as db:
                    completed = self.repo.update_job(
                        identity,
                        {
                            "state": "completed",
                            "stage": "comic_ready",
                            "code": "comic_completed",
                            "progress": 1,
                            "library_results": [result],
                        },
                        owner=owner,
                        generation=generation,
                        db=db,
                    )
                    key = digest(
                        {"chapter": job["comic_chapter_id"], "kind": job["comic_library_kind"]}
                    )[:24]
                    self.repo.put(
                        "comic_artifact",
                        key,
                        {
                            "comic_id": job["comic_id"],
                            "chapter_id": job["comic_chapter_id"],
                            "library_kind": job["comic_library_kind"],
                            "version_key": job["comic_chapter"]["version_key"],
                            "job_id": identity,
                            "relative_path": job["comic_relative_path"],
                            "sha256": job["comic_artifact"]["sha256"],
                            "size_bytes": job["comic_artifact"]["size_bytes"],
                            "page_count": job["comic_artifact"]["page_count"],
                            "asset_receipt": job["asset_receipt"],
                            "komga": result,
                            "updated_at": self.repo.clock(),
                        },
                        db=db,
                    )
                    current = self.comics.catalog.comic(job["comic_id"], db)
                    count = db.execute(
                        "SELECT COUNT(*) FROM documents WHERE kind='comic_artifact' AND (body::jsonb #>> '{comic_id}')=%s AND (body::jsonb #>> '{library_kind}')=%s",
                        (job["comic_id"], current["library_kind"]),
                    ).fetchone()[0]
                    self.repo.put(
                        "comic",
                        job["comic_id"],
                        {**current, "downloaded": count, "updated_at": self.repo.clock()},
                        db=db,
                    )
                    return completed

            completed = await self.media.local(finish)
            try:
                await self.media.local(self.cleanup, completed)
            except (Fault, OSError):
                # Retention retries only private staging; publication receipts stay complete.
                pass
        except asyncio.CancelledError:
            current = await self.media.local(self.repo.job, identity)
            if current and current["generation"] == generation and current["state"] != "completed":
                await self.media.local(
                    self.repo.update_job,
                    identity,
                    {
                        "state": "cancelled" if current["cancel_requested"] else "queued",
                        "code": "user_cancelled"
                        if current["cancel_requested"]
                        else "comic_interrupted",
                    },
                    generation=generation,
                )
            raise
        except Fault as error:
            from ..recovery import DownloadDeferred
            if error.code == "lease_conflict":
                return
            if comic and health_stage and error.code != "job_cancelled":
                await self.media.local(
                    self.comics.health.record,
                    comic,
                    health_stage,
                    error.code,
                    facts={"job_id": identity, "library_kind": job["comic_library_kind"]},
                )
            deferred = isinstance(error, DownloadDeferred)
            attempts = job.get("attempts", 0) + (0 if deferred else 1)
            retry = deferred or error.code in RETRYABLE and (attempts < 6 or error.code in PENDING)
            await self.media.local(
                self.repo.update_job,
                identity,
                {
                    "state": "cancelled"
                    if error.code == "job_cancelled"
                    else "unknown"
                    if error.code == "media_download_original_unresolved"
                    else "retry_wait"
                    if retry
                    else "failed",
                    "stage": "comic_attention",
                    "code": error.code,
                    "attempts": attempts,
                    "retry_delay": error.retry_after if deferred else min(1800, 30 * 2 ** min(attempts, 6)),
                },
                owner=owner,
                generation=generation,
            )
        except OSError:
            if comic and health_stage:
                await self.media.local(
                    self.comics.health.record,
                    comic,
                    health_stage,
                    "comic_storage_unavailable",
                    facts={"job_id": identity, "library_kind": job["comic_library_kind"]},
                )
            await self.media.local(
                self.repo.update_job,
                identity,
                {
                    "state": "failed",
                    "stage": "comic_attention",
                    "code": "comic_storage_unavailable",
                },
                owner=owner,
                generation=generation,
            )
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)

    async def pages(self, comic, job, update, checkpoint, owner):
        if comic["source_type"] != "remote":
            return await self._pages(comic, job, update, checkpoint, owner)
        lock = self.comics.engine.source_locks.setdefault(comic["source_id"], asyncio.Lock())
        async with lock:
            result = await self._pages(comic, job, update, checkpoint, owner)
            await self.media.local(
                self.comics.health.record,
                comic,
                "download",
                "ready",
                facts={
                    "job_id": job["job_id"],
                    "page_count": len(result),
                    "library_kind": job["comic_library_kind"],
                },
            )
            return result

    async def _pages(self, comic, job, update, checkpoint, owner):
        chapter = job["comic_chapter"]
        cache = job.get("comic_cache") or []
        directory = safe_path(self.comics.data_root, job["job_id"] + "/" + str(job["generation"]))
        if comic["source_type"] == "remote":
            require(
                comic["engine_id"] == await self.comics.sources.engine_id(),
                "comic_source_changed",
                409,
            )
            source = next(
                (
                    s
                    for s in await self.comics.sources.list()
                    if s["source_id"] == comic["source_id"]
                ),
                None,
            )
            require(source and source["enabled"], "comic_source_disabled", 409)
            inputs = await self.comics.engine.pages(
                chapter["engine_chapter_id"], comic["engine_manga_id"]
            )
            fingerprint = digest(inputs)
        else:
            await self.media.local(self.comics.imports.recheck, comic, chapter)
            if chapter["input_kind"] == "archive":
                await update(stage="comic_pages", code="comic_unpacking")
                source_path = await self.media.local(
                    self.comics.imports.archive_path, comic, chapter, directory
                )
                pages = await self.media.local(extract_images, source_path, directory)
                await self.media.local(self.comics.imports.recheck, comic, chapter)
                await update(
                    comic_pages_done=len(pages), comic_pages_total=len(pages), progress=0.8
                )
                return pages
            inputs = chapter["input_files"]
            fingerprint = chapter["input_digest"]
        if job.get("comic_input_fingerprint") != fingerprint:
            cache = []
        await update(
            state="downloading",
            stage="comic_pages",
            code="comic_downloading",
            comic_pages_total=len(inputs),
            comic_input_fingerprint=fingerprint,
            comic_cache=cache,
        )
        pages, total = [], 0
        for index, source in enumerate(inputs):
            await checkpoint()
            entry = cache[index] if index < len(cache) else None
            record = None
            if entry:
                path = safe_path(self.comics.data_root, entry["relative"])
                try:
                    current = await self.media.local(image_record, path)
                    if current == entry["record"]:
                        record = current
                except (Fault, OSError):
                    pass
            if record is None:
                path = directory / f"{index:06d}.page"
                if comic["source_type"] == "remote":
                    from ..recovery import physical_download
                    async with physical_download(
                        self.media, job, owner, family="comic", material=[job["comic_chapter_id"], index],
                        route="comic:" + digest(comic["source_id"]),
                        request={"fingerprint": fingerprint, "index": index, "source": source},
                    ):
                        await self.comics.engine.download_page(source, path)
                        record = await self.media.local(image_record, path)
                        entry = {
                            "relative": path.relative_to(self.comics.data_root).as_posix(),
                            "record": record,
                        }
                        if index < len(cache):
                            cache[index] = entry
                        else:
                            cache.append(entry)
                        # Persist the actual page evidence before settling the
                        # physical attempt, so a restart can reuse those bytes.
                        await update(comic_cache=cache, comic_pages_done=len(pages) + 1,
                                     progress=0.8 * (len(pages) + 1) / len(inputs))
                else:
                    record = await self.media.local(
                        self.comics.imports.copy_image, comic, chapter, index, path
                    )
                    entry = {
                        "relative": path.relative_to(self.comics.data_root).as_posix(),
                        "record": record,
                    }
                    if index < len(cache):
                        cache[index] = entry
                    else:
                        cache.append(entry)
            total += record["size_bytes"]
            require(total <= MAX_CHAPTER_BYTES, "comic_chapter_too_large", 409)
            pages.append((path, record))
            await update(
                comic_cache=cache,
                comic_pages_done=len(pages),
                progress=0.8 * len(pages) / len(inputs),
            )
        if comic["source_type"] == "local":
            await self.media.local(self.comics.imports.recheck, comic, chapter)
        return pages
