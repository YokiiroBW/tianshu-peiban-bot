"""Resume local NAS transfers over a private Unix socket with kernel peer identity."""

import hashlib
import io
import json

import aiohttp

from ...contracts import Fault, require
from .publisher import CHUNK_BYTES, PREFIX, PROTOCOL, checked_intent, checked_key


class Publisher:
    def __init__(self):
        self.session, self.socket = None, None

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def request(self, socket_path, method, suffix, *, body=None, data=None):
        if self.session is None or self.socket != socket_path:
            await self.close()
            self.socket = socket_path
            self.session = aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=socket_path),
                timeout=aiohttp.ClientTimeout(total=1800, sock_connect=10, sock_read=1800),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
        try:
            async with self.session.request(
                method,
                "http://comic-publisher" + PREFIX + suffix,
                json=body,
                data=io.BytesIO(data) if data is not None else None,
                allow_redirects=False,
                headers={"X-Chunk-Sha256": hashlib.sha256(data).hexdigest()} if data else {},
            ) as response:
                require(response.status != 403, "comic_publisher_forbidden", 409)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    raw.extend(chunk)
                    require(len(raw) <= 16384, "comic_publisher_invalid_response", 503)
                value = json.loads(raw)
                require(isinstance(value, dict), "comic_publisher_invalid_response", 503)
                require(response.status != 409, value.get("code", "comic_publisher_conflict"), 409)
                require(200 <= response.status < 300, "comic_publisher_unavailable", 503)
                return value
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise Fault("comic_publisher_unavailable", 503) from None
        except (ValueError, UnicodeError):
            raise Fault("comic_publisher_invalid_response", 503) from None

    async def probe(self, socket_path):
        value = await self.request(socket_path, "GET", "/health")
        require(
            value.get("protocol") == PROTOCOL
            and value.get("status") == "alive"
            and isinstance(value.get("targets"), list),
            "comic_publisher_invalid_response",
            503,
        )
        return value

    async def publish(self, job, path, checkpoint):
        target, artifact = job["comic_target"], job["comic_artifact"]
        intent = checked_intent(
            {
                "target_id": target["asset_target_id"],
                "relative_path": job["comic_relative_path"],
                "length": str(artifact["size_bytes"]),
                "sha256": artifact["sha256"],
            }
        )
        key = checked_key(artifact["archive_key"])
        suffix = "/" + target["asset_target_id"] + "/" + key

        async def request(method, tail="", **kwargs):
            await checkpoint()
            value = await self.request(target["publisher_socket"], method, suffix + tail, **kwargs)
            require(
                value.get("protocol") == PROTOCOL
                and value.get("key") == key
                and value.get("intent") == intent
                and value.get("state") in {"uploading", "published"}
                and type(value.get("offset")) is int
                and 0 <= value["offset"] <= artifact["size_bytes"],
                "comic_publisher_invalid_response",
                503,
            )
            await checkpoint()
            return value

        value = await request("POST", body=intent)
        with path.open("rb") as stream:
            while value["state"] != "published" and value["offset"] < artifact["size_bytes"]:
                offset = value["offset"]
                stream.seek(offset)
                data = stream.read(CHUNK_BYTES)
                require(data, "comic_package_changed", 409)
                value = await request("PUT", "/chunks/" + str(offset), data=data)
                require(
                    value["offset"] >= offset + len(data), "comic_publisher_invalid_response", 503
                )
        # Always recheck the final file, including after a lost completion reply.
        value = await request("POST", "/complete")
        require(
            value["state"] == "published"
            and value["offset"] == artifact["size_bytes"]
            and type(value.get("published_at")) in {float, int}
            and type(value.get("reused")) is bool,
            "comic_publisher_invalid_response",
            503,
        )
        return {"published_at": value["published_at"], "reused": value["reused"]}
