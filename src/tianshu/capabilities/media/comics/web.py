"""Same-origin comic commands reuse the existing console authority and receipts."""

from ...contracts import Fault, require
from .catalog import public_comic
from .versions import version_options, choose_version, eligible


class WebComics:
    def __init__(self, web):
        self.web, self.media, self.comics = web, web.media, web.media.comics

    async def route(self, operation, body, session):
        await self.web.live(session)
        comics, repo, local = self.comics, self.media.repo, self.media.local
        if operation == "view":
            return await comics.view()
        if operation == "extensions/view":
            return await comics.extensions.view(body)
        if operation == "extensions/control":
            identity = await comics.sources.engine_id()
            return await self.web.receipt(
                body,
                "comics/extensions/control",
                session,
                lambda db: comics.extensions.control(body, identity, db),
            )
        if operation == "settings/save":
            return await self.web.receipt(
                body, "comics/settings/save", session, lambda db: comics.settings.save(body, db)
            )
        if operation == "connections/probe":
            result = {}
            for name, call in (
                ("suwayomi", comics.engine.probe),
                ("komga", comics.komga.libraries),
                ("assetlibrary", comics.downloads.archive.probe),
            ):
                try:
                    result[name] = {"state": "ready", "value": await call()}
                except Fault as error:
                    result[name] = {"state": "unavailable", "code": error.code}
            settings = await local(comics.settings.read)
            if settings.get("import_socket"):
                try:
                    reader = await local(comics.imports.access)
                    result["inputs"] = {"state": "ready", "value": await local(reader.probe)}
                except Fault as error:
                    result["inputs"] = {"state": "unavailable", "code": error.code}
            if settings.get("publisher_socket"):
                try:
                    result["publisher"] = {
                        "state": "ready",
                        "value": await comics.downloads.publisher.probe(
                            settings["publisher_socket"]
                        ),
                    }
                except Fault as error:
                    result["publisher"] = {"state": "unavailable", "code": error.code}
            return result
        if operation == "sources/list":
            return {"sources": await comics.sources.list()}
        if operation == "sources/refresh":
            return await comics.sources.refresh()
        if operation == "sources/control":
            require(
                isinstance(body.get("source_key"), str) and type(body.get("enabled")) is bool,
                "invalid_input",
                400,
            )

            def control(db):
                current = repo._document(db, "comic_source", body["source_key"])
                require(current, "not_found", 404)
                return {
                    "source": repo.put(
                        "comic_source",
                        body["source_key"],
                        {**current, "enabled": body["enabled"]},
                        body.get("expected_revision"),
                        db=db,
                    )
                }

            return await self.web.receipt(body, "comics/sources/control", session, control)
        if operation == "search/start":
            return await comics.sources.search(body, session)
        if operation == "search/results":
            return comics.sources.results(body.get("search_id"), session)
        if operation == "search/cancel":
            return comics.sources.cancel(body.get("search_id"), session)
        if operation == "detail":
            comic, chapters = await comics.detail(
                body.get("comic_id"), refresh=body.get("refresh") is True
            )
            offset = body.get("offset", 0)
            require(type(offset) is int and 0 <= offset <= 20000, "invalid_input", 400)
            versions = version_options(comic, chapters)
            selected = body.get("chapter_version")
            if selected == "preferred":
                selected = versions[0]["id"] if versions else "all"
            if selected and selected != "all":
                version = next((v for v in versions if v["id"] == selected), None)
                require(version, "comic_chinese_version_required", 409)
                chapters = [
                    c
                    for c in chapters
                    if c.get("language_evidence", {}).get("language") == version["language"]
                    and c.get("scanlator", "") == version["scanlator"]
                ]
            page = chapters[offset : offset + 200]
            jobs = await local(comics.catalog.chapter_jobs, comic, page)
            return {
                "comic": public_comic(comic),
                "chapters": [
                    {**chapter, "job": jobs.get(chapter["chapter_id"])} for chapter in page
                ],
                "total": len(chapters),
                "next_offset": offset + 200 if offset + 200 < len(chapters) else None,
                "scanlators": sorted({c["scanlator"] for c in chapters if c.get("scanlator")}),
                "versions": versions,
            }
        if operation == "metadata/save":

            def metadata_save(db):
                comic = comics.catalog.comic(body.get("comic_id"), db)
                incoming = body.get("metadata")
                limits = {
                    "title": 500,
                    "author": 500,
                    "artist": 500,
                    "description": 20000,
                    "language": 20,
                    "direction": 10,
                }
                require(
                    isinstance(incoming, dict) and set(incoming) <= set(limits),
                    "invalid_input",
                    400,
                )
                require(
                    all(isinstance(v, str) and len(v) <= limits[k] for k, v in incoming.items()),
                    "invalid_input",
                    400,
                )
                metadata = {**comic["metadata"], **incoming}
                require(
                    metadata["title"].strip()
                    and metadata["direction"] in {"rtl", "ltr", "vertical"},
                    "invalid_input",
                    400,
                )
                updated = repo.put(
                    "comic",
                    comic["comic_id"],
                    {
                        **comic,
                        "metadata": metadata,
                        "manual_fields": list(
                            dict.fromkeys([*comic.get("manual_fields", []), *incoming])
                        ),
                        "updated_at": repo.clock(),
                    },
                    body.get("expected_revision"),
                    db=db,
                )
                return {"comic": public_comic(updated)}

            return await self.web.receipt(body, "comics/metadata/save", session, metadata_save)
        if operation == "library/view":
            offset, query = body.get("offset", 0), body.get("query", "")
            kind = body.get("library_kind", "general")
            require(
                type(offset) is int
                and 0 <= offset <= 100000
                and isinstance(query, str)
                and len(query) <= 200
                and kind in {"general", "private", "all"},
                "invalid_input",
                400,
            )
            rows = await local(repo.list, "comic")
            rows = [
                r
                for r in rows
                if (r.get("downloaded") or r["source_type"] == "local")
                and (kind == "all" or r["library_kind"] == kind)
                and query.casefold()
                in (r["metadata"]["title"] + " " + r["metadata"].get("author", "")).casefold()
            ]
            rows.sort(key=lambda r: (-r["updated_at"], r["comic_id"]))
            return {
                "comics": [public_comic(r) for r in rows[offset : offset + 100]],
                "total": len(rows),
                "next_offset": offset + 100 if offset + 100 < len(rows) else None,
            }
        if operation == "imports/browse":
            return await local(
                comics.imports.browse, body.get("relative_path", ""), body.get("offset", 0)
            )
        if operation == "imports/inspect":
            inspected = await local(comics.imports.inspect, body.get("relative_path", ""))
            return {
                **inspected,
                "chapters": [
                    {k: v for k, v in c.items() if k != "input_files"}
                    for c in inspected["chapters"]
                ],
            }
        if operation == "imports/enqueue":
            inspected = await local(comics.imports.inspect, body.get("relative_path", ""))
            require(
                inspected["input_digest"] == body.get("input_digest"), "comic_input_changed", 409
            )
            title = body.get("title", inspected["title"])
            require(isinstance(title, str) and 0 < len(title.strip()) <= 500, "invalid_input", 400)
            # Registration is an idempotent local snapshot; network/file publication is queued.
            comic, chapters = await local(
                comics.catalog.local_import,
                inspected["relative_path"],
                title.strip(),
                inspected["chapters"],
                body.get("library_kind", "general"),
            )

            def enqueue_import(db):
                jobs = comics.catalog.enqueue(comic, chapters, db=db)
                return {"comic": public_comic(comic), "job_ids": [j["job_id"] for j in jobs]}

            result = await self.web.receipt(body, "comics/imports/enqueue", session, enqueue_import)
            self.media.wake.set()
            return result
        if operation in {"downloads/enqueue", "subscriptions/save"}:
            comic, chapters = await comics.detail(body.get("comic_id"))

            def enqueue_remote(db):
                current = comics.catalog.comic(comic["comic_id"], db)
                kind = body.get("library_kind", current["library_kind"])
                require(kind in {"general", "private"}, "invalid_input", 400)
                if kind != current["library_kind"]:
                    current = repo.put(
                        "comic", current["comic_id"], {**current, "library_kind": kind}, db=db
                    )
                if operation == "subscriptions/save":
                    return comics.catalog.subscribe(comic["comic_id"], body, db)
                chosen = body.get("chapter_ids")
                require(
                    isinstance(chosen, list)
                    and 0 < len(chosen) <= 2000
                    and all(isinstance(c, str) for c in chosen),
                    "invalid_input",
                    400,
                )
                selected = [c for c in chapters if c["chapter_id"] in set(chosen)]
                require(len(selected) == len(set(chosen)), "comic_chapter_changed", 409)
                require(
                    not any(c.get("source_missing") for c in selected), "comic_chapter_missing", 409
                )
                policy = choose_version(current, chapters, body) if body.get("version_id") else None
                if policy:
                    allowed = {
                        c["chapter_id"]
                        for c in eligible(current, chapters, {"version_policy": policy})
                    }
                    require(
                        all(c["chapter_id"] in allowed for c in selected),
                        "comic_chinese_version_required",
                        409,
                    )
                jobs = comics.catalog.enqueue(current, selected, db=db, version_policy=policy)
                return {"job_ids": [j["job_id"] for j in jobs]}

            result = await self.web.receipt(body, "comics/" + operation, session, enqueue_remote)
            self.media.wake.set()
            return result
        if operation == "subscriptions/control":
            action = body.get("action")
            require(action in {"pause", "resume", "interval", "remove"}, "invalid_input", 400)

            def subscription_control(db):
                comic_id = comics.catalog.comic(body.get("comic_id"), db)["comic_id"]
                current = repo._document(db, "comic_subscription", comic_id)
                require(current and current.get("state") != "removed", "not_found", 404)
                if action == "remove":
                    repo.remove_subscription(
                        "comic_subscription",
                        comic_id,
                        body.get("expected_revision"),
                        "comics:" + comic_id,
                        db=db,
                    )
                    return {"removed": True}
                interval = body.get("interval_seconds", current["interval_seconds"])
                require(type(interval) is int and 3600 <= interval <= 2592000, "invalid_input", 400)
                state = (
                    "paused"
                    if action == "pause"
                    else "active"
                    if action == "resume"
                    else current["state"]
                )
                return {
                    "subscription": repo.put(
                        "comic_subscription",
                        comic_id,
                        {
                            **current,
                            "state": state,
                            "interval_seconds": interval,
                            "next_scan_at": repo.clock()
                            if action == "resume"
                            else repo.clock() + interval,
                        },
                        body.get("expected_revision"),
                        db=db,
                    )
                }

            return await self.web.receipt(
                body, "comics/subscriptions/control", session, subscription_control
            )
        if operation == "subscriptions/scan":
            result = await comics.scan(body.get("comic_id"))
            await self.web.live(session)
            return result
        raise Fault("not_found", 404)
