"""Validated per-BV maintenance sources with sequential request pacing."""

import asyncio

from ..contracts import Fault, digest, require
from .metadata import normalize_bilibili


class SourceBatch:
    def __init__(self, media, plan_id, local):
        self.media, self.plan_id, self.local = media, plan_id, local
        self.last_request = None

    async def obtain(self, bvid, records, supplied, errors, interval):
        identity = digest({"plan_id": self.plan_id, "bvid": bvid})
        previous = await self.local(self.media.repo.get, "import_metadata_source", identity)
        value = supplied.get(bvid) or (previous or {}).get("resolved")
        error = errors.get(bvid)
        try:
            if value is None:
                if error:
                    raise Fault(error.get("code", "source_unavailable"), 503)
                if self.last_request is not None:
                    delay = interval - (asyncio.get_running_loop().time() - self.last_request)
                    if delay > 0:
                        await asyncio.sleep(delay)
                self.last_request = asyncio.get_running_loop().time()
                value = await self.media.connector.resolve(bvid, with_formats=False)
            normalize_bilibili(value["snapshot"])
            require(value["snapshot"]["bvid"] == bvid, "import_metadata_identity_conflict", 409)
            require(
                {record["cid"] for record in records}
                <= {part["cid"] for part in value["snapshot"]["parts"]},
                "import_metadata_identity_conflict",
                409,
            )
            result = {
                "plan_id": self.plan_id,
                "bvid": bvid,
                "resolved": value,
                "state": "ready",
                "code": None,
            }
        except (Fault, ValueError, KeyError, TypeError) as failure:
            result = {
                "plan_id": self.plan_id,
                "bvid": bvid,
                "resolved": None,
                "state": "unavailable",
                "code": failure.code if isinstance(failure, Fault) else "source_invalid_response",
            }
        if previous and all(previous.get(key) == value for key, value in result.items()):
            return previous
        return await self.local(self.media.repo.put, "import_metadata_source", identity, result)
