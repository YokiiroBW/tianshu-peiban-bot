"""Comic lifecycle: one subscription scheduler, stable catalogue and staged page reads."""

import asyncio
from pathlib import Path
import uuid

from ...contracts import Fault, require
from ..destination import safe_path
from ..scheduling import pump_scans
from .catalog import Catalog, public_comic
from .aliases import Aliases
from .versions import eligible, chapter_evidence
from .imports import Imports
from .extensions import Extensions
from .health import SourceHealth
from .komga import Komga
from .packages import copy_page, folder_pages, image_record
from .settings import Settings
from .sources import Sources
from .suwayomi import Suwayomi


class Comics:
    def __init__(self, media, *, file_defaults=None):
        self.media, self.repo = media, media.repo
        self.settings = Settings(media, file_defaults=file_defaults)
        self.catalog = Catalog(self)
        self.health = SourceHealth(self)
        self.engine = Suwayomi(lambda: self.settings.remote_connection("suwayomi"))
        self.aliases = Aliases(self)
        self.sources = Sources(self)
        self.imports = Imports(self)
        self.komga = Komga(self)
        self.data_root = Path(self.repo.database).with_suffix(".comic-content")
        self.extensions = Extensions(self)
        from .downloads import Downloads

        self.downloads = Downloads(self)

    def start_background(self, background):
        background.start("comic_extensions", self.extensions.pump)
        if self.media.config is None:
            background.start("comic_cleanup", self.downloads.clean_completed)
        background.start(
            "comic_subscriptions",
            pump_scans,
            self.media,
            "comic_subscription",
            "comic_id",
            self.scan,
        )

    async def close(self):
        await self.sources.close()
        await self.aliases.close()
        await self.engine.close()
        await self.komga.close()
        await self.downloads.close()

    async def view(self):
        settings = await self.media.local(self.settings.public)
        rows = await self.media.local(self.repo.list, "comic")
        subscriptions = await self.media.local(self.repo.list, "comic_subscription")
        lookup = {}
        for comic in rows:
            lookup.setdefault(comic["comic_id"], comic)
        return {
            "settings": settings,
            "configured": bool(settings["connections"].get("suwayomi", {}).get("url")),
            "subscriptions": [
                {
                    **s,
                    "comic": public_comic(lookup.get(s["comic_id"], {})),
                }
                for s in subscriptions
            ],
            "counts": {
                "comics": len(rows),
                "subscriptions": len(subscriptions),
                "downloaded_chapters": sum(c.get("downloaded", 0) for c in rows),
            },
        }

    async def detail(self, comic_id, refresh=False):
        comic = await self.media.local(self.catalog.comic, comic_id)
        chapters = await self.media.local(self.catalog.chapters, comic_id)
        if comic["source_type"] == "remote" and (refresh or not chapters):
            require(
                comic["engine_id"] == await self.sources.engine_id(), "comic_source_changed", 409
            )
            rows = await self.sources.list()
            source = next((s for s in rows if s["source_id"] == comic["source_id"]), None)
            require(source and source["enabled"], "comic_source_disabled", 409)
            try:
                result = await self.engine.detail(comic["engine_manga_id"], comic["source_id"])
                registered = await self.media.local(self.catalog.register, source, result["manga"])
                require(registered["comic_id"] == comic_id, "comic_source_changed", 409)
                comic, chapters = await self.media.local(
                    self.catalog.save_directory, comic_id, result["chapters"]
                )
            except Fault as error:
                await self.media.local(
                    self.health.record,
                    comic,
                    "directory",
                    error.code,
                    facts={"comic_id": comic_id},
                )
                raise
            await self.media.local(
                self.health.record,
                comic,
                "directory",
                "ready",
                facts={"comic_id": comic_id, "count": len(chapters)},
            )
        artifacts = await self.media.local(self.repo.list, "comic_artifact")
        lookup = {
            a["chapter_id"]: a
            for a in artifacts
            if a["comic_id"] == comic_id and a["library_kind"] == comic["library_kind"]
        }
        return comic, [
            {
                **c,
                "language_evidence": c.get("language_evidence") or chapter_evidence(comic, c),
                "artifact": lookup.get(c["chapter_id"]),
            }
            for c in chapters
        ]

    async def scan(self, comic_id):
        subscription = await self.media.local(self.repo.get, "comic_subscription", comic_id)
        require(subscription, "not_found", 404)
        require(subscription["state"] == "active", "comic_subscription_paused", 409)
        owner = uuid.uuid4().hex
        key = "comics:" + comic_id
        generation = await self.media.local(self.repo.scan_claim, key, owner)

        async def renew():
            while True:
                await asyncio.sleep(5)
                await self.media.local(self.repo.scan_heartbeat, key, owner, generation)

        lease = asyncio.create_task(renew())
        try:
            comic, chapters = await self.detail(comic_id, refresh=True)

            def save():
                with self.repo.transaction() as db:
                    self.repo.require_scan(db, key, owner, generation)
                    current = self.repo._document(db, "comic_subscription", comic_id)
                    require(
                        current
                        and current["state"] == "active"
                        and current["revision"] == subscription["revision"],
                        "comic_subscription_paused",
                        409,
                    )
                    seen = set(current["seen"])
                    matching = eligible(comic, chapters, current)
                    require(
                        not current.get("version_policy") or matching,
                        "comic_chinese_version_missing",
                        409,
                    )
                    added = [c for c in matching if c["version_key"] not in seen]
                    jobs = self.catalog.enqueue(
                        comic, added, db=db, version_policy=current.get("version_policy")
                    )
                    value = self.repo.fact(
                        "comic_subscription",
                        comic_id,
                        {
                            **current,
                            "seen": list(
                                dict.fromkeys(
                                    [*current["seen"], *(c["version_key"] for c in added)]
                                )
                            ),
                            "last_scan_at": self.repo.clock(),
                            "next_scan_at": self.repo.clock() + current["interval_seconds"],
                            "code": "ready",
                            "last_added": len(added),
                        },
                        current["revision"],
                        db=db,
                    )
                    return {"subscription": value, "job_ids": [j["job_id"] for j in jobs]}

            result = await self.media.local(save)
            self.media.wake.set()
            return result
        except Fault as error:
            code = error.code

            def fail():
                with self.repo.transaction() as db:
                    self.repo.require_scan(db, key, owner, generation)
                    current = self.repo._document(db, "comic_subscription", comic_id)
                    if (
                        current
                        and current["state"] == "active"
                        and current["revision"] == subscription["revision"]
                    ):
                        self.repo.fact(
                            "comic_subscription",
                            comic_id,
                            {
                                **current,
                                "last_scan_at": self.repo.clock(),
                                "next_scan_at": self.repo.clock()
                                + min(3600, current["interval_seconds"]),
                                "code": code,
                            },
                            current["revision"],
                            db=db,
                        )

            await self.media.local(fail)
            raise
        finally:
            lease.cancel()
            await asyncio.gather(lease, return_exceptions=True)
            await self.media.local(self.repo.scan_release, key, owner, generation)

    async def page(self, comic_id, chapter_id, index):
        require(type(index) is int and 0 <= index < 3, "comic_preview_limit", 400)
        comic, chapters = await self.detail(comic_id)
        chapter = next((c for c in chapters if c["chapter_id"] == chapter_id), None)
        require(chapter, "not_found", 404)
        target = safe_path(
            self.data_root, "preview/" + comic_id + "/" + chapter_id + f"/{index:06d}.page"
        )
        if target.is_file():
            record = await self.media.local(image_record, target)
            return target, record
        if comic["source_type"] == "remote":
            require(
                comic["engine_id"] == await self.sources.engine_id(), "comic_source_changed", 409
            )
            lock = self.engine.source_locks.setdefault(comic["source_id"], asyncio.Lock())
            async with lock:
                try:
                    pages = await self.engine.pages(
                        chapter["engine_chapter_id"], comic["engine_manga_id"]
                    )
                    require(index < len(pages), "not_found", 404)
                    await self.engine.download_page(pages[index], target)
                    record = await self.media.local(image_record, target)
                except Fault as error:
                    await self.media.local(self.health.record, comic, "preview", error.code)
                    raise
                await self.media.local(
                    self.health.record,
                    comic,
                    "preview",
                    "ready",
                    facts={"comic_id": comic_id, "chapter_id": chapter_id, "index": index},
                )
                return target, record
        else:
            require(chapter["input_kind"] == "images", "comic_preview_after_import", 409)
            source = await self.media.local(self.imports.recheck, comic, chapter)
            pages = await self.media.local(folder_pages, source)
            require(index < len(pages), "not_found", 404)
            await self.media.local(copy_page, pages[index], target)
        record = await self.media.local(image_record, target)
        return target, record

    async def cover(self, comic_id):
        comic = await self.media.local(self.catalog.comic, comic_id)
        require(comic["source_type"] == "remote", "not_found", 404)
        require(comic["engine_id"] == await self.sources.engine_id(), "comic_source_changed", 409)
        target = safe_path(self.data_root, "covers/" + comic_id + ".page")
        if not target.is_file():
            # Binary delivery is the server's supported image route; all controls use GraphQL.
            await self.engine.download_page(
                f"/api/v1/manga/{comic['engine_manga_id']}/thumbnail", target
            )
        return target, await self.media.local(image_record, target)

    async def file(self, parts):
        from .catalog import identity

        require(len(parts) in {2, 4}, "not_found", 404)
        identity(parts[0])
        if len(parts) == 2 and parts[1] == "cover":
            path, record = await self.cover(parts[0])
        else:
            require(
                len(parts) == 4 and parts[1] == "preview" and parts[3] in {"0", "1", "2"},
                "not_found",
                404,
            )
            identity(parts[2])
            path, record = await self.page(parts[0], parts[2], int(parts[3]))
        mime = {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".avif": "image/avif",
        }[record["extension"]]
        return path, mime
