"""Authenticated AssetLibrary publication and independent media-server observations."""

import asyncio
import base64
import json
import os
import ssl
from pathlib import Path
from io import BytesIO
from urllib.parse import quote, urlsplit

import aiohttp

from ..contracts import Fault, require


def validate_connection(value):
    require(isinstance(value, dict), "invalid_input", 400)
    url = value.get("base_url")
    require(isinstance(url, str) and len(url) <= 2048, "invalid_input", 400)
    parts = urlsplit(url)
    require(
        parts.scheme in {"http", "https"}
        and parts.hostname
        and not parts.username
        and not parts.password
        and not parts.query
        and not parts.fragment,
        "invalid_input",
        400,
    )
    require(
        (isinstance(value.get("token_env"), str) and value["token_env"].isidentifier())
        or isinstance(value.get("token_ref"), str)
        or isinstance(value.get("token"), str),
        "invalid_input",
        400,
    )
    if value.get("ca_file") is not None:
        require(
            isinstance(value["ca_file"], str)
            and Path(value["ca_file"]).is_absolute()
            and Path(value["ca_file"]).is_file(),
            "invalid_input",
            400,
        )


class Remote:
    def __init__(self, token_resolver=None):
        self.session = None
        self.token_resolver = token_resolver

    async def token(self, config):
        if config.get("token_ref") and self.token_resolver:
            return await asyncio.to_thread(self.token_resolver, config["token_ref"])
        return config.get("token") or os.environ.get(config.get("token_env", ""))

    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def request(self, config, method, path, *, body=None, params=None, server=False):
        validate_connection(config)
        token = await self.token(config)
        require(token and token.strip(), "media_peer_not_configured", 503)
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
        headers = (
            (
                {"Authorization": f'MediaBrowser Token="{token}"'}
                if config.get("kind") == "jellyfin"
                else {"X-Emby-Token": token}
            )
            if server
            else {"Authorization": "Bearer " + token}
        )
        if server and config.get("kind") == "plex":
            headers = {
                "X-Plex-Token": token,
                "Accept": "application/json",
                "X-Plex-Client-Identifier": "tianshu-media",
            }
        try:
            async with self.session.request(
                method,
                config["base_url"].rstrip("/") + path,
                json=body,
                params=params,
                headers=headers,
                allow_redirects=False,
                ssl=ssl.create_default_context(cafile=config.get("ca_file")),
            ) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    require(len(raw) <= 2 * 1024**2, "media_peer_invalid_response", 503)
                if response.status not in {200, 201, 202, 204}:
                    try:
                        value = json.loads(raw)
                        code = value.get("code")
                    except (ValueError, AttributeError):
                        code = None
                    allowed = {
                        "target_exists",
                        "scope_changed",
                        "unauthorized",
                        "idempotency_conflict",
                        "version_conflict",
                        "invalid_manifest",
                        "hash_mismatch",
                        "insufficient_space",
                        "source_missing",
                        "source_changed",
                        "manual_review",
                        "busy",
                    }
                    raise Fault(code if code in allowed else "media_peer_unavailable", 503)
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise Fault("media_peer_invalid_response", 503) from None
        except (aiohttp.ClientError, TimeoutError):
            raise Fault(
                "publication_unverified" if not server else "media_server_unavailable", 503
            ) from None

    async def image(self, config, item_id):
        validate_connection(config)
        token = await self.token(config)
        require(token, "media_peer_not_configured", 503)
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
        try:
            headers = (
                {"Authorization": f'MediaBrowser Token="{token}"'}
                if config.get("kind") == "jellyfin"
                else {"X-Emby-Token": token}
            )
            async with self.session.get(
                config["base_url"].rstrip("/") + f"/Items/{item_id}/Images/Primary",
                headers=headers,
                allow_redirects=False,
                ssl=ssl.create_default_context(cafile=config.get("ca_file")),
            ) as response:
                require(response.status == 200, "library_image_unavailable", 503)
                data = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    data.extend(chunk)
                    require(len(data) <= 32 * 1024**2, "library_image_invalid", 503)
                return bytes(data)
        except (aiohttp.ClientError, TimeoutError):
            raise Fault("library_image_unavailable", 503) from None

    async def upload_image(self, config, item_id, raw, extension):
        """Emby ImageService expects base64 ASCII with an image MIME type, not JSON."""
        validate_connection(config)
        require(config.get("kind") == "emby" and extension in {"jpg", "png"}, "invalid_input", 400)
        require(len(raw) <= 32 * 1024**2, "creator_avatar_invalid", 503)
        token = await self.token(config)
        require(token and token.strip(), "media_peer_not_configured", 503)
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
        headers = {
            "X-Emby-Token": token,
            "Content-Type": "image/jpeg" if extension == "jpg" else "image/png",
        }
        try:
            async with self.session.post(
                config["base_url"].rstrip("/")
                + "/Items/"
                + quote(item_id, safe="")
                + "/Images/Primary",
                data=base64.b64encode(raw),
                headers=headers,
                allow_redirects=False,
                ssl=ssl.create_default_context(cafile=config.get("ca_file")),
            ) as response:
                require(response.status in {200, 201, 202, 204}, "director_image_unavailable", 503)
                async for _ in response.content.iter_chunked(16384):
                    pass
        except (aiohttp.ClientError, TimeoutError):
            raise Fault("director_image_unavailable", 503) from None


