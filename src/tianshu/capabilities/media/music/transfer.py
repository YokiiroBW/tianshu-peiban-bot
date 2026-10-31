"""Credential-free, bounded NetEase CDN transfer. Signed URLs live only in memory."""

import hashlib
import os
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit

import aiohttp
import time

from .request_control import Limited, request_slot, retry_after

from ...contracts import Fault, require


def media_url(value, provider, *, artwork=False):
    if provider == "netease":
        return cdn_url(value)
    hosts = {
        "qq": ("qqmusic.qq.com", "qqmusic.tc.qq.com", "aqqmusic.tc.qq.com", "music.tc.qq.com"),
        "kugou": ("kugou.com", "kugou.net"),
    }.get(provider, ())
    if artwork and provider == "qq":
        hosts += ("gtimg.cn", "qlogo.cn", "qpic.cn")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        valid = (
            url.scheme in {"http", "https"}
            and any(host == suffix or host.endswith("." + suffix) for suffix in hosts)
            and not url.username
            and not url.password
            and url.port in {None, 80, 443}
        )
    except (ValueError, TypeError):
        valid = False
    require(valid, "music_download_url_invalid", 502)
    # Kugou still issues signed HTTP links on CDN hosts without a valid TLS
    # certificate. Preserve that explicit transport; never disable TLS checks
    # or downgrade a URL that the provider issued as HTTPS. No cookies travel here.
    scheme = url.scheme if provider == "kugou" and not artwork else "https"
    return url._replace(scheme=scheme, netloc=host, fragment="").geturl()


def cdn_url(value):
    try:
        url = urlsplit(value)
        valid = (
            url.scheme in {"http", "https"}
            and (url.hostname or "").endswith((".music.126.net", ".music.163.com"))
            and not url.username
            and not url.password
            and url.port in {None, 80, 443}
        )
    except (ValueError, TypeError):
        valid = False
    require(valid, "music_download_url_invalid", 502)
    return url._replace(scheme="https", netloc=url.hostname, fragment="").geturl()


class Transfer:
    def __init__(self, *, validate_url=cdn_url, control=None):
        self.validate_url = validate_url
        self.control = control

    @asynccontextmanager
    async def response(
        self, url, *, provider=None, account_id=None, artwork=False, headers=None, timeout=1800
    ):
        # Account cookies never enter either quality probes or full transfers.
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=timeout, connect=20, sock_read=60),
        ) as session:
            try:
                for _ in range(5):
                    url = (
                        media_url(url, provider, artwork=artwork)
                        if provider and self.validate_url is cdn_url
                        else self.validate_url(url)
                    )
                    async with request_slot(self.control, provider or "netease", account_id):
                        async with session.get(
                            url, headers=headers, allow_redirects=False
                        ) as response:
                            if response.status == 429:
                                raise Limited(
                                    retry_after(response.headers.get("Retry-After"), time.time())
                                )
                            if response.status in {301, 302, 303, 307, 308}:
                                url = urljoin(url, response.headers.get("Location", ""))
                                continue
                            yield response
                            return
                raise Fault("music_download_url_invalid", 502)
            except (aiohttp.ClientError, TimeoutError):
                raise Fault("music_download_unavailable", 502) from None

    async def sample(self, url, destination, *, provider, account_id=None):
        maximum = 1024**2
        async with self.response(
            url,
            provider=provider,
            account_id=account_id,
            headers={"Range": f"bytes=0-{maximum - 1}"},
            timeout=30,
        ) as response:
            require(response.status in {200, 206}, "music_download_unavailable", 502)
            if response.status == 206:
                require(
                    response.headers.get("Content-Range", "").startswith("bytes 0-"),
                    "music_download_incomplete",
                    502,
                )
            # A server may ignore Range. Stop after the bounded prefix either way.
            with destination.open("xb") as stream:
                total = 0
                while total < maximum:
                    chunk = await response.content.read(min(64 * 1024, maximum - total))
                    if not chunk:
                        break
                    stream.write(chunk)
                    total += len(chunk)
            require(total > 0, "music_download_incomplete", 502)

    async def fetch(
        self,
        url,
        destination,
        *,
        maximum,
        expected_size=None,
        expected_md5=None,
        progress=None,
        provider=None,
        account_id=None,
        artwork=False,
    ):
        async with self.response(
            url, provider=provider, account_id=account_id, artwork=artwork
        ) as response:
            require(response.status == 200, "music_download_unavailable", 502)
            length = response.content_length
            require(length is None or 0 < length <= maximum, "music_file_too_large", 409)
            require(
                expected_size is None or length in {None, expected_size},
                "music_download_incomplete",
                502,
            )
            total, checksum, last = 0, hashlib.md5(), 0
            with destination.open("xb") as stream:
                async for chunk in response.content.iter_chunked(256 * 1024):
                    total += len(chunk)
                    require(total <= maximum, "music_file_too_large", 409)
                    stream.write(chunk)
                    checksum.update(chunk)
                    if progress and total - last >= 2 * 1024**2:
                        await progress(total / expected_size if expected_size else None)
                        last = total
                stream.flush()
                os.fsync(stream.fileno())
            require(
                total > 0 and (expected_size is None or total == expected_size),
                "music_download_incomplete",
                502,
            )
            require(
                not expected_md5 or checksum.hexdigest() == expected_md5.lower(),
                "music_download_incomplete",
                502,
            )
            disposition = response.content_disposition
            return {
                "filename": disposition.filename if disposition else None,
                "path": response.url.path,
            }
