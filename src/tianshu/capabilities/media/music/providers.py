"""Provider-neutral music metadata, backed by the existing NetEase adapter and local tools."""

import re

from ...contracts import digest, require
from . import tools
from .transfer import media_url
from .request_control import request_slot
from .metadata_cache import MetadataCache
from .catalog import search_title

PROVIDERS = {"netease": "网易云音乐", "qq": "QQ 音乐", "kugou": "酷狗音乐"}
KUGOU_HASHES = {
    "hires": ("res_hash",),
    "lossless": ("sq_hash", "sqhash"),
    "exhigh": ("hq_hash", "hqhash", "ogg_320_hash"),
    "higher": ("hq_hash", "hqhash", "ogg_320_hash"),
    "standard": ("file_hash", "ogg_128_hash"),
}


def kugou_hashes(value):
    extra = value.get("provider_data") or {}
    return {
        str(v).lower()
        for v in [
            value.get("source_id"),
            extra.get("hash"),
            *[extra.get(k) for keys in KUGOU_HASHES.values() for k in keys],
        ]
        if isinstance(v, str) and re.fullmatch(r"[a-fA-F0-9]{32}", v)
    }


def identity(value):
    require(type(value) in (str, int), "invalid_music_id", 400)
    value = str(value)
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,159}", value), "invalid_music_id", 400)
    return value


def playlist(row):
    source = identity(row["id"])
    extra = row.get("extra") or {}
    kind = "liked" if source == "profile:favorites" or extra.get("dirid") == "201" else "created"
    return {
        "source_id": source,
        "name": str(row.get("name") or source)[:256],
        "description": str(row.get("description") or "")[:8192],
        "cover_url": row.get("cover") or None,
        "track_count": row.get("track_count"),
        "source_updated_at": extra.get("update_time") or extra.get("publish_time"),
        "kind": kind,
    }


def track(row, provider):
    require(isinstance(row, dict), "music_upstream_invalid", 502)
    names = [
        value.strip() for value in re.split(r"、|, | / ", row.get("artist") or "") if value.strip()
    ]
    extra = row.get("extra") or {}
    safe_extra = {
        key: str(value)[:160]
        for key, value in extra.items()
        if key
        in {
            "songmid",
            "song_id",
            "media_mid",
            "hash",
            "sqhash",
            "hqhash",
            "album_id",
            "album_audio_id",
            "mixsongid",
            "sq_hash",
            "hq_hash",
            "res_hash",
            "file_hash",
            "ogg_320_hash",
            "ogg_128_hash",
            "audio_id",
            "privilege",
        }
        and isinstance(value, (str, int))
    }
    artwork = None
    if row.get("cover"):
        try:
            artwork = media_url(row["cover"], provider, artwork=True)
        except Exception:
            pass
    duration = row.get("duration")
    return {
        "source_id": identity(row["id"]),
        "provider": provider,
        "title": str(row.get("name") or "")[:512],
        "artists": names,
        "album": str(row.get("album") or "")[:512],
        "album_id": str(row.get("album_id") or safe_extra.get("album_id") or "") or None,
        "cover_url": artwork,
        "duration_ms": duration * 1000
        if isinstance(duration, (int, float)) and duration > 0
        else None,
        "disc": "",
        "track_number": None,
        "availability": "known",
        "audio_state": "not_downloaded",
        "provider_data": safe_extra,
    }


def bridge_song(value, provider):
    extra = dict(value.get("provider_data", {}))
    if provider == "kugou" and extra.get("res_hash"):
        # The upstream adapter tries hash first, then SQ/HQ fallbacks.
        extra["hash"] = extra["res_hash"]
    return {
        "id": value["source_id"],
        "source": provider,
        "name": value.get("title", ""),
        "artist": "、".join(value.get("artists", [])),
        "album": value.get("album", ""),
        "album_id": value.get("album_id") or "",
        "duration": int((value.get("duration_ms") or 0) / 1000),
        "extra": extra,
    }


def playlist_track(row, provider, source):
    require(isinstance(row, dict), "music_upstream_invalid", 502)
    if row.get("id") not in (None, ""):
        return track(row, provider)
    # Deleted/private uploads can retain a title and position without a usable ID.
    # Keep them in this playlist, without registering or downloading a guessed song.
    unavailable_id = "unavailable:" + digest(
        {"playlist": source, **{k: row.get(k) for k in ("name", "artist", "album", "duration")}}
    )
    return {
        **track({**row, "id": unavailable_id}, provider),
        "availability": "unavailable",
    }


