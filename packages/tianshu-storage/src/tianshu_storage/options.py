"""One deployment configuration convention, independent of business configuration."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .errors import StorageError


@dataclass(frozen=True)
class StorageOptions:
    postgresql_url: str = field(default="", repr=False)
    redis_url: str = field(default="", repr=False)
    namespace: str = "tianshu"
    connect_timeout: float = 3.0
    pool_max_size: int = 8

    @classmethod
    def from_settings(cls, settings=None, environ=None):
        environ = os.environ if environ is None else environ
        settings = settings or {}
        values = settings.get("storage_backends", {})
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise StorageError("storage_configuration_invalid")

        def value(key, env, default=""):
            override = environ.get(env)
            if isinstance(override, str) and override.strip():
                return override.strip()
            result = values.get(key, default)
            if not isinstance(result, str):
                raise StorageError("storage_configuration_invalid")
            return result.strip()

        namespace = value("namespace", "TIANSHU_STORAGE_NAMESPACE", "tianshu")
        if not namespace or len(namespace) > 96 or any(ord(c) < 32 for c in namespace):
            raise StorageError("storage_namespace_invalid")
        timeout = values.get("connect_timeout", 3.0)
        size = values.get("pool_max_size", 8)
        if type(timeout) not in (int, float) or not 0.1 <= timeout <= 60:
            raise StorageError("storage_configuration_invalid")
        if type(size) is not int or not 1 <= size <= 64:
            raise StorageError("storage_configuration_invalid")
        return cls(
            value("postgresql_url", "TIANSHU_POSTGRES_URL"),
            value("redis_url", "TIANSHU_REDIS_URL"),
            namespace,
            float(timeout),
            size,
        )


def valid_url(value, schemes):
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in schemes
            and parsed.hostname
            and (parsed.port is None or 0 < parsed.port <= 65535)
            and not parsed.fragment
        )
    except (ValueError, TypeError):
        return False
