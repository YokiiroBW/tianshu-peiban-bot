"""Books lifecycle, source previews and durable subscription scheduling."""

import asyncio
import base64
import hashlib
import io
from pathlib import Path
import uuid

from PIL import Image

from ...contracts import Fault, digest, require
from ..destination import safe_path
from ..scheduling import pump_scans
from .catalog import Catalog, public_book, public_source
from .engine import RuleEngine
from .metadata import Metadata
from .network import Network, origin
from .readability import Readability, check_content
from .sources import Sources
from .selection import SourceSelection
from .warehouse import Warehouse


class Books:
    def __init__(self, media):
        self.media, self.repo = media, media.repo
        self.catalog = Catalog(media)
        self.engine = RuleEngine()
        self.network = Network()
        self.sources = Sources(self)
        self.warehouse = Warehouse(self)
        self.metadata = Metadata(self)
        self.readability = Readability(self)
        self.selection = SourceSelection(self)
        self.data_root = Path(self.repo.database).with_suffix(".book-content")
        from .downloads import Downloads

        self.downloads = Downloads(self)
        from .audio import BookAudio

        self.audio = BookAudio(self)
        from .reading import Reading

        self.reading = Reading(self)

    async def view(self):
        sources = await self.media.local(self.repo.list, "book_source")
        subscriptions = await self.media.local(self.repo.list, "book_subscription")
        books = await self.media.local(self.repo.list, "book")
        lookup = {book["book_id"]: book for book in books}
        return {
            "configured": True,
            "settings": await self.media.local(self.catalog.public_settings),
            "sources": [public_source(source) for source in sources[:500]],
            "source_count": len(sources),
            "groups": sorted({source["group"] for source in sources if source["group"]}),
            "feeds": await self.media.local(self.repo.list, "book_feed"),
            "subscriptions": [
                {**row, "book": public_book(lookup[row["book_id"]])}
                for row in subscriptions
                if row["book_id"] in lookup
            ],
            "library_count": sum(bool(book.get("artifact")) for book in books),
            "engine": {
                "name": "LegadoParser",
                "available": True,
                "javascript": "partial",
                "webview": False,
            },
        }

    async def detail(self, identity, *, refresh=False):
        book = await self.media.local(self.catalog.book, identity)
        if (book.get("source_selection") or {}).get("mode") == "automatic":
            return await self.selection.prepare(identity, refresh=refresh)
        return await self.source_detail(identity, refresh=refresh)

    async def source_detail(self, identity, *, refresh=False):
        book = await self.media.local(self.catalog.book, identity)
        directory = await self.media.local(self.catalog.source_directory, identity)
        if book["source_type"] == "remote" and (refresh or not directory):
            result = await self.sources.call(
                book["source_id"],
                "detail",
                url=book["source_url"],
                variables=book.get("variables", {}),
                book={
                    "name": book["metadata"]["title"],
                    "author": book["metadata"].get("author", ""),
                },
            )
            source, _ = await self.media.local(self.catalog.source, book["source_id"])
            result["bookUrl"] = book["source_url"]
            result.setdefault("name", book["metadata"]["title"])
            book = await self.media.local(self.catalog.register, source, result, identity)
            entries = await self.sources.call(
                book["source_id"],
                "chapters",
                url=book["toc_url"] or book["source_url"],
                variables=book.get("variables", {}),
                book={
                    "name": book["metadata"]["title"],
                    "author": book["metadata"].get("author", ""),
                },
            )
            book, directory = await self.media.local(self.catalog.save_directory, book, entries)
        return book, directory

    async def preview(self, identity, chapter_id, *, pinned=None, refresh=False, observe=True):
        try:
            result = await self._preview(identity, chapter_id, pinned=pinned, refresh=refresh)
            if observe:
                result["readability"] = await self.media.local(
                    self.readability.record, identity, "previewed", chapter=result["chapter"]
                )
            return result
        except Fault as error:
            directory = await self.media.local(self.catalog.directory, identity)
            chapter = next((item for item in directory if item["chapter_id"] == chapter_id), None)
            if chapter:
                await self.media.local(
                    self.readability.record, identity, "failed", chapter=chapter, code=error.code
                )
            raise

    async def _preview(self, identity, chapter_id, *, pinned=None, refresh=False):
        book = await self.media.local(self.catalog.book, identity)
        directory = await self.media.local(self.catalog.directory, identity)
        chapter = next((item for item in directory if item["chapter_id"] == chapter_id), None)
        require(chapter, "not_found", 404)
        cached = await self.media.local(self.catalog.chapter, identity, chapter_id)
        if cached and book["source_type"] == "remote":
            try:
                check_content(cached["content"])
            except Fault:
                cached = None
        if cached and not refresh:
            return self.preview_result(cached, cached=True)
        if "origins" in chapter:
            return await self.selection.content(book, chapter, pinned=pinned, refresh=refresh)
        return await self.source_preview(identity, chapter_id, pinned=pinned, refresh=refresh)

    @staticmethod
    def preview_result(chapter, *, cached):
        return {
            "chapter": {
                key: chapter.get(key)
                for key in (
                    "chapter_id",
                    "title",
                    "content",
                    "content_hash",
                    "downloaded_at",
                    "origin",
                )
            },
            "cached": cached,
        }

    async def source_preview(self, identity, chapter_id, *, pinned=None, refresh=False):
        book = await self.media.local(self.catalog.book, identity)
        directory = await self.media.local(self.catalog.source_directory, identity)
        chapter = next((row for row in directory if row["chapter_id"] == chapter_id), None)
        require(chapter, "not_found", 404)
        cached = await self.media.local(self.catalog.chapter, identity, chapter_id)
        if cached:
            try:
                check_content(cached["content"])
                if (cached.get("origin") or {}).get("source_id", book["source_id"]) != book[
                    "source_id"
                ]:
                    cached = None
            except Fault:
                cached = None
        if cached and not refresh:
            return self.preview_result(cached, cached=True)
        require(book["source_type"] == "remote", "book_content_unavailable", 409)
        current = await self.media.local(self.repo.get, "book_source", book["source_id"])
        require(current and current["enabled"], "book_source_disabled", 409)
        if isinstance(pinned, dict):
            pinned = pinned.get(book["source_id"])
        next_url = (
            directory[chapter["order"] + 1]["url"] if chapter["order"] + 1 < len(directory) else ""
        )
        result = await self.sources.call(
            book["source_id"],
            "content",
            pinned=pinned,
            url=chapter["url"],
            variables=chapter.get("variables", {}),
            next_url=next_url,
            book={"name": book["metadata"]["title"], "author": book["metadata"].get("author", "")},
        )
        check_content(result.get("content", ""))
        saved = await self.media.local(
            self.catalog.save_chapter, identity, chapter, result.get("content", "")
        )
        return self.preview_result(saved, cached=False)

    async def scan(self, identity):
        subscription = await self.media.local(self.repo.get, "book_subscription", identity)
        require(subscription and subscription.get("state") != "removed", "not_found", 404)
        owner = uuid.uuid4().hex
        generation = await self.media.local(self.repo.scan_claim, "books:" + identity, owner)

        async def renew():
            while True:
                await asyncio.sleep(5)
                await self.media.local(
                    self.repo.scan_heartbeat, "books:" + identity, owner, generation
                )

        lease = asyncio.create_task(renew())
        try:
            book, _ = await self.selection.prepare(identity, refresh=True)

            def save():
                with self.repo.transaction() as db:
                    self.repo.require_scan(db, "books:" + identity, owner, generation)
                    current = self.repo._document(db, "book_subscription", identity)
                    require(
                        current
                        and current.get("state") != "removed"
                        and current["revision"] == subscription["revision"],
                        "version_conflict",
                        409,
                    )
                    job = self.catalog.enqueue(book, db=db, refresh=False)
                    updated = self.repo.put(
                        "book_subscription",
                        identity,
                        {
                            **current,
                            "last_scan_at": self.repo.clock(),
                            "next_scan_at": self.repo.clock() + current["interval_seconds"],
                            "code": "ready",
                        },
                        db=db,
                    )
                    return {"subscription": updated, "job_id": job["job_id"]}

            result = await self.media.local(save)
            self.media.wake.set()
            return result
        except Fault as error:
            current = await self.media.local(self.repo.get, "book_subscription", identity)
            if (
                current
                and current.get("state") != "removed"
                and current["revision"] == subscription["revision"]
            ):
                await self.media.local(
                    self.repo.put,
                    "book_subscription",
                    identity,
                    {
                        **current,
                        "last_scan_at": self.repo.clock(),
                        "next_scan_at": self.repo.clock() + min(3600, current["interval_seconds"]),
                        "code": error.code,
                    },
                    current["revision"],
                )
            raise
        finally:
            lease.cancel()
            await asyncio.gather(lease, return_exceptions=True)
            await self.media.local(self.repo.scan_release, "books:" + identity, owner, generation)

    def register_import(self, filename, raw, parsed, *, db):
        metadata, chapters, cover = parsed
        identity = hashlib.sha256(raw).hexdigest()[:24]
        previous = self.repo._document(db, "book", identity)
        if previous:
            return {"book": public_book(previous), "reused": True}
        book = {
            "book_id": identity,
            "metadata": metadata,
            "metadata_revision": 1,
            "source_id": "local",
            "source_name": "本地导入",
            "source_type": "epub" if filename.lower().endswith(".epub") else "local",
            "filename": filename,
            "chapter_count": len(chapters),
            "downloaded": len(chapters),
            "manual_fields": [],
            "field_sources": {key: "本地文件" for key in metadata},
            "latest_chapter": chapters[-1]["title"],
            "updated_at": self.repo.clock(),
            "artifact": None,
            "has_cover": bool(cover),
            "status": "complete",
            "group_key": identity,
        }
        saved = self.repo.put("book", identity, book, db=db)
        self.repo.put(
            "book_directory",
            identity,
            {
                "book_id": identity,
                "chapters": [
                    {key: value for key, value in chapter.items() if key != "content"}
                    for chapter in chapters
                ],
            },
            db=db,
        )
        for chapter in chapters:
            self.repo.put(
                "book_chapter",
                identity + ":" + chapter["chapter_id"],
                {
                    **chapter,
                    "book_id": identity,
                    "content_hash": hashlib.sha256(chapter["content"].encode()).hexdigest(),
                    "downloaded_at": self.repo.clock(),
                },
                db=db,
            )
        return {"book": public_book(saved), "reused": False}

    def save_import_file(self, identity, raw, cover=None):
        folder = safe_path(self.data_root, identity)
        folder.mkdir(parents=True, exist_ok=True)
        original = safe_path(self.data_root, identity + "/original")
        if not original.exists():
            temporary = safe_path(
                self.data_root, identity + "/original-" + uuid.uuid4().hex + ".partial"
            )
            temporary.write_bytes(raw)
            temporary.replace(original)
        if cover:
            try:
                self.save_cover(identity, cover)
            except (OSError, ValueError, Fault):
                pass

    def save_cover(self, identity, raw):
        require(len(raw) <= 8 * 1024 * 1024, "book_response_too_large", 413)
        with Image.open(io.BytesIO(raw)) as image:
            require(image.width * image.height <= 20000000, "book_cover_invalid", 400)
            image.thumbnail((1600, 2400))
            output = io.BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=90)
        folder = safe_path(self.data_root, identity)
        folder.mkdir(parents=True, exist_ok=True)
        path = safe_path(self.data_root, identity + "/cover.jpg")
        temporary = safe_path(self.data_root, identity + "/cover-" + uuid.uuid4().hex + ".partial")
        temporary.write_bytes(output.getvalue())
        temporary.replace(path)
        return path

    async def cover(self, book):
        identity = book["book_id"]
        current = safe_path(self.data_root, identity + "/cover.jpg")
        url = book["metadata"].get("cover_url", "")
        marker = await self.media.local(self.repo.get, "book_cover", identity)
        if current.is_file() and (
            not url
            or marker
            and marker.get("url_digest") == digest(url)
            and marker.get("code", "ready") == "ready"
        ):
            return current
        if not url:
            return None
        if (
            marker
            and marker.get("url_digest") == digest(url)
            and marker.get("retry_at", 0) > self.repo.clock()
        ):
            return current if current.is_file() else None
        try:
            settings = await self.media.local(self.catalog.settings)
            network = (
                self.network
                if not settings.get("opds_url")
                else Network(allowed_private_origins=[settings["opds_url"]])
            )
            headers = (
                self.opds_headers(settings)
                if settings.get("opds_url") and origin(url) == origin(settings["opds_url"])
                else {}
            )
            response = await asyncio.to_thread(network.fetch, url, headers=headers)
            current = await self.media.local(self.save_cover, identity, response.content)
            await self.media.local(
                self.repo.put, "book_cover", identity, {"url_digest": digest(url), "code": "ready"}
            )
            return current
        except (Fault, OSError, ValueError):
            await self.media.local(
                self.repo.put,
                "book_cover",
                identity,
                {
                    "url_digest": digest(url),
                    "code": "unavailable",
                    "retry_at": self.repo.clock() + 3600,
                },
            )
            return current if current.is_file() else None

    def opds_headers(self, settings):
        if not settings.get("opds_secret"):
            return {}
        password = self.media.accounts.unseal("book-opds", settings["opds_secret"])
        encoded = base64.b64encode(
            (settings.get("opds_username", "") + ":" + password).encode()
        ).decode()
        return {"Authorization": "Basic " + encoded}

    async def file(self, identity, kind):
        book = await self.media.local(self.catalog.book, identity)
        if kind.startswith("audio-"):
            return await self.audio.file(identity, kind[6:])
        if kind == "cover":
            path = await self.cover(book)
            require(path and path.is_file(), "not_found", 404)
            return path, "cover.jpg", "image/jpeg"
        require(kind == "epub" and book.get("artifact"), "not_found", 404)
        artifact = book["artifact"]
        path = safe_path(Path(artifact["root"]), artifact["relative_path"])
        require(
            path.is_file() and path.stat().st_size == artifact["bytes"],
            "book_content_unavailable",
            409,
        )
        from ..destination import path_component

        return (
            path,
            path_component(book["metadata"]["title"], "book", 150) + ".epub",
            "application/epub+zip",
        )

    def start_background(self, background):
        background.start("book_audio", self.audio.loop)
        background.start(
            "book_subscriptions", pump_scans, self.media, "book_subscription", "book_id", self.scan
        )
        background.start(
            "book_sources",
            pump_scans,
            self.media,
            "book_feed",
            "feed_id",
            self.sources.refresh_feed,
        )

    async def close(self):
        await self.sources.close()
        await self.engine.close()
