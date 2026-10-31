"""Published store catalogues, deterministic package selection and bounded downloads."""

import json
import re
from urllib.parse import urljoin, urlsplit

import aiohttp

from ...contracts import Fault, require

STORES = (
    {
        "id": "keiyoushi",
        "name": "Keiyoushi",
        "url": "https://github.com/keiyoushi/extensions/raw/repo/index.pb",
        "catalog": "https://raw.githubusercontent.com/keiyoushi/extensions/repo/index.json",
    },
    {
        "id": "yuzono",
        "name": "Yūzōnō",
        "url": "https://raw.githubusercontent.com/yuzono/manga-repo/repo/index.json",
        "catalog": "https://raw.githubusercontent.com/yuzono/manga-repo/repo/index.json",
    },
    {
        "id": "littlesurvival",
        "name": "LittleSurvival",
        "url": "https://raw.githubusercontent.com/LittleSurvival/copymanga-copy20/repo/index.min.json",
        "catalog": "https://raw.githubusercontent.com/LittleSurvival/copymanga-copy20/repo/index.min.json",
    },
)
HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


def public_url(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise Fault("comic_extension_url_invalid", 409) from None
    require(
        parsed.scheme == "https"
        and parsed.hostname in HOSTS
        and not parsed.username
        and not parsed.password
        and port in {None, 443},
        "comic_extension_url_invalid",
        409,
    )
    return url


async def fetch(client, url, limit=32 * 1024**2):
    """Store files never receive the Suwayomi credentials, including on redirects."""
    try:
        for _ in range(6):
            async with client.get(
                public_url(url),
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=120, sock_connect=15),
            ) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                require(response.status == 200, "comic_extension_download_failed", 503)
                content = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    content.extend(chunk)
                    require(len(content) <= limit, "comic_extension_file_too_large", 409)
                return bytes(content)
    except (aiohttp.ClientError, TimeoutError):
        raise Fault("comic_extension_download_failed", 503) from None
    raise Fault("comic_extension_url_invalid", 409)


def catalogue(store, data):
    modern = isinstance(data, dict)
    entries = data.get("extensionList", {}).get("extensions", []) if modern else data
    require(
        isinstance(entries, list) and len(entries) <= 20000, "comic_extension_catalog_invalid", 503
    )
    result = []
    for row in entries:
        pkg = row.get("packageName" if modern else "pkg", "")
        require(
            isinstance(pkg, str) and re.fullmatch(r"[A-Za-z0-9_.]{1,240}", pkg),
            "comic_extension_catalog_invalid",
            503,
        )
        version = str(row.get("versionName" if modern else "version", ""))
        lib = str(row.get("extensionLib") or version.rsplit(".", 1)[0])
        sources = row.get("sources") or []
        ids = sorted({str(s["id"]) for s in sources if str(s.get("id", "0")) != "0"})
        resources = row.get("resources", {})
        url = (
            (resources.get("jarUrl") or resources.get("apkUrl") or "")
            if modern
            else urljoin(store["catalog"], "apk/" + row.get("apk", ""))
        )
        warning = (
            str(row.get("contentWarning", "")).removeprefix("CONTENT_WARNING_")
            if modern
            else "NSFW"
            if row.get("nsfw")
            else "SAFE"
        )
        compatible = lib in {"1.3", "1.4", "1.5", "1.6"}
        artifact = urlsplit(url).path.endswith((".jar", ".apk"))
        state = (
            "queued"
            if compatible and ids and artifact
            else "incompatible"
            if not compatible
            else "placeholder"
        )
        result.append(
            {
                "package": pkg,
                "name": str(row.get("name") or pkg).removeprefix("Tachiyomi: "),
                "store_id": store["id"],
                "store_url": store["url"],
                "version": version,
                "version_code": int(row.get("versionCode" if modern else "code", 0)),
                "lib": lib,
                "source_ids": ids,
                "languages": sorted({str(s.get("language", s.get("lang", ""))) for s in sources}),
                "content_warning": warning or "UNKNOWN",
                "artifact_url": url,
                "state": state,
                "code": "comic_extension_" + state,
            }
        )
    return result


def select_packages(candidates, installed, pinned):
    """Keep an existing package's origin; never let a fork silently take it over."""
    grouped = {}
    for row in candidates:
        grouped.setdefault(row["package"], []).append(row)
    output = []
    occupied = {
        str(s["id"]): pkg
        for pkg, e in installed.items()
        for s in e.get("source", {}).get("nodes", [])
    }
    for pkg, choices in grouped.items():
        existing = installed.get(pkg)
        origin = pinned.get(pkg) or (existing or {}).get("storeIndexUrl")
        matching = [
            c
            for c in choices
            if c["store_url"].removesuffix("index.min.json").removesuffix("repo.json")
            == str(origin).removesuffix("index.min.json").removesuffix("repo.json")
        ]
        chosen = dict((matching or choices)[0])
        chosen["duplicates"] = len(choices) - 1
        chosen["installed"] = bool(existing)
        chosen["installed_version"] = existing.get("versionName") if existing else None
        if existing and origin and not matching:
            chosen.update(state="origin_conflict", code="comic_extension_origin_conflict")
            chosen["store_url"] = origin
        if chosen["state"] == "queued":
            if any(s in occupied and occupied[s] != pkg for s in chosen["source_ids"]):
                chosen.update(state="source_conflict", code="comic_extension_source_conflict")
            elif existing and int(existing["versionCodeLong"]) >= chosen["version_code"]:
                chosen.update(state="installed", code="comic_extension_current")
            else:
                for s in chosen["source_ids"]:
                    occupied[s] = pkg
        output.append(chosen)
    return output


async def read_store(client, store):
    try:
        data = json.loads(await fetch(client, store["catalog"]))
        rows = catalogue(store, data)
        if isinstance(data, dict):
            key = data.get("signingKey", "")
        else:
            meta = json.loads(await fetch(client, urljoin(store["catalog"], "repo.json")))
            key = meta.get("meta", {}).get("signingKeyFingerprint", "")
        return rows, key
    except (KeyError, TypeError, ValueError):
        raise Fault("comic_extension_catalog_invalid", 503) from None
