"""Read public web/forum/Bilibili content; searches remain unread candidates."""

import base64
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
from ..execution_context import CURRENT_EXECUTION, current_generation
from ..content_discovery.service import Discovery
from ..content_discovery.media import MediaCatalog
from ..content_discovery.candidates import public_url, candidate
from ..content_discovery.network import PublicResolver, public_target, search_request
from .discovery import BilibiliDiscovery
from .parsing import html_content, discourse, post_text
from .providers import availability as discovery_availability, providers, select_provider, validate_provider


class Content:
    discovery_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 400},
            "intention": {"type": "string", "maxLength": 1000},
            "mode": {"enum": ["research", "explore", "continue"]},
            "sources": {"type": "array", "uniqueItems": True, "minItems": 1, "maxItems": 6,
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
        self.recovery_policy = None

    async def availability(self, context):
        return Availability(
            True,
            self.sources is not None,
            reason=None if self.sources else "source_port_unavailable",
        )

    async def discovery_availability(self, context):
        return await discovery_availability(self, context)

    async def fetch(
        self,
        url,
        *,
        accept="text/html,application/json,text/plain",
        maximum=4 * 1024 * 1024,
        headers=None,
    ):
        public_target(url)
        resolver = PublicResolver()
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20, connect=10),
                connector=aiohttp.TCPConnector(resolver=resolver),
                cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
            ) as session:
                for _ in range(4):
                    async with session.get(
                        url, headers={"Accept": accept, **(headers or {})}, allow_redirects=False
                    ) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            url = urljoin(url, response.headers.get("Location", ""))
                            public_target(url)
                            continue
                        if response.status != 200:
                            raise Fault("content_source_unavailable", 503)
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            raw.extend(chunk)
                            if len(raw) > maximum:
                                raise Fault("content_too_large", 413)
                        return bytes(raw), response.content_type, url, dict(response.headers)
        finally:
            await resolver.close()
        raise Fault("content_redirect_limit", 409)

    async def discover(self, context, body, *, action=None, attempt=None):
        await self.authority.check(context, action.source_refs if action else ())
        body = dict(body)
        body.setdefault("mode", "research" if body.get("query") else "explore")
        configs = await providers(self.config, context)
        config = select_provider(configs, body, self.media)
        if config.get("id"):
            body["provider_id"] = config["id"]
        interests = body.get("interests", config.get("interests", []))
        if not body.get("query") and interests and body.get("mode", "research") == "explore":
            body["query"] = interests[body.get("interest_index", 0) % len(interests)]
        if body.get("mode", "research") == "research" and not body.get("query", "").strip():
            raise Fault("research_question_required", 400)

        async def request(value):
            return await search_request(config, value)

        requested_sources = body.get("sources", ["web", "bilibili"])
        pending = body.get("pending_sources")
        local_sources = [item for item in requested_sources if item != "forum"
                         and (item != "bilibili" or config.get("subscription_ids")
                              or config.get("include_video_library"))
                         and (pending is None or item in pending)]
        result = await self.discovery.discover(config, {**body, "sources": local_sources}, request=request)
        if "bilibili" in requested_sources:
            rows, statuses = await self.bilibili.discover(config, body)
            result["candidates"].extend(rows)
            result["sources"].extend(statuses)
        if "forum" in requested_sources and (pending is None or "forum" in pending):
            topics = config.get("followed_forums", [])
            invalid = False
            for topic in topics[:20]:
                try:
                    url = public_target(topic["url"])
                    item = candidate("web", "url:" + digest(url), kind="webpage",
                        title=topic.get("title"), url=url, acquired=time.time())
                    item["read_locator"]["kind"] = "forum"
                    result["candidates"].append(item)
                except (Fault, TypeError, KeyError):
                    invalid = True
            result["sources"].append({"source": "forum", "state": "partial" if invalid else "ok" if topics else "not_configured",
                "code": "followed_forums_invalid" if invalid else None if topics else "followed_forums_not_configured",
                "has_more": False, "next_cursor": None})
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
            raw=canonical(result).encode(), mime_type="application/json", generation=current_generation())
        result["source_refs"] = wire((source,))
        await self.authority.check(context, (source,))
        return Receipt(str(uuid4()), action.action_id, attempt.attempt_id, "succeeded", "none",
                       "not_requested", result=result)

    def _read_phase(self, phase, *, method=None, url=None):
        current = CURRENT_EXECUTION.get()
        if current is not None:
            current["content_read_phase"] = {"phase": phase, "method": method, "url": url}

    def interruption_details(self, *, explicit, unknown):
        current = CURRENT_EXECUTION.get() or {}
        phase = current.get("content_read_phase") or {"phase": "unknown"}
        unknown = unknown or current.get("content_read_effect_unknown", False)
        recoverable = (not explicit and not unknown and phase.get("phase") == "acquisition"
                       and current.get("content_read_recovery_allowed", False))
        return {"phase": phase["phase"], "material_read": phase,
                **current.get("content_read_budget", {}),
                "outcome": "wait" if unknown else "failed",
                "recovery": "observe_original_attempt" if unknown else "resume_material_read" if recoverable else "none"}

    async def execute(self, action, attempt):
        return await self._execute_with_phase(action, attempt)

    async def _execute_with_phase(self, action, attempt, *, resume=None):
        if (resume or {}).get("phase") == "commit":
            self._read_phase("commit")
        else:
            self._read_phase("acquisition", method=(resume or {}).get("method") or
                             ("browser" if action.arguments.get("render") == "browser" else "http"),
                             url=(resume or {}).get("url") or action.arguments["url"])
        try:
            await self.authority.check(action.context, action.source_refs)
            if (resume or {}).get("phase") == "commit":
                from .. import action_checkpoint
                saved = await action_checkpoint.load(self.config.database, action, field="content_read")
                if not saved or digest(saved) != resume.get("checkpoint_digest"):
                    raise Fault("read_checkpoint_changed", 409)
                current = CURRENT_EXECUTION.get()
                if current is not None:
                    current["content_read_checkpoint"] = True
                return await self._commit_read(action, attempt, saved)
            from .recovery import physical_read
            phase = (CURRENT_EXECUTION.get() or {}).get("content_read_phase") or {
                "method": (resume or {}).get("method") or "http", "url": (resume or {}).get("url") or action.arguments["url"]}
            async with physical_read(self, action, attempt, phase["method"], phase["url"]):
                return await self._execute_read(action, attempt, resume=resume)
        except Exception as error:
            from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
            current = CURRENT_EXECUTION.get() or {}
            if isinstance(error, RecoveryWait):
                if not recovery_wait_retryable(error):
                    raise
                from .recovery import MAX_ATTEMPTS
                phase = current.get("content_read_phase") or {"phase": "acquisition",
                    "method": (resume or {}).get("method") or "http", "url": (resume or {}).get("url") or action.arguments["url"]}
                return Receipt(str(uuid4()), action.action_id, attempt.attempt_id, "failed", "none", "not_requested",
                    error_code=error.code, result={"phase": phase["phase"], "material_read": phase,
                        "outcome": "wait", "recovery": "resume_material_read" if phase["phase"] == "acquisition" else "none",
                        "attempt_number": current.get("content_read_budget", {}).get("attempt_number", 0),
                        "max_attempts": MAX_ATTEMPTS,
                        "retry_at": error.retry_at.isoformat() if error.retry_at else None})
            submitted = current.get("submitted", False)
            unknown = (current.get("content_read_effect_unknown", False)
                       or getattr(error, "effect", None) == "unknown"
                       or (submitted and not hasattr(error, "effect")))
            from tianshu.runtime.database import classify_database_failure
            database_failure = classify_database_failure(error)
            if database_failure and database_failure.receipt_required:
                unknown = True
            details = self.interruption_details(explicit=False, unknown=unknown)
            if (details["phase"] == "commit" and current.get("content_read_checkpoint")
                    and database_failure and database_failure.retryable and database_failure.rollback_confirmed
                    and not database_failure.receipt_required and not unknown):
                details["recovery"] = "resume_commit"
                details["retry_at"] = database_failure.retry_at.isoformat() if database_failure.retry_at else None
            if current.get("content_read_source_refs"):
                details["source_refs"] = current["content_read_source_refs"]
            # Client errors and unsupported material need correction, not an
            # automatic repeat of the same request.
            status = getattr(error, "status", 500)
            if isinstance(status, int) and status < 500:
                details["recovery"] = "none"
            code = "capability_timeout" if isinstance(error, TimeoutError) else getattr(error, "code", "capability_failed")
            return Receipt(str(uuid4()), action.action_id, attempt.attempt_id,
                           "unknown" if unknown else "failed", "unknown" if unknown else "none",
                           "not_requested", error_code=code, result=details)

    async def _execute_read(self, action, attempt, *, resume=None):
        url = (resume or {}).get("url") or action.arguments["url"]
        public_target(url)
        rendered = None
        if (resume or {}).get("method") == "browser" or action.arguments.get("render") == "browser":
            self._read_phase("acquisition", method="browser", url=url)
            if self.browser is None:
                raise Fault("browser_adapter_unavailable", 503)
            rendered = await self.browser.read(
                action.context, url, session_id=action.arguments.get("session_id")
            )
            raw, mime, final, headers = rendered["raw"], rendered["mime_type"], rendered["url"], {}
        else:
            try:
                self._read_phase("acquisition", method="http", url=url)
                raw, mime, final, headers = await self.fetch(url)
            except Fault as error:
                if error.code == "content_address_denied":
                    raise
                if action.arguments.get("render") == "http" or self.browser is None:
                    raise
                self._read_phase("acquisition", method="browser", url=url)
                rendered = await self.browser.read(
                    action.context, url, session_id=action.arguments.get("session_id")
                )
                raw, mime, final, headers = (
                    rendered["raw"],
                    rendered["mime_type"],
                    rendered["url"],
                    {},
                )
        self._read_phase("parse", url=final)
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
            self._read_phase("acquisition", method="browser", url=final)
            rendered = await self.browser.read(
                action.context, final, session_id=action.arguments.get("session_id")
            )
            raw, mime, content, final = (
                rendered["raw"],
                rendered["mime_type"],
                rendered["content"],
                rendered["url"],
            )
            self._read_phase("parse", url=final)
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
                        "phase": "completed", "outcome": "completed",
                        "follow_up": {"capability_id": "content.read", "arguments": dict(action.arguments)}})
        locator = {
            "kind": "forum" if content["posts"] else "webpage",
            "representation": "rendered_dom" if rendered else "http_body",
            "session_id": rendered.get("session_id") if rendered else None,
            "url": final,
            "requested_url": action.arguments["url"],
            "acquired_at": datetime.now(UTC).isoformat(),
            "etag": headers.get("Etag"),
            "last_modified": headers.get("Last-Modified"),
            "title": content["title"],
            "authors": content.get("authors", []),
            "published_at": content.get("published_at"),
            "posts": content["posts"],
            "forum_cursor": content.get("forum_cursor"),
        }
        self._read_phase("commit", url=final)
        from .. import action_checkpoint
        saved = {"raw_base64": base64.b64encode(raw).decode("ascii"), "mime_type": mime,
                 "content": content, "locator": locator, "after": after}
        await action_checkpoint.save(self.config.database, self.sources, action, attempt, saved,
                                     action.source_refs, field="content_read")
        current = CURRENT_EXECUTION.get()
        if current is not None:
            current["content_read_checkpoint"] = True
        return await self._commit_read(action, attempt, saved)

    async def _commit_read(self, action, attempt, saved):
        from .. import action_checkpoint

        raw = base64.b64decode(saved["raw_base64"], validate=True)
        mime, content, locator, after = saved["mime_type"], saved["content"], saved["locator"], saved["after"]
        original = SourceRef(**saved["original_source"]) if saved.get("original_source") else None
        if original is not None:
            await self.authority.check(action.context, (original,))
        else:
            original = await self.sources.capture(
                action.context, locator=locator, raw=raw, mime_type=mime, generation=current_generation()
            )
            current = CURRENT_EXECUTION.get()
            if current is not None:
                current["content_read_source_refs"] = [wire(original)]
            await action_checkpoint.save(self.config.database, self.sources, action, attempt,
                                         {"original_source": wire(original)}, (*action.source_refs, original),
                                         field="content_read")
        source = SourceRef(**saved["normalized_source"]) if saved.get("normalized_source") else None
        if source is None:
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
                generation=current_generation(),
            )
        current = CURRENT_EXECUTION.get()
        if current is not None:
            current["content_read_source_refs"] = wire((original, source))
        await action_checkpoint.save(self.config.database, self.sources, action, attempt,
                                     {"normalized_source": wire(source)}, (*action.source_refs, original, source),
                                     field="content_read")
        await self.authority.check(action.context, (original, source))
        return self._read_receipt(action, attempt, content, locator, after, original, source)

    def _read_receipt(self, action, attempt, content, locator, after, original, source):
        final = locator["url"]
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
                "phase": "completed", "outcome": "continue" if content["next_url"] else "completed",
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
        validate_provider(value)
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
        # A missing receipt cannot settle an existing unknown effect.
        from .. import action_checkpoint
        from ..serialization import receipt_from
        saved = await action_checkpoint.load(self.config.database, action, force=True, field="content_read")
        if saved.get("original_source") and saved.get("normalized_source"):
            original, source = SourceRef(**saved["original_source"]), SourceRef(**saved["normalized_source"])
            await self.authority.check(action.context, (*action.source_refs, original, source))
            return self._read_receipt(action, attempt, saved["content"], saved["locator"], saved["after"], original, source)
        async with self.config.database.transaction(read_only=True) as conn:
            row = await (await conn.execute(
                "SELECT attempt,receipt FROM capabilities.actions WHERE id=%s", (action.action_id,))).fetchone()
        if row and row["attempt"]["attempt_id"] == attempt.attempt_id and row["receipt"]["execution"] == "unknown":
            return receipt_from(row["receipt"])
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "unknown",
            "unknown",
            "not_requested",
            error_code="read_receipt_not_available",
            result={"phase": "unknown", "outcome": "wait", "recovery": "observe_original_attempt"},
        )

    async def resume_preflight(self, action, attempt, current):
        material = current.result.get("material_read") or {}
        if (current.result.get("recovery") == "resume_commit" and current.effect == "none"
                and current.execution == "failed" and current.delivery == "not_requested"):
            from .. import action_checkpoint
            saved = await action_checkpoint.load(self.config.database, action, force=True, field="content_read")
            if not saved:
                raise Fault("read_checkpoint_unavailable", 409)
            refs = tuple(SourceRef(**saved[key]) for key in ("original_source", "normalized_source") if saved.get(key))
            await self.authority.check(action.context, (*action.source_refs, *refs))
            return {"phase": "commit", "checkpoint_digest": digest(saved), "source_refs": wire(refs)}
        if (current.effect != "none" or current.delivery != "not_requested"
                or current.result.get("recovery") != "resume_material_read"
                or current.execution not in {"failed", "cancelled"}
                or current.execution == "cancelled" and current.error_code != "capability_timeout"
                or material.get("phase") != "acquisition"
                or material.get("method") not in {"http", "browser"}
                or not isinstance(material.get("url"), str)):
            raise Fault("read_recovery_not_eligible", 409)
        public_target(material["url"])
        await self.authority.check(action.context, action.source_refs)
        return dict(material)

    async def validate_resume_on(self, conn, action, attempt, current, proof):
        from ..withdrawal import validate_sources
        refs = (*action.source_refs, *(SourceRef(**item) for item in proof.get("source_refs", ())))
        if not await validate_sources(conn, action.context, refs, self.sources):
            raise Fault("source_revoked", 410)
        row = await (await conn.execute(
            "SELECT attempt,receipt,document FROM capabilities.actions WHERE id=%s FOR UPDATE",
            (action.action_id,))).fetchone()
        document = json.loads(row["document"]) if row else {}
        if (not row or row["attempt"]["attempt_id"] != attempt.attempt_id
                or row["receipt"]["receipt_id"] != current.receipt_id
                or document.get("source_revoked") or document.get("cancel_requested")
                or proof.get("phase") == "commit" and digest(document.get("content_read", {})) != proof["checkpoint_digest"]):
            raise Fault("read_recovery_changed", 409)
        document["content_read_resume"] = proof
        await conn.execute("UPDATE capabilities.actions SET document=%s WHERE id=%s",
                           (canonical(document), action.action_id))

    async def prepare_resume_on(self, conn, action, old, current, attempt, proof):
        if proof.get("phase") == "commit":
            return
        from .recovery import identity, OWNER, MAX_ATTEMPTS
        row = await (await conn.execute("SELECT document FROM capabilities.actions WHERE id=%s",
                                        (action.action_id,))).fetchone()
        generation = json.loads(row["document"]).get("data_generation")
        item = identity(action, attempt, proof["method"], proof["url"])
        lineage = await self.recovery_policy.register_on(
            conn, domain="capability_read", subject_id=action.actor_id or "principal:" + action.context.principal_id,
            owner=OWNER, material_id=item["material_id"], material_digest=item["version"],
            dependency=item["dependency"], current_version=item["version"], max_attempts=MAX_ATTEMPTS,
            data_generation=generation)
        wait = await self.recovery_policy.inspect_wait_on(conn, lineage["lineage_id"], owner=OWNER,
                                                           expected_version=item["version"])
        if wait is not None:
            raise wait
        if lineage["attempts"] and lineage["due_at"] is None:
            raise Fault("read_recovery_not_eligible", 409)

    async def resume(self, action, attempt):
        async with self.config.database.transaction(read_only=True) as conn:
            row = await (await conn.execute("SELECT document FROM capabilities.actions WHERE id=%s",
                                            (action.action_id,))).fetchone()
        if not row:
            raise Fault("not_found", 404)
        resume = json.loads(row["document"]).get("content_read_resume")
        if not resume:
            raise Fault("read_recovery_not_eligible", 409)
        return await self._execute_with_phase(action, attempt, resume=resume)

    async def cancel(self, action, attempt):
        return Receipt(
            str(uuid4()), action.action_id, attempt.attempt_id, "cancelled", "none", "not_requested"
        )
