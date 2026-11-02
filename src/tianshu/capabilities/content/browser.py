"""Actual controlled Chromium reading; a rendered DOM is a sourced representation."""

import asyncio
import aiohttp
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from ..contracts import Fault
from ..content_discovery.network import PublicResolver, public_target


class Browser:
    def __init__(self, configuration, authority, settings):
        self.configuration, self.authority, self.settings = configuration, authority, dict(settings)
        self.slots = asyncio.Semaphore(max(1, min(2, settings.get("contexts", 1))))

    async def profile(self, context, url, session_id=None):
        origin = urlsplit(url).scheme + "://" + urlsplit(url).netloc
        profiles = await self.configuration.list("content_provider")
        for profile in profiles:
            if profile.get("enabled", True) is False:
                continue
            if session_id is not None and profile["id"] != session_id:
                continue
            if origin not in profile.get("browser_origins", []):
                continue
            if (
                profile.get("actor_id") != context.actor_id
                or not set(profile.get("scope_ids", [])) <= set(context.scope_ids)
                or not set(profile.get("audience", [])) <= set(context.audience)
            ):
                continue
            state = None
            if profile.get("browser_session_configured"):
                state = (await self.configuration.secret("content_provider", profile["id"]))[
                    "storage_state"
                ]
            return profile, state
        if session_id is not None:
            raise Fault("browser_session_not_authorized", 403)
        return {"browser_origins": [origin]}, None

    async def read(self, context, url, *, session_id=None):
        public_target(url)
        await self.authority.check(context)
        if self.settings.get("enabled", True) is False:
            raise Fault("browser_disabled", 503)
        try:
            from playwright.async_api import async_playwright, Error
        except ImportError:
            raise Fault("browser_runtime_not_installed", 503) from None
        profile, state = await self.profile(context, url, session_id)
        allowed = set(profile["browser_origins"])
        options = {
            "headless": True,
            "chromium_sandbox": self.settings.get("chromium_sandbox", True),
        }
        if self.settings.get("executable_path"):
            options["executable_path"] = self.settings["executable_path"]
        async with self.slots:
            playwright = await async_playwright().start()
            browser = None
            session = None
            resolver = PublicResolver()
            public_resources = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(),
                connector=aiohttp.TCPConnector(limit=4, resolver=resolver),
                timeout=aiohttp.ClientTimeout(total=15),
                trust_env=False,
            )
            resource_count = 0
            try:
                try:
                    browser = await playwright.chromium.launch(**options)
                except Error:
                    raise Fault("browser_executable_unavailable", 503) from None
                session = await browser.new_context(
                    storage_state=state, accept_downloads=False, service_workers="block"
                )

                async def route(request_route):
                    nonlocal resource_count
                    request = request_route.request
                    address = request.url
                    try:
                        public_target(address)
                        origin = urlsplit(address).scheme + "://" + urlsplit(address).netloc
                        if request.method not in {"GET", "HEAD", "OPTIONS"}:
                            await request_route.abort()
                            return
                        if origin not in allowed:
                            # Account state never enters an unrelated CDN request.
                            if request.method != "GET" or request.resource_type not in {
                                "script",
                                "stylesheet",
                                "font",
                                "image",
                            }:
                                await request_route.abort()
                                return
                            headers = {"Accept": request.headers.get("accept", "*/*")}
                        else:
                            headers = {key: value for key, value in (await request.all_headers()).items()
                                       if key.lower() not in {"host", "content-length", "connection",
                                                              "accept-encoding", "proxy-authorization"}}
                        if request.resource_type in {"media", "websocket"}:
                            await request_route.abort()
                            return
                        resource_count += 1
                        if resource_count > 128:
                            await request_route.abort()
                            return
                        # Chromium must not resolve the same-origin URL again after
                        # authorization. All network bytes use the checked, pinned
                        # public resolver; the provider's LAN exception is absent.
                        async with public_resources.request(request.method, address,
                                allow_redirects=False, headers=headers) as resource:
                            if origin not in allowed and resource.status != 200:
                                await request_route.abort()
                                return
                            body = bytearray()
                            async for chunk in resource.content.iter_chunked(65536):
                                body.extend(chunk)
                                if len(body) > 8 * 1024 * 1024:
                                    await request_route.abort()
                                    return
                            await request_route.fulfill(status=resource.status, body=bytes(body),
                                headers={key: value for key, value in resource.headers.items()
                                    if key.lower() not in {"content-length", "content-encoding",
                                                          "transfer-encoding", "connection"}
                                    and (origin in allowed or key.lower() in {
                                        "content-type", "access-control-allow-origin", "cache-control"})})
                    except (Fault, ValueError, aiohttp.ClientError, OSError, asyncio.TimeoutError):
                        await request_route.abort()

                await session.route("**/*", route)
                page = await session.new_page()

                async def dismiss(dialog):
                    await dialog.dismiss()

                page.on("dialog", dismiss)
                async with asyncio.timeout(min(self.settings.get("timeout_seconds", 35), 60)):
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if response is None or response.status >= 400:
                        raise Fault("browser_source_unavailable", 503)
                    selector = profile.get("browser_selector", "article,main,[role=main],body")
                    await page.locator(selector).first.wait_for(state="attached", timeout=10000)
                    # Wait for an actual visible body, bounded even for pages with
                    # perpetual analytics/network activity.
                    await page.wait_for_function(
                        "() => (document.body?.innerText || '').trim().length > 0", timeout=10000
                    )
                    raw = (await page.content()).encode("utf-8")
                    if len(raw) > 4 * 1024 * 1024:
                        raise Fault("content_too_large", 413)
                    soup = BeautifulSoup(raw, "html.parser")
                    if soup.select_one("input[type=password]") and not soup.select_one(
                        "article,[data-post-id]"
                    ):
                        raise Fault("browser_authentication_required", 503)
                    for node in soup.select("script,style,form,input,textarea,iframe"):
                        node.decompose()
                    for node in soup.find_all(True):
                        for key in list(node.attrs):
                            if key.lower().startswith("on"):
                                del node.attrs[key]
                    rendered = str(soup).encode("utf-8")
                    from .service import html_content

                    content = html_content(rendered, page.url)
                    if not content["text"].strip():
                        raise Fault("content_body_unavailable", 503)
                    if state is not None:
                        refreshed = await session.storage_state()
                        # Reuse the provider's sealed account session, never expose
                        # cookies/localStorage through its public projection.
                        current = await self.configuration.get("content_provider", profile["id"])
                        if current["revision"] == profile["revision"]:
                            try:
                                await self.configuration.save(
                                    "content_provider",
                                    profile["id"],
                                    current,
                                    current["revision"],
                                    secret={"storage_state": refreshed},
                                )
                            except Fault as error:
                                if error.code != "revision_conflict":
                                    raise
                    await self.authority.check(context)
                    return {
                        "raw": rendered,
                        "mime_type": "text/html",
                        "content": content,
                        "url": page.url,
                        "representation": "rendered_dom",
                        "session_id": profile.get("id"),
                    }
            finally:

                async def close():
                    try:
                        if session is not None:
                            await session.close()
                    finally:
                        try:
                            if browser is not None:
                                await browser.close()
                        finally:
                            try:
                                await public_resources.close()
                            finally:
                                try:
                                    await resolver.close()
                                finally:
                                    await playwright.stop()

                closing = asyncio.create_task(close())
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    await asyncio.shield(closing)
                    raise


def needs_browser(raw, content):
    if not content.get("text", "").strip():
        return True
    soup = BeautifulSoup(raw, "html.parser")
    return len(content["text"].strip()) < 120 and bool(soup.select("script[src]"))
