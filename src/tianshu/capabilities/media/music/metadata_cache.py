"""Short-lived metadata reuse only; credentials and download URLs are never cached."""

import asyncio
import copy
import time
import math
from collections import OrderedDict
from contextlib import asynccontextmanager

from ...contracts import digest


class MetadataCache:
    def __init__(self, *, clock=time.monotonic, shared=None, scope=""):
        self.clock = clock
        self.values = OrderedDict()
        self.locks = {}
        self.shared, self.scope = shared, scope

    @asynccontextmanager
    async def _lock(self, key):
        # Only duplicate metadata reads wait together. A slow playlist or provider search must
        # not block unrelated keys/account revisions, including already-cached answers.
        entry = self.locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del self.locks[key]

    async def read(self, name, cookie, parameters, fetch, *, fresh=False, ttl=3600):
        key = digest(
            {
                "operation": name,
                "provider": self.scope,
                "account": (cookie or {}).get("_music_account_id"),
                "revision": (cookie or {}).get("_music_account_revision"),
                "credentials": digest(cookie or {}),
                "parameters": parameters,
            }
        )
        async with self._lock(key):
            cached = self.values.get(key)
            if not fresh and cached and cached[0] > self.clock():
                self.values.move_to_end(key)
                return copy.deepcopy(cached[1])
            if not fresh and self.shared is not None:
                shared = await asyncio.to_thread(self.shared.get, "music_metadata", key)
                if (
                    isinstance(shared, dict)
                    and "value" in shared
                    and type(shared.get("expires_at")) in (int, float)
                    and math.isfinite(shared["expires_at"])
                    and shared["expires_at"] > time.time()
                ):
                    remaining = shared["expires_at"] - time.time()
                    self.values[key] = (self.clock() + remaining, copy.deepcopy(shared["value"]))
                    while len(self.values) > 256:
                        self.values.popitem(last=False)
                    return copy.deepcopy(shared["value"])
            result = await fetch()
            self.values[key] = (self.clock() + ttl, copy.deepcopy(result))
            self.values.move_to_end(key)
            while len(self.values) > 256:
                self.values.popitem(last=False)
            if self.shared is not None:
                fetched = time.time()
                await asyncio.to_thread(
                    self.shared.set,
                    "music_metadata",
                    key,
                    {"value": result, "fetched_at": fetched, "expires_at": fetched + ttl},
                    ttl=math.ceil(ttl),
                )
            return result
