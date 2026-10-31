"""Progressive aggregated searches and source health; sessions own transient results."""

import asyncio
import uuid

from ...contracts import Fault, digest, require
from .catalog import public_comic
from .aliases import names as distinct_names
from .versions import chinese, chinese_filter_plans
from .search_text import (
    chinese_language,
    match_score,
    query_variants,
    search_names,
    source_priority,
)


class Sources:
    def __init__(self, comics):
        self.comics, self.media, self.repo = comics, comics.media, comics.repo
        self.searches = {}

    async def close(self):
        tasks = [s["task"] for s in self.searches.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.searches.clear()

    async def engine_id(self):
        row = await self.comics.settings.remote_connection("suwayomi")
        require(row.get("url"), "comic_engine_not_configured", 409)
        return digest(row["url"])[:24]

    async def refresh(self):
        rows, total = await self.comics.engine.sources()
        sources = await self.media.local(
            self.comics.catalog.save_sources, rows, await self.engine_id()
        )
        return {"sources": sources, "total": total, "truncated": total > len(rows) + 1}

    async def list(self):
        engine_id = await self.engine_id()
        rows = await self.media.local(self.repo.list, "comic_source")
        return [row for row in rows if row["engine_id"] == engine_id]

    async def search(self, body, session):
        query = body.get("query", "")
        names = search_names(query, body.get("aliases", []))
        variants = query_variants(names)
        ids = body.get("source_ids", [])
        require(
            isinstance(ids, list) and len(ids) <= 20000 and all(isinstance(v, str) for v in ids),
            "invalid_input",
            400,
        )
        language = body.get("language", "zh")
        if language in (None, ""):
            language = "zh"
        require(isinstance(language, str) and len(language) <= 20, "invalid_input", 400)
        language = language.lower().replace("_", "-")
        kind = body.get("library_kind", "general")
        require(kind in {"general", "private", "all"}, "invalid_input", 400)
        page = body.get("page", 1)
        require(type(page) is int and 1 <= page <= 100, "invalid_input", 400)
        rows = await self.list()
        if not rows:
            rows = (await self.refresh())["sources"]
        selected = [
            r
            for r in rows
            if r["enabled"]
            and r.get("available", True)
            and (not ids or r["source_id"] in ids)
            and (
                language == "all"
                or r["language"].lower().replace("_", "-") == language
                or language == "zh"
                and (chinese_language(r["language"]) or r["language"].lower() == "all")
            )
            and (kind == "all" or (r["content_warning"] == "SAFE") == (kind == "general"))
        ]
        now = self.repo.clock()
        selected.sort(
            key=lambda source: (source_priority(source, now), source["name"], source["source_id"])
        )
        for key, search in list(self.searches.items()):
            if (
                search["created_at"] < now - 600
                or len(self.searches) >= 32
                and search["task"].done()
            ):
                search["task"].cancel()
                del self.searches[key]
        require(len(self.searches) < 32, "comic_search_capacity", 429)
        search_id = uuid.uuid4().hex
        result = {
            "search_id": search_id,
            "created_at": now,
            "session": session,
            "state": "running",
            "comics": [],
            "sources": [],
            "total": len(selected),
            "query": query.strip(),
            "aliases": names[1:],
            "queries": variants,
            "language": language,
            "ranks": {},
            "page": page,
            "alias_resolution": {"state": "resolving", "names": [], "candidates": []},
        }
        self.searches[search_id] = result

        async def collect(source):
            returned, accepted, errors = {}, {}, []
            successful, attempted, has_next, search_mode = 0, 0, False, "search"
            filter_state = "not_needed"
            try:
                # One source stays bounded even when several spellings are needed.
                async with asyncio.timeout(90):
                    plans = []
                    if language == "zh" and source["language"].lower() == "all":
                        try:
                            plans = chinese_filter_plans(
                                await self.comics.engine.filters(source["source_id"])
                            )
                            filter_state = "applied" if plans else "not_provided"
                        except Fault:
                            filter_state = "unavailable"
                    for spelling in variants:
                        for plan in plans or [None]:
                            attempted += 1
                            try:
                                found = await self.comics.engine.search(
                                    source["source_id"],
                                    spelling,
                                    page,
                                    **({"filters": plan} if plan else {}),
                                )
                            except Fault as error:
                                errors.append(error.code)
                                continue
                            successful += 1
                            search_mode = found.get("search_mode", "search")
                            if plans and search_mode == "catalogue":
                                filter_state = "not_provided"
                            has_next = has_next or bool(found.get("hasNextPage"))
                            for item in found["mangas"][:200]:
                                if not isinstance(item, dict):
                                    continue
                                key = str(item.get("url") or item.get("id"))
                                returned[key] = item
                                score = match_score(names, item)
                                if not score or key in accepted:
                                    continue
                                comic = await self.media.local(
                                    self.comics.catalog.register, source, item
                                )
                                detected = comic["language_evidence"]["language"]
                                if (
                                    language == "zh"
                                    and detected != "unknown"
                                    and not chinese(detected)
                                ):
                                    continue
                                accepted[key] = comic["comic_id"]
                                result["comics"].append(
                                    {
                                        **public_comic(comic),
                                        "chinese_available": bool(found.get("filters_applied")),
                                    }
                                )
                                result["ranks"][comic["comic_id"]] = (
                                    0 if chinese(detected) else 1,
                                    -score,
                                    source_priority(source, now),
                                    source["name"],
                                    comic["comic_id"],
                                )
                            if search_mode == "catalogue":
                                break
                        # Catalogue fallback is the same page for every spelling.
                        if search_mode == "catalogue":
                            break
            except Fault as error:
                errors.append(error.code)
            except TimeoutError:
                errors.append("comic_engine_timeout")
            except (KeyError, ValueError, TypeError):
                errors.append("comic_engine_invalid_response")
            status = {
                "source_id": source["source_id"],
                "name": source["name"],
                "code": "comic_search_partial"
                if errors and successful
                else errors[0]
                if errors
                else "ready",
                "count": len(accepted),
                "filtered": len(returned) - len(accepted),
                "search_mode": search_mode,
                "has_next_page": has_next,
                "query_count": attempted,
                "query_errors": errors,
                "language_filter": filter_state,
            }
            result["sources"].append(status)

            await self.media.local(
                self.comics.health.record,
                source,
                "search",
                status["code"],
                facts={
                    "count": status["count"],
                    "returned": status["count"] + status.get("filtered", 0),
                    "filtered": status.get("filtered", 0),
                    "search_mode": status.get("search_mode", "search"),
                    "query_count": attempted,
                    "query_errors": errors,
                },
            )

        async def run():
            nonlocal names, variants
            try:
                if language == "zh":
                    resolved = await self.comics.aliases.resolve(
                        query, body.get("alias_subject", "")
                    )
                    result["alias_resolution"] = resolved
                    names = distinct_names([*names, *resolved["names"]])[:9]
                    variants = query_variants(names)[:18]
                    result["queries"] = variants
                else:
                    result["alias_resolution"] = {
                        "state": "not_requested",
                        "names": [],
                        "candidates": [],
                    }
                # Bounded workers cover every selected source without a task per plugin.
                remaining = iter(selected)

                async def worker():
                    for source in remaining:
                        await collect(source)

                await asyncio.gather(*(worker() for _ in range(min(4, len(selected)))))
                result["state"] = "completed"
            except asyncio.CancelledError:
                result["state"] = "cancelled"
                raise
            except Fault as error:
                result["state"] = "failed"
                result["code"] = error.code
            except Exception:
                result["state"] = "failed"
                result["code"] = "comic_search_failed"

        result["task"] = asyncio.create_task(run())
        return self.results(search_id, session)

    def results(self, identity, session):
        search = self.searches.get(identity)
        require(search and search["session"] is session, "not_found", 404)
        return {
            **{
                k: v
                for k, v in search.items()
                if k not in {"session", "task", "created_at", "comics", "sources", "ranks"}
            },
            "comics": sorted(
                {c["comic_id"]: c for c in search["comics"]}.values(),
                key=lambda comic: search["ranks"][comic["comic_id"]],
            ),
            "sources": list(search["sources"]),
        }

    def cancel(self, identity, session):
        result = self.results(identity, session)
        self.searches[identity]["task"].cancel()
        return {**result, "state": "cancelled"}
