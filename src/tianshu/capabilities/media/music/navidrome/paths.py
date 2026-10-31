"""Resolve ambiguous Subsonic paths through Navidrome's authenticated file metadata."""

import hashlib
import json
import time
from pathlib import PurePosixPath
from urllib.parse import quote

import aiohttp

from ....contracts import Fault, require
from ...publication import validate_connection


class NativePaths:
    def __init__(self):
        self.session = None
        self.scope = None
        self.token = None
        self.token_until = 0
        self.retry_at = 0
        self.cache = {}
        self.cursor = ""

    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None
        self.token = None
        self.cache.clear()

    async def request(self, connection, method, path, **kwargs):
        validate_connection({"base_url": connection["base_url"], "token": ""})
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                headers={"User-Agent": "TianShu-Music/1"},
            )
        try:
            async with self.session.request(
                method, connection["base_url"] + path, allow_redirects=False, **kwargs
            ) as response:
                if response.status == 401:
                    self.token = None
                require(response.status == 200, "navidrome_path_unavailable", 502)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    require(len(raw) <= 1024**2, "navidrome_invalid_response", 502)
                value = json.loads(raw)
                require(isinstance(value, dict), "navidrome_invalid_response", 502)
                return value
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise Fault("navidrome_path_unavailable", 502) from None

    async def resolve(self, connection, identities):
        scope = (
            connection["base_url"],
            connection["username"],
            hashlib.sha256(connection["password"].encode()).hexdigest(),
            str(connection["music_folder_id"]),
        )
        if scope != self.scope:
            self.scope, self.token, self.retry_at = scope, None, 0
            self.cache.clear()
            self.cursor = ""
        now = time.monotonic()
        self.cache = {key: value for key, value in self.cache.items() if value[0] > now}
        identities = sorted(set(identities))
        result = {
            identity: self.cache[identity][1]
            for identity in identities
            if identity in self.cache and self.cache[identity][1]
        }
        missing = [identity for identity in identities if identity not in self.cache]
        # Cached results cost no requests. Rotate remaining work as well, so a
        # playlist larger than the cache cannot starve its later candidates.
        missing = [i for i in missing if i > self.cursor] + [i for i in missing if i <= self.cursor]
        for identity in missing[:100]:
            if now < self.retry_at:
                break
            self.cursor = identity
            try:
                if not self.token or now >= self.token_until:
                    login = await self.request(
                        connection,
                        "POST",
                        "/auth/login",
                        json={
                            "username": connection["username"],
                            "password": connection["password"],
                        },
                    )
                    require(
                        isinstance(login.get("token"), str) and login["token"],
                        "navidrome_invalid_response",
                        502,
                    )
                    self.token, self.token_until = login["token"], now + 3600
                song = await self.request(
                    connection,
                    "GET",
                    "/api/song/" + quote(identity, safe=""),
                    headers={"X-ND-Authorization": "Bearer " + self.token},
                )
                value = native_path(song, identity, connection["music_folder_id"])
                self.cache[identity] = (now + 300, value)
                if value:
                    result[identity] = value
            except Fault:
                # This optional lookup must not break unambiguous Subsonic matches
                # or turn a native API failure into a guessed song identity.
                self.retry_at = time.monotonic() + 300
                break
        if len(self.cache) > 1000:
            self.cache = dict(list(self.cache.items())[-1000:])
        return {"paths": result, "pending": set(identities) - self.cache.keys() - result.keys()}


def native_path(song, identity, folder):
    if (
        song.get("id") != identity
        or str(song.get("libraryId")) != str(folder)
        or song.get("missing")
        or type(song.get("size")) is not int
        or not all(isinstance(song.get(k), str) and song[k] for k in ("libraryPath", "path"))
    ):
        return None
    root, path = PurePosixPath(song["libraryPath"]), PurePosixPath(song["path"])
    if not root.is_absolute() or ".." in root.parts or ".." in path.parts:
        return None
    absolute = root / path
    if not absolute.is_relative_to(root):
        return None
    return {"path": str(absolute), "size": song["size"]}
