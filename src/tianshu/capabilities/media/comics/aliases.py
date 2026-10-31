"""Bounded public bibliographic lookups; names are evidence, never translations."""

import asyncio
import json
import re

import aiohttp

from ...contracts import Fault, digest, require
from .search_text import normalize

USER_AGENT = "YokiiroBW/tianshu-platform/1.0 (https://github.com/YokiiroBW/tianshu-platform)"


def names(values):
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str) or not 0 < len(value.strip()) <= 200:
            continue
        key = normalize(value).replace(" ", "")
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result[:24]


def exact(query, values):
    key = normalize(query).replace(" ", "")
    return any(key == normalize(v).replace(" ", "") for v in values)


def chinese_name(value):
    return bool(re.search(r"[\u3400-\u9fff]", value)) and not re.search(r"[\u3040-\u30ff]", value)


def subject(row):
    if row.get("type") != 1 or row.get("platform") not in {"漫画", "マンガ", "Manga"}:
        return None
    values = [row.get("name"), row.get("name_cn")]
    authors = []
    for info in row.get("infobox") or []:
        key, value = info.get("key"), info.get("value")
        if key in {"别名", "中文名", "英文名", "日文名", "原名"}:
            values.extend(
                [value]
                if isinstance(value, str)
                else [v.get("v") for v in value or [] if isinstance(v, dict)]
            )
        elif isinstance(key, str) and key.startswith("版本:") and isinstance(value, list):
            values.extend(
                v.get("v")
                for v in value
                if isinstance(v, dict) and v.get("k") in {"版本名", "别名"}
            )
        elif key == "作者":
            authors.extend(
                [value]
                if isinstance(value, str)
                else [v.get("v") for v in value or [] if isinstance(v, dict)]
            )
    collected = names(values)
    if not collected or type(row.get("id")) is not int:
        return None
    return {
        "id": "bangumi:" + str(row["id"]),
        "title": row.get("name_cn") or row.get("name"),
        "names": collected,
        "authors": names(authors),
        "provider": "Bangumi",
        "url": "https://bgm.tv/subject/" + str(row["id"]),
    }


class Aliases:
    def __init__(self, comics):
        self.comics, self.repo = comics, comics.repo
        self.session = None
        self.parallel = asyncio.Semaphore(2)

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def request(self, provider, path, body=None):
        base = {"bangumi": "https://api.bgm.tv", "anilist": "https://graphql.anilist.co"}[provider]
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5, sock_connect=3),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                headers={"User-Agent": USER_AGENT},
            )
        try:
            async with self.session.request(
                "POST" if body is not None else "GET", base + path, json=body, allow_redirects=False
            ) as response:
                require(response.status == 200, "comic_alias_provider_unavailable", 503)
                chunks, length = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    length += len(chunk)
                    require(length <= 1024 * 1024, "comic_alias_provider_unavailable", 503)
                    chunks.append(chunk)
                value = json.loads(b"".join(chunks))
                require(
                    isinstance(value, dict) and not value.get("errors"),
                    "comic_alias_provider_unavailable",
                    503,
                )
                return value
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise Fault("comic_alias_provider_unavailable", 503) from None

    async def bangumi(self, query):
        result = await self.request(
            "bangumi",
            "/v0/search/subjects?limit=6",
            {"keyword": query, "sort": "match", "filter": {"type": [1]}},
        )
        rows = result.get("data")
        require(isinstance(rows, list), "comic_alias_provider_unavailable", 503)
        candidates = []
        for row in rows[:6]:
            if row.get("platform") != "漫画" or type(row.get("id")) is not int:
                continue
            parsed = subject(await self.request("bangumi", "/v0/subjects/" + str(row["id"])))
            if parsed:
                candidates.append(parsed)
        return candidates

    async def anilist(self, query):
        response = await self.request(
            "anilist",
            "",
            {
                "query": "query($search:String!){Page(perPage:6){media(search:$search,type:MANGA,format:MANGA){id title{romaji english native} synonyms}}}",
                "variables": {"search": query},
            },
        )
        rows = response.get("data", {}).get("Page", {}).get("media", [])
        matches = []
        for row in rows[:6]:
            title = row.get("title") or {}
            values = names([*title.values(), *(row.get("synonyms") or [])])
            if exact(query, values):
                matches.append(
                    (values, title.get("english") or title.get("romaji") or title.get("native"))
                )
        return matches

    def save(self, key, value):
        with self.repo.transaction() as db:
            self.repo.put("comic_alias", key, value, db=db)
            # This namespace contains only replaceable bibliographic cache entries.
            db.execute(
                "DELETE FROM documents WHERE kind='comic_alias' AND id NOT IN (SELECT id FROM documents WHERE kind='comic_alias' ORDER BY (body::jsonb #>> '{checked_at}')::double precision DESC LIMIT 256)"
            )

    async def resolve(self, query, selected=""):
        require(isinstance(selected, str) and len(selected) <= 80, "invalid_input", 400)
        key = digest({"query": normalize(query), "version": 1})[:24]
        cached = await self.comics.media.local(self.repo.get, "comic_alias", key)
        now = self.repo.clock()
        if cached and cached.get("expires_at", 0) > now:
            return self.choose(cached, selected)
        candidates, bridges, errors = [], [], []
        try:
            async with asyncio.timeout(18), self.parallel:
                try:
                    candidates = await self.bangumi(query)
                except (Fault, KeyError, TypeError, AttributeError, ValueError):
                    errors.append("Bangumi")
                direct = [c for c in candidates if exact(query, c["names"])]
                if not direct:
                    try:
                        bridges = await self.anilist(query)
                        if len(bridges) == 1:
                            bridge_names, lookup = bridges[0]
                            if lookup and not exact(lookup, [query]):
                                linked = await self.bangumi(lookup)
                                for candidate in linked:
                                    if exact(lookup, candidate["names"]):
                                        candidate["names"] = names(
                                            [*candidate["names"], *bridge_names]
                                        )
                                        candidate["via"] = "AniList"
                                candidates.extend(linked)
                    except (Fault, KeyError, TypeError, AttributeError, ValueError):
                        errors.append("AniList/Bangumi")
        except TimeoutError:
            errors.append("timeout")
        unique = {c["id"]: c for c in candidates}
        candidates = list(unique.values())[:12]
        matched = [c for c in candidates if exact(query, c["names"])]
        value = {
            "query": query,
            "candidates": candidates,
            "matched_ids": [c["id"] for c in matched],
            "provider_unavailable": bool(errors),
            "checked_at": now,
            "expires_at": now + (7 * 86400 if len(matched) == 1 else 60 if errors else 900),
        }
        await self.comics.media.local(self.save, key, value)
        return self.choose(value, selected)

    @staticmethod
    def choose(value, selected):
        candidates = value["candidates"]
        ids = [selected] if selected else value["matched_ids"]
        chosen = next((c for c in candidates if c["id"] == ids[0]), None) if len(ids) == 1 else None
        if selected and not value["provider_unavailable"]:
            require(chosen is not None, "comic_alias_choice_expired", 409)
        state = (
            "ready"
            if chosen
            else "ambiguous"
            if len(ids) > 1
            else "unavailable"
            if value["provider_unavailable"]
            else "unmatched"
        )
        expanded = names(chosen["names"]) if chosen else []
        expanded.sort(key=lambda value: not chinese_name(value))
        return {
            "state": state,
            "selected": chosen,
            "names": expanded[:6],
            "candidates": candidates,
            "provider_unavailable": value["provider_unavailable"],
        }
