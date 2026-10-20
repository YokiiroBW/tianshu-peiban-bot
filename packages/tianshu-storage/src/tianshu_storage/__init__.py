"""Tianshu's optional persistence infrastructure; no network at import time."""

from .cache import RedisCache
from .errors import IntegrityError, StorageError
from .files import FileRef, FileStore
from .options import StorageOptions
from .postgres import MemoryCursor, PostgresConnection, Row
from .runtime import Database, StorageRuntime, schema_name

__version__ = "0.1.2"
__all__ = [
    "Database",
    "FileRef",
    "FileStore",
    "IntegrityError",
    "MemoryCursor",
    "PostgresConnection",
    "RedisCache",
    "Row",
    "StorageError",
    "StorageOptions",
    "StorageRuntime",
    "schema_name",
]
