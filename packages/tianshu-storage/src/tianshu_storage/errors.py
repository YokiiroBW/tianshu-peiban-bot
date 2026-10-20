"""Stable, value-free errors also caught by existing SQLite error boundaries."""

import sqlite3


class StorageError(sqlite3.OperationalError):
    def __init__(self, code="storage_unavailable"):
        self.code = code
        super().__init__(code)


class IntegrityError(sqlite3.IntegrityError):
    def __init__(self, code="storage_integrity_error"):
        self.code = code
        super().__init__(code)


def postgres_error(exc):
    import psycopg

    if isinstance(exc, psycopg.IntegrityError):
        return IntegrityError()
    return StorageError("postgresql_operation_failed")