class Bridge:
    def __init__(self, provider, control=None):
        self.provider = provider
        self.control, self.cache = control, MetadataCache()

    async def close(self):
        pass

    async def call(self, operation, cookie=None, **body):
        async with request_slot(
            self.control,
            self.provider,
            (cookie or {}).get("_music_account_id"),
            32,
            (cookie or {}).get("_music_account_revision"),
        ) as ticket:
            return await tools.invoke(
                "musicbridge",
                {
                    "provider": self.provider,
                    "operation": operation,
                    "cookie": (cookie or {}).get("cookie", ""),
                    **body,
                    "request_limit": 32,
                    "request_interval_ms": 3000 if self.control else 0,
                },
                meter=ticket,
            )

    async def qr_start(self, login_type="qq"):
        value = await self.call("qr_start", login_type=login_type)
        require(value.get("key"), "music_upstream_invalid", 502)
        return {
            "key": value["key"],
            "cookie": {"login_type": login_type},
            "url": value.get("url") or "",
            "image": value.get("image_url"),
        }

    async def qr_poll(self, key, cookie):
        value = await self.call("qr_poll", key=key, login_type=cookie.get("login_type", "qq"))
        status = {"success": "ready", "failed": "expired"}.get(
            value.get("status"), value.get("status")
        )
        require(status in {"ready", "waiting", "scanned", "expired"}, "music_upstream_invalid", 502)
        return status, {"cookie": value.get("cookie") or ""} if status == "ready" else cookie

    async def account(self, cookie):
        value = await self.call("account", cookie)
        identity(value.get("user_id"))
        return {
            "user_id": str(value["user_id"]),
            "label": PROVIDERS[self.provider] + " " + str(value["user_id"]),
        }

    async def playlists(self, uid, cookie, *, fresh=False):
        if self.control is None:
            return await self._playlists(uid, cookie)
        return await self.cache.read(
            "playlists", cookie, uid, lambda: self._playlists(uid, cookie), fresh=fresh
        )

    async def _playlists(self, uid, cookie):
        result, seen = [], set()
        for page in range(1, 101):
            rows = await self.call("playlists", cookie, page=page, limit=100)
            require(isinstance(rows, list) or rows is None, "music_upstream_invalid", 502)
            rows = rows or []
            new = 0
            for raw in rows:
                item = playlist(raw)
                if item["source_id"] not in seen:
                    seen.add(item["source_id"])
                    result.append(item)
                    new += 1
            if len(rows) < 100:
                return result
            require(new > 0, "music_scan_incomplete", 502)
        require(False, "music_snapshot_too_large", 502)

    async def snapshot(self, source, uid, cookie):
        source = identity(source)
        catalog = await self.playlists(uid, cookie)
        info = next((p for p in catalog if p["source_id"] == source), None)
        if (
            self.provider == "qq"
            and source in {"profile:dir:205", "profile:dir:206"}
            and info
            and info["track_count"] == 0
        ):
            current = next(
                (
                    p
                    for p in await self.playlists(uid, cookie, fresh=True)
                    if p["source_id"] == source
                ),
                None,
            )
            require(current and current["track_count"] == 0, "music_source_changed", 409)
            return {"playlist": current, "entries": [], "tracks": []}
        if info:
            rows = await self.call("playlist_songs", cookie, id=source)
        else:
            value = await self.call("playlist", cookie, id=source)
            info, rows = playlist(value["playlist"]), value["songs"]
        rows = rows or []
        require(
            type(info["track_count"]) is int and len(rows) == info["track_count"] <= 20000,
            "music_scan_incomplete",
            502,
        )
        tracks = [playlist_track(row, self.provider, source) for row in rows]
        current = await self.call("playlist_songs", cookie, id=source)
        entries = [row["source_id"] for row in tracks]
        require(
            [playlist_track(r, self.provider, source)["source_id"] for r in (current or [])]
            == entries,
            "music_source_changed",
            409,
        )
        return {
            "playlist": info,
            "entries": entries,
            "tracks": list({t["source_id"]: t for t in tracks}.values()),
        }

    async def search(self, query, cookie):
        if self.control is None:
            return await self._search(query, cookie)
        return await self.cache.read("search", cookie, query, lambda: self._search(query, cookie))

    async def _search(self, query, cookie):
        rows = await self.call("search", cookie, query=query)
        return [track(row, self.provider) for row in (rows or [])[:30]]

    async def enrich(self, value, cookie):
        if self.provider != "kugou" or all(
            value.get(k) for k in ("artists", "album", "duration_ms")
        ):
            return value

        async def fetch():
            query = search_title(value) + " " + " ".join(value.get("artists", []))
            rows = await self._search(query.strip(), cookie)
            hashes = kugou_hashes(value)
            matches = [row for row in rows if hashes & kugou_hashes(row)]
            if not matches:
                return value
            signatures = {
                digest({k: row.get(k) for k in ("title", "artists", "album", "duration_ms")})
                for row in matches
            }
            require(len(signatures) == 1, "music_match_ambiguous", 409)
            found = matches[0]
            return {
                **value,
                **{k: v for k, v in found.items() if v and k not in {"source_id", "provider_data"}},
                "source_id": value["source_id"],
                "provider_data": {
                    **value.get("provider_data", {}),
                    **found.get("provider_data", {}),
                },
                "metadata_enriched": True,
            }

        # A hash match, never a title guess, supplies missing source metadata.
        return await self.cache.read("enrich", cookie, value, fetch, ttl=86400)

    async def download_track(self, value, level, cookie):
        song = bridge_song(value, self.provider)
        if self.provider == "kugou":
            extra = song["extra"]
            fields = {key for keys in KUGOU_HASHES.values() for key in keys}
            known = any(extra.get(key) for key in fields)
            selected = next(
                (
                    extra[key]
                    for key in KUGOU_HASHES["hires" if level == "best" else level]
                    if extra.get(key)
                ),
                None,
            )
            if not known:
                selected = extra.get("hash") or song["id"]
            require(selected, "music_quality_unavailable", 409)
            # A playlist can name the SQ hash even when the account can only get MP3.
            # The resolver tries each tier; do not silently retry that same SQ hash.
            song = {
                **song,
                "id": selected,
                "extra": {**{k: v for k, v in extra.items() if k not in fields}, "hash": selected},
            }
        offered = await self.call("download", cookie, level=level, song=song)
        offered["url"] = media_url(offered["url"], self.provider)
        require(
            offered.get("level")
            in {"observed", "standard", "higher", "exhigh", "lossless", "hires"},
            "music_quality_unknown",
            409,
        )
        return offered

    async def lyrics_track(self, value, cookie):
        result = await self.call("lyrics", cookie, song=bridge_song(value, self.provider))
        return {"original": result[: 512 * 1024] if isinstance(result, str) else ""}
