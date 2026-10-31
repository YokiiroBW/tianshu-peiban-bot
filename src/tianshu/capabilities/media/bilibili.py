"""Bounded Bilibili connector. No upstream payload or signed URL reaches the browser.

Endpoint/signature interoperability was checked against yt-dlp 2026.08.19's Bilibili extractor.
The injected origins are an internal test port; production always uses the fixed HTTPS origins.
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import aiohttp

from ..contracts import Fault, require
from .config import source
from .identity import require_bvid
from .metadata import normalize_bilibili
from .rule_context import RuleSourceContext
from .types import MetadataValidationError

ALLOWED_LINK_HOSTS = frozenset({"www.bilibili.com", "bilibili.com", "m.bilibili.com", "b23.tv"})
MIXIN_ORDER = (
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
)


def timestamp(value):
    if type(value) not in (int, float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def cover_url(value):
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.replace("http://", "https://", 1))
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or not (host.endswith(".hdslb.com") or host.endswith(".biliimg.com"))
    ):
        return None
    return parts._replace(query="", fragment="").geturl()


class Bilibili:
    def __init__(
        self,
        *,
        api="https://api.bilibili.com",
        passport="https://passport.bilibili.com",
        clock=time.time,
    ):
        self.api, self.passport, self.clock = api, passport, clock
        self.session = None
        self._wbi = None

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                headers={
                    "User-Agent": "Mozilla/5.0 TianshuMedia/1",
                    "Referer": "https://www.bilibili.com/",
                },
            )

    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def request(
        self, path, *, params=None, cookie=None, passport=False, cookies=False, anonymous_wbi=False
    ):
        await self.start()
        headers = {"Cookie": cookie} if cookie else {}
        try:
            async with self.session.get(
                (self.passport if passport else self.api) + path,
                params=params,
                headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status in {401, 403}:
                    raise Fault("auth_required", 503)
                if response.status in {412, 429}:
                    raise Fault("source_rate_limited", 503)
                require(response.status == 200, "source_unavailable", 503)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    require(len(raw) <= 2 * 1024**2, "source_budget_exceeded", 503)
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise Fault("source_invalid_response", 503) from None
                require(isinstance(value, dict), "source_invalid_response", 503)
                code = value.get("code")
                if (
                    anonymous_wbi
                    and path == "/x/web-interface/nav"
                    and code == -101
                    and isinstance(value.get("data"), dict)
                    and isinstance(value["data"].get("wbi_img"), dict)
                ):
                    return value["data"]
                if code in {-101, -111, -403}:
                    raise Fault("auth_required", 503)
                if code in {-352, -401, -412}:
                    raise Fault("source_rate_limited", 503)
                if code in {-404, 62002, 62004}:
                    raise Fault("source_removed", 503)
                require(code == 0, "source_unavailable", 503)
                data = value.get("data", value.get("result"))
                require(isinstance(data, (dict, list)), "source_invalid_response", 503)
                return (data, response.cookies) if cookies else data
        except (aiohttp.ClientError, TimeoutError):
            raise Fault("source_unavailable", 503) from None

    async def account(self, cookie):
        data = await self.request("/x/web-interface/nav", cookie=cookie)
        require(data.get("isLogin") is True and data.get("mid"), "auth_required", 503)
        return {"mid": str(data["mid"]), "uname": str(data.get("uname", ""))}

    async def signed(self, params, cookie):
        if self._wbi is None or self.clock() - self._wbi[0] > 30:
            nav = await self.request("/x/web-interface/nav", cookie=cookie, anonymous_wbi=True)
            images = nav.get("wbi_img", {})
            joined = "".join(
                urlsplit(images.get(name, "")).path.rsplit("/", 1)[-1].split(".", 1)[0]
                for name in ("img_url", "sub_url")
            )
            require(len(joined) >= 64, "source_invalid_response", 503)
            self._wbi = (self.clock(), "".join(joined[index] for index in MIXIN_ORDER)[:32])
        clean = {
            key: "".join(c for c in str(value) if c not in "!'()*")
            for key, value in sorted({**params, "wts": int(self.clock())}.items())
        }
        clean["w_rid"] = hashlib.md5((urlencode(clean) + self._wbi[1]).encode()).hexdigest()
        return clean

    async def expand(self, value):
        require(isinstance(value, str) and len(value) <= 2048, "invalid_input", 400)
        if value.startswith("BV"):
            require_bvid(value)
            return {"bvid": value}
        current = value
        await self.start()
        for _ in range(4):
            parts = urlsplit(current)
            require(
                parts.scheme == "https"
                and parts.hostname in ALLOWED_LINK_HOSTS
                and not parts.username
                and not parts.password
                and parts.port in {None, 443},
                "invalid_source_url",
                400,
            )
            if parts.hostname != "b23.tv":
                identifier = next(
                    (
                        part
                        for part in parts.path.split("/")
                        if part.startswith("BV") or part.startswith("av")
                    ),
                    None,
                )
                if identifier and identifier.startswith("BV"):
                    require_bvid(identifier)
                    return {"bvid": identifier}
                if identifier and identifier[2:].isdigit():
                    return {"aid": identifier[2:]}
                raise Fault("invalid_source_url", 400)
            try:
                async with self.session.get(current, allow_redirects=False) as response:
                    require(
                        response.status in {301, 302, 303, 307, 308}
                        and response.headers.get("Location"),
                        "source_unavailable",
                        503,
                    )
                    current = urljoin(current, response.headers["Location"])
            except (aiohttp.ClientError, TimeoutError):
                raise Fault("source_unavailable", 503) from None
        raise Fault("source_redirect_limit", 503)

    async def formats(self, bvid, cid, cookie):
        query = await self.signed(
            {"bvid": bvid, "cid": cid, "fnval": 4048, **({"try_look": 1} if not cookie else {})},
            cookie,
        )
        data = await self.request("/x/player/wbi/playurl", params=query, cookie=cookie)
        available = {
            str(item["id"])
            for item in data.get("dash", {}).get("video", [])
            if isinstance(item, dict) and item.get("id")
        }
        if data.get("durl") and data.get("quality"):
            available.add(str(data["quality"]))
        labels = {
            str(item.get("quality")): str(
                item.get("new_description") or item.get("display_desc") or item.get("quality")
            )
            for item in data.get("support_formats", [])
            if isinstance(item, dict)
        }
        require(available, "quality_unavailable", 503)
        return [
            {"quality_id": number, "label": labels.get(number, number)}
            for number in sorted(available, key=int, reverse=True)
        ]

    async def resolve(self, value, cookie=None, *, with_formats=True):
        try:
            resolved = await self._resolve(value, cookie, with_formats=with_formats)
            normalize_bilibili(resolved["snapshot"])
            return resolved
        except (KeyError, ValueError, TypeError, MetadataValidationError):
            raise Fault("source_invalid_response", 503) from None

    async def _resolve(self, value, cookie=None, *, with_formats=True):
        query = await self.expand(value)
        data = await self.request("/x/web-interface/view", params=query, cookie=cookie)
        bvid = str(data.get("bvid", ""))
        require_bvid(bvid)
        pages = data.get("pages")
        require(isinstance(pages, list) and 0 < len(pages) <= 1000, "source_invalid_response", 503)
        staff = data.get("staff", [])
        require(
            isinstance(staff, list) and all(isinstance(creator, dict) for creator in staff),
            "source_invalid_response",
            503,
        )
        creators = [
            {
                "mid": str(data.get("owner", {}).get("mid", "")),
                "name": str(data.get("owner", {}).get("name", "")),
                "role": "uploader",
            }
        ]
        for creator in staff:
            if isinstance(creator, dict) and str(creator.get("mid")) != creators[0]["mid"]:
                creators.append(
                    {
                        "mid": str(creator.get("mid", "")),
                        "name": str(creator.get("name", "")),
                        "role": "collaborator",
                    }
                )
        parts = [
            {
                "cid": str(part["cid"]),
                "index": int(part.get("page", index + 1)),
                "title": part.get("part"),
                "duration_seconds": part.get("duration"),
            }
            for index, part in enumerate(pages)
        ]
        tags = await self.request("/x/tag/archive/tags", params={"bvid": bvid}, cookie=cookie)
        snapshot = {
            "schema_version": 1,
            "bvid": bvid,
            "title": data.get("title"),
            "description": data.get("desc"),
            "published_at": timestamp(data.get("pubdate")),
            "captured_at": timestamp(self.clock()),
            "creators": creators,
            "parts": parts,
            "tags": [
                str(tag["tag_name"])
                for tag in tags
                if isinstance(tag, dict) and tag.get("tag_name")
            ],
            "cover_available": cover_url(data.get("pic")) is not None,
        }
        video_parts = []
        for part in parts:
            video_parts.append(
                {
                    **part,
                    "formats": await self.formats(bvid, part["cid"], cookie)
                    if with_formats
                    else [],
                }
            )
        return {
            "snapshot": snapshot,
            "rule_context": {"is_collaboration": bool(staff)},
            "creator_images": [
                {"mid": creators[0]["mid"], "url": cover_url(data.get("owner", {}).get("face"))}
            ],
            "video": {
                "bvid": bvid,
                "title": data.get("title") or "",
                "description": data.get("desc") or "",
                "creator": {"mid": creators[0]["mid"], "name": creators[0]["name"]},
                "cover_url": cover_url(data.get("pic")),
                "parts": video_parts,
            },
        }

    async def scan(
        self, source_value, cookie, *, page_limit=200, item_limit=5000
    ) -> dict[str, RuleSourceContext]:
        """Return complete visible membership with facts bound to the exact selected source."""
        source_value = source(source_value)
        kind, identity = source_value["kind"], source_value["id"]
        if kind == "watch_later":
            return await self.scan_watch_later(cookie, item_limit=item_limit)
        seen, expected = {}, None
        for page in range(1, page_limit + 1):
            if kind == "favorite":
                data = await self.request(
                    "/x/v3/fav/resource/list",
                    params={"media_id": identity, "pn": page, "ps": 20, "order": "mtime"},
                    cookie=cookie,
                )
                require(isinstance(data, dict), "source_invalid_response", 503)
                info = data.get("info")
                more = data.get("has_more")
                require(
                    isinstance(info, dict) and "medias" in data and type(more) is bool,
                    "source_invalid_response",
                    503,
                )
                items = [] if data["medias"] is None else data["medias"]
                total = info.get("media_count")
            elif kind == "uploader":
                query = await self.signed(
                    {
                        "mid": identity,
                        "pn": page,
                        "ps": 30,
                        "order": "pubdate",
                        "platform": "web",
                        "web_location": "333.1387",
                    },
                    cookie,
                )
                data = await self.request("/x/space/wbi/arc/search", params=query, cookie=cookie)
                items = data.get("list", {}).get("vlist", [])
                total = data.get("page", {}).get("count")
                more = page * 30 < (total or 0)
            else:
                mid, sid = identity.split(":")
                path = (
                    "/x/polymer/web-space/seasons_archives_list"
                    if kind == "collection"
                    else "/x/series/archives"
                )
                params = (
                    {"mid": mid, "season_id": sid, "page_num": page, "page_size": 30}
                    if kind == "collection"
                    else {"mid": mid, "series_id": sid, "pn": page, "ps": 30}
                )
                data = await self.request(path, params=params, cookie=cookie)
                items = data.get("archives", [])
                total = data.get("page", {}).get("total")
                more = page * 30 < (total or 0)
            require(
                isinstance(items, list) and type(total) is int and total >= 0,
                "source_invalid_response",
                503,
            )
            if expected is None:
                expected = total
            require(total == expected, "source_changed_during_scan", 503)
            if kind == "favorite":
                require(len(items) <= 20, "source_invalid_response", 503)
            for item in items:
                require(isinstance(item, dict), "source_invalid_response", 503)
                bvid = item.get("bvid") or item.get("bv_id")
                # A deleted/private member still belongs to the baseline; never call an incomplete list empty.
                require(isinstance(bvid, str), "source_member_unavailable", 503)
                require_bvid(bvid)
                if kind == "favorite":
                    require(bvid not in seen, "source_scan_incomplete", 503)
                seen[bvid] = RuleSourceContext(
                    bvid=bvid,
                    source_kind=kind,
                    source_id=identity,
                    favorited_at=timestamp(item.get("fav_time")) if kind == "favorite" else None,
                )
                require(len(seen) <= item_limit, "source_budget_exceeded", 503)
            if kind == "favorite":
                require(len(seen) <= expected, "source_scan_incomplete", 503)
            if not more:
                # Favorites can count hidden entries that are absent from every visible page.
                # Their explicit terminal marker completes this visible set, not media_count.
                if kind != "favorite":
                    require(len(seen) == expected, "source_scan_incomplete", 503)
                return seen
            require(items, "source_scan_incomplete", 503)
        raise Fault("source_budget_exceeded", 503)

    async def scan_watch_later(self, cookie, *, item_limit) -> dict[str, RuleSourceContext]:
        """toview returns the entire authenticated list; count must confirm its completeness."""
        require(bool(cookie), "auth_required", 503)
        data = await self.request("/x/v2/history/toview", cookie=cookie)
        require(
            isinstance(data, dict)
            and type(data.get("count")) is int
            and data["count"] >= 0
            and "list" in data,
            "source_invalid_response",
            503,
        )
        total = data["count"]
        items = [] if data["list"] is None and total == 0 else data["list"]
        require(isinstance(items, list), "source_invalid_response", 503)
        require(total <= item_limit, "source_budget_exceeded", 503)
        require(len(items) == total, "source_scan_incomplete", 503)
        seen = {}
        for item in items:
            require(isinstance(item, dict), "source_invalid_response", 503)
            bvid = item.get("bvid")
            require(isinstance(bvid, str), "source_member_unavailable", 503)
            try:
                require_bvid(bvid)
            except MetadataValidationError:
                raise Fault("source_member_unavailable", 503) from None
            require(bvid not in seen, "source_scan_incomplete", 503)
            seen[bvid] = RuleSourceContext(bvid, "watch_later", "self")
        return seen

    async def qr_start(self):
        data = await self.request("/x/passport-login/web/qrcode/generate", passport=True)
        require(
            isinstance(data.get("url"), str) and isinstance(data.get("qrcode_key"), str),
            "source_invalid_response",
            503,
        )
        return data

    async def qr_poll(self, key):
        data, cookies = await self.request(
            "/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": key},
            passport=True,
            cookies=True,
        )
        code = data.get("code")
        if code in {86101, 86090, 86038}:
            return {"state": {86101: "waiting", 86090: "scanned", 86038: "expired"}[code]}
        require(code == 0, "source_invalid_response", 503)
        cookie = "; ".join(f"{name}={item.value}" for name, item in cookies.items())
        if not cookie:
            # The callback URL is the site's documented credential delivery, never a browser response.
            values = parse_qs(urlsplit(data.get("url", "")).query)
            cookie = "; ".join(
                f"{name}={value[0]}"
                for name, value in values.items()
                if name in {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5"}
            )
        return {"state": "ready", "cookie": cookie}
