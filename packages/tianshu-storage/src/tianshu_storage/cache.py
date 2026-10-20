"""Optional, bounded Redis operations; cache errors never become business failures."""

import hashlib
import json
import math
import threading
import time
from uuid import uuid4

import redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from .options import valid_url


class RedisCache:
    MAX_ENTRY_BYTES = 1024 * 1024

    def __init__(self, url="", *, prefix="tianshu", timeout=0.25, max_connections=8):
        self.prefix = prefix
        self._client = None
        self._state = "not_configured"
        self._retry_at = 0.0
        self._lock = threading.Lock()
        self._closed = False
        if not url:
            return
        if not valid_url(url, {"redis", "rediss"}):
            self._state = "configuration_invalid"
            return
        try:
            self._client = redis.Redis.from_url(
                url,
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
                max_connections=max_connections,
                retry=Retry(NoBackoff(), 0),
                retry_on_error=[],
                decode_responses=False,
            )
        except (RedisError, TypeError, ValueError, OSError):
            self._state = "configuration_invalid"
            return
        self.probe()

    def __repr__(self):
        return f"RedisCache(state={self._state!r})"

    @property
    def enabled(self):
        if self._client is None or self._closed:
            return False
        if self._state != "ok" and time.monotonic() >= self._retry_at:
            self.probe()
        return self._state == "ok"

    def status(self):
        return self._state

    def _failed(self):
        self._state = "unavailable"
        self._retry_at = time.monotonic() + 1

    def probe(self):
        if self._client is None or self._closed:
            return False
        if not self._lock.acquire(blocking=False):
            return self._state == "ok"
        key = self.prefix + ":probe:" + uuid4().hex
        try:
            if not self._client.ping():
                raise RuntimeError()
            value = uuid4().hex.encode("ascii")
            if not self._client.set(key, value, px=2000):
                raise RuntimeError()
            if self._client.get(key) != value or self._client.pttl(key) <= 0:
                raise RuntimeError()
            if self._client.delete(key) != 1:
                raise RuntimeError()
            self._state = "ok"
            self._retry_at = 0
            return True
        except (RedisError, RuntimeError, OSError):
            self._failed()
            return False
        finally:
            self._lock.release()

    def _key(self, kind, identity):
        if not isinstance(kind, str) or not kind or len(kind) > 96:
            raise ValueError("cache kind invalid")
        canonical = json.dumps(
            identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self.prefix + ":" + kind + ":v1:" + digest

    def get(self, kind, identity):
        if not self.enabled:
            return None
        try:
            raw = self._client.get(self._key(kind, identity))
            if raw is None or len(raw) > self.MAX_ENTRY_BYTES:
                return None
            envelope = json.loads(raw)
            if (
                envelope.get("version") != 1
                or type(envelope.get("expires_at")) not in (int, float)
                or not math.isfinite(envelope["expires_at"])
                or envelope["expires_at"] <= time.time()
            ):
                return None
            return envelope["value"]
        except (ValueError, TypeError, KeyError, AttributeError):
            return None
        except (RedisError, OSError):
            self._failed()
            return None

    def set(self, kind, identity, value, *, ttl):
        if not self.enabled:
            return False
        try:
            if type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl <= 0:
                return False
            now = time.time()
            raw = json.dumps(
                {"version": 1, "fetched_at": now, "expires_at": now + ttl, "value": value},
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if len(raw) > self.MAX_ENTRY_BYTES:
                return False
            return bool(
                self._client.set(self._key(kind, identity), raw, px=max(1, math.ceil(ttl * 1000)))
            )
        except (TypeError, ValueError, OverflowError):
            return False
        except (RedisError, OSError):
            self._failed()
            return False

    def delete(self, kind, identity):
        if not self.enabled:
            return False
        try:
            self._client.delete(self._key(kind, identity))
            return True
        except (TypeError, ValueError):
            return False
        except (RedisError, OSError):
            self._failed()
            return False

    def close(self):
        self._closed = True
        if self._client is not None:
            try:
                self._client.close()
            except (RedisError, OSError):
                self._state = "closed"
        self._state = "closed"
