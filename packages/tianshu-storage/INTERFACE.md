# tianshu-storage 0.1：本批稳定接口

共享包安装名 `tianshu-storage`，导入名 `tianshu_storage`。产品不复制包源码；本地从此路径安装，部署包由协调者生成固定版本 wheel 并供各镜像安装。

```python
from tianshu_storage import StorageOptions, StorageRuntime, FileStore

options = StorageOptions.from_settings(settings, environ=None)
runtime = StorageRuntime(options, service="platform", state_directory=existing_state_root)
runtime.start()  # PG/Redis 独立检测；首次 PG 不通回 SQLite，已有 PG 绑定不回退
database = runtime.database(
    "media",
    existing_sqlite_path,
    prepare=prepare_postgres,
    dialect=product_dialect,
)
# prepare_postgres(connection, source): product-native DDL/import/validation.
# source is a read-only sqlite3.Connection only for first migration; otherwise None.
# connection is PostgresConnection (connection.raw exposes psycopg), search_path already scoped.
# product_dialect(connection, sql, parameters) -> cursor | None
# Return None for ordinary native PostgreSQL SQL (shared code binds ? parameters safely).
# Return a MemoryCursor for handled PRAGMA/catalog commands, or connection.native(...)
# for transformed statements. Product code owns native DDL, JSON/FTS and authority rules.
runtime.finalize()  # after all primary/side databases initialized, before serving requests

with database.connect(timeout=5, isolation_level="") as connection:
    row = connection.execute("SELECT id FROM own_table WHERE id=?", (identity,)).fetchone()
runtime.close()  # drains connections/pool/cache; explicit lifecycle
```

- `StorageOptions` fields: `postgresql_url`, `redis_url`, `namespace`, `connect_timeout` (default 3s), `pool_max_size` (default 8). Input object `storage_backends` + nonempty `TIANSHU_POSTGRES_URL` / `TIANSHU_REDIS_URL` / `TIANSHU_STORAGE_NAMESPACE` overrides. URLs never appear in repr/status/exceptions.
- `StorageRuntime.backend` is `sqlite` or `postgresql`; `status()` returns only sanitized selection/check state. `.cache` is always a RedisCache object, disabled cache returns misses/no-op. `.epoch` namespaces cache state; `.state_directory` remains filesystem location independent of PG URL.
- `Database.backend`, `.path` (original SQLite path), `.schema` (PG namespace), `.connect(**sqlite_connect_kwargs)`; without PG, returns original sqlite3 connection. `prepare` is required for PG and is never called for SQLite. Register every owned logical DB; don't merge same-name tables from sidecars into one PG schema. `.connect` checks backend binding and never falls back to old SQLite.
- `PostgresConnection`: `.execute/.executemany/.executescript/.commit/.rollback/.close`, context manager, `.raw`, `.in_transaction`, `.row_factory`, `.total_changes`. Row supports integer/string keys, iteration over values and `.keys()`. Cursor supports iteration, `.fetchone/.fetchmany/.fetchall`, `.rowcount/.description/.lastrowid`. Product dialect intercepts SQLite-specific statements; ordinary native SQL uses `.native(sql, parameters=())` and shared qmark binding. `.cursor()` exposes matching wrapper. Generic adapter never invents business triggers, FTS ranks or authorization rules.
- `.native(..., savepoint=True)` restores the current transaction after an expected statement constraint conflict. Ordinary PG statements keep native aborted-transaction behavior. Connections must be explicitly closed or used with `contextlib.closing`; their transaction context commits/rolls back without closing, matching SQLite.
- Since 0.1.2, `.pipeline()` is a thin context for current-session/transaction setup only: queue existing `SET`, `BEGIN` and advisory-lock `SELECT` statements without fetching results or business DML. Normal exit confirms every queued statement, including lock acquisition; a snapshot-sensitive metadata query must be a **separate statement after exit**. Delayed psycopg errors use the same sanitized StorageError/IntegrityError boundary. It does not add commit/rollback/retry or release the connection; callers retain their existing rollback/close path. Native SQLite connections do not expose or enter this context.
- Since 0.1.3, `.read_batch(requests)` confirms a sequence of trusted internal `(sql, parameters)` pairs for independent native SELECTs and returns a Cursor list in input order only after the entire batch succeeds. SQL must start with SELECT; this restriction is a programming contract, not a parser/authorization boundary for arbitrary SQL or side-effecting functions. Do not pass DML, setup/lock commands, multi-statements or dependent queries. Nonempty batches require a healthy explicit transaction (`INTRANS`); an empty batch returns `[]` without SQL, while closed connections always fail. The method bypasses the product dialect and retains `native`'s `prepare=False`. On failure it closes created batch cursors, returns no partial list and propagates sanitized psycopg errors; it does not commit, roll back, retry or release the connection. The caller must use its existing rollback path. Source snapshots may batch only after CAS/lock confirmation and before their first mutation; this does not move lock-sensitive metadata into the setup pipeline or share a snapshot between transactions.
- `MemoryCursor(rows=(), columns=(), rowcount=-1, lastrowid=None)` is available to product dialects. `source` is only a migration source: all new product writes use target connection. PG schema binding/migration completion is recorded by shared runtime; existing unrelated target data is not overwritten. Backend marker contains identity/state/epoch, no DSN/secret.
- Fixed 0.1 migration callbacks may read `SELECT state FROM __tianshu_storage_binding WHERE singleton=1`: `initializing` means a new logical database, `ready` means an existing one. This read-only migration contract distinguishes a new empty target from an already active target whose product tables were lost. Products must validate their existing required tables before DDL and must never write the shared binding.
- PG errors are sanitized `StorageError` / `IntegrityError`, subclassing sqlite3.OperationalError / sqlite3.IntegrityError respectively so existing sqlite exception catches remain effective. Native SQLite errors retain behavior. Unsupported PG SQL must fail, never be silently ignored.
- PostgreSQL source import and schema migrations belong in each product. Do not translate queries by sending SQLite files/BLOBs to PG. Single-owner/source-guard and CAS semantics remain product responsibility. Existing local SQLite backup calls must not be accidentally pointed at PG.

