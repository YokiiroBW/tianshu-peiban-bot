"""Validated global BV/CID/actual-quality sources with fenced, process-safe ownership."""

import asyncio
from pathlib import Path

from ..contracts import Fault, digest, require
from .engine import file_record
from .organizer import library_lock
from .stream_policy import constrained, validate_policy


def validate_download(download, cid, quality, requested=None):
    require(download.get("actual_quality") == quality, "quality_mismatch", 503)
    path = Path(download["file"])
    require(
        not any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)),
        "download_cache_changed",
        409,
    )
    require(path.suffix == "." + download["extension"], "download_cache_changed", 409)
    record = file_record(path, "cache", "video", cid)
    require(
        record["size_bytes"] > 0
        and record["sha256"] == download.get("sha256")
        and record["size_bytes"] == download.get("size_bytes"),
        "download_cache_changed",
        409,
    )
    return dict(validate_policy(download, requested)) if requested else dict(download)


class DownloadCache:
    def __init__(self, media):
        self.media = media
        if not hasattr(media, "retention"):
            from .retention import Retention

            media.retention = Retention(media)

    async def selected_quality(self, job, part, cookie):
        requested = job["quality"]
        if requested["mode"] == "exact" and not requested["allow_fallback"]:
            return requested["quality_id"]
        directory = (
            Path(self.media.config["staging_directory"])
            / ".probes"
            / digest({"job": job["job_id"], "cid": part["cid"], "generation": job["generation"]})
        )
        kwargs = {"requested": requested} if constrained(requested) else {}
        formats = await self.media.engine.formats(job["bvid"], part, cookie, directory, **kwargs)
        available = {item["quality_id"] for item in formats}
        require(available, "quality_unavailable", 503)
        if requested["mode"] == "exact" and requested["quality_id"] in available:
            return requested["quality_id"]
        require(
            requested["mode"] != "exact" or requested["allow_fallback"], "quality_unavailable", 503
        )
        return max(available, key=int)

    async def obtain(self, job, part, cookie, owner, quality):
        media, repo = self.media, self.media.repo
        bvid, cid = job["bvid"], part["cid"]
        key = (bvid, cid, quality)
        from .retention import source_directory

        directory = source_directory(media.config["staging_directory"], bvid, cid, quality)
        while True:
            await media.local(repo.heartbeat, job["job_id"], owner, job["generation"])
            lock = library_lock(directory)
            acquisition = asyncio.create_task(media.local(lock.__enter__))
            try:
                await asyncio.shield(acquisition)
            except asyncio.CancelledError:
                # LocalWork may already be opening the descriptor off-loop. Close it
                # after that operation finishes even when this caller no longer waits.
                def release_acquired(task):
                    try:
                        task.result()
                    except BaseException:
                        return
                    lock.__exit__(None, None, None)

                acquisition.add_done_callback(release_acquired)
                raise
            except Fault as error:
                if error.code != "busy":
                    raise
                await asyncio.sleep(0.25)
                continue
            pulse = None
            finished = True
            generation = None
            pending_file_work = None

            async def locked_file_work(operation, *args, **kwargs):
                nonlocal pending_file_work
                pending_file_work = asyncio.create_task(media.local(operation, *args, **kwargs))
                # Caller cancellation cannot unlock a file operation that is still
                # copying/hashing in LocalWork's thread pool.
                return await asyncio.shield(pending_file_work)

            try:
                # Lock first: an expired database lease cannot steal from a process
                # that is still downloading. Crashed processes release the OS lock.
                try:
                    retained = await locked_file_work(media.retention.source, *key, job=job)
                except Fault as error:
                    if error.code != "busy":
                        raise
                    await asyncio.sleep(0.25)
                    continue
                if retained:
                    return retained, True
                claim = await media.local(repo.download_claim, *key, owner)
                if claim["state"] == "waiting":
                    await asyncio.sleep(0.25)
                    continue
                generation = claim["generation"]
                if claim["state"] == "ready":
                    try:
                        require(
                            Path(claim["download"]["file"]).is_relative_to(
                                Path(media.config["staging_directory"])
                            ),
                            "download_cache_changed",
                            409,
                        )
                        download = await locked_file_work(
                            validate_download, claim["download"], cid, quality
                        )
                        # Keep incompatible valid bytes as another source under the
                        # same QN lock; replacing the current pointer never rewrites them.
                        try:
                            validate_policy(download, job["quality"])
                        except Fault:
                            await media.local(repo.remember_download, *key, download)
                            raise
                        return await locked_file_work(media.retention.pin, job, cid, download), True
                    except (Fault, OSError, KeyError, ValueError):
                        await media.local(repo.download_invalidate, *key, generation)
                        continue
                finished = False
                current_task = asyncio.current_task()

                async def heartbeat():
                    while True:
                        await asyncio.sleep(5)
                        try:
                            await media.local(
                                repo.heartbeat, job["job_id"], owner, job["generation"]
                            )
                            await media.local(repo.download_heartbeat, *key, owner, generation)
                        except Fault:
                            current_task.cancel()
                            return

                pulse = asyncio.create_task(heartbeat())
                # Older completed, interrupted and publication-failed jobs can all supply
                # a source, but only a matching, fully hashed download is accepted.
                for previous in await media.local(repo.cached_downloads, bvid, cid):
                    try:
                        require(
                            Path(previous["file"]).is_relative_to(
                                Path(media.config["staging_directory"])
                            ),
                            "download_cache_changed",
                            409,
                        )
                        download = await locked_file_work(
                            validate_download, previous, cid, quality, job["quality"]
                        )
                    except (Fault, OSError, KeyError, ValueError):
                        continue
                    await media.local(repo.download_finish, *key, owner, generation, download)
                    finished = True
                    return await locked_file_work(media.retention.pin, job, cid, download), True
                await media.engine.check()
                require(media.engine.status["state"] == "ready", media.engine.status["code"], 503)
                before = await media.connector.resolve(bvid, cookie, with_formats=False)
                mapping = {value["cid"]: value for value in before["snapshot"]["parts"]}
                require(cid in mapping, "source_cid_removed", 409)
                exact_job = {
                    **job,
                    "quality": {
                        **job["quality"],
                        "mode": "exact",
                        "quality_id": quality,
                        "allow_fallback": False,
                    },
                }
                download = await media.engine.download(
                    exact_job, mapping[cid], cookie, directory / str(generation)
                )
                require(download["actual_quality"] == quality, "quality_mismatch", 503)
                after = await media.connector.resolve(bvid, cookie, with_formats=False)
                require(
                    [(p["cid"], p["index"]) for p in before["snapshot"]["parts"]]
                    == [(p["cid"], p["index"]) for p in after["snapshot"]["parts"]],
                    "source_identity_changed",
                    409,
                )
                record = await locked_file_work(
                    file_record, Path(download["file"]), "cache", "video", cid
                )
                download.update(
                    sha256=record["sha256"],
                    size_bytes=record["size_bytes"],
                    source_index=mapping[cid]["index"],
                    source_captured_at=before["snapshot"]["captured_at"],
                )
                download = await locked_file_work(
                    validate_download, download, cid, quality, job["quality"]
                )
                await media.local(repo.heartbeat, job["job_id"], owner, job["generation"])
                await media.local(repo.download_finish, *key, owner, generation, download)
                finished = True
                return await locked_file_work(media.retention.pin, job, cid, download), False
            finally:
                try:
                    if pulse:
                        pulse.cancel()
                        await asyncio.gather(pulse, return_exceptions=True)
                    if not finished:
                        try:
                            await media.local(repo.download_finish, *key, owner, generation)
                        except Fault:
                            pass
                finally:
                    # Unlock is a bounded descriptor operation. A second cancellation
                    # cannot leave the live process holding this download indefinitely.
                    if pending_file_work and not pending_file_work.done():

                        def release_after_files(task):
                            try:
                                task.result()
                            except BaseException:
                                pass
                            finally:
                                lock.__exit__(None, None, None)

                        pending_file_work.add_done_callback(release_after_files)
                    else:
                        lock.__exit__(None, None, None)
