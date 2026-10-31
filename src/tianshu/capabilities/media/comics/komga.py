"""Komga discovery, scoped refresh and observed path/page verification."""

import asyncio
from urllib.parse import quote, unquote, urlsplit

from ...contracts import Fault, require
from .suwayomi import service_url


class Komga:
    def __init__(self, comics):
        self.comics = comics
        self.session = None
        self.scan_locks = {}
        self.scanned_at = {}
        self.series_cache = {}

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def request(self, method, path, *, params=None):
        import aiohttp

        config = await self.comics.settings.remote_connection("komga")
        require(config.get("url") and config.get("token"), "comic_komga_not_configured", 409)
        url = service_url(config["url"]) + path
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
        try:
            async with self.session.request(
                method,
                url,
                headers={"X-API-Key": config["token"]},
                params=params,
                allow_redirects=False,
            ) as response:
                require(response.status not in {401, 403}, "comic_komga_auth_required", 409)
                require(200 <= response.status < 300, "comic_komga_unavailable", 503)
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    require(len(data) <= 4 * 1024**2, "comic_response_too_large", 503)
                if not data:
                    return {}
                import json

                try:
                    return json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    raise Fault("comic_komga_invalid_response", 503) from None
        except (aiohttp.ClientError, TimeoutError):
            raise Fault("comic_komga_unavailable", 503) from None

    async def libraries(self):
        rows = await self.request("GET", "/api/v1/libraries")
        require(isinstance(rows, list), "comic_komga_invalid_response", 503)
        return [
            {"library_id": str(row["id"]), "name": row["name"], "root": row.get("root", "")}
            for row in rows
        ]

    async def scan(self, library_id, published_at):
        lock = self.scan_locks.setdefault(library_id, asyncio.Lock())
        async with lock:
            now = self.comics.repo.clock()
            if self.scanned_at.get(library_id, 0) >= published_at:
                return
            delay = max(0, 8 - (now - self.scanned_at.get(library_id, 0)))
            if delay:
                await asyncio.sleep(delay)
            await self.request("POST", "/api/v1/libraries/" + quote(library_id, safe="") + "/scan")
            self.scanned_at[library_id] = self.comics.repo.clock()

    @staticmethod
    def path(row):
        path = str(row.get("url") or "")
        path = unquote(urlsplit(path).path) if path.startswith("file:") else path
        return path.replace("\\", "/").rstrip("/")

    async def rows(self, path, params):
        # Bound every response and page; the configured library limits this enumeration.
        for page in range(200):
            value = await self.request(
                "GET", path, params={**params, "size": 200, "page": page, "sort": "created,asc"}
            )
            require(
                isinstance(value, dict) and isinstance(value.get("content"), list),
                "comic_komga_invalid_response",
                503,
            )
            for row in value["content"]:
                yield row
            if value.get("last", len(value["content"]) < 200):
                return
        raise Fault("comic_reader_index_pending", 503)

    async def series(self, library_id, folder):
        key = (library_id, folder)
        if key not in self.series_cache:
            async for row in self.rows("/api/v1/series", {"library_id": library_id}):
                if str(row.get("libraryId")) == library_id:
                    self.series_cache[(library_id, self.path(row))] = str(row["id"])
        return self.series_cache.get(key)

    async def verify(self, job):
        target = job["comic_target"]
        library_id = target["komga_library_id"]
        expected = target["komga_root"].rstrip("/") + "/" + job["comic_relative_path"]
        connection = await self.comics.settings.remote_connection("komga")
        require(service_url(connection["url"]) == target["komga_url"], "comic_target_changed", 409)
        await self.scan(library_id, job["comic_artifact"]["published_at"])
        # Filename tokens are not title searches: ComicInfo can replace the indexed title.
        # Resolve the exact physical series folder, then enumerate only that series' books.
        for attempt in range(4):
            series_id = await self.series(library_id, expected.rsplit("/", 1)[0])
            if not series_id:
                if attempt < 3:
                    await asyncio.sleep(2)
                continue
            async for row in self.rows(
                "/api/v1/series/" + quote(series_id, safe="") + "/books", {}
            ):
                if str(row.get("libraryId")) != library_id or self.path(row) != expected:
                    continue
                media = row.get("media") or {}
                pages = media.get("pagesCount")
                require(
                    media.get("status") == "READY"
                    and pages == job["comic_artifact"]["page_count"]
                    and row.get("sizeBytes") == job["comic_artifact"]["size_bytes"],
                    "comic_reader_index_pending",
                    503,
                )
                connection = await self.comics.settings.remote_connection("komga")
                return {
                    "server_id": "comic-komga",
                    "kind": "komga",
                    "code": "ready",
                    "item_id": str(row["id"]),
                    "state": "verified",
                    "book_id": str(row["id"]),
                    "series_id": str(row["seriesId"]),
                    "library_id": library_id,
                    "page_count": pages,
                    "reader_url": service_url(connection["url"])
                    + "/book/"
                    + quote(str(row["id"]), safe="")
                    + "/read",
                    "verified_at": self.comics.repo.clock(),
                }
            if attempt < 3:
                await asyncio.sleep(2)
        raise Fault("comic_reader_index_pending", 503)
