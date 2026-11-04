"""Chapter-level recovery and atomic EPUB publication through media jobs."""

import asyncio
import hashlib
import json
import os
import zipfile
from pathlib import Path

from ...contracts import Fault, digest, require
from ..destination import safe_path
from ..organizer import library_lock
from ..video_storage import sync_directory, sync_file
from .documents import build_epub, validate_epub
from .organization import book_category, book_relative_path
from .readability import check_content

RETRYABLE_ERRORS = {
    "book_source_unavailable",
    "book_source_rate_limited",
    "book_rule_timeout",
    "busy",
}


class Downloads:
    def __init__(self, books):
        self.books, self.media, self.repo = books, books.media, books.repo

    async def run(self, job, owner):
        generation = job["generation"]
        identity = job["job_id"]

        async def update(**changes):
            nonlocal job
            job = await self.media.local(
                self.repo.update_job, identity, changes, owner=owner, generation=generation
            )
            return job

        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                try:
                    await self.media.local(self.repo.heartbeat, identity, owner, generation)
                except Fault:
                    task = self.media.worker.active.get(identity)
                    if task:
                        task.cancel()
                    return

        pulse = asyncio.create_task(heartbeat())
        try:
            book = await self.media.local(self.books.catalog.book, job["book_id"])
            if book["source_type"] == "remote":
                book, directory = await self.books.selection.prepare(
                    book["book_id"], refresh=job.get("refresh_directory", True)
                )
                pinned = await self.books.selection.rules(book, directory)
                await update(book_rule_snapshot=None, refresh_directory=False)
            else:
                directory = await self.media.local(self.books.catalog.directory, book["book_id"])
                pinned = None
            require(directory, "book_directory_empty", 409)
            if pinned:
                await update(stage="book_checking", code="book_checking_content")
                await self.books.readability.verify(book, directory, pinned)
            available, missing = [], []
            consecutive_failures = 0
            last_error = "book_content_unavailable"
            for index, chapter in enumerate(directory):
                cached = await self.media.local(
                    self.books.catalog.chapter, book["book_id"], chapter["chapter_id"]
                )
                if cached and pinned:
                    try:
                        check_content(cached["content"])
                    except Fault:
                        cached = None
                if not cached:
                    if consecutive_failures >= 3:
                        missing.append(chapter)
                        continue
                    try:
                        await self.books.preview(
                            book["book_id"], chapter["chapter_id"], pinned=pinned, observe=False,
                            recovery={"job": job, "owner": owner, "material": chapter["chapter_id"]},
                        )
                        cached = await self.media.local(
                            self.books.catalog.chapter, book["book_id"], chapter["chapter_id"]
                        )
                        consecutive_failures = 0
                    except Fault as error:
                        if error.code in RETRYABLE_ERRORS:
                            # Leave cached chapters and the last complete EPUB intact.
                            # The normal job backoff will resume only the missing content.
                            await self.media.local(
                                self.books.catalog.progress, book["book_id"], len(available)
                            )
                            raise
                        last_error = error.code
                        consecutive_failures += 1
                if cached:
                    available.append(
                        {**cached, "title": chapter["title"], "order": chapter["order"]}
                    )
                else:
                    missing.append(chapter)
                changes = {
                    "stage": "book_chapters",
                    "code": "book_downloading",
                    "progress": len(available) / len(directory),
                    "book_chapters_done": len(available),
                    "book_chapters_total": len(directory),
                }
                if job["state"] != "downloading":
                    changes["state"] = "downloading"
                await update(**changes)
                if index % 10 == 0 or index == len(directory) - 1:
                    await self.media.local(
                        self.books.catalog.progress, book["book_id"], len(available)
                    )
            require(available, last_error, 409)
            book = await self.media.local(self.books.catalog.book, book["book_id"])
            old_artifact = book.get("artifact")
            require(
                not old_artifact
                or not old_artifact.get("complete")
                or not missing
                and len(available) >= old_artifact["chapters"],
                "book_incomplete_kept_previous",
                409,
            )
            book = await self.books.metadata.enrich(book)
            cover = await self.books.cover(book)
            await update(state="validating", stage="book_epub", code="book_building_epub")
            artifact = await self.media.local(
                self.publish, book, available, missing, job, owner, cover
            )

            def finish():
                with self.repo.transaction() as db:
                    current = self.books.catalog.book(book["book_id"], db)
                    current_job = json.loads(
                        db.execute('SELECT body FROM jobs WHERE id=%s', (identity,)).fetchone()[0]
                    )
                    require(current_job["generation"] == generation, "lease_conflict", 409)
                    self.repo.put(
                        "book",
                        book["book_id"],
                        {
                            **current,
                            "artifact": artifact,
                            "downloaded": len(available),
                            "updated_at": self.repo.clock(),
                            **(
                                {
                                    "source_selection": {
                                        **current["source_selection"],
                                        **self.books.selection.provenance(book, available),
                                    }
                                }
                                if current.get("source_selection")
                                else {}
                            ),
                            "readability": current.get("readability")
                            if missing
                            else {
                                "state": "complete",
                                "checked_at": self.repo.clock(),
                                "sample_count": len(available),
                            },
                        },
                        db=db,
                    )
                    metadata_changed = digest(current["metadata"]) != artifact["metadata_digest"]
                    current_directory = self.books.catalog.directory(book["book_id"], db)
                    directory_changed = [row["chapter_id"] for row in current_directory] != [
                        row["chapter_id"] for row in directory
                    ]
                    # A scheduled scan can discover chapters while this snapshot downloads.
                    # Keep the cached chapters and continue immediately with the new directory.
                    changed = metadata_changed or directory_changed
                    state = "failed" if missing else "queued" if changed else "completed"
                    return self.repo.update_job(
                        identity,
                        {
                            "state": state,
                            "stage": "book_partial" if missing else "book_published",
                            "code": "book_content_incomplete"
                            if missing
                            else "book_directory_changed"
                            if directory_changed
                            else "book_metadata_changed"
                            if changed
                            else "book_completed",
                            "book_artifact": artifact,
                            "book_chapters_done": len(available),
                            "book_chapters_total": len(directory),
                            "book_reused": artifact.get("reused", False),
                            "progress": len(available) / len(directory),
                            "refresh_directory": False,
                            "title": current["metadata"]["title"],
                            "creator": current["metadata"].get("author", ""),
                        },
                        db=db,
                    )

            await self.media.local(finish)
        except asyncio.CancelledError:
            current = await self.media.local(self.repo.job, identity)
            if current and current["generation"] == generation:
                await self.media.local(
                    self.repo.update_job,
                    identity,
                    {
                        "state": "cancelled" if current["cancel_requested"] else "queued",
                        "stage": "book_paused",
                        "code": "user_cancelled"
                        if current["cancel_requested"]
                        else "book_interrupted",
                    },
                    generation=generation,
                )
            raise
        except Fault as error:
            from ..recovery import DownloadDeferred
            deferred = isinstance(error, DownloadDeferred)
            attempts = job.get("attempts", 0) + (0 if deferred else 1)
            retry = deferred or error.code in RETRYABLE_ERRORS and attempts < 4
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
                    "stage": "book_attention",
                    "code": error.code,
                    "attempts": attempts,
                    "retry_delay": error.retry_after if deferred else 2 if error.code == "busy" else min(3600, 60 * 2**attempts),
                },
                generation=generation,
            )
        except (OSError, ValueError, zipfile.BadZipFile):
            await self.media.local(
                self.repo.update_job,
                identity,
                {"state": "failed", "stage": "book_attention", "code": "book_storage_unavailable"},
                generation=generation,
            )
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)

    def guard(self, job, owner):
        current = self.repo.job(job["job_id"])
        require(
            current
            and current["generation"] == job["generation"]
            and not current["cancel_requested"],
            "job_cancelled",
            409,
        )
        self.repo.heartbeat(job["job_id"], owner, job["generation"])

    def publish(self, book, chapters, missing, job, owner, cover_path):
        root = Path(job["destination_directory"])
        require(root.is_dir(), "book_directory_unavailable", 409)
        self.guard(job, owner)
        previous = book.get("artifact")
        pending = self.repo.get("book_publication", book["book_id"])
        relative = book_relative_path(book)
        target = safe_path(root, relative)
        content_digest = digest(
            [(chapter["chapter_id"], chapter["content_hash"]) for chapter in chapters]
        )
        metadata_digest = digest(book["metadata"])
        with library_lock(root):
            if target.exists():
                if (
                    pending
                    and pending["root"] == str(root)
                    and pending["relative_path"] == relative
                ):
                    with target.open("rb") as stream:
                        recovered = (
                            hashlib.file_digest(stream, "sha256").hexdigest() == pending["sha256"]
                        )
                    if recovered:
                        previous = {**pending, "mtime_ns": target.stat().st_mtime_ns}
                require(
                    previous
                    and previous["root"] == str(root)
                    and previous["relative_path"] == relative,
                    "book_file_conflict",
                    409,
                )
                stat = target.stat()
                if (
                    stat.st_mtime_ns != previous.get("mtime_ns")
                    or stat.st_size != previous["bytes"]
                ):
                    with target.open("rb") as stream:
                        require(
                            hashlib.file_digest(stream, "sha256").hexdigest() == previous["sha256"],
                            "book_artifact_changed",
                            409,
                        )
                if (
                    previous.get("content_digest") == content_digest
                    and previous.get("metadata_digest") == metadata_digest
                    and previous.get("complete") == (not missing)
                ):
                    return {**previous, "reused": True}
            staging_relative = f".book-staging/{job['job_id']}/{job['generation']}"
            stage = safe_path(root, staging_relative)
            stage.mkdir(parents=True, exist_ok=True)
            staged_epub = safe_path(root, staging_relative + "/book.epub")
            original = None
            if book["source_type"] == "epub":
                original = safe_path(
                    self.books.data_root, book["book_id"] + "/original"
                ).read_bytes()
            cover = cover_path.read_bytes() if cover_path else None
            export_book = (
                book
                if not missing
                else {
                    **book,
                    "metadata": {
                        **book["metadata"],
                        "title": book["metadata"]["title"] + "（部分章节）",
                    },
                }
            )
            export_book = {
                **export_book,
                "metadata": {
                    **export_book["metadata"],
                    "category": book_category(book["metadata"], book.get("source_name", "")),
                },
            }
            build_epub(
                staged_epub,
                export_book,
                chapters,
                cover=cover,
                original=original,
                modified_at=self.repo.clock(),
            )
            validate_epub(staged_epub)
            sync_file(staged_epub)
            with staged_epub.open("rb") as stream:
                fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
            self.guard(job, owner)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = {
                "book_id": book["book_id"],
                "source_id": book["source_id"],
                "source_name": book["source_name"],
                "chapters": len(chapters),
                "complete": not missing,
                "missing": [
                    {"chapter_id": chapter["chapter_id"], "title": chapter["title"]}
                    for chapter in missing
                ],
                "content_digest": content_digest,
                "sha256": fingerprint,
                "chapter_sources": [
                    {
                        "chapter_id": chapter["chapter_id"],
                        "title": chapter["title"],
                        "source": chapter.get("origin")
                        or {
                            "book_id": book["book_id"],
                            "source_id": book["source_id"],
                            "source_name": book["source_name"],
                            "chapter_ids": [chapter["chapter_id"]],
                        },
                    }
                    for chapter in chapters
                ],
            }
            (stage / "source.json").write_text(
                json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with zipfile.ZipFile(staged_epub) as archive:
                from .documents import CONTAINER, ET

                container = ET.fromstring(archive.read("META-INF/container.xml"))
                package_path = container.find(f".//{{{CONTAINER}}}rootfile").get("full-path")
                (stage / "metadata.opf").write_bytes(archive.read(package_path))
            for name in ("source.json", "metadata.opf"):
                sync_file(stage / name)
                os.replace(stage / name, safe_path(root, (Path(relative).parent / name).as_posix()))
            if cover:
                (stage / "cover.jpg").write_bytes(cover)
                sync_file(stage / "cover.jpg")
                os.replace(
                    stage / "cover.jpg",
                    safe_path(root, (Path(relative).parent / "cover.jpg").as_posix()),
                )
            self.guard(job, owner)
            artifact = {
                "root": str(root),
                "category": book_category(book["metadata"], book.get("source_name", "")),
                "relative_path": relative,
                "bytes": staged_epub.stat().st_size,
                "sha256": fingerprint,
                "content_digest": content_digest,
                "metadata_digest": metadata_digest,
                "chapters": len(chapters),
                "expected_chapters": len(chapters) + len(missing),
                "complete": not missing,
                "published_at": self.repo.clock(),
                "version": (previous or {}).get("version", 0) + 1,
                "reused": False,
            }
            # Persist the exact output before rename so a crash before ledger completion
            # can adopt our file without overwriting an unrelated file.
            self.repo.put("book_publication", book["book_id"], artifact)
            os.replace(staged_epub, target)
            sync_directory(target.parent)
            stat = target.stat()
            return {**artifact, "mtime_ns": stat.st_mtime_ns}
