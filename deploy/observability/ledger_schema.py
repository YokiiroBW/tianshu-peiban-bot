"""Lossless ledger evolution and transactionally maintained aggregate metadata."""

VERSION = 1

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
    CREATE TRIGGER event_insert AFTER INSERT ON events BEGIN
        UPDATE totals SET landed=landed+1,pending=pending+(NEW.verified=0) WHERE singleton=1;
        INSERT INTO streams VALUES(NEW.service,NEW.instance,1,NEW.sequence)
            ON CONFLICT(service,instance) DO UPDATE SET
                landed=landed+1,maximum=max(maximum,excluded.maximum);
    END;
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
    if version != 0:
        raise ValueError("unsupported_ledger_schema")
    try:
        connection.executescript(MIGRATION)
        rows = connection.execute("SELECT rowid,hash FROM events WHERE typeof(hash)='text'")
        while batch := rows.fetchmany(1000):
            converted = []
            for rowid, value in batch:
                binary = bytes.fromhex(value)
                if len(binary) != 32 or len(value) != 64:
                    raise ValueError("invalid_ledger_digest")
                converted.append((binary, rowid))
            connection.executemany("UPDATE events SET hash=? WHERE rowid=?", converted)
        connection.execute(f"PRAGMA user_version={VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
