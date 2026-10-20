# TianShu storage

Optional SQL backends and disposable caches for TianShu services. Product repositories own their native PostgreSQL schemas, queries and migrations. This package owns configuration, connection lifetimes, durable backend selection, sanitized diagnostics, bounded Redis operations and immutable file references.

Without backend URLs it opens the original SQLite files and creates no extra backend state. A successful first PostgreSQL connection records a pending binding before any import. Product callbacks import a read-only SQLite snapshot and validate their domain records in the shared migration transaction. `finalize()` activates the complete service. Once pending or active PostgreSQL has been selected, missing configuration or an unavailable server cannot write back to SQLite.

Redis is independent. Every entry has a fixed TTL, a size bound and a service/installation epoch prefix. Errors return misses and later retry; cache loss does not alter business authority. Do not store permissions, source guards, outboxes, durable tasks, credentials or files in Redis.

See [INTERFACE.md](INTERFACE.md) for the product API and read-only diagnostics. Media and original documents belong in the existing file library; `FileStore` publishes content-addressed immutable files before a SQL reference is committed and verifies their size and digest on reads.

## Distribution

Release wheels are immutable. Build each release once and vendor that exact artifact in consumers adopting it. Version 0.1.1 uses `vendor/tianshu_storage-0.1.1-py3-none-any.whl` and a `tianshu-storage==0.1.1` pin; uv projects bind the package to the relative wheel. Consumers retaining 0.1.0 keep their original artifact and pin. Container builds copy the selected wheel before installing the product. Development editable paths must never appear in deployment locks.

Version 0.1.1 reuses PostgreSQL column names and immutable name-to-position mappings for each result set. Public row/cursor behavior and the 0.1 interface remain unchanged; the native SQLite connection path is unchanged.

## Validation

The isolated suite under `tests/storage` in the coordination repository uses a supplied `TIANSHU_TEST_POSTGRES_URL` for real PostgreSQL transactions and a fake Redis for deterministic expiry/disconnect tests. An absent PostgreSQL URL explicitly skips those tests. Real Redis and complete product integration are separate acceptance work; fake Redis is not evidence of a real deployment.