```python
cache = runtime.cache
cache.get("weather", stable_key)  # JSON-compatible value, or None
cache.set("weather", stable_key, value, ttl=300)  # bool, ttl in seconds; no sliding expiry
cache.delete("weather", stable_key)  # bool
cache.enabled  # currently usable; subsequent calls can recover after transient failure

files = FileStore(existing_module_file_root)
reference = files.put_bytes(payload, media_type="application/pdf")
reference.as_dict()  # format, sha256, size_bytes, relative_path, media_type
payload = files.read_bytes(reference)  # accepts FileRef or as_dict(), verifies bytes/hash
```

FileStore filenames are immutable content hashes; temp/fsync/atomic rename precedes SQL reference commit. No automatic deletion or retention decisions. `.put_text(text)` / `.read_text(ref)` use UTF-8. Keep each module's existing file root/mount. No changes to existing wire payloads: materialize file references at send/read boundary and preserve exact frozen request hashes and unknown recovery.

## Read-only tools and connection checks

`StorageRuntime.inspect(options, service=..., state_directory=...)` reads existing binding metadata and verifies the PG logical schemas without creating pools, schema, files or Redis keys. `StorageRuntime.open_existing(options, service=..., state_directory=..., name=..., path=..., dialect=None)` opens the selected current database read-only; a pending/missing PG binding fails rather than selecting a SQLite copy. Always close the returned connection.

`StorageRuntime.test_connections(options, service="connection-test")` tests PG with a rolled-back schema/write/read transaction and Redis with a short-lived random key; it does not activate a backend. CLI equivalents:

```console
python -m tianshu_storage --config settings.json test
python -m tianshu_storage --config settings.json --service platform status --state-directory /var/lib/tianshu
```

Activation state and installation identity are filesystem recovery inputs, not disposable caches. Back them up with native PG dumps, product source guards/keys and referenced files. A known schema or binding disappearing is an error. During pending activation the SQLite snapshot digest is retained in PG, so a resumed migration refuses a source changed after an already committed logical import. Active PG never reads that old SQLite source again.
