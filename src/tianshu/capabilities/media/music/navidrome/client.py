"""Navidrome's supported Subsonic API. Credentials only travel in POST bodies."""

import hashlib
import json
import secrets

import aiohttp

from ....contracts import Fault, require
from ...publication import validate_connection
from .paths import NativePaths


def object_field(value, key):
    result = value.get(key, {})
    require(isinstance(result, dict), "navidrome_invalid_response", 502)
    return result


def rows_field(value, key, id_types=(str,)):
    rows = value.get(key, [])
    require(
        isinstance(rows, list)
        and all(isinstance(row, dict) and isinstance(row.get("id"), id_types) for row in rows),
        "navidrome_invalid_response",
        502,
    )
    return rows


class Client:
    def __init__(self):
        self.session = None
        self.paths = NativePaths()

    async def close(self):
        await self.paths.close()
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def request(self, connection, operation, params=()):
        validate_connection({"base_url": connection["base_url"], "token": ""})
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                headers={"User-Agent": "TianShu-Music/1"},
            )
        salt = secrets.token_hex(16)
        token = hashlib.md5((connection["password"] + salt).encode()).hexdigest()
        data = [
            ("u", connection["username"]),
            ("t", token),
            ("s", salt),
            ("v", "1.16.1"),
            ("c", "TianShu"),
            ("f", "json"),
            *((k, str(v)) for k, v in (params.items() if isinstance(params, dict) else params)),
        ]
        try:
            async with self.session.post(
                connection["base_url"] + "/rest/" + operation + ".view",
                data=data,
                allow_redirects=False,
            ) as response:
                require(response.status not in {401, 403}, "navidrome_auth_required", 409)
                require(response.status == 200, "navidrome_unavailable", 502)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    require(len(raw) <= 8 * 1024**2, "navidrome_invalid_response", 502)
                try:
                    value = json.loads(raw)["subsonic-response"]
                    require(isinstance(value, dict), "navidrome_invalid_response", 502)
                except (ValueError, KeyError, TypeError):
                    raise Fault("navidrome_invalid_response", 502) from None
                if value.get("status") != "ok":
                    code = object_field(value, "error").get("code")
                    error = (
                        "navidrome_auth_required"
                        if code in {40, 41, 42, 43, 44}
                        else "navidrome_forbidden"
                        if code == 50
                        else "navidrome_not_found"
                        if code == 70
                        else "navidrome_request_failed"
                    )
                    raise Fault(error, 409)
                return value
        except (aiohttp.ClientError, TimeoutError):
            raise Fault("navidrome_unavailable", 502) from None

    async def check(self, connection):
        ping = await self.request(connection, "ping")
        require(ping.get("type") == "navidrome", "navidrome_server_required", 409)
        folders = rows_field(
            object_field(await self.request(connection, "getMusicFolders"), "musicFolders"),
            "musicFolder",
            (str, int),
        )
        user = object_field(
            await self.request(connection, "getUser", {"username": connection["username"]}), "user"
        )
        require(user.get("username") == connection["username"], "navidrome_invalid_response", 502)
        return {
            "version": ping.get("serverVersion", ""),
            "can_scan": bool(user.get("adminRole")),
            "folders": [{"id": str(f["id"]), "name": str(f.get("name", f["id"]))} for f in folders],
        }

    async def songs(self, connection):
        songs, seen = [], set()
        for offset in range(0, 100000, 500):
            response = await self.request(
                connection,
                "search3",
                {
                    "query": "",
                    "artistCount": 0,
                    "albumCount": 0,
                    "songCount": 500,
                    "songOffset": offset,
                    "musicFolderId": connection["music_folder_id"],
                },
            )
            rows = rows_field(object_field(response, "searchResult3"), "song")
            require(
                isinstance(rows, list)
                and all(isinstance(r, dict) and isinstance(r.get("id"), str) for r in rows),
                "navidrome_invalid_response",
                502,
            )
            require(not any(r["id"] in seen for r in rows), "navidrome_catalog_incomplete", 502)
            songs.extend(rows)
            seen.update(r["id"] for r in rows)
            if len(rows) < 500:
                return songs
        raise Fault("navidrome_catalog_incomplete", 502)

    async def playlists(self, connection):
        rows = rows_field(
            object_field(await self.request(connection, "getPlaylists"), "playlists"), "playlist"
        )
        return rows

    async def playlist(self, connection, identity):
        row = (await self.request(connection, "getPlaylist", {"id": identity})).get("playlist")
        require(
            isinstance(row, dict)
            and row.get("id") == identity
            and isinstance(row.get("entry", []), list),
            "navidrome_invalid_response",
            502,
        )
        rows_field(row, "entry")
        return row
