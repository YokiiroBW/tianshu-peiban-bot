"""A configured search endpoint is distinct from untrusted public content URLs."""

import ipaddress
import json
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from ..contracts import Fault, require
from .candidates import public_url


def public_target(url):
    """Literal IPs bypass aiohttp's resolver, so validate them before connecting."""
    url = public_url(url)
    host = urlsplit(url).hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    require(address.is_global and not address.is_multicast, "content_address_denied", 403)
    return url


class PublicResolver(AbstractResolver):
    """The checked DNS answers are exactly the addresses used by the connector."""

    def __init__(self, resolver=None):
        self.resolver = resolver or aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=0):
        values = await self.resolver.resolve(host, port, family)
        require(values and all(ipaddress.ip_address(value["host"]).is_global
                               and not ipaddress.ip_address(value["host"]).is_multicast for value in values),
                "content_address_denied", 403)
        return values

    async def close(self):
        await self.resolver.close()


def search_endpoint(config):
    engine = config.get("search_engine", "searxng")
    require(isinstance(engine, str) and engine in {"searx", "searxng"},
            "search_engine_unsupported", 400)
    base = public_url(config.get("base_url"))
    parts = urlsplit(base)
    require(not parts.query and not parts.fragment, "search_base_url_invalid", 400)
    require(type(config.get("allow_private_network", False)) is bool,
            "search_private_network_invalid", 400)
    endpoint = base.rstrip("/") + "/search"
    if not config.get("allow_private_network", False):
        public_target(endpoint)
    return endpoint


async def search_request(config, value):
    """Only a saved provider's fixed /search endpoint may use its private grant.

    No redirect inherits that grant. The query can never replace its origin or
    path, and no caller-supplied headers, cookies, credentials or proxy are used.
    """
    endpoint = search_endpoint(config)
    target = public_url(value["url"])
    expected, actual = urlsplit(endpoint), urlsplit(target)
    require((actual.scheme, actual.netloc, actual.path) ==
            (expected.scheme, expected.netloc, expected.path) and not actual.fragment,
            "search_endpoint_mismatch", 403)
    private = config.get("allow_private_network", False)
    resolver = None if private else PublicResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, limit=2)
    try:
        async with aiohttp.ClientSession(
            connector=connector, cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
            timeout=aiohttp.ClientTimeout(total=value.get("timeout_seconds", 8), connect=5),
        ) as session:
            async with session.get(target, allow_redirects=False,
                                   headers={"Accept": "application/json", "User-Agent": "TianshuContent/1"}) as response:
                if response.status == 403:
                    raise Fault("search_json_disabled_or_forbidden", 503)
                if response.status == 429:
                    raise Fault("search_rate_limited", 503)
                require(response.status == 200, "search_service_unavailable", 503)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    require(len(raw) <= min(value.get("max_response_bytes", 384 * 1024), 512 * 1024),
                            "search_response_too_large", 413)
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise Fault("search_invalid_json", 503) from None
                return {"status": response.status, "headers": dict(response.headers), "body": body}
    except (aiohttp.ClientError, OSError):
        raise Fault("search_service_unavailable", 503) from None
    finally:
        if resolver is not None:
            await resolver.close()
