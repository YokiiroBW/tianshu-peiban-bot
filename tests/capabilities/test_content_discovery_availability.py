"""Source constraints and the real SearX-compatible transport, using isolated HTTP."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.content.browser import Browser
from tianshu.capabilities.content.service import Content
from tianshu.capabilities.content_discovery.network import PublicResolver, search_request
from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.service import Operation
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, TrustedContext


def context():
    return TrustedContext("author", "actor", "person", "conversation", ("private",),
                          ("person",), 5, OriginKind.TIMER, frozenset({"sources.read"}))


def content_for(providers=()):
    configuration = SimpleNamespace(list=AsyncMock(return_value=list(providers)))
    return Content(configuration, object(), SimpleNamespace(check=AsyncMock()))


@pytest.mark.asyncio
async def test_catalog_exposes_real_source_constraints_but_given_url_read_remains_available():
    content = content_for()
    catalog = Catalog()
    catalog.register(content)
    catalog.register(Operation(SimpleNamespace(prepared=True, content=content), "content", "discover"))
    rows = {item["definition"]["id"]: item for item in wire(await catalog.discover(context()))}
    available = rows["content.discover"]["availability"]
    states = available["constraints"]["sources"]
    assert not states["web"]["can_execute"] and states["web"]["reason"] == "search_provider_not_configured"
    assert not states["forum"]["can_execute"] and states["forum"]["reason"] == "followed_forums_not_configured"
    assert states["bilibili"]["can_execute"]
    assert available["verified_at"] is None
    assert available["constraints"]["source_options"] == [{"provider_id": None, "sources": ["bilibili"]}]
    assert rows["content.read"]["can_execute"]
    assert rows["content.read"]["availability"]["constraints"] == {}


@pytest.mark.asyncio
async def test_global_profile_does_not_bypass_its_scope_ceiling_and_disabled_profiles_are_unavailable():
    content = content_for([
        {"id": "other-scope", "base_url": "https://search.example", "scope_ids": ["not-granted"]},
        {"id": "other-actor", "base_url": "https://search.example", "actor_id": "other"},
        {"id": "disabled", "base_url": "https://search.example", "enabled": False},
    ])
    available = await content.discovery_availability(context())
    assert not available.constraints["sources"]["web"]["can_execute"]
    with pytest.raises(Fault) as denied:
        await content.discover(context(), {"provider_id": "other-scope", "query": "topic", "sources": ["web"]})
    assert denied.value.code == "content_provider_not_authorized"


@pytest.mark.asyncio
async def test_authorized_search_service_is_preferred_to_browser_only_profile_and_returns_unread_real_candidates():
    requests = []

    async def search(request):
        requests.append(dict(request.query))
        page = int(request.query["pageno"])
        return web.json_response({"results": [
            {"url": f"https://www.iana.org/real-{page}-{index}", "title": "Actual title",
             "content": "A search snippet, not the article body", "publishedDate": "2026-11-02"}
            for index in range(2)] if page == 1 else [], "unresponsive_engines": []})

    app = web.Application()
    app.router.add_get("/search", search)
    server = TestServer(app)
    await server.start_server()
    try:
        provider = {"id": "search", "base_url": str(server.make_url("/")),
                    "allow_private_network": True, "search_engine": "searxng"}
        content = content_for([{"id": "a-browser", "browser_origins": ["https://example.org"]}, provider])
        available = await content.discovery_availability(context())
        assert available.constraints["sources"]["web"]["provider_ids"] == ["search"]
        assert not available.constraints["sources"]["forum"]["can_execute"]
        first = await content.discover(context(), {"query": "a real question", "sources": ["web"], "limit": 1})
        assert first["candidates"][0]["summary_kind"] == "search_snippet"
        assert first["candidates"][0]["readability"] == "unverified"
        assert first["read_basis"] == "discovery_metadata" and not first["experience_eligible"]
        assert first["continuation"]["capability_id"] == "content.read"
        second = await content.discover(context(), first["discovery_continuation"]["arguments"])
        third = await content.discover(context(), second["discovery_continuation"]["arguments"])
        assert [value["pageno"] for value in requests] == ["1", "1", "2"]
        assert all(value["format"] == "json" and value["q"] == "a real question" for value in requests)
        assert first["discovery_continuation"]["arguments"]["provider_id"] == "search"
        assert not third["candidates"] and third["sources"][0]["has_more"] is False
        with pytest.raises(Fault) as body_denied:
            await content.fetch(str(server.make_url("/search")))
        assert body_denied.value.code == "content_address_denied"
        with pytest.raises(Fault) as browser_denied:
            await Browser(content.config, content.authority, {}).read(context(), str(server.make_url("/search")))
        assert browser_denied.value.code == "content_address_denied"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_web_and_forum_missing_configuration_is_reported_per_source():
    content = content_for()
    result = await content.discover(context(), {"query": "actual question", "sources": ["web", "forum"]})
    statuses = {value["source"]: value for value in result["sources"]}
    assert statuses["web"]["state"] == statuses["forum"]["state"] == "not_configured"
    assert statuses["web"]["code"] == "search_provider_not_configured"
    assert statuses["forum"]["code"] == "followed_forums_not_configured"
    assert result["next_actions"] == [] and not result["experience_eligible"]


@pytest.mark.asyncio
async def test_trusted_search_grant_is_exact_and_does_not_follow_redirects():
    redirected = []

    async def redirect(_request):
        return web.Response(status=302, headers={"Location": "/private-target"})

    async def private_target(_request):
        redirected.append(True)
        return web.json_response({"results": []})

    app = web.Application()
    app.router.add_get("/search", redirect)
    app.router.add_get("/private-target", private_target)
    server = TestServer(app)
    await server.start_server()
    try:
        config = {"base_url": str(server.make_url("/")), "allow_private_network": True}
        with pytest.raises(Fault) as redirect_denied:
            await search_request(config, {"url": str(server.make_url("/search?q=topic&format=json"))})
        assert redirect_denied.value.code == "search_service_unavailable" and not redirected
        with pytest.raises(Fault) as path_denied:
            await search_request(config, {"url": str(server.make_url("/private-target"))})
        assert path_denied.value.code == "search_endpoint_mismatch" and not redirected
        with pytest.raises(Fault) as no_private_grant:
            await search_request({**config, "allow_private_network": False},
                                 {"url": str(server.make_url("/search"))})
        assert no_private_grant.value.code == "content_address_denied"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_dns_answers_are_checked_before_connect_and_not_reresolved_after_authorization():
    private = {"hostname": "public.example", "host": "192.168.31.230", "port": 80,
               "family": 2, "proto": 0, "flags": 0}
    underlying = SimpleNamespace(resolve=AsyncMock(return_value=[private]), close=AsyncMock())
    resolver = PublicResolver(underlying)
    with pytest.raises(Fault) as denied:
        await resolver.resolve("public.example", 80)
    assert denied.value.code == "content_address_denied"
    public = {**private, "host": "1.1.1.1"}
    underlying.resolve.return_value = [public]
    assert await resolver.resolve("public.example", 80) == [public]
    assert underlying.resolve.await_count == 2
    await resolver.close()


@pytest.mark.asyncio
async def test_engine_403_and_invalid_json_are_visible_and_cannot_become_successful_body_reads():
    async def search(request):
        return web.Response(status=403) if request.query["q"] == "forbidden" else web.Response(text="<html>login</html>")

    app = web.Application()
    app.router.add_get("/search", search)
    server = TestServer(app)
    await server.start_server()
    try:
        content = content_for([{"id": "search", "base_url": str(server.make_url("/")),
                                "allow_private_network": True}])
        for query, code in (("forbidden", "search_json_disabled_or_forbidden"), ("login", "search_invalid_json")):
            result = await content.discover(context(), {"query": query, "sources": ["web"]})
            assert result["sources"][0]["state"] == "unavailable"
            assert result["sources"][0]["code"] == code
            assert not result["experience_eligible"] and not result["next_actions"]
    finally:
        await server.close()
