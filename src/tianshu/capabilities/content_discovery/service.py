"""Bounded candidate discovery; providers fail independently and never mark content read."""

import asyncio
import base64
import json
import time
from urllib.parse import urlencode, urlsplit

from ..contracts import Fault, canonical, digest, require
from .candidates import candidate, public_url, select

SOURCES = ("web", "bilibili", "music", "books", "comics")


class Discovery:
    def __init__(self, media=None, *, local=asyncio.to_thread, clock=time.time):
        self.media, self.local, self.clock = media, local, clock

    @staticmethod
    def position(config, query, source, cursor):
        if not cursor:
            return {}
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            require(
                value["scope"] == digest([config, query, source])
                and isinstance(value["position"], dict),
                "invalid_input",
                400,
            )
            return value["position"]
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise Fault("invalid_input", 400) from None

    @staticmethod
    def cursor(config, query, source, position):
        if position is None:
            return None
        value = {"scope": digest([config, query, source]), "position": position}
        return base64.urlsafe_b64encode(canonical(value).encode()).decode()

    async def search(self, config, query, page, limit, acquired, request, position):
        if not config.get("base_url"):
            return [], "not_configured", None
        base = config["base_url"]
        public_url(base)
        parsed = urlsplit(base)
        require(not parsed.query and not parsed.fragment, "invalid_input", 400)
        url = (
            base.rstrip("/")
            + "/search?"
            + urlencode({"q": query, "format": "json", "pageno": page})
        )
        response = await request(
            {"method": "GET", "url": url, "timeout_seconds": 8, "max_response_bytes": 384 * 1024}
        )
        require(response["status"] == 200, "dependency_unavailable", 503)
        body = response["body"]
        require(
            isinstance(body, dict) and isinstance(body.get("results"), list), "invalid_input", 400
        )
        offset = position.get("offset", 0)
        require(type(offset) is int and 0 <= offset <= len(body["results"]), "invalid_input", 400)
        fingerprint = digest(body["results"])
        require(
            not position
            or (position.get("page") == page and position.get("snapshot") == fingerprint),
            "version_conflict",
            409,
        )
        rows, invalid = [], False
        for result in body["results"][offset : offset + limit]:
            try:
                url = public_url(result["url"])
                rows.append(
                    candidate(
                        "web",
                        "url:" + digest(url),
                        kind="webpage",
                        title=result.get("title"),
                        authors=[result.get("author")],
                        summary=result.get("content", ""),
                        url=url,
                        published=result.get("publishedDate"),
                        acquired=acquired,
                        summary_kind="search_snippet",
                    )
                )
            except (Fault, KeyError, TypeError, ValueError):
                invalid = True
        return (
            rows,
            "partial" if invalid or body.get("unresponsive_engines") else "ok",
            {"page": page, "offset": offset + limit, "snapshot": fingerprint}
            if len(body["results"]) > offset + limit
            else None,
        )

    async def discover(self, config, value, *, request):
        acquired = self.clock()
        query, limit, page = (
            value.get("query", "").strip(),
            value.get("limit", 8),
            value.get("page", 1),
        )
        sources = value.get("sources", list(SOURCES))
        positions = {
            source: self.position(config, query, source, value.get("cursors", {}).get(source))
            for source in sources
        }

        async def one(source):
            try:
                async with asyncio.timeout(10):
                    if source == "web":
                        if not query:
                            return [], {
                                "source": source,
                                "state": "not_requested",
                                "code": None,
                                "has_more": None,
                                "next_cursor": None,
                            }
                        rows, state, following = await self.search(
                            config, query, page, limit, acquired, request, positions[source]
                        )
                        more = True if following else None
                    elif self.media is None:
                        rows, state, more = [], "not_configured", None
                        following = None
                    else:
                        rows, state, following = await self.local(
                            self.media.read, source, config, positions[source], limit, acquired
                        )
                        more = following is not None
                    code = (
                        "source_partial"
                        if state == "partial"
                        else "not_configured"
                        if state == "not_configured"
                        else None
                    )
                    return rows, {
                        "source": source,
                        "state": state,
                        "code": code,
                        "has_more": more,
                        "next_cursor": self.cursor(config, query, source, following),
                    }
            except TimeoutError:
                code = "source_timeout"
            except (Fault, ValueError, KeyError, TypeError, OSError):
                code = "source_unavailable"
            return [], {
                "source": source,
                "state": "unavailable",
                "code": code,
                "has_more": None,
                "next_cursor": None,
            }

        results = await asyncio.gather(*(one(source) for source in sources))
        rows, omitted = select(
            [item[0] for item in results],
            query,
            value.get("exclude_keys", []),
            limit * len(sources),
        )
        answer = {
            "candidates": rows,
            "sources": [item[1] for item in results],
            "omitted_count": omitted,
            "seen_state": "unknown",
        }
        require(len(canonical(answer).encode()) <= 512 * 1024, "budget_exceeded", 413)
        return answer
