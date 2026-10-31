"""Persist verified media sources before reclaiming precisely owned staging files."""

import asyncio
from pathlib import Path

from ..contracts import Fault, digest, require
from .destination import safe_path
from .engine import file_record
from .organizer import library_lock
from .video_storage import copy_file, sync_directory, try_link, verify_video
from .stream_policy import validate_policy


def source_directory(staging, bvid, cid, quality):
    return safe_path(
        Path(staging), ".downloads/global/" + digest({"bvid": bvid, "cid": cid, "quality": quality})
    )


class Retention:
    def __init__(self, media):
        self.media = media

    def pin(self, job, cid, download):
        """Called under the source OS lock: a caller never returns an unowned input."""
        path = safe_path(
            Path(self.media.config["staging_directory"]),
            f".inputs/{job['job_id']}/{job['generation']}/{cid}.{download['extension']}",
        )
        identity = digest({"job": job["job_id"], "file": str(path)})
        result = {**download, "file": str(path)}
        ownership = {"job_id": job["job_id"], "generation": job["generation"], "cid": cid, **result}
        # Persist the exact reserved path before a copy can leave partial bytes on crash.
        self.media.repo.put("media_input", identity, {**ownership, "state": "prepared"})
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            actual = file_record(path, "cache", "video", cid)
            if (actual["sha256"], actual["size_bytes"]) != (
                download["sha256"],
                download["size_bytes"],
            ):
                path.unlink()
        if not path.exists():
            if not try_link(Path(download["file"]), path):
                copy_file(Path(download["file"]), path)
        actual = file_record(path, "cache", "video", cid)
        require(
            (actual["sha256"], actual["size_bytes"])
            == (download["sha256"], download["size_bytes"]),
            "download_cache_changed",
            409,
        )
        sync_directory(path.parent)
        self.media.repo.put(
            "media_input",
            identity,
            {**ownership, "state": "ready"},
        )
        return result

    def source(self, bvid, cid, quality, job=None):
        for candidate in self.media.repo.media_sources(bvid, cid, quality):
            if candidate.get("kind") == "import":
                try:
                    download = self.media.imports.source(candidate, None)
                    record = self.media.repo.get("media_import", candidate["import_record_id"])
                    require(
                        record
                        and record["revision"] == candidate["import_record_revision"]
                        and record["sha256"] == download["sha256"],
                        "import_source_changed",
                        409,
                    )
                    download["media"] = record.get("media")
                    validate_policy(download, job["quality"] if job else {})
                    return self.pin(job, cid, download) if job else download
                except (Fault, OSError, KeyError, ValueError):
                    continue
            target = self.media.config.get("targets", {}).get(candidate["target_id"])
            # Removed/repointed mounts cannot remain implicit read authority.
            if (
                not target
                or target.get("kind") != "media_server"
                or target.get("directory") != candidate["directory"]
                or target.get("library_id") != candidate["library_id"]
            ):
                continue
            try:
                require(Path(target["directory"]).is_dir(), "source_missing", 409)
                with library_lock(target["directory"]) as (root, _):
                    operation = self.media.publisher.organizer.operation(
                        target, candidate["operation_id"]
                    )
                    require(operation["status"] == "indexed", "source_missing", 409)
                    destination = safe_path(root, operation["relative"])
                    require(
                        self.media.publisher.organizer.matches(destination, operation),
                        "source_changed",
                        409,
                    )
                    entry = candidate["entry"]
                    require(
                        entry in operation["manifest"]["files"]
                        and operation["manifest"]["bvid"] == bvid,
                        "source_changed",
                        409,
                    )
                    path = safe_path(destination, entry["path"])
                    verify_video(path, entry)
                    download = {
                        "file": str(path),
                        "actual_quality": quality,
                        "extension": path.suffix[1:],
                        "sha256": entry["sha256"],
                        "size_bytes": entry["size_bytes"],
                        "media": candidate.get("media"),
                        "stream_evidence": candidate.get("stream_evidence"),
                        "source": {
                            "kind": "library",
                            "target_id": target["target_id"],
                            "directory": str(root),
                            "operation_id": operation["operation_id"],
                            "file": str(path),
                            "sha256": entry["sha256"],
                            "size_bytes": entry["size_bytes"],
                        },
                    }
                    validate_policy(download, job["quality"] if job else {})
                    return self.pin(job, cid, download) if job else download
            except Fault as error:
                if error.code == "busy":
                    raise
            except (OSError, KeyError, ValueError):
                pass
        return None

    def register(self, job):
        target = self.media.config["targets"].get(job["target_id"])
        require(target and target.get("kind") == "media_server", "retention_not_supported", 409)
        require(
            job["state"] == "completed"
            and job.get("library_results")
            and all(r["state"] == "verified" for r in job["library_results"]),
            "publication_unverified",
            409,
        )
        organizer = self.media.publisher.organizer
        require(Path(target["directory"]).is_dir(), "source_missing", 409)
        with library_lock(target["directory"]) as (root, _):
            operation = organizer.operation(target, job["asset_receipt"]["operation_id"])
            require(
                operation["status"] == "indexed"
                and operation.get("receipt")
                and operation["receipt"]["files"] == job["asset_receipt"]["files"],
                "publication_unverified",
                409,
            )
            require(operation["manifest"]["bvid"] == job["bvid"], "source_changed", 409)
            destination = safe_path(root, operation["relative"])
            marker = organizer.marker(destination)
            require(marker is not None, "source_changed", 409)
            current = organizer.operation(target, marker["operation_id"])
            require(
                current["status"] == "indexed"
                and current["relative"] == operation["relative"]
                and organizer.matches(destination, current),
                "source_changed",
                409,
            )
            candidates = []
            for entry in operation["manifest"]["files"]:
                if entry["kind"] != "video":
                    continue
                require(entry in current["manifest"]["files"], "source_changed", 409)
                verify_video(safe_path(destination, entry["path"]), entry)
                download = job["downloads"][entry["cid"]]
                require(
                    (download["sha256"], download["size_bytes"])
                    == (entry["sha256"], entry["size_bytes"]),
                    "source_changed",
                    409,
                )
                candidates.append(
                    {
                        "bvid": job["bvid"],
                        "cid": entry["cid"],
                        "actual_quality": download["actual_quality"],
                        "target_id": target["target_id"],
                        "directory": str(root),
                        "library_id": target["library_id"],
                        "operation_id": current["operation_id"],
                        "entry": entry,
                        "media": download.get("media"),
                        "stream_evidence": download.get("stream_evidence"),
                    }
                )
            # These transactions commit before any cache file is removed.
            for candidate in candidates:
                identity = digest(
                    {
                        key: candidate[key]
                        for key in (
                            "bvid",
                            "cid",
                            "actual_quality",
                            "target_id",
                            "directory",
                            "operation_id",
                        )
                    }
                )
                self.media.repo.put("media_source", identity, candidate)
        return operation["manifest"]

    def owned_download(self, path, job, cid, quality):
        root = Path(self.media.config["staging_directory"])
        try:
            relative = path.absolute().relative_to(root.absolute()).as_posix()
        except ValueError:
            return None
        safe = safe_path(root, relative)
        parts = relative.split("/")
        global_root = source_directory(root, job["bvid"], cid, quality)
        if safe.is_relative_to(global_root) and len(parts) == 5 and parts[3].isdigit():
            return safe
        # Before the global cache, Engine used .downloads/<job>/<generation>/<CID>.
        if (
            len(parts) == 5
            and parts[:2] == [".downloads", job["job_id"]]
            and parts[2].isdigit()
            and parts[3] == cid
        ):
            return safe
        if len(parts) == 4 and parts[:2] == [".inputs", job["job_id"]] and parts[2].isdigit():
            return safe
        return None

    def remove_download(self, download, job, cid, quality):
        path = self.owned_download(Path(download["file"]), job, cid, quality)
        if path is None:
            return True  # A library source or unknown legacy path is never reclaimed.
        if self.media.repo.staging_referenced(str(path)):
            return False
        if path.exists():
            if download.get("state") != "prepared":
                record = file_record(path, "cache", "video", cid)
                require(
                    (record["sha256"], record["size_bytes"])
                    == (download["sha256"], download["size_bytes"]),
                    "download_cache_changed",
                    409,
                )
            path.unlink()
            sync_directory(path.parent)
            parent = path.parent
            while parent != Path(self.media.config["staging_directory"]):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        return True

    @staticmethod
    def retained_content(job, cid, download, inputs):
        """Receipt-verified output plus a durably recorded stream-copy parent."""
        content = {(download["sha256"], download["size_bytes"])}
        source = download.get("remux_source")
        if source:
            require(
                source.get("kind") == "ffmpeg_av_stream_copy"
                and source.get("extension") == "mp4"
                and download["extension"] == "mkv"
                and any(
                    record.get("state") == "ready"
                    and record.get("generation") == job["generation"]
                    and record.get("cid") == cid
                    and record.get("remux_source") == source
                    and all(
                        record.get(key) == download.get(key)
                        for key in ("file", "sha256", "size_bytes", "actual_quality", "extension")
                    )
                    for record in inputs
                ),
                "source_changed",
                409,
            )
            content.add((source["sha256"], source["size_bytes"]))
        return content

    def cleanup(self, job):
        manifest = self.register(job)
        complete = True
        inputs = [
            record
            for record in self.media.repo.job_inputs(job["job_id"])
            if record.get("generation", int(Path(record["file"]).parent.name)) <= job["generation"]
        ]
        for cid, download in job.get("downloads", {}).items():
            quality = download["actual_quality"]
            with library_lock(
                source_directory(self.media.config["staging_directory"], job["bvid"], cid, quality)
            ):
                # Recheck durable bytes immediately before reclaiming their source cache.
                require(self.source(job["bvid"], cid, quality) is not None, "source_missing", 409)
                content = self.retained_content(job, cid, download, inputs)
                cache = self.media.repo.download_source(job["bvid"], cid, quality)
                if (
                    cache
                    and (cache["download"].get("sha256"), cache["download"].get("size_bytes"))
                    in content
                ):
                    removed = self.remove_download(cache["download"], job, cid, quality)
                    complete = complete and removed
                    if removed and not Path(cache["download"]["file"]).exists():
                        self.media.repo.download_invalidate(
                            job["bvid"], cid, quality, cache["generation"]
                        )
                # Only the exact receipted output or its proven stream-copy
                # parent is reclaimed. Unrelated policy variants stay intact.
                for variant in self.media.repo.download_variants(job["bvid"], cid):
                    if (
                        variant.get("sha256"),
                        variant.get("size_bytes"),
                    ) not in content or variant.get("actual_quality") != quality:
                        continue
                    removed = self.remove_download(variant, job, cid, quality)
                    complete = complete and removed
                    if removed:
                        self.media.repo.forget_download(
                            job["bvid"], cid, quality, variant["sha256"]
                        )
                for record in [download, *(i for i in inputs if i["cid"] == cid)]:
                    if record["actual_quality"] != quality:
                        # An older attempted quality is protected by a different
                        # source lock and has no confirmed copy in this publication.
                        complete = not Path(record["file"]).exists() and complete
                        continue
                    complete = self.remove_download(record, job, cid, quality) and complete
        package = job["package"]
        require(
            package["staging_ref"] == manifest["staging_ref"]
            and package["staging_ref"] == "pkg_" + manifest["package_id"].replace("-", ""),
            "unsafe_staging",
            409,
        )
        root = safe_path(Path(self.media.config["staging_directory"]), package["staging_ref"])
        if root.exists():
            # Refuse a symlink anywhere; unknown files are preserved rather than recursively removed.
            for entry in manifest["files"]:
                path = safe_path(root, entry["path"])
                if path.exists():
                    actual = file_record(path, entry["path"], entry["kind"], entry["cid"])
                    require(actual == entry, "source_changed", 409)
                    path.unlink()
            for directory in sorted(
                (p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
            ):
                safe_path(root, directory.relative_to(root).as_posix())
                try:
                    directory.rmdir()
                except OSError:
                    pass
            try:
                root.rmdir()
            except OSError:
                complete = False
            sync_directory(root.parent)
        return "cleaned" if complete else "pending"

    def finish(self, job):
        if job.get("state") != "completed":
            return
        if job.get("media_kind") == "comic":
            self.media.comics.downloads.cleanup(job)
            return
        if job.get("media_kind") == "book":
            return
        if job.get("media_kind") == "audio":
            self.media.music.downloads.storage.cleanup(job)
            return
        prior = self.media.repo.get("media_retention", job["job_id"])
        operation_id = (job.get("asset_receipt") or {}).get("operation_id")
        if prior and prior.get("state") == "cleaned" and prior.get("operation_id") == operation_id:
            return
        target = self.media.config.get("targets", {}).get(job["target_id"])
        if not target or target.get("kind") != "media_server":
            return
        try:
            state, code = self.cleanup(job), "verified_media_retained"
        except (Fault, OSError, KeyError, ValueError) as error:
            state, code = "pending", error.code if isinstance(error, Fault) else "media_io_failure"
        self.media.repo.put(
            "media_retention",
            job["job_id"],
            {
                "operation_id": operation_id,
                "state": state,
                "code": code,
                "updated_at": self.media.repo.clock(),
            },
        )

    async def recover(self):
        for job in await self.media.local(self.media.repo.retention_jobs):
            if job["job_id"] not in self.media.worker.active:
                try:
                    await self.media.local(self.finish, job)
                except (Fault, OSError):
                    # A temporarily unavailable ledger is retried by the same pump.
                    pass

    async def pump(self):
        await self.recover()
