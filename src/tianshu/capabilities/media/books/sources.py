"""Source bundles and cancellable progressive aggregation."""

import asyncio
import json
import uuid

from ...contracts import Fault, digest, require
from .catalog import public_book


class Sources:
    def __init__(self, books):
        self.books, self.media, self.repo = books, books.media, books.repo
        self.searches = {}
        self.locks = {}

    async def call(self, identity, operation, *, pinned=None, **values):
        lock = self.locks.setdefault(identity, asyncio.Lock())
        async with lock:
            return await self._call(identity, operation, pinned=pinned, **values)

    async def _call(self, identity, operation, *, pinned=None, **values):
        source, rules = pinned or await self.media.local(self.books.catalog.source, identity)
        try:
            result = await self.books.engine.call(rules, operation, **values)
            await self.media.local(
                self.books.catalog.source_result,
                source,
                good=operation == "content" and bool(result.get("content")),
            )
            return result
        except Fault as error:
            await self.media.local(self.books.catalog.source_result, source, code=error.code)
            if (
                not pinned
                and error.code
                in {
                    "book_rule_failed",
                    "book_rule_timeout",
                    "book_rule_capability_unsupported",
                    "book_content_empty",
                }
                and source.get("last_good_secret")
                and source["last_good_digest"] != source["digest"]
            ):
                rules = json.loads(self.media.accounts.unseal(identity, source["last_good_secret"]))
                result = await self.books.engine.call(rules, operation, **values)
                await self.media.local(self._restore_good, source)
                return result
            raise

    def _restore_good(self, old):
        with self.repo.transaction() as db:
            current = self.repo._document(db, "book_source", old["source_id"])
            if current and current["digest"] == old["digest"]:
                self.repo.put(
                    "book_source",
                    old["source_id"],
                    {
                        **current,
                        "failed_secret": current["secret"],
                        "secret": old["last_good_secret"],
                        "digest": old["last_good_digest"],
                        "code": "book_source_previous_restored",
                    },
                    db=db,
                )

    async def find(self, source, query, page=1):
        result = await self.call(source["source_id"], "search", query=query, page=page)
        books = []
        for item in result:
            if isinstance(item, dict) and item.get("name") and item.get("bookUrl"):
                books.append(await self.media.local(self.books.catalog.register, source, item))
        return books, len(result)

    async def start_search(self, body, session):
        query = body.get("query", "")
        require(isinstance(query, str) and 0 < len(query.strip()) <= 200, "invalid_input", 400)
        query = query.strip()
        now = self.repo.clock()
        for identity, search in list(self.searches.items()):
            if search["created_at"] < now - 600:
                search["task"].cancel()
                del self.searches[identity]
        # Finished result caches must not prevent a user from starting another
        # search. Retain active searches and evict the oldest completed cache.
        while len(self.searches) >= 32:
            finished = next(
                (key for key, item in self.searches.items() if item["task"].done()), None
            )
            if finished is None:
                break
            del self.searches[finished]
        require(len(self.searches) < 32, "book_search_capacity", 429)
        ids = body.get("source_ids", [])
        require(
            isinstance(ids, list)
            and len(ids) <= 100
            and all(isinstance(item, str) for item in ids),
            "invalid_input",
            400,
        )
        group = body.get("group", "")
        require(isinstance(group, str) and len(group) <= 256, "invalid_input", 400)
        offset, page = body.get("offset", 0), body.get("page", 1)
        require(
            type(offset) is int and 0 <= offset <= 20000 and type(page) is int and 1 <= page <= 100,
            "invalid_input",
            400,
        )
        rows = await self.media.local(self.repo.list, "book_source")
        rows = [
            row
            for row in rows
            if row["enabled"]
            and row["capabilities"]["search"]
            and (not ids or row["source_id"] in ids)
            and (not group or group in row["group"])
        ]
        selected = rows[offset : offset + 40]
        identity = uuid.uuid4().hex
        search = {
            "search_id": identity,
            "state": "running",
            "created_at": now,
            "session": session,
            "query": query,
            "books": [],
            "sources": [],
            "total": len(selected),
            "next_offset": offset + 40 if offset + 40 < len(rows) else None,
        }
        self.searches[identity] = search

        async def collect(source):
            try:
                found, count = await self.find(source, query, page)
                search["books"].extend(public_book(book) for book in found)
                search["sources"].append(
                    {
                        "source_id": source["source_id"],
                        "name": source["name"],
                        "code": "ready",
                        "count": count,
                    }
                )
            except Fault as error:
                search["sources"].append(
                    {
                        "source_id": source["source_id"],
                        "name": source["name"],
                        "code": error.code,
                        "count": 0,
                    }
                )
            except Exception:
                search["sources"].append(
                    {
                        "source_id": source["source_id"],
                        "name": source["name"],
                        "code": "book_rule_failed",
                        "count": 0,
                    }
                )

        async def run():
            try:
                local = await self.media.local(self.repo.list, "book")
                search["books"].extend(
                    public_book(book)
                    for book in local
                    if (book.get("artifact") or book["source_type"] != "remote")
                    and query.casefold()
                    in (
                        book["metadata"]["title"]
                        + book["metadata"].get("author", "")
                        + book["metadata"].get("isbn", "")
                    ).casefold()
                )
                await asyncio.gather(*(collect(source) for source in selected))
                search["state"] = "completed"
            except asyncio.CancelledError:
                search["state"] = "cancelled"
                raise

        search["task"] = asyncio.create_task(run())
        return self.results(identity, session)

    def results(self, identity, session):
        search = self.searches.get(identity)
        require(search and search["session"] is session, "not_found", 404)
        result = {
            key: value
            for key, value in search.items()
            if key not in {"session", "task", "created_at"}
        }
        result["books"] = list({book["book_id"]: book for book in result["books"]}.values())
        return result

    def cancel(self, identity, session):
        result = self.results(identity, session)
        self.searches[identity]["task"].cancel()
        return {**result, "state": "cancelled"}

    def save_feed(self, url, db, *, title="", interval=86400):
        require(type(interval) is int and 3600 <= interval <= 2592000, "invalid_input", 400)
        url, entry = self.books.warehouse.normalize(url)
        identity = digest(url)[:24]
        current = self.repo._document(db, "book_feed", identity)
        # Older manually saved HTTP/:443 URLs refer to the same warehouse entry.
        if entry and not current:
            for row in db.execute("SELECT body FROM documents WHERE kind='book_feed'").fetchall():
                feed = json.loads(row[0])
                try:
                    _, reference = self.books.warehouse.normalize(feed["url"])
                except Fault:
                    continue
                if reference and reference["key"] == entry["key"]:
                    identity, current = feed["feed_id"], feed
                    break
        if current:
            # Repeated selection must not reset pause, interval, scan state or source ownership.
            updated = {
                **current,
                "url": url,
                "title": current.get("title") or title,
                "warehouse_key": entry["key"] if entry else None,
                "page_url": entry["page_url"] if entry else None,
            }
            return (
                current
                if updated == current
                else self.repo.put("book_feed", identity, updated, db=db)
            )
        return self.repo.put(
            "book_feed",
            identity,
            {
                "feed_id": identity,
                "url": url,
                "title": title,
                "warehouse_key": entry["key"] if entry else None,
                "page_url": entry["page_url"] if entry else None,
                "state": "active",
                "interval_seconds": interval,
                "next_scan_at": self.repo.clock(),
                "code": "book_feed_pending",
            },
            db=db,
        )

    async def refresh_feed(self, identity):
        feed = await self.media.local(self.repo.get, "book_feed", identity)
        require(feed, "not_found", 404)
        try:
            response = await asyncio.to_thread(
                self.books.network.fetch, feed["url"], max_bytes=32 * 1024 * 1024, timeout=25
            )
            entries = json.loads(response.text)
            require(isinstance(entries, (list, dict)), "book_source_input_invalid", 400)

            def apply():
                with self.repo.transaction() as db:
                    current = self.repo._document(db, "book_feed", identity)
                    require(
                        current and current["revision"] == feed["revision"], "version_conflict", 409
                    )
                    result = self.books.catalog.import_sources(entries, db, feed_id=identity)
                    require(result["imported"] > 0, "book_source_input_invalid", 400)
                    if not result["invalid"]:
                        for record in db.execute(
                            "SELECT body FROM documents WHERE kind='book_source'"
                        ).fetchall():
                            source = json.loads(record[0])
                            if (
                                source.get("feed_id") == identity
                                and source["source_id"] not in result["source_ids"]
                            ):
                                self.repo.put(
                                    "book_source",
                                    source["source_id"],
                                    {**source, "upstream_removed": True},
                                    db=db,
                                )
                    self.repo.put(
                        "book_feed",
                        identity,
                        {
                            **feed,
                            "last_scan_at": self.repo.clock(),
                            "next_scan_at": self.repo.clock() + feed["interval_seconds"],
                            "code": "book_feed_partial" if result["invalid"] else "ready",
                            "digest": digest(entries),
                            "result": {
                                "imported": result["imported"],
                                "invalid": result["invalid"],
                                "added": sum(
                                    row["state"] == "imported" for row in result["results"]
                                ),
                                "updated": sum(
                                    row["state"] == "updated" for row in result["results"]
                                ),
                                "unchanged": sum(
                                    row["state"] == "unchanged" for row in result["results"]
                                ),
                                "reasons": {
                                    code: sum(row.get("code") == code for row in result["results"])
                                    for code in sorted(
                                        {
                                            row["code"]
                                            for row in result["results"]
                                            if row.get("code")
                                        }
                                    )
                                },
                            },
                        },
                        db=db,
                    )
                    return result

            return await self.media.local(apply)
        except (Fault, ValueError, UnicodeError) as error:
            current = await self.media.local(self.repo.get, "book_feed", identity)
            if current and current["revision"] == feed["revision"]:
                await self.media.local(
                    self.repo.put,
                    "book_feed",
                    identity,
                    {
                        **feed,
                        "last_scan_at": self.repo.clock(),
                        "next_scan_at": self.repo.clock() + min(feed["interval_seconds"], 3600),
                        "code": error.code
                        if isinstance(error, Fault)
                        else "book_source_input_invalid",
                    },
                    feed["revision"],
                )
            if not isinstance(error, Fault):
                raise Fault("book_source_input_invalid", 400) from None
            raise

    async def close(self):
        tasks = [row["task"] for row in self.searches.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.searches.clear()
