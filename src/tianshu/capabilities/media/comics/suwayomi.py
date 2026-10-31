"""Suwayomi GraphQL control and same-origin image streams; no direct site scraping."""

import asyncio
import json
import base64
import os
import uuid
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp

from ...contracts import Fault, require
from .health import source_error
from .packages import MAX_PAGE_BYTES

MANGA_FIELDS = (
    "id title author artist description genre status thumbnailUrl sourceId url realUrl inLibrary"
)
CHAPTER_FIELDS = "id name chapterNumber pageCount mangaId isDownloaded sourceOrder scanlator url realUrl uploadDate"
EXTENSION_FIELDS = "pkgName name apkName isInstalled isObsolete versionName versionCodeLong extensionLib storeIndexUrl contentWarning source { nodes { id } }"


def service_url(value):
    require(isinstance(value, str) and 0 < len(value) <= 2048, "comic_connection_invalid", 400)
    try:
        parsed = urlsplit(value)
        require(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment,
            "comic_connection_invalid",
            400,
        )
        _ = parsed.port
    except ValueError:
        raise Fault("comic_connection_invalid", 400) from None
    return value.rstrip("/")


class Suwayomi:
    def __init__(self, connection):
        self.connection = connection
        self.session = None
        self.parallel = asyncio.Semaphore(4)
        self.source_locks = {}
        self.filter_cache = {}

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def client(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60, sock_connect=10, sock_read=35),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
        return self.session

    async def settings(self):
        config = await self.connection()
        require(config.get("url"), "comic_engine_not_configured", 409)
        base = service_url(config["url"])
        headers = {}
        if config.get("username"):
            token = base64.b64encode(
                (config["username"] + ":" + config.get("password", "")).encode()
            ).decode()
            headers["Authorization"] = "Basic " + token
        elif config.get("token"):
            headers["Authorization"] = "Bearer " + config["token"]
        return base, headers

    @staticmethod
    def status(status):
        require(status not in {401, 403}, "comic_engine_auth_required", 409)
        require(status != 429, "comic_source_rate_limited", 503)
        require(200 <= status < 300, "comic_engine_unavailable", 503)

    async def query(self, query, variables=None):
        base, headers = await self.settings()
        try:
            async with (await self.client()).post(
                base + "/api/graphql",
                json={"query": query, "variables": variables or {}},
                headers=headers,
                allow_redirects=False,
            ) as response:
                self.status(response.status)
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    require(len(data) <= 8 * 1024**2, "comic_response_too_large", 503)
                import json

                try:
                    result = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    raise Fault("comic_engine_invalid_response", 503) from None
                if isinstance(result, dict) and result.get("errors"):
                    messages = [
                        str(e.get("message", "")) for e in result["errors"] if isinstance(e, dict)
                    ]
                    code = source_error(messages)
                    raise Fault(code, 409 if code.endswith(("required", "unsupported")) else 503)
                require(
                    isinstance(result, dict)
                    and not result.get("errors")
                    and isinstance(result.get("data"), dict),
                    "comic_source_unavailable",
                    503,
                )
                return result["data"]
        except TimeoutError:
            raise Fault("comic_engine_timeout", 503) from None
        except aiohttp.ClientError:
            raise Fault("comic_engine_unavailable", 503) from None

    async def probe(self):
        value = await self.query(
            "query { aboutServer { name version } settings { globalUpdateInterval autoDownloadNewChapters downloadAsCbz } }"
        )
        return {
            "state": "ready",
            **value["aboutServer"],
            "engine_schedule": value["settings"],
            "mode": "page_streams_without_library_membership",
        }

    async def sources(self):
        rows = await self.nodes("sources", "id name displayName lang contentWarning supportsLatest")
        return [{**s, "id": str(s["id"])} for s in rows if str(s["id"]) != "0"], len(rows)

    async def nodes(self, field, fields):
        rows = []
        while True:
            value = await self.query(
                "query($offset:Int!){"
                + field
                + "(first:500,offset:$offset){nodes{"
                + fields
                + "} totalCount}}",
                {"offset": len(rows)},
            )
            page = value[field]
            require(isinstance(page.get("nodes"), list), "comic_engine_invalid_response", 503)
            rows.extend(page["nodes"])
            require(len(rows) <= 20000, "comic_response_too_large", 503)
            if len(rows) >= page["totalCount"]:
                return rows
            require(page["nodes"], "comic_engine_invalid_response", 503)

    async def extensions(self):
        return await self.nodes("extensions", EXTENSION_FIELDS)

    async def extension(self, package):
        value = await self.query(
            "query($pkg:String!){extensions(first:2,filter:{pkgName:{equalTo:$pkg}}){nodes{"
            + EXTENSION_FIELDS
            + "}}}",
            {"pkg": package},
        )
        rows = value["extensions"]["nodes"]
        require(
            all(row["pkgName"] == package for row in rows),
            "comic_engine_invalid_response",
            503,
        )
        return next(iter(rows), None)

    async def extension_stores(self):
        return await self.nodes("extensionStores", "name indexUrl isLegacy")

    async def add_extension_store(self, url):
        value = await self.query(
            "mutation($url:String!){addExtensionStore(input:{indexUrl:$url}){extensionStore{name indexUrl}}}",
            {"url": url},
        )
        return value["addExtensionStore"]["extensionStore"]

    async def refresh_extensions(self):
        await self.query("mutation{fetchExtensions(input:{}){clientMutationId}}")

    async def install_extension_file(self, path):
        """Upload a staged artifact (also used to restore an exact prior JAR)."""
        base, headers = await self.settings()
        operations = {
            "query": "mutation($file:Upload!){installExternalExtension(input:{extensionFile:$file}){extension{pkgName isInstalled versionName versionCodeLong}}}",
            "variables": {"file": None},
        }
        try:
            with Path(path).open("rb") as source:
                form = aiohttp.FormData()
                form.add_field("operations", json.dumps(operations))
                form.add_field("map", json.dumps({"0": ["variables.file"]}))
                form.add_field(
                    "0", source, filename=Path(path).name, content_type="application/octet-stream"
                )
                async with (await self.client()).post(
                    base + "/api/graphql",
                    data=form,
                    headers=headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=180, sock_connect=10),
                ) as response:
                    self.status(response.status)
                    raw = await response.content.read(1024**2 + 1)
                    require(len(raw) <= 1024**2, "comic_engine_invalid_response", 503)
                    result = json.loads(raw)
                    require(
                        not result.get("errors") and result.get("data"),
                        "comic_extension_install_failed",
                        503,
                    )
                    return result["data"]["installExternalExtension"]["extension"]
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise Fault("comic_extension_install_unverified", 503) from None

    async def filters(self, source_id):
        cached = self.filter_cache.get(source_id)
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        fields = "__typename ... on SelectFilter{name values} ... on CheckBoxFilter{name} ... on TriStateFilter{name}"
        value = await self.query(
            "query($id:LongString!){sources(first:1,filter:{id:{equalTo:$id}}){nodes{id filters{"
            + fields
            + " ... on GroupFilter{name filters{"
            + fields
            + "}}}}}}",
            {"id": source_id},
        )
        rows = value["sources"]["nodes"]
        require(
            len(rows) == 1 and str(rows[0]["id"]) == source_id, "comic_engine_invalid_response", 503
        )
        result = rows[0]["filters"]
        require(isinstance(result, list), "comic_engine_invalid_response", 503)
        self.filter_cache[source_id] = (time.monotonic(), result)
        return result

    async def search(self, source_id, query, page=1, *, filters=None):
        # Source execution is serial, while independent sources can return progressively.
        lock = self.source_locks.setdefault(source_id, asyncio.Lock())
        async with self.parallel, lock:
            mutation = (
                "mutation($input: FetchSourceMangaInput!) { fetchSourceManga(input: $input) { hasNextPage mangas { "
                + MANGA_FIELDS
                + " } } }"
            )
            inputs = {"source": source_id, "type": "SEARCH", "query": query, "page": page}
            if filters:
                inputs["filters"] = filters
            mode = "search"
            try:
                value = await self.query(mutation, {"input": inputs})
            except Fault as error:
                if error.code != "comic_source_search_unsupported":
                    raise
                # Some official catalogues intentionally have no search operation.
                # Fetch only this catalogue page; Sources still checks query relevance.
                value = await self.query(
                    mutation, {"input": {"source": source_id, "type": "POPULAR", "page": page}}
                )
                mode = "catalogue"
            result = value.get("fetchSourceManga")
            require(
                isinstance(result, dict) and isinstance(result.get("mangas"), list),
                "comic_engine_invalid_response",
                503,
            )
            return {
                **result,
                "search_mode": mode,
                "filters_applied": bool(filters) and mode == "search",
            }

    async def detail(self, manga_id, source_id):
        lock = self.source_locks.setdefault(source_id, asyncio.Lock())
        async with self.parallel, lock:
            value = await self.query(
                "mutation($input: FetchMangaAndChaptersInput!) { fetchMangaAndChapters(input: $input) { manga { "
                + MANGA_FIELDS
                + " } chapters { "
                + CHAPTER_FIELDS
                + " } } }",
                {"input": {"id": int(manga_id), "fetchManga": True, "fetchChapters": True}},
            )
            result = value.get("fetchMangaAndChapters")
            require(
                isinstance(result, dict)
                and isinstance(result.get("manga"), dict)
                and isinstance(result.get("chapters"), list),
                "comic_engine_invalid_response",
                503,
            )
            require(str(result["manga"]["sourceId"]) == source_id, "comic_source_changed", 409)
            return result

    async def pages(self, chapter_id, manga_id):
        value = await self.query(
            "mutation($id: Int!) { fetchChapterPages(input: {chapterId: $id}) { chapter { id mangaId pageCount } pages } }",
            {"id": int(chapter_id)},
        )
        result = value.get("fetchChapterPages")
        require(
            isinstance(result, dict)
            and isinstance(result.get("pages"), list)
            and result.get("chapter", {}).get("mangaId") == int(manga_id),
            "comic_engine_invalid_response",
            503,
        )
        pages = result["pages"]
        require(
            0 < len(pages) <= 10000 and all(isinstance(p, str) and len(p) <= 8192 for p in pages),
            "comic_directory_empty",
            409,
        )
        require(
            not result["chapter"].get("pageCount") or result["chapter"]["pageCount"] == len(pages),
            "comic_page_count_mismatch",
            409,
        )
        return pages

    async def download_page(self, page_url, target):
        base, headers = await self.settings()
        url = urljoin(base + "/", page_url)
        origin, peer = urlsplit(base), urlsplit(url)
        require(
            (origin.scheme, origin.hostname, origin.port) == (peer.scheme, peer.hostname, peer.port)
            and not peer.username
            and not peer.fragment,
            "comic_image_origin_changed",
            409,
        )
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".partial")
        try:
            async with (await self.client()).get(
                url, headers=headers, allow_redirects=False
            ) as response:
                if response.status >= 400 and response.status not in {401, 403, 429}:
                    detail = (await response.content.read(65536)).decode("utf-8", "replace")
                    code = source_error([detail])
                    raise Fault(
                        code if code != "comic_source_unavailable" else "comic_source_image_failed",
                        503,
                    )
                self.status(response.status)
                length = 0
                with temporary.open("xb") as output:
                    async for chunk in response.content.iter_chunked(128 * 1024):
                        length += len(chunk)
                        require(length <= MAX_PAGE_BYTES, "comic_page_too_large", 409)
                        output.write(chunk)
                    require(length > 0, "comic_page_invalid", 409)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(target)
        except TimeoutError:
            raise Fault("comic_engine_timeout", 503) from None
        except aiohttp.ClientError:
            raise Fault("comic_engine_unavailable", 503) from None
        finally:
            temporary.unlink(missing_ok=True)
