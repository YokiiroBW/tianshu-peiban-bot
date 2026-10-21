"""Explicit per-service ownership and durable selection of one SQL backend."""

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from .cache import RedisCache
from .errors import StorageError, postgres_error
from .files import sync_directory
from .options import StorageOptions, valid_url
from .postgres import PostgresConnection


def schema_name(namespace, service, logical):
    value = f"{namespace}:{service}:{logical}"
    slug = re.sub("[^a-z0-9_]", "_", f"ts_{namespace}_{service}_{logical}".lower())
    return slug[:49] + "_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def state_filename(service):
    if not re.fullmatch("[a-z][a-z0-9_-]{0,63}", service):
        raise StorageError("storage_service_invalid")
    return ".tianshu-storage-" + service + ".json"


def read_state(path, *, namespace, service):
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise StorageError("storage_binding_unreadable") from None
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("namespace") != namespace
        or value.get("service") != service
        or value.get("backend") not in {"sqlite", "postgresql"}
        or value.get("state") not in {"pending", "active"}
        or not re.fullmatch("[0-9a-f]{32}", str(value.get("installation_id", "")))
        or not re.fullmatch("[0-9a-f]{32}", str(value.get("epoch", "")))
        or not isinstance(value.get("databases"), dict)
    ):
        raise StorageError("storage_binding_invalid")
    for logical, schema in value["databases"].items():
        if schema != schema_name(namespace, service, logical):
            raise StorageError("storage_binding_invalid")
    return value


def source_fingerprint(connection):
    """A restart-stable digest of a SQLite snapshot, including WAL-visible facts."""
    digest = hashlib.sha256()
    for statement in connection.iterdump():
        value = statement.encode("utf-8", errors="surrogatepass")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


class Database:
    def __init__(self, runtime, name, path, dialect=None):
        self.runtime = runtime
        self.name = name
        self.path = str(path)
        self.backend = runtime.backend
        self.schema = schema_name(runtime.options.namespace, runtime.service, name)
        self.dialect = dialect

    def connect(self, *, timeout=5, isolation_level="", read_only=False, **kwargs):
        if self.runtime._closed:
            raise StorageError("storage_runtime_closed")
        if self.backend == "sqlite":
            path = self.path
            if read_only:
                path = Path(path).resolve().as_uri() + "?mode=ro"
                kwargs["uri"] = True
            result = sqlite3.connect(
                path, timeout=timeout, isolation_level=isolation_level, **kwargs
            )
            if read_only:
                result.execute("PRAGMA query_only=ON")
            return result
        raw = self.runtime._checkout(self.schema, timeout=timeout, read_only=read_only)
        try:
            row = raw.execute(
                "SELECT installation_id,logical_name,state FROM __tianshu_storage_binding "
                "WHERE singleton=1"
            ).fetchone()
            if row != (self.runtime._state["installation_id"], self.name, "ready"):
                raise StorageError("postgresql_binding_mismatch")
        except (psycopg.Error, StorageError) as exc:
            self.runtime._release(raw)
            if isinstance(exc, StorageError):
                raise
            raise postgres_error(exc) from None
        return PostgresConnection(
            raw,
            release=self.runtime._release,
            dialect=self.dialect,
            isolation_level=isolation_level,
            path=self.path,
        )


