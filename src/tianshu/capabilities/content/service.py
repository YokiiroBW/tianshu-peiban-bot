"""Read public web/forum/Bilibili content; searches remain unread candidates."""

import json
import time
from datetime import UTC, datetime
from urllib.parse import urljoin
from uuid import uuid4
import aiohttp
from tianshu.types import SourceRef, Receipt, Coverage
from tianshu.runtime.serialization import wire
from ..catalog import CapabilityDefinition, Availability
from ..contracts import Fault, canonical, digest
from ..content_discovery.service import Discovery
from ..content_discovery.media import MediaCatalog
from ..content_discovery.candidates import public_url, candidate
from .discovery import BilibiliDiscovery
from .parsing import html_content, discourse, post_text


class Content:
    discovery_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 400},
            "intention": {"type": "string", "maxLength": 1000},
            "mode": {"enum": ["research", "explore", "continue"]},
            "sources": {"type": "array", "uniqueItems": True,
                        "items": {"enum": ["web", "forum", "bilibili", "music", "books", "comics"]}},
            "interests": {"type": "array", "maxItems": 20,
                          "items": {"type": "string", "maxLength": 200}},
            "interest_index": {"type": "integer", "minimum": 0},
            "creator_ids": {"type": "array", "maxItems": 8,
                            "items": {"type": "string", "pattern": "^[0-9]+$"}},
            "include_followed": {"type": "boolean"},
            "provider_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "page": {"type": "integer", "minimum": 1, "maximum": 100},
            "exclude_keys": {"type": "array", "maxItems": 256,
                             "items": {"type": "string"}},
            "cursors": {"type": "object", "additionalProperties": {"type": "string"}},
            "pending_sources": {"type": "array", "items": {"type": "string"}},
            "budget": {"type": "object", "properties": {
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 100},
                "pages_used": {"type": "integer", "minimum": 0},
                "max_items": {"type": "integer", "minimum": 1, "maximum": 256}}},
        },
    }
    definition = CapabilityDefinition(
        "content.read",
        "阅读内容",
        "获取真实网页或论坛正文，保留原URL/作者/楼层与分页。",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "mode": {"enum": ["research", "explore", "continue"]},
                "render": {"enum": ["auto", "http", "browser"]},
                "session_id": {"type": "string"},
                "forum_cursor": {"type": "object"},
                "after_post_number": {"type": "integer", "minimum": 0},
                "content_key": {"type": "string"},
                "intention": {"type": "string"},
            },
            "required": ["url"],
        },
        {"type": "object"},
        "read",
        long_running=True,
    )

    def __init__(self, configuration, sources, authority, media=None, browser=None):
        self.config, self.sources, self.authority, self.media, self.browser = (
            configuration,
            sources,
            authority,
            media,
            browser,
        )
        self.discovery = Discovery(MediaCatalog(media) if media else None)
        self.bilibili = BilibiliDiscovery()

    async def availability(self, context):
        return Availability(
            True,
            self.sources is not None,
            reason=None if self.sources else "source_port_unavailable",
        )

    async def fetch(
        self,
        url,
        *,
        accept="text/html,application/json,text/plain",
        maximum=4 * 1024 * 1024,
        headers=None,
    ):
        public_url(url)
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20, connect=10)
        ) as session:
            for _ in range(4):
                async with session.get(
                    url, headers={"Accept": accept, **(headers or {})}, allow_redirects=False
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers.get("Location", ""))
                        public_url(url)
                        continue
                    if response.status != 200:
                        raise Fault("content_source_unavailable", 503)
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > maximum:
                            raise Fault("content_too_large", 413)
                    return bytes(raw), response.content_type, url, dict(response.headers)
        raise Fault("content_redirect_limit", 409)

    async def discover(self, context, body, *, action=None, attempt=None):
        await self.authority.check(context, action.source_refs if action else ())
        body = dict(body)
        body.setdefault("mode", "research" if body.get("query") else "explore")
        configs = [item for item in await self.config.list("content_provider")
                   if item.get("enabled", True) and (not item.get("actor_id")
                   or item["actor_id"] == context.actor_id
                   and set(item.get("scope_ids", [])) <= set(context.scope_ids)
                   and set(item.get("audience", [])) <= set(context.audience))]
        config = next(
            (x for x in configs if x.get("id") == body.get("provider_id")),
            configs[0] if configs else {},
        )
        if body.get("provider_id") and config.get("id") != body["provider_id"]:
            raise Fault("content_provider_not_authorized", 403)
        interests = body.get("interests", config.get("interests", []))
        if not body.get("query") and interests and body.get("mode", "research") == "explore":
            body["query"] = interests[body.get("interest_index", 0) % len(interests)]
        if body.get("mode", "research") == "research" and not body.get("query", "").strip():
            raise Fault("research_question_required", 400)

        async def request(value):
            raw, mime, url, headers = await self.fetch(
                value["url"], maximum=value.get("max_response_bytes", 512 * 1024)
            )
            return {
                "status": 200,
                "headers": {"content-type": mime},
                "body": json.loads(raw) if mime == "application/json" else raw.decode("utf-8"),
            }

        requested_sources = body.get("sources", ["web", "bilibili"])
        pending = body.get("pending_sources")
        local_sources = [item for item in requested_sources if item != "forum"
                         and (pending is None or item in pending)]
        result = await self.discovery.discover(config, {**body, "sources": local_sources}, request=request)
        if "bilibili" in requested_sources:
            rows, statuses = await self.bilibili.discover(config, body)
            result["candidates"].extend(rows)
            result["sources"].extend(statuses)
        if "forum" in requested_sources and (pending is None or "forum" in pending):
            topics = config.get("followed_forums", [])
            for topic in topics[:20]:
                url = public_url(topic["url"])
                item = candidate("web", "url:" + digest(url), kind="webpage",
                    title=topic.get("title"), url=url, acquired=time.time())
                item["read_locator"]["kind"] = "forum"
                result["candidates"].append(item)
            result["sources"].append({"source": "forum", "state": "ok" if topics else "not_configured",
                "code": None if topics else "not_configured", "has_more": False, "next_cursor": None})
        result["activity_mode"] = body.get("mode", "research")
        result["seen_state"] = "unread_candidates"
        excluded = set(body.get("exclude_keys", []))
        unique = {}
        for item in result["candidates"]:
            if item["content_key"] not in excluded and item["candidate_key"] not in excluded:
                unique.setdefault(item["content_key"], item)
        result["candidates"] = list(unique.values())
        seen = list(dict.fromkeys([*body.get("exclude_keys", []), *unique]))
        cursors = {item["source"]: item["next_cursor"] for item in result["sources"]
                   if item.get("next_cursor")}
        budget = {"max_pages": 6, "max_items": 256, **body.get("budget", {})}
        budget["pages_used"] = budget.get("pages_used", 0) + 1
        exhausted = budget["pages_used"] >= budget["max_pages"] or len(seen) >= budget["max_items"]
        result.update(read_basis="discovery_metadata", experience_eligible=False,
            exploration={"intention": body.get("intention", body.get("query")), "query": body.get("query"), "budget": budget,
                "stop_reason": "budget_exhausted" if exhausted else "checkpoint" if cursors else "source_exhausted"},
            continuation={"capability_id": "content.discover", "arguments": {
                **body, "mode": body.get("mode", "research"), "cursors": cursors,
                "pending_sources": list(cursors), "exclude_keys": seen, "budget": budget}}
            if cursors and not exhausted else None)
        result["discovery_continuation"] = result["continuation"]
        next_actions = []
        for item in result["candidates"]:
            url = item["read_locator"].get("url")
            if not url:
                continue
            if item["kind"] == "video" and item["source"] == "bilibili":
                next_actions.append({"capability_id": "video.observe", "content_key": item["content_key"],
                    "candidate_key": item["candidate_key"], "arguments": {"url": url, "start": 0,
                        "content_key": item["content_key"], "question": body.get("intention", body.get("query", ""))}})
            elif item["source"] == "web":
                next_actions.append({"capability_id": "content.read", "content_key": item["content_key"],
                    "candidate_key": item["candidate_key"], "arguments": {"url": url,
                        "mode": body.get("mode", "research"), "content_key": item["content_key"],
                        "intention": body.get("intention", body.get("query", ""))}})
        result["next_actions"] = next_actions
        if next_actions:
            result["continuation"] = {key: next_actions[0][key] for key in ("capability_id", "arguments")}
        if action is None:
            return result
        source = await self.sources.capture(context,
            locator={"kind": "content_discovery", "query": body.get("query"),
                     "mode": body.get("mode", "research"), "read_basis": "discovery_metadata",
                     "action_id": action.action_id},
            raw=canonical(result).encode(), mime_type="application/json")
        result["source_refs"] = wire((source,))
        await self.authority.check(context, (source,))
        return Receipt(str(uuid4()), action.action_id, attempt.attempt_id, "succeeded", "none",
                       "not_requested", result=result)

    async def execute(self, action, attempt):
        await self.authority.check(action.context, action.source_refs)
        url = action.arguments["url"]
        rendered = None
        if action.arguments.get("render") == "browser":
            if self.browser is None:
                raise Fault("browser_adapter_unavailable", 503)
            rendered = await self.browser.read(
                action.context, url, session_id=action.arguments.get("session_id")
            )
            raw, mime, final, headers = rendered["raw"], rendered["mime_type"], rendered["url"], {}
        else:
            try:
                raw, mime, final, headers = await self.fetch(url)
            except Fault:
                if action.arguments.get("render") == "http" or self.browser is None:
                    raise
                rendered = await self.browser.read(
                    action.context, url, session_id=action.arguments.get("session_id")
                )
                raw, mime, final, headers = (
                    rendered["raw"],
                    rendered["mime_type"],
                    rendered["url"],
                    {},
                )
        if mime == "application/json":
            data = json.loads(raw)
            if "post_stream" in data:
                content = discourse(data, final, action.arguments.get("forum_cursor"))
            else:
                content = {
                    "title": data.get("title"),
                    "text": canonical(data),
                    "posts": [],
                    "next_url": None,
                }
        elif mime in {"text/html", "application/xhtml+xml"}:
            content = html_content(raw, final)
        elif mime.startswith("text/"):
            content = {"title": None, "text": raw.decode("utf-8"), "posts": [], "next_url": None}
        else:
            raise Fault("content_representation_unsupported", 415)
        from .browser import needs_browser

        if (
            rendered is None
            and action.arguments.get("render", "auto") != "http"
            and self.browser
            and needs_browser(raw, content)
        ):
            rendered = await self.browser.read(
                action.context, final, session_id=action.arguments.get("session_id")
            )
            raw, mime, content, final = (
                rendered["raw"],
                rendered["mime_type"],
                rendered["content"],
                rendered["url"],
            )
        if not content["text"].strip():
            raise Fault("content_body_unavailable", 503)
        after = action.arguments.get("after_post_number", 0)
        if after and content["posts"]:
            content["posts"] = [post for post in content["posts"] if int(post["floor"]) > after]
            content["text"] = "\n\n".join(map(post_text, content["posts"]))
            if not content["text"] and not content["next_url"]:
                return Receipt(str(uuid4()), action.action_id, attempt.attempt_id,
                    "succeeded", "none", "not_requested", result={"read_basis": "no_new_content",
                        "experience_eligible": False, "content": content, "continuation": None,
                        "follow_up": {"capability_id": "content.read", "arguments": dict(action.arguments)}})
        locator = {
            "kind": "forum" if content["posts"] else "webpage",
            "representation": "rendered_dom" if rendered else "http_body",
            "session_id": rendered.get("session_id") if rendered else None,
            "url": final,
            "requested_url": url,
            "acquired_at": datetime.now(UTC).isoformat(),
            "etag": headers.get("Etag"),
            "last_modified": headers.get("Last-Modified"),
            "title": content["title"],
            "authors": content.get("authors", []),
            "published_at": content.get("published_at"),
            "posts": content["posts"],
            "forum_cursor": content.get("forum_cursor"),
        }
        original = await self.sources.capture(
            action.context, locator=locator, raw=raw, mime_type=mime
        )
        source = await self.sources.capture(
            action.context,
            locator={
                **locator,
                "kind": "normalized_content",
                "representation": "normalized_text",
                "original_source": wire(original),
                "support_groups": [[wire(original)]],
            },
            raw=content["text"].encode("utf-8"),
            mime_type="text/plain",
        )
        await self.authority.check(action.context, (original, source))
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded",
            "none",
            "not_requested",
            coverage=Coverage(
                content["next_url"] is None,
                missing=("more_pages",) if content["next_url"] else (),
                representation="actual_body",
            ),
            result={
                "source": wire(source),
                "original_source": wire(original),
                "source_refs": wire((original, source)),
                "content": content,
                "locator": locator,
                "activity_mode": action.arguments.get("mode", "research"),
                "content_key": action.arguments.get("content_key", "url:" + digest(content.get("canonical_url", final))),
                "read_basis": "actual_source_body",
                "experience_eligible": bool(content["text"].strip()),
                "follow_up": {"capability_id": "content.read", "arguments": {
                    "url": content.get("canonical_url", final), "mode": "continue",
                    "after_post_number": max((int(post["floor"]) for post in content["posts"]), default=after),
                    "render": action.arguments.get("render", "auto"),
                    **({"content_key": action.arguments["content_key"]} if action.arguments.get("content_key") else {}),
                    **({"intention": action.arguments["intention"]} if action.arguments.get("intention") else {}),
                    **({"session_id": action.arguments["session_id"]} if action.arguments.get("session_id") else {})}}
                    if content["posts"] else None,
                "continuation": {
                    "capability_id": "content.read",
                    "arguments": {
                        "url": content["next_url"],
                        "mode": "continue",
                        "render": action.arguments.get("render", "auto"),
                        **({"content_key": action.arguments["content_key"]} if action.arguments.get("content_key") else {}),
                        **({"intention": action.arguments["intention"]} if action.arguments.get("intention") else {}),
                        **({"forum_cursor": content["forum_cursor"]} if content.get("forum_cursor") else {}),
                        **({"after_post_number": after} if after else {}),
                        **(
                            {"session_id": action.arguments["session_id"]}
                            if action.arguments.get("session_id")
                            else {}
                        ),
                    },
                }
                if content["next_url"]
                else None,
            },
        )

    async def save_provider(self, context, body):
        identifier = body.get("id", "search")
        old = await self.config.get("content_provider", identifier)
        value = {
            **(old or {}),
            **{
                key: item
                for key, item in body.items()
                if key not in {"id", "expected_revision", "browser_storage_state"}
            },
        }
        secret = None
        if "browser_storage_state" in body:
            state = body["browser_storage_state"]
            if (
                not isinstance(state, dict)
                or not isinstance(state.get("cookies", []), list)
                or not isinstance(state.get("origins", []), list)
            ):
                raise Fault("browser_session_invalid", 400)
            origins = body.get("browser_origins", [])
            for origin in origins:
                public_url(origin)
            if not origins:
                raise Fault("browser_session_origins_required", 400)
            value.update(
                actor_id=context.actor_id,
                scope_ids=list(context.scope_ids),
                audience=list(context.audience),
                browser_session_configured=True,
            )
            secret = {"storage_state": state}
        return await self.config.save(
            "content_provider",
            body.get("id", "search"),
            value,
            body.get("expected_revision", 0),
            secret=secret,
        )

    async def read(self, context, source, requested):
        await self.authority.check(context, (source,))
        return await self.sources.read(context, source, requested)

    async def read_source(self, context, body):
        source = SourceRef(**body["source"])
        await self.authority.check(context, (source,))
        return await self.sources.read(context, source, body.get("requested", {}))

    async def reconcile(self, action, attempt):
        # Reads may be repeated explicitly as a new revision; they never imply an external write.
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "unknown",
            "none",
            "not_requested",
            error_code="read_receipt_not_available",
        )

    async def cancel(self, action, attempt):
        return Receipt(
            str(uuid4()), action.action_id, attempt.attempt_id, "cancelled", "none", "not_requested"
        )