def matching_image(original, observed):
    """Compare small RGB thumbnails, tolerating server resizing and JPEG recompression.

    Mean absolute per-channel deviation may be at most 12/255, and no more than 15%
    of channels may deviate over 40/255. This is evidence, not a cryptographic identity.
    """
    from PIL import Image, ImageChops, ImageStat

    try:
        with Image.open(BytesIO(original)) as source, Image.open(BytesIO(observed)) as target:
            require(0 < target.width * target.height <= 50_000_000, "library_image_invalid", 503)
            require(
                abs(source.width / source.height - target.width / target.height) <= 0.03,
                "library_image_mismatch",
                503,
            )
            difference = ImageChops.difference(
                source.convert("RGB").resize((64, 64)), target.convert("RGB").resize((64, 64))
            )
            deviations = list(difference.tobytes())
            return (
                max(ImageStat.Stat(difference).mean) <= 12
                and sum(value > 40 for value in deviations) / len(deviations) <= 0.15
            )
    except (OSError, ValueError, Image.DecompressionBombError):
        raise Fault("library_image_invalid", 503) from None


class Publisher:
    def __init__(self, remote):
        self.remote = remote

    def checked(self, value):
        require(
            isinstance(value, dict)
            and isinstance(value.get("operation_id"), str)
            and len(value["operation_id"]) == 36
            and value.get("status")
            in {
                "prepared",
                "rejected",
                "queued",
                "publishing",
                "published",
                "index_pending",
                "indexed",
                "cancelled",
                "failed",
                "manual_review",
            }
            and type(value.get("version")) is int
            and value["version"] > 0,
            "media_peer_invalid_response",
            503,
        )
        if value["status"] == "prepared":
            require(
                isinstance(value.get("confirmation_digest"), str)
                and len(value["confirmation_digest"]) == 64,
                "media_peer_invalid_response",
                503,
            )
        if value["status"] == "indexed":
            require(
                isinstance(value.get("receipt"), dict)
                and isinstance(value["receipt"].get("files"), list),
                "media_peer_invalid_response",
                503,
            )
        return value

    async def prepare(self, target, package, identity):
        body = {
            "idempotency_key": "tianshu-media:" + identity,
            "manifest_base64": package["manifest_base64"],
            "manifest_digest": package["manifest_digest"],
        }
        if package.get("base_operation_id"):
            body.update(
                base_operation_id=package["base_operation_id"], base_version=package["base_version"]
            )
            if package.get("replace_video_cids"):
                body["replace_video_cids"] = package["replace_video_cids"]
        result = await self.remote.request(
            target["publisher"], "POST", "/assetlink/v1/media/packages/prepare", body=body
        )
        require(
            isinstance(result, dict)
            and result.get("operation_id")
            and result.get("manifest_digest") == package["manifest_digest"],
            "media_peer_invalid_response",
            503,
        )
        return self.checked(result)

    async def read(self, target, identity):
        require(
            isinstance(identity, str) and len(identity) == 36, "media_peer_invalid_response", 503
        )
        return self.checked(
            await self.remote.request(
                target["publisher"], "GET", f"/assetlink/v1/media/packages/{identity}"
            )
        )

    async def publish(self, target, operation):
        return self.checked(
            await self.remote.request(
                target["publisher"],
                "POST",
                f"/assetlink/v1/media/packages/{operation['operation_id']}/publish",
                body={
                    "confirmation_digest": operation["confirmation_digest"],
                    "expected_version": operation["version"],
                },
            )
        )

    async def cancel(self, target, operation):
        return self.checked(
            await self.remote.request(
                target["publisher"],
                "POST",
                f"/assetlink/v1/media/packages/{operation['operation_id']}/cancel",
                body={"expected_version": operation["version"]},
            )
        )