class StorageRuntime:
    def __init__(self, options=None, *, service, state_directory):
        self.options = options or StorageOptions()
        self.service = service
        self.state_directory = Path(state_directory).resolve()
        self.state_path = self.state_directory / state_filename(service)
        self.backend = "sqlite"
        self._state = None
        self._pool = None
        self._started = False
        self._closed = False
        self._databases = {}
        self._expected = set()
        self._sources = []
        self._lock = threading.RLock()
        self._pg_status = "not_configured"
        self.epoch = uuid4().hex
        self.cache = RedisCache()

    def __repr__(self):
        return f"StorageRuntime(service={self.service!r}, backend={self.backend!r})"

    def _save(self):
        self.state_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(self.state_path.name + "." + uuid4().hex + ".pending")
        try:
            with temporary.open("xb") as stream:
                stream.write((json.dumps(self._state, sort_keys=True) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, self.state_path)
            sync_directory(self.state_directory)
        except OSError:
            raise StorageError("storage_binding_write_failed") from None
        finally:
            temporary.unlink(missing_ok=True)

    def _new_state(self):
        return {
            "version": 1,
            "namespace": self.options.namespace,
            "service": self.service,
            "installation_id": uuid4().hex,
            "epoch": self.epoch,
            "backend": "sqlite",
            "state": "active",
            "databases": {},
        }

    def _postgres_start(self):
        if not valid_url(self.options.postgresql_url, {"postgres", "postgresql"}):
            raise StorageError("postgresql_configuration_invalid")
        try:

            class SafeConnection(psycopg.Connection):
                @classmethod
                def connect(cls, *args, **kwargs):
                    try:
                        return super().connect(*args, **kwargs)
                    except psycopg.Error:
                        # The pool logs connection errors internally. Redact at the
                        # connection boundary, before its logger can see libpq text.
                        raise psycopg.OperationalError("postgresql_connection_failed") from None

                def __repr__(self):
                    return "<TianshuPostgresConnection>"

            self._pool = ConnectionPool(
                self.options.postgresql_url,
                connection_class=SafeConnection,
                kwargs={
                    "autocommit": True,
                    "connect_timeout": math.ceil(self.options.connect_timeout),
                    "application_name": "tianshu-" + self.service,
                },
                min_size=0,
                max_size=self.options.pool_max_size,
                timeout=self.options.connect_timeout,
                num_workers=2,
                open=False,
            )
            self._pool.open(wait=False)
            raw = self._pool.getconn(timeout=self.options.connect_timeout)
            try:
                # Match the rights needed by first-time schema initialization, while
                # rolling back every probe object. Never read someone else's tables.
                name = "ts_probe_" + uuid4().hex
                raw.execute("BEGIN")
                raw.execute("CREATE SCHEMA " + name)
                raw.execute(f"CREATE TABLE {name}.roundtrip(value TEXT NOT NULL UNIQUE)")
                value = "天枢 storage " + uuid4().hex
                raw.execute(f"INSERT INTO {name}.roundtrip VALUES (%s)", (value,))
                if raw.execute(f"SELECT value FROM {name}.roundtrip").fetchone() != (value,):
                    raise StorageError("postgresql_probe_failed")
            finally:
                raw.rollback()
                self._pool.putconn(raw)
        except (psycopg.Error, StorageError, OSError):
            if self._pool is not None:
                self._pool.close(timeout=1)
                self._pool = None
            raise StorageError("postgresql_unavailable") from None

    def start(self):
        with self._lock:
            if self._closed:
                raise StorageError("storage_runtime_closed")
            if self._started:
                return self
            existing = read_state(
                self.state_path, namespace=self.options.namespace, service=self.service
            )
            self._state = existing or self._new_state()
            self.epoch = self._state["epoch"]
            self._expected = set(self._state["databases"])
            already_postgres = self._state["backend"] == "postgresql"
            if self.options.postgresql_url:
                try:
                    self._postgres_start()
                except StorageError:
                    self._pg_status = "unavailable"
                    if already_postgres:
                        raise StorageError("postgresql_bound_backend_unavailable") from None
                else:
                    self.backend = "postgresql"
                    self._pg_status = "ok"
                    if not already_postgres:
                        self._state.update(backend="postgresql", state="pending", epoch=uuid4().hex)
                        self.epoch = self._state["epoch"]
                        self._save()
            elif already_postgres:
                raise StorageError("postgresql_bound_configuration_missing")
            if self.options.redis_url and existing is None and self.backend == "sqlite":
                self._save()
            self.cache.close()
            self.cache = RedisCache(
                self.options.redis_url,
                prefix=f"tianshu:{self.options.namespace}:{self.epoch}:{self.service}",
                max_connections=self.options.pool_max_size,
            )
            self._started = True
            return self

    def _checkout(self, schema, *, timeout=5, read_only=False):
        if self._pool is None or self._closed:
            raise StorageError("postgresql_unavailable")
        raw = None
        try:
            raw = self._pool.getconn(timeout=self.options.connect_timeout)
            raw.execute(
                "SELECT pg_catalog.set_config('search_path', %s, false),"
                "pg_catalog.set_config('lock_timeout', %s, false),"
                "pg_catalog.set_config('default_transaction_read_only', %s, false)",
                (
                    sql.Identifier(schema).as_string() + ", pg_catalog",
                    f"{max(1, math.ceil(timeout * 1000))}ms",
                    "on" if read_only else "off",
                ),
            )
            return raw
        except (psycopg.Error, OSError):
            if raw is not None:
                self._release(raw)
            raise StorageError("postgresql_unavailable") from None

    def _release(self, raw):
        try:
            raw.rollback()
        except psycopg.Error:
            raw.close()
        finally:
            if self._pool is not None:
                self._pool.putconn(raw)
            else:
                raw.close()

    def database(self, name, path, *, prepare=None, dialect=None):
        with self._lock:
            self.start()
            if not isinstance(name, str) or not name or len(name) > 256:
                raise StorageError("storage_database_name_invalid")
            if name in self._databases:
                database = self._databases[name]
                if database.path != str(path):
                    raise StorageError("storage_database_path_changed")
                return database
            database = Database(self, name, path, dialect)
            if self.backend == "sqlite":
                self._databases[name] = database
                return database
            if prepare is None:
                raise StorageError("postgresql_schema_adapter_required")
            source = None
            source_version = None
            raw = self._checkout("pg_catalog")
            target = PostgresConnection(raw, dialect=dialect, isolation_level=None, path=str(path))
            try:
                raw.execute("BEGIN")
                # Serialize the one-time creation/import across startup processes. Ongoing
                # business locking remains in the product's transactions.
                lock_id = int.from_bytes(
                    hashlib.sha256(database.schema.encode()).digest()[:8], "big", signed=True
                )
                raw.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
                exists = raw.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname=%s", (database.schema,)
                ).fetchone()
                if not exists and name in self._expected:
                    raise StorageError("postgresql_bound_schema_missing")
                if not exists:
                    raw.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(database.schema)))
                raw.execute(
                    sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                        sql.Identifier(database.schema)
                    )
                )
                owned = raw.execute(
                    "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND c.relname='__tianshu_storage_binding'",
                    (database.schema,),
                ).fetchone()
                if not owned and name in self._expected:
                    raise StorageError("postgresql_bound_schema_missing")
                if not owned:
                    populated = raw.execute(
                        "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=%s LIMIT 1",
                        (database.schema,),
                    ).fetchone()
                    if populated:
                        raise StorageError("postgresql_target_not_owned")
                    raw.execute(
                        "CREATE TABLE __tianshu_storage_binding (singleton INTEGER PRIMARY KEY "
                        "CHECK(singleton=1),installation_id TEXT NOT NULL,logical_name TEXT NOT NULL,"
                        "state TEXT NOT NULL,source_sha256 TEXT)"
                    )
                    raw.execute(
                        "INSERT INTO __tianshu_storage_binding(singleton,installation_id,logical_name,state) "
                        "VALUES(1,%s,%s,'initializing')",
                        (self._state["installation_id"], name),
                    )
                    if str(path) != ":memory:" and Path(path).is_file():
                        source = sqlite3.connect(
                            Path(path).resolve().as_uri() + "?mode=ro", uri=True
                        )
                        source.row_factory = sqlite3.Row
                        source.execute("PRAGMA query_only=ON")
                        source_version = source.execute("PRAGMA data_version").fetchone()[0]
                        source.execute("BEGIN")
                        raw.execute(
                            "UPDATE __tianshu_storage_binding SET source_sha256=%s WHERE singleton=1",
                            (source_fingerprint(source),),
                        )
                else:
                    raw.execute(
                        "ALTER TABLE __tianshu_storage_binding ADD COLUMN IF NOT EXISTS source_sha256 TEXT"
                    )
                    row = raw.execute(
                        "SELECT installation_id,logical_name,state FROM __tianshu_storage_binding "
                        "WHERE singleton=1"
                    ).fetchone()
                    if row != (self._state["installation_id"], name, "ready"):
                        raise StorageError("postgresql_target_not_owned")
                    fingerprint = raw.execute(
                        "SELECT source_sha256 FROM __tianshu_storage_binding WHERE singleton=1"
                    ).fetchone()[0]
                    if self._state["state"] == "pending" and fingerprint:
                        if not Path(path).is_file():
                            raise StorageError("sqlite_source_changed_during_migration")
                        source = sqlite3.connect(
                            Path(path).resolve().as_uri() + "?mode=ro", uri=True
                        )
                        source.execute("PRAGMA query_only=ON")
                        source_version = source.execute("PRAGMA data_version").fetchone()[0]
                        source.execute("BEGIN")
                        if source_fingerprint(source) != fingerprint:
                            raise StorageError("sqlite_source_changed_during_migration")
                        # The product sees None: this logical database has already
                        # imported successfully and must not duplicate source rows.
                        self._sources.append((source, source_version))
                        source = None
                # Product callback creates native tables/indexes, imports and verifies. The
                # enclosing transaction is owned here; it must not commit inside callback.
                prepare(target, source)
                if not target.in_transaction:
                    raise StorageError("postgresql_migration_transaction_lost")
                if source is not None:
                    source.rollback()
                    if source.execute("PRAGMA data_version").fetchone()[0] != source_version:
                        raise StorageError("sqlite_source_changed_during_migration")
                raw.execute("UPDATE __tianshu_storage_binding SET state='ready' WHERE singleton=1")
                raw.commit()
                self._databases[name] = database
                self._state["databases"][name] = database.schema
                self._save()
                if source is not None:
                    self._sources.append((source, source_version))
                    source = None
                return database
            except (StorageError, sqlite3.Error):
                raw.rollback()
                raise
            except psycopg.Error as exc:
                raw.rollback()
                raise postgres_error(exc) from None
            except BaseException:
                raw.rollback()
                raise
            finally:
                if source is not None:
                    source.close()
                self._release(raw)

    def finalize(self):
        with self._lock:
            self.start()
            if self.backend != "postgresql":
                return
            if not self._expected <= self._databases.keys():
                raise StorageError("postgresql_activation_incomplete")
            try:
                for source, version in self._sources:
                    if source.execute("PRAGMA data_version").fetchone()[0] != version:
                        raise StorageError("sqlite_source_changed_during_migration")
                self._state["state"] = "active"
                self._save()
            finally:
                for source, _ in self._sources:
                    source.close()
                self._sources.clear()

    def status(self):
        return {
            "backend": self.backend,
            "postgresql": self._pg_status,
            "redis": self.cache.status(),
            "state": self._state["state"] if self._state is not None else "not_started",
            "databases": sorted(self._databases),
        }

    @classmethod
    def test_connections(cls, options=None, *, service="connection-test"):
        """Connection tests only: rollback PG probe and bounded short-lived Redis key."""
        options = options or StorageOptions()
        manager = cls(options, service=service, state_directory=Path.cwd())
        result = {"postgresql": "not_configured", "redis": "not_configured"}
        try:
            if options.postgresql_url:
                try:
                    manager._postgres_start()
                    result["postgresql"] = "ok"
                except StorageError:
                    result["postgresql"] = "unavailable"
            manager.cache.close()
            manager.cache = RedisCache(
                options.redis_url, prefix=f"tianshu:{options.namespace}:{service}:connection-test"
            )
            result["redis"] = manager.cache.status()
            return result
        finally:
            manager.close()

    @classmethod
    def open_existing(
        cls,
        options=None,
        *,
        service,
        state_directory,
        name,
        path,
        dialect=None,
        timeout=5,
    ):
        """Open the current database read-only, without startup/migration/probe writes."""
        options = options or StorageOptions()
        state = read_state(
            Path(state_directory).resolve() / state_filename(service),
            namespace=options.namespace,
            service=service,
        )
        if state is None or state["backend"] == "sqlite":
            result = sqlite3.connect(
                Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout
            )
            result.execute("PRAGMA query_only=ON")
            return result
        if not options.postgresql_url:
            raise StorageError("postgresql_bound_configuration_missing")
        if state["state"] != "active" or name not in state["databases"]:
            raise StorageError("postgresql_activation_incomplete")
        raw = None
        try:
            raw = psycopg.connect(
                options.postgresql_url,
                autocommit=True,
                connect_timeout=math.ceil(options.connect_timeout),
                options="-c default_transaction_read_only=on",
            )
            raw.execute(
                sql.SQL("SET search_path TO {}, pg_catalog").format(
                    sql.Identifier(state["databases"][name])
                )
            )
            row = raw.execute(
                "SELECT installation_id,logical_name,state FROM __tianshu_storage_binding "
                "WHERE singleton=1"
            ).fetchone()
            if row != (state["installation_id"], name, "ready"):
                raise StorageError("postgresql_binding_mismatch")
            return PostgresConnection(raw, dialect=dialect, isolation_level=None, path=str(path))
        except (psycopg.Error, StorageError) as exc:
            if raw is not None:
                raw.close()
            if isinstance(exc, StorageError):
                raise
            raise postgres_error(exc) from None

    @classmethod
    def inspect(cls, options=None, *, service, state_directory):
        """Read existing binding and PG metadata; create no pool/schema/file or cache key."""
        options = options or StorageOptions()
        state = read_state(
            Path(state_directory).resolve() / state_filename(service),
            namespace=options.namespace,
            service=service,
        )
        backend = state["backend"] if state is not None else "sqlite"
        result = {
            "backend": backend,
            "state": state["state"] if state else "unbound",
            "postgresql": "not_configured",
            "databases": sorted(state["databases"]) if state else [],
        }
        if backend != "postgresql":
            if options.postgresql_url:
                result["postgresql"] = "requires_initialization"
            return result
        if not options.postgresql_url:
            result["postgresql"] = "configuration_missing"
            return result
        try:
            with psycopg.connect(
                options.postgresql_url,
                autocommit=True,
                connect_timeout=math.ceil(options.connect_timeout),
                options="-c default_transaction_read_only=on",
            ) as connection:
                for logical, schema in state["databases"].items():
                    row = connection.execute(
                        sql.SQL(
                            "SELECT installation_id,logical_name,state FROM {}.__tianshu_storage_binding WHERE singleton=1"
                        ).format(sql.Identifier(schema))
                    ).fetchone()
                    if row != (state["installation_id"], logical, "ready"):
                        raise StorageError("postgresql_binding_mismatch")
                if not state["databases"]:
                    result["postgresql"] = "requires_initialization"
                else:
                    result["postgresql"] = (
                        "ok" if state["state"] == "active" else "requires_initialization"
                    )
        except (psycopg.Error, StorageError):
            result["postgresql"] = "unavailable"
        return result

    def close(self):
        if self._closed:
            return
        self._closed = True
        for source, _ in self._sources:
            source.close()
        self._sources.clear()
        self.cache.close()
        if self._pool is not None:
            self._pool.close(timeout=1)
            self._pool = None

    def __enter__(self):
        return self.start()

    def __exit__(self, kind, exc, traceback):
        self.close()
        return False
