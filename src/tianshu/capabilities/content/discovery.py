"""Public Bilibili discovery reuses the existing authenticated/signature connector."""

import asyncio
import time

from ..content_discovery.candidates import candidate
from ..content_discovery.service import Discovery
from ..contracts import Fault, require
from ..media.bilibili import Bilibili


class BilibiliDiscovery:
    def __init__(self, *, connector_factory=Bilibili):
        self.connector_factory = connector_factory

    async def page(self, config, body, kind, *, creator=None):
        query, limit = body.get("query", ""), min(20, body.get("limit", 8))
        identity = kind + (":" + creator if creator else "")
        position = Discovery.position(config, query, identity, body.get("cursors", {}).get(identity))
        page = position.get("page", body.get("page", 1))
        offset = position.get("offset", 0)
        require(type(page) is int and 1 <= page <= 100 and type(offset) is int and offset >= 0,
                "invalid_discovery_cursor", 400)
        connector = self.connector_factory()
        try:
            if kind == "bilibili_search":
                data = await connector.request("/x/web-interface/search/type", params={
                    "search_type": "video", "keyword": query, "page": page, "page_size": 20})
                items, page_size = data.get("result", []), 20
                more = page < int(data.get("numPages") or 0)
            elif kind == "bilibili_creator":
                params = await connector.signed({"mid": creator, "pn": page, "ps": 30,
                    "order": "pubdate", "platform": "web", "web_location": "333.1387"}, None)
                data = await connector.request("/x/space/wbi/arc/search", params=params)
                items, page_size = data.get("list", {}).get("vlist", []), 30
                more = page * page_size < int(data.get("page", {}).get("count") or 0)
            else:
                data = await connector.request("/x/web-interface/popular", params={"pn": page, "ps": 20})
                items, page_size = data.get("list", []), 20
                more = data.get("no_more") is False or ("no_more" not in data and len(items) == page_size)
            require(isinstance(items, list), "source_invalid_response", 503)
            rows = []
            for row in items[offset:offset + limit]:
                bvid = row.get("bvid")
                if not isinstance(bvid, str) or not bvid.startswith("BV"):
                    continue
                owner = row.get("owner") or {}
                rows.append(candidate("bilibili", "bilibili:" + bvid, kind="video",
                    title=row.get("title"), authors=[row.get("author") or owner.get("name")],
                    summary=row.get("description") or row.get("desc") or "",
                    url="https://www.bilibili.com/video/" + bvid,
                    published=row.get("pubdate") or row.get("created"), acquired=time.time()))
            following = ({"page": page, "offset": offset + limit} if len(items) > offset + limit
                         else {"page": page + 1, "offset": 0} if more and page < 100 else None)
            return rows, {"source": identity, "state": "ok", "code": None,
                "has_more": following is not None,
                "next_cursor": Discovery.cursor(config, query, identity, following)}
        finally:
            await connector.close()

    async def discover(self, config, body):
        creators = body.get("creator_ids", config.get("followed_creator_ids", []))
        require(isinstance(creators, list) and len(creators) <= 8
                and all(isinstance(item, str) and item.isdigit() for item in creators),
                "invalid_creator_ids", 400)
        requests = [("bilibili_search", None)] if body.get("query") else [("bilibili_popular", None)]
        if body.get("mode") == "continue" or body.get("include_followed", True):
            requests.extend(("bilibili_creator", item) for item in dict.fromkeys(creators))
        requested = body.get("pending_sources")
        if requested is not None:
            requests = [(kind, creator) for kind, creator in requests
                        if kind + (":" + creator if creator else "") in requested]

        async def one(kind, creator):
            try:
                async with asyncio.timeout(20):
                    return await self.page(config, body, kind, creator=creator)
            except (Fault, TimeoutError, ValueError, TypeError, KeyError, OSError) as error:
                return [], {"source": kind + (":" + creator if creator else ""),
                    "state": "unavailable", "code": getattr(error, "code", "source_unavailable"),
                    "has_more": None, "next_cursor": None}

        results = await asyncio.gather(*(one(*item) for item in requests))
        return [row for rows, _ in results for row in rows], [status for _, status in results]
