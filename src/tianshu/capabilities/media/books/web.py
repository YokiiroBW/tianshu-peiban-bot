"""Book commands reuse the console session, CSRF and durable media receipts."""

import base64
import hashlib
import json

from ...contracts import Fault, digest, require
from .catalog import public_book, public_source
from .documents import import_file


class WebBooks:
    def __init__(self, web):
        self.web, self.media = web, web.media
        self.books = web.media.books

    async def route(self, operation, body, session):
        await self.web.live(session)
        books, repo, local = self.books, self.media.repo, self.media.local
        if operation.startswith("reading/"):
            return await books.reading.web(self, operation[8:], body, session)
        if operation.startswith("audio/"):
            from .audio.web import route

            return await route(self, operation[6:], body, session)
        if operation == "view":
            return await books.view()
        if operation == "warehouse/browse":
            return await books.warehouse.browse(body)
        if operation == "warehouse/detail":
            return await books.warehouse.detail(body)
        if operation == "warehouse/subscribe":
            entries = books.warehouse.selection(body.get("entries"))

            def subscribe_sources(db):
                return {
                    "feeds": [
                        books.sources.save_feed(
                            entry["url"],
                            db,
                            title=entry["title"],
                            interval=body.get("interval_seconds", 86400),
                        )
                        for entry in entries
                    ]
                }

            result = await self.web.receipt(
                body, "books/warehouse/subscribe", session, subscribe_sources
            )
            self.media.wake.set()
            return result
        if operation == "settings/save":
            return await self.web.receipt(
                body,
                "books/settings/save",
                session,
                lambda db: books.catalog.save_settings(body, db),
            )
        if operation == "sources/import":
            entries = body.get("sources")
            if isinstance(entries, str):
                try:
                    entries = json.loads(entries)
                except ValueError:
                    raise Fault("book_source_input_invalid", 400) from None
            result = await self.web.receipt(
                {"client_request_id": body.get("client_request_id"), "digest": digest(entries)},
                "books/sources/import",
                session,
                lambda db: books.catalog.import_sources(entries, db),
            )
            return result
        if operation == "sources/list":
            offset = body.get("offset", 0)
            require(type(offset) is int and 0 <= offset <= 20000, "invalid_input", 400)
            query = body.get("query", "")
            require(isinstance(query, str) and len(query) <= 256, "invalid_input", 400)
            rows = await local(repo.list, "book_source")
            rows = [
                row for row in rows if query.casefold() in (row["name"] + row["group"]).casefold()
            ]
            return {
                "sources": [public_source(row) for row in rows[offset : offset + 500]],
                "total": len(rows),
            }
        if operation == "sources/control":
            require(type(body.get("enabled")) is bool, "invalid_input", 400)

            def control(db):
                current = repo._document(db, "book_source", body["source_id"])
                require(current, "not_found", 404)
                return {
                    "source": public_source(
                        repo.put(
                            "book_source",
                            current["source_id"],
                            {**current, "enabled": body["enabled"]},
                            body.get("expected_revision"),
                            db=db,
                        )
                    )
                }

            return await self.web.receipt(body, "books/sources/control", session, control)
        if operation == "sources/export":
            source = await local(repo.get, "book_source", body.get("source_id"))
            require(source, "not_found", 404)
            rules = json.loads(self.media.accounts.unseal(source["source_id"], source["secret"]))
            try:
                headers = json.loads(rules.get("header") or "{}")
                rules["header"] = json.dumps(
                    {
                        key: value
                        for key, value in headers.items()
                        if key.lower() not in {"cookie", "authorization"}
                    },
                    ensure_ascii=False,
                )
            except (ValueError, AttributeError):
                rules["header"] = ""
            return {"sources": [rules]}
        if operation == "feeds/save":
            result = await self.web.receipt(
                body,
                "books/feeds/save",
                session,
                lambda db: {"feed": books.sources.save_feed(body.get("url"), db)},
            )
            self.media.wake.set()
            return result
        if operation == "feeds/refresh":
            return await books.sources.refresh_feed(body.get("feed_id"))
        if operation == "feeds/control":
            require(
                body.get("action") in {"pause", "resume", "remove", "interval"},
                "invalid_input",
                400,
            )

            def feed_control(db):
                feed = repo._document(db, "book_feed", body["feed_id"])
                require(feed, "not_found", 404)
                require(feed["revision"] == body.get("expected_revision"), "version_conflict", 409)
                interval = body.get("interval_seconds", feed["interval_seconds"])
                require(type(interval) is int and 3600 <= interval <= 2592000, "invalid_input", 400)
                if body["action"] == "remove":
                    db.execute(
                        "DELETE FROM documents WHERE kind='book_feed' AND id=%s", (body["feed_id"],)
                    )
                    return {"removed": True}
                return {
                    "feed": repo.put(
                        "book_feed",
                        body["feed_id"],
                        {
                            **feed,
                            "state": "active"
                            if body["action"] == "resume"
                            else "paused"
                            if body["action"] == "pause"
                            else feed["state"],
                            "interval_seconds": interval,
                            "code": "book_feed_pending"
                            if body["action"] == "resume"
                            else feed["code"],
                            "next_scan_at": repo.clock() + interval
                            if body["action"] == "interval" and feed.get("last_scan_at")
                            else repo.clock(),
                        },
                        body.get("expected_revision"),
                        db=db,
                    )
                }

            return await self.web.receipt(body, "books/feeds/control", session, feed_control)
        if operation == "search/start":
            return await books.sources.start_search(body, session)
        if operation == "search/results":
            return books.sources.results(body.get("search_id"), session)
        if operation == "search/cancel":
            return books.sources.cancel(body.get("search_id"), session)
        if operation in {"detail", "chapters"}:
            book, chapters = await books.detail(
                body.get("book_id"), refresh=body.get("refresh") is True
            )
            offset = body.get("offset", 0)
            require(type(offset) is int and 0 <= offset <= 20000, "invalid_input", 400)
            return {
                "book": public_book(book),
                "chapters": [
                    {key: chapter.get(key) for key in ("chapter_id", "title", "order", "is_vip")}
                    for chapter in chapters[offset : offset + 100]
                ],
                "total": len(chapters),
                "offset": offset,
            }
        if operation == "preview":
            return await books.preview(
                body.get("book_id"), body.get("chapter_id"), refresh=body.get("refresh") is True
            )
        if operation in {"subscriptions/save", "downloads/create", "library/rebuild"}:

            def enqueue(db):
                if operation == "subscriptions/save":
                    return books.catalog.subscribe(body, db)
                book = books.catalog.logical_book(body["book_id"], db)
                job = books.catalog.enqueue(book, db=db, refresh=operation == "downloads/create")
                return {"job_id": job["job_id"]}

            result = await self.web.receipt(body, "books/" + operation, session, enqueue)
            self.media.wake.set()
            return result
        if operation == "subscriptions/scan":
            return await books.scan(body.get("book_id"))
        if operation == "subscriptions/control":
            require(
                body.get("action") in {"pause", "resume", "remove", "interval"},
                "invalid_input",
                400,
            )

            def subscription_control(db):
                current = repo._document(db, "book_subscription", body["book_id"])
                require(current and current.get("state") != "removed", "not_found", 404)
                require(
                    current and current["revision"] == body.get("expected_revision"),
                    "version_conflict",
                    409,
                )
                if body["action"] == "remove":
                    repo.remove_subscription(
                        "book_subscription",
                        body["book_id"],
                        body["expected_revision"],
                        "books:" + body["book_id"],
                        db=db,
                    )
                    return {"removed": True}
                interval = body.get("interval_seconds", current["interval_seconds"])
                require(type(interval) is int and 3600 <= interval <= 2592000, "invalid_input", 400)
                state = (
                    "active"
                    if body["action"] == "resume"
                    else "paused"
                    if body["action"] == "pause"
                    else current["state"]
                )
                return {
                    "subscription": repo.put(
                        "book_subscription",
                        body["book_id"],
                        {
                            **current,
                            "state": state,
                            "interval_seconds": interval,
                            "next_scan_at": repo.clock()
                            if body["action"] == "resume"
                            else repo.clock() + interval,
                        },
                        current["revision"],
                        db=db,
                    )
                }

            return await self.web.receipt(
                body, "books/subscriptions/control", session, subscription_control
            )
        if operation == "metadata/search":
            book = await local(books.catalog.book, body.get("book_id"))
            query = body.get("query")
            require(
                query is None or isinstance(query, str) and len(query) <= 300, "invalid_input", 400
            )
            return await books.metadata.search(book, query)
        if operation == "metadata/apply":
            result = await self.web.receipt(
                body,
                "books/metadata/apply",
                session,
                lambda db: books.catalog.apply_metadata(body, db),
            )
            self.media.wake.set()
            return result
        if operation == "library/view":
            offset = body.get("offset", 0)
            query = body.get("query", "")
            require(isinstance(query, str) and len(query) <= 300, "invalid_input", 400)
            query = query.casefold()
            require(type(offset) is int and 0 <= offset <= 100000, "invalid_input", 400)
            rows = await local(repo.list, "book")
            rows = [
                row
                for row in rows
                if (row.get("artifact") or row["source_type"] != "remote")
                and query
                in (row["metadata"]["title"] + row["metadata"].get("author", "")).casefold()
            ]
            return {
                "books": [public_book(row) for row in rows[offset : offset + 100]],
                "total": len(rows),
                "offset": offset,
            }
        if operation == "library/import":
            filename, encoded = body.get("filename"), body.get("content_base64")
            require(isinstance(filename,str) and 0<len(filename)<=512,"invalid_input",400)
            if body.get("media"):
                from tianshu.types import MediaRef
                reference=MediaRef(**body["media"])
                require(reference.size_bytes is not None and reference.size_bytes<=64*1024*1024,"book_import_too_large",413)
                raw,_=await self.media.capabilities.artifacts.resolve(session,reference)
            else:
                require(isinstance(encoded,str),"invalid_input",400)
                try:raw=base64.b64decode(encoded,validate=True)
                except ValueError:raise Fault("invalid_input",400) from None
            prepared = await local(import_file, filename, raw)
            identity = hashlib.sha256(raw).hexdigest()[:24]
            await self.web.live(session)
            await local(books.save_import_file, identity, raw, prepared[2])
            safe = {
                "client_request_id": body.get("client_request_id"),
                "filename": filename,
                "content_hash": hashlib.sha256(raw).hexdigest(),
            }

            def register(db):
                result = books.register_import(filename, raw, prepared, db=db)
                if books.catalog.settings(db)["directory"]:
                    book = books.catalog.book(identity, db)
                    result["job_id"] = books.catalog.enqueue(book, db=db, refresh=False)["job_id"]
                return result

            result = await self.web.receipt(safe, "books/library/import", session, register)
            self.media.wake.set()
            return result
        raise Fault("not_found", 404)
