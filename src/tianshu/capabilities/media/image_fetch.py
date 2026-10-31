"""Bounded image bytes through the existing credential-free Bilibili session."""

import aiohttp

from ..contracts import Fault, require


async def fetch_image(connector, url):
    await connector.start()
    try:
        async with connector.session.get(url, allow_redirects=False) as response:
            require(response.status == 200, "cover_unavailable", 503)
            raw = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                raw.extend(chunk)
                require(len(raw) <= 32 * 1024**2, "cover_invalid", 503)
            return bytes(raw)
    except (aiohttp.ClientError, TimeoutError, OSError):
        raise Fault("cover_unavailable", 503) from None
