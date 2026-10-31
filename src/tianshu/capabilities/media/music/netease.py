"""NetEase account, playlist and authorized download adapter; no browser cookie jar."""

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import aiohttp

from ...contracts import Fault, require
from .protocol import compact, eapi, weapi
from .quality import LEVELS
from .transfer import cdn_url
from .request_control import Limited, request_slot, retry_after
from .metadata_cache import MetadataCache

MAX_TRACKS = 20000
COOKIE_NAMES = {"MUSIC_U", "MUSIC_A", "__csrf", "NMTID", "__remember_me", "deviceId"}


def source_id(value):
    require(type(value) in (str, int), "invalid_music_id", 400)
    value = str(value)
    require(re.fullmatch(r"[1-9][0-9]{0,19}", value), "invalid_music_id", 400)
    return value


def clean_text(value, maximum=1024):
    return value[:maximum] if isinstance(value, str) else ""


def cover_url(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if (
        url.scheme not in {"http", "https"}
        or not (url.hostname or "").endswith(".music.126.net")
        or url.username
        or url.password
    ):
        return None
    return url._replace(scheme="https", fragment="").geturl()


def playlist_info(row, uid):
    require(isinstance(row, dict), "music_upstream_invalid", 502)
    creator = row.get("creator") or {}
    require(isinstance(creator, dict), "music_upstream_invalid", 502)
    return {
        "source_id": source_id(row.get("id")),
        "name": clean_text(row.get("name"), 256),
        "description": clean_text(row.get("description"), 8192),
        "cover_url": cover_url(row.get("coverImgUrl")),
        "track_count": row.get("trackCount") if type(row.get("trackCount")) is int else None,
        "source_updated_at": row.get("updateTime"),
        "kind": "liked"
        if row.get("specialType") == 5
        else "created"
        if str(creator.get("userId")) == str(uid)
        else "collected",
    }


def track_info(row):
    require(isinstance(row, dict), "music_upstream_invalid", 502)
    artists = row.get("ar") or row.get("artists") or []
    album = row.get("al") or row.get("album") or {}
    require(isinstance(artists, list) and isinstance(album, dict), "music_upstream_invalid", 502)
    return {
        "source_id": source_id(row.get("id")),
        "title": clean_text(row.get("name"), 512),
        "artists": [clean_text(a.get("name"), 256) for a in artists if isinstance(a, dict)],
        "album": clean_text(album.get("name"), 512),
        "album_id": str(album["id"]) if album.get("id") else None,
        "cover_url": cover_url(album.get("picUrl")),
        "duration_ms": row.get("dt", row.get("duration")),
        "disc": clean_text(str(row.get("cd") or ""), 32),
        "track_number": row.get("no"),
        "availability": "known",
        "audio_state": "not_downloaded",
    }


class Netease:
    def __init__(
        self,
        *,
        web_origin="https://music.163.com",
        api_origin="https://interface.music.163.com",
        control=None,
    ):
        self.web_origin = web_origin.rstrip("/")
        self.api_origin = api_origin.rstrip("/")
        self.session = None
        self.control = control
        self.cache = MetadataCache()

    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def request(self, path, data, cookie=None, *, mode="weapi", codes=(200,)):
        async with request_slot(
            self.control,
            "netease",
            (cookie or {}).get("_music_account_id"),
            account_revision=(cookie or {}).get("_music_account_revision"),
        ):
            return await self._request(path, data, cookie, mode=mode, codes=codes)

    async def _request(self, path, data, cookie=None, *, mode="weapi", codes=(200,)):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=15)
            )
        cookies = {k: v for k, v in (cookie or {}).items() if k in COOKIE_NAMES}
        cookies.setdefault("deviceId", uuid.uuid4().hex)
        retained = dict(cookies)
        data = {**data, "e_r": False}
        headers = {"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"}
        if mode == "eapi":
            envelope = {
                "os": "pc",
                "appver": "3.1.17.204416",
                "osver": "Microsoft-Windows-10",
                "deviceId": cookies["deviceId"],
                "channel": "netease",
                "versioncode": "140",
                "requestId": str(int(time.time() * 1000)),
                **cookies,
            }
            data["header"] = envelope
            form = eapi(path, data)
            url = self.api_origin + "/eapi/" + path.removeprefix("/api/")
            cookies = envelope
        else:
            data["csrf_token"] = cookies.get("__csrf", "")
            form = weapi(data)
            url = self.web_origin + "/weapi/" + path.removeprefix("/api/")
        try:
            async with self.session.post(
                url, data=form, cookies=cookies, headers=headers, allow_redirects=False
            ) as response:
                if response.status == 429:
                    raise Limited(retry_after(response.headers.get("Retry-After"), time.time()))
                require(response.status != 401, "music_auth_required", 409)
                require(response.status == 200, "music_upstream_unavailable", 502)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    require(len(raw) <= 8 * 1024 * 1024, "music_snapshot_too_large", 502)
                value = json.loads(raw)
                require(isinstance(value, dict), "music_upstream_invalid", 502)
                code = value.get("code")
                require(code not in {301, 401}, "music_auth_required", 409)
                if code in {429, 460}:
                    raise Limited(retry_after(response.headers.get("Retry-After"), time.time()))
                require(code in codes, "music_upstream_rejected", 502)
                received = {k: v.value for k, v in response.cookies.items() if k in COOKIE_NAMES}
                return value, {**retained, **received}
        except (aiohttp.ClientError, TimeoutError):
            raise Fault("music_upstream_unavailable", 502) from None
        except (ValueError, TypeError):
            raise Fault("music_upstream_invalid", 502) from None

    async def qr_start(self):
        value, cookie = await self.request("/api/login/qrcode/unikey", {"type": 3}, mode="eapi")
        key = value.get("unikey")
        require(isinstance(key, str) and 0 < len(key) <= 512, "music_upstream_invalid", 502)
        return {
            "key": key,
            "cookie": cookie,
            "url": "https://music.163.com/login?codekey=" + quote(key),
        }

    async def qr_poll(self, key, cookie):
        value, cookie = await self.request(
            "/api/login/qrcode/client/login",
            {"key": key, "type": 3},
            cookie,
            mode="eapi",
            codes=(800, 801, 802, 803),
        )
        state = {800: "expired", 801: "waiting", 802: "scanned", 803: "ready"}[value["code"]]
        if state == "ready":
            require(bool(cookie.get("MUSIC_U")), "music_upstream_invalid", 502)
        return state, cookie

    async def account(self, cookie):
        value, _ = await self.request("/api/w/nuser/account/get", {}, cookie)
        profile = value.get("profile")
        require(isinstance(profile, dict) and profile.get("userId"), "music_auth_required", 409)
        return {
            "user_id": source_id(profile["userId"]),
            "label": clean_text(profile.get("nickname"), 256),
        }

    async def search(self, query, cookie):
        if self.control is None:
            return await self._search(query, cookie)
        return await self.cache.read("search", cookie, query, lambda: self._search(query, cookie))

    async def _search(self, query, cookie):
        require(isinstance(query, str) and 0 < len(query) <= 512, "invalid_input", 400)
        value, _ = await self.request(
            "/api/cloudsearch/pc", {"s": query, "type": 1, "limit": 30, "offset": 0}, cookie
        )
        result = value.get("result") or {}
        require(isinstance(result, dict), "music_upstream_invalid", 502)
        rows = result.get("songs") or []
        require(isinstance(rows, list), "music_upstream_invalid", 502)
        return [track_info(row) for row in rows[:30]]

    async def download(self, identity, level, cookie):
        value, _ = await self.request(
            "/api/song/enhance/download/url/v1",
            {
                "id": source_id(identity),
                "level": "hires" if level == "best" else level,
                "immerseType": "c51",
            },
            cookie,
            mode="eapi",
        )
        row = value.get("data")
        require(isinstance(row, dict), "music_upstream_invalid", 502)
        require(str(row.get("id")) == identity, "music_upstream_invalid", 502)
        require(not row.get("freeTrialInfo"), "music_preview_rejected", 409)
        require(row.get("code", 200) == 200 and row.get("url"), "music_download_forbidden", 409)
        require(
            isinstance(row.get("level"), str) and row["level"] in LEVELS,
            "music_quality_unknown",
            409,
        )
        require(type(row.get("size")) is int and row["size"] > 0, "music_upstream_invalid", 502)
        md5 = row.get("md5")
        require(
            md5 is None or isinstance(md5, str) and re.fullmatch(r"[a-fA-F0-9]{32}", md5),
            "music_upstream_invalid",
            502,
        )
        return {"url": cdn_url(row["url"]), "level": row["level"], "size": row["size"], "md5": md5}

    async def lyrics(self, identity, cookie):
        value, _ = await self.request(
            "/api/song/lyric",
            {"id": source_id(identity), "tv": -1, "lv": -1, "rv": -1, "kv": -1, "_nmclfl": 1},
            cookie,
        )
        result = {}
        for name, key in (
            ("original", "lrc"),
            ("translated", "tlyric"),
            ("romanized", "romalrc"),
        ):
            row = value.get(key) or {}
            require(isinstance(row, dict), "music_upstream_invalid", 502)
            result[name] = clean_text(row.get("lyric"), 512 * 1024)
        return result

    async def album(self, identity, cookie):
        value, _ = await self.request("/api/v1/album/" + source_id(identity), {}, cookie)
        row = value.get("album")
        require(
            isinstance(row, dict) and str(row.get("id")) == identity, "music_upstream_invalid", 502
        )
        artists = row.get("artists") or [row.get("artist") or {}]
        require(isinstance(artists, list), "music_upstream_invalid", 502)
        date = ""
        if type(row.get("publishTime")) is int and 0 < row["publishTime"] < 4102444800000:
            date = (
                datetime.fromtimestamp(row["publishTime"] / 1000, timezone(timedelta(hours=8)))
                .date()
                .isoformat()
            )
        return {
            "album_artists": [
                clean_text(a.get("name"), 256) for a in artists if isinstance(a, dict)
            ],
            "date": date,
        }

    async def playlists(self, uid, cookie, *, fresh=False):
        if self.control is None:
            return await self._playlists(uid, cookie)
        return await self.cache.read(
            "playlists", cookie, uid, lambda: self._playlists(uid, cookie), fresh=fresh
        )

    async def _playlists(self, uid, cookie):
        result, seen = [], set()
        async with asyncio.timeout(90):
            for offset in range(0, 10000, 100):
                value, _ = await self.request(
                    "/api/user/playlist",
                    {"uid": source_id(uid), "limit": 100, "offset": offset, "includeVideo": False},
                    cookie,
                )
                rows = value.get("playlist")
                require(
                    isinstance(rows, list) and type(value.get("more")) is bool,
                    "music_upstream_invalid",
                    502,
                )
                for row in rows:
                    item = playlist_info(row, uid)
                    require(item["source_id"] not in seen, "music_scan_incomplete", 502)
                    seen.add(item["source_id"])
                    result.append(item)
                if value["more"] is False:
                    return result
                require(len(rows) == 100, "music_scan_incomplete", 502)
        raise Fault("music_snapshot_too_large", 502)

    async def snapshot(self, playlist_id, uid, cookie):
        return await self.snapshot_since(playlist_id, uid, cookie, None)

    async def snapshot_since(self, playlist_id, uid, cookie, previous):
        async def detail():
            value, _ = await self.request(
                "/api/v6/playlist/detail",
                {"id": source_id(playlist_id), "n": MAX_TRACKS, "s": 0},
                cookie,
                mode="eapi",
            )
            row = value.get("playlist")
            require(isinstance(row, dict), "music_upstream_invalid", 502)
            info = playlist_info(row, uid)
            require(info["source_id"] == playlist_id, "music_upstream_invalid", 502)
            rows = row.get("trackIds")
            require(isinstance(rows, list), "music_scan_incomplete", 502)
            ids = [source_id(r.get("id")) for r in rows if isinstance(r, dict)]
            require(
                len(ids) == len(rows) == info["track_count"] and len(ids) <= MAX_TRACKS,
                "music_scan_incomplete",
                502,
            )
            return info, ids

        info, ids = await detail()
        if (
            previous
            and previous.get("complete")
            and previous.get("metadata_synced_at") is not None
            and (self.control.repo.clock() if self.control else time.time())
            - previous["metadata_synced_at"]
            < 7 * 86400
            and isinstance(info["source_updated_at"], int)
            and info["source_updated_at"] == previous.get("source_updated_at")
            and ids == previous.get("entries")
        ):
            return {"playlist": info, "entries": ids, "unchanged": True}
        tracks = {}
        unique = list(dict.fromkeys(ids))
        for offset in range(0, len(unique), 300):
            batch = unique[offset : offset + 300]
            value, _ = await self.request(
                "/api/v3/song/detail", {"c": compact([{"id": int(i)} for i in batch])}, cookie
            )
            rows = value.get("songs")
            require(isinstance(rows, list), "music_upstream_invalid", 502)
            for row in rows:
                song = track_info(row)
                require(
                    song["source_id"] in batch and song["source_id"] not in tracks,
                    "music_upstream_invalid",
                    502,
                )
                tracks[song["source_id"]] = song
        current, current_ids = await detail()
        require(
            current_ids == ids and current["source_updated_at"] == info["source_updated_at"],
            "music_source_changed",
            409,
        )
        for identity in unique:
            tracks.setdefault(
                identity,
                {
                    "source_id": identity,
                    "title": "",
                    "artists": [],
                    "album": "",
                    "availability": "unavailable",
                    "audio_state": "not_downloaded",
                },
            )
        return {"playlist": current, "entries": ids, "tracks": list(tracks.values())}
