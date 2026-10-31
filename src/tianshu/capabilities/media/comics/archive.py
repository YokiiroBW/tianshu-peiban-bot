"""Ask AssetLibrary to verify and formally index an already published NAS file."""

import json
import ssl
from urllib.parse import quote

import aiohttp

from ...contracts import Fault, require
from .suwayomi import service_url

PREFIX = "/assetlink/v1/service-imports"


class Archive:
    def __init__(self, comics):
        self.comics, self.session = comics, None

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def request(self, method, suffix, *, body=None, expected_url=None, missing=False):
        config = await self.comics.settings.remote_connection("assetlibrary")
        require(config.get("url") and config.get("token"), "comic_archive_not_configured", 409)
        base = service_url(config["url"])
        require(expected_url is None or base == expected_url, "comic_target_changed", 409)
        headers = {"Authorization": "Bearer " + config["token"]}
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=90, sock_connect=10, sock_read=60),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
        try:
            timeout = (
                aiohttp.ClientTimeout(total=1810, sock_connect=10, sock_read=1810)
                if method == "POST" and suffix.endswith("/observe")
                else aiohttp.ClientTimeout(total=90, sock_connect=10, sock_read=60)
            )
            async with self.session.request(
                method,
                base + PREFIX + suffix,
                json=body,
                headers=headers,
                allow_redirects=False,
                timeout=timeout,
                ssl=ssl.create_default_context(cadata=config["ca_pem"])
                if config.get("ca_pem")
                else None,
            ) as response:
                if response.status == 404 and missing:
                    return None
                require(response.status not in {401, 403}, "comic_archive_auth_required", 409)
                require(response.status != 409, "comic_archive_conflict", 409)
                require(response.status != 413, "comic_archive_file_limit", 409)
                require(200 <= response.status < 300, "comic_archive_unavailable", 503)
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    require(len(data) <= 2 * 1024**2, "comic_response_too_large", 503)
                try:
                    value = json.loads(data) if data else {}
                except (ValueError, UnicodeDecodeError):
                    raise Fault("comic_archive_invalid_response", 503) from None
                require(isinstance(value, dict), "comic_archive_invalid_response", 503)
                return value
        except (aiohttp.ClientError, TimeoutError):
            raise Fault("comic_archive_unavailable", 503) from None

    async def probe(self):
        value = await self.request("GET", "/targets")
        require(
            value.get("protocol") == "service-imports-v1"
            and isinstance(value.get("targets"), list),
            "comic_archive_invalid_response",
            503,
        )
        require(
            any(
                t.get("observation_ready") and t.get("operation") == "observe"
                for t in value["targets"]
            ),
            "comic_archive_targets_unavailable",
            409,
        )
        return value

    @staticmethod
    def receipt(value, job):
        require(value.get("state") == "committed", "comic_archive_pending", 503)
        # An upload receipt, a generic 200, or a mismatched index entry is insufficient.
        receipt = value.get("receipt", value)
        require(isinstance(receipt, dict), "comic_archive_invalid_response", 503)
        expected = job["comic_artifact"]
        target = job["comic_target"]
        require(
            all(
                isinstance(receipt.get(k), str) and receipt[k]
                for k in (
                    "receipt_id",
                    "library_id",
                    "entry_id",
                    "observation_version",
                    "content_version",
                    "observed_at",
                )
            ),
            "comic_archive_invalid_response",
            503,
        )
        require(
            receipt.get("sha256") == expected["sha256"]
            and receipt.get("length") == str(expected["size_bytes"])
            and receipt["library_id"] == target["asset_library_id"],
            "comic_archive_receipt_mismatch",
            409,
        )
        return {
            "relative_path": job["comic_relative_path"],
            **{
                k: receipt[k]
                for k in (
                    "receipt_id",
                    "library_id",
                    "entry_id",
                    "content_version",
                    "observation_version",
                    "observed_at",
                    "length",
                    "sha256",
                )
            },
        }

    async def observe(self, job, checkpoint):
        artifact, target = job["comic_artifact"], job["comic_target"]
        suffix = "/" + quote(artifact["archive_key"], safe="")
        params = {"expected_url": target["asset_url"]}
        await checkpoint()
        value = await self.request("GET", suffix, missing=True, **params)
        if value is None or value.get("state") in {"observing", "unknown"}:
            value = await self.request(
                "POST",
                suffix + "/observe",
                body={
                    "target_id": target["asset_target_id"],
                    "relative_path": job["comic_relative_path"],
                    "length": str(artifact["size_bytes"]),
                    "sha256": artifact["sha256"],
                },
                **params,
            )
        state = value.get("state")
        require(
            value.get("protocol") == "service-imports-v1"
            and value.get("key") == artifact["archive_key"],
            "comic_archive_invalid_response",
            503,
        )
        require(
            state in {"observing", "committed", "unknown", "rejected"},
            "comic_archive_invalid_response",
            503,
        )
        require(state != "rejected", "comic_archive_rejected", 409)
        intent = value.get("intent")
        require(
            isinstance(intent, dict)
            and all(
                intent.get(k) == v
                for k, v in {
                    "target_id": target["asset_target_id"],
                    "relative_path": job["comic_relative_path"],
                    "length": str(artifact["size_bytes"]),
                    "sha256": artifact["sha256"],
                }.items()
            ),
            "comic_archive_receipt_mismatch",
            409,
        )
        # A lost reply is queried first; an unfinished observation resumes the same intent.
        return self.receipt(value, job)
