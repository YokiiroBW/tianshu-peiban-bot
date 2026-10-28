"""Lossless ledger evolution and transactionally maintained aggregate metadata."""

from contextlib import contextmanager

VERSION = 4

BASE = """
    CREATE TABLE IF NOT EXISTS files (name TEXT PRIMARY KEY, identity TEXT, offset INTEGER);
    CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, service TEXT, instance TEXT,
        sequence INTEGER, hash BLOB, first_seen REAL, verified REAL DEFAULT 0, last_checked REAL DEFAULT 0);
    CREATE UNIQUE INDEX IF NOT EXISTS event_sequence ON events(service,instance,sequence);
    CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER);
"""

MIGRATION = """
    BEGIN IMMEDIATE;
    DROP INDEX IF EXISTS event_pending;
    CREATE INDEX event_pending ON events(last_checked,first_seen) WHERE verified=0;
    CREATE INDEX event_pending_age ON events(first_seen) WHERE verified=0;
    CREATE INDEX event_confirmed ON events(last_checked) WHERE verified>0;
    CREATE TABLE totals (singleton INTEGER PRIMARY KEY CHECK(singleton=1), landed INTEGER, pending INTEGER);
    INSERT INTO totals SELECT 1,count(*),coalesce(sum(verified=0),0) FROM events;
    CREATE TABLE streams (service TEXT, instance TEXT, landed INTEGER, maximum INTEGER,
        PRIMARY KEY(service,instance)) WITHOUT ROWID;
    INSERT INTO streams SELECT service,instance,count(*),max(sequence) FROM events GROUP BY service,instance;
    CREATE TRIGGER event_verified AFTER UPDATE OF verified ON events
        WHEN (OLD.verified=0)!=(NEW.verified=0) BEGIN
        UPDATE totals SET pending=pending+(NEW.verified=0)-(OLD.verified=0) WHERE singleton=1;
    END;
    CREATE TRIGGER event_delete AFTER DELETE ON events BEGIN
        UPDATE totals SET landed=landed-1,pending=pending-(OLD.verified=0) WHERE singleton=1;
        UPDATE streams SET landed=landed-1,
            maximum=coalesce((SELECT max(sequence) FROM events
                WHERE service=OLD.service AND instance=OLD.instance),0)
            WHERE service=OLD.service AND instance=OLD.instance;
        DELETE FROM streams WHERE service=OLD.service AND instance=OLD.instance AND landed=0;
    END;
"""

RETENTION = """
    ALTER TABLE events ADD COLUMN occurred REAL;
    ALTER TABLE events ADD COLUMN collected REAL;
    CREATE TABLE retired_streams (service TEXT, instance TEXT, through INTEGER NOT NULL,
        PRIMARY KEY(service,instance)) WITHOUT ROWID;
    CREATE TABLE source_receipts (name TEXT, identity TEXT, size INTEGER NOT NULL,
        hash BLOB NOT NULL, coverage TEXT NOT NULL,
        PRIMARY KEY(name,identity)) WITHOUT ROWID;
    CREATE TABLE retention_control (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        clear_before REAL NOT NULL, scan_after TEXT);
    INSERT INTO retention_control VALUES (1,0,NULL);
    CREATE TABLE retention_deletes (service TEXT, instance TEXT, through INTEGER NOT NULL,
        submitted INTEGER NOT NULL DEFAULT 0, start REAL, end REAL,
        PRIMARY KEY(service,instance)) WITHOUT ROWID;
    CREATE TABLE sequence_conflicts (service TEXT, instance TEXT, sequence INTEGER,
        PRIMARY KEY(service,instance,sequence)) WITHOUT ROWID;
    DROP TRIGGER event_delete;
    CREATE TRIGGER event_delete AFTER DELETE ON events BEGIN
        UPDATE totals SET landed=landed-1,pending=pending-(OLD.verified=0) WHERE singleton=1;
        UPDATE streams SET landed=landed-1,
            maximum=CASE WHEN maximum=OLD.sequence THEN max(
                coalesce((SELECT max(sequence) FROM events
                    WHERE service=OLD.service AND instance=OLD.instance),0),
                coalesce((SELECT through FROM retired_streams
                    WHERE service=OLD.service AND instance=OLD.instance),0))
                ELSE maximum END
            WHERE service=OLD.service AND instance=OLD.instance;
        DELETE FROM streams WHERE service=OLD.service AND instance=OLD.instance AND landed=0
            AND NOT EXISTS (SELECT 1 FROM retired_streams
                WHERE service=OLD.service AND instance=OLD.instance);
    END;
"""


@contextmanager
def insert_summary(connection):
    """Summarize accepted inserts once, before their owning transaction commits.

    Call the yielded function only after a successful INSERT. The caller owns
    the transaction and must not update/delete these rows inside this scope;
    verified/deletion triggers continue to maintain those later changes. This
    keeps source offsets, events and summaries atomic, including early returns.
    """
    streams = {}
    landed = pending = 0

    def accepted(service, instance, sequence, verified=0):
        nonlocal landed, pending
        landed += 1
        pending += verified == 0
        key = (service, instance)
        count, maximum = streams.get(key, (0, sequence))
        streams[key] = (count + 1, max(maximum, sequence))

    yield accepted
    if landed:
        if not connection.in_transaction:
            raise RuntimeError("ledger_insert_requires_transaction")
        connection.execute(
            "UPDATE totals SET landed=landed+?,pending=pending+? WHERE singleton=1",
            (landed, pending),
        )
        connection.executemany(
            "INSERT INTO streams VALUES (?,?,?,?) ON CONFLICT(service,instance) "
            "DO UPDATE SET landed=landed+excluded.landed,maximum=max(maximum,excluded.maximum)",
            [
                (service, instance, count, maximum)
                for (service, instance), (count, maximum) in streams.items()
            ],
        )


def initialize(connection):
    """Upgrade old text hashes without discarding events or vacuuming live files.

    Conversion and metadata backfill commit together. Freed pages stay available
    for subsequent writes; migration does not promise that an existing DB file
    immediately shrinks. No event, source segment or retention evidence is removed.
    """
    connection.executescript(BASE)
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == VERSION:
        return
    if version not in (0, 1, 2, 3):
        raise ValueError("unsupported_ledger_schema")
    try:
        if version == 0:
            connection.executescript(MIGRATION)
            rows = connection.execute(
                "SELECT rowid,hash FROM events WHERE typeof(hash)='text'"
            )
            while batch := rows.fetchmany(1000):
                converted = []
                for rowid, value in batch:
                    binary = bytes.fromhex(value)
                    if len(binary) != 32 or len(value) != 64:
                        raise ValueError("invalid_ledger_digest")
                    converted.append((binary, rowid))
                connection.executemany(
                    "UPDATE events SET hash=? WHERE rowid=?", converted
                )
        else:
            connection.execute("BEGIN IMMEDIATE")
            # v1 already has lossless BLOBs, summaries and indexed revisit
            # queues. Only its per-event insert maintenance is replaced.
            if version == 1:
                connection.execute("DROP TRIGGER event_insert")
        # executescript would commit the earlier conversion before this step.
        # Keep all migration changes, including the version, in one transaction.
        if version < 3:
            for statement in _statements(RETENTION):
                connection.execute(statement)
        connection.execute(
            "ALTER TABLE retention_deletes ADD COLUMN submitted_at REAL NOT NULL DEFAULT 0"
        )
        connection.execute(f"PRAGMA user_version={VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _statements(script):
    """Split complete SQLite statements while retaining trigger BEGIN/END bodies."""
    import sqlite3

    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            yield statement
            statement = ""
