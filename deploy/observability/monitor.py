"""Local durable hash ledger and external observations, independent of product databases."""

import shutil
import sqlite3
import time

from policy import digest, segments


class Ledger:
    def __init__(self, path, policy, max_events=1_000_000):
        self.policy, self.max_events = policy, max_events
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS files (name TEXT PRIMARY KEY, identity TEXT, offset INTEGER);
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, service TEXT, instance TEXT,
                sequence INTEGER, hash TEXT, first_seen REAL, verified REAL DEFAULT 0, last_checked REAL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS event_pending ON events(verified, last_checked, first_seen);
            CREATE UNIQUE INDEX IF NOT EXISTS event_sequence ON events(service,instance,sequence);
            CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER);
        """)

    def count(self, name, amount=1):
        self.db.execute(
            "INSERT INTO counters VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
            (name, amount),
        )

    def scan(self, roots, line_budget=20000):
        partial, count = 0, self.db.execute("SELECT count(*) FROM events").fetchone()[0]
        with self.db:
            for service, root in roots.items():
                for path in segments(root):
                    if line_budget <= 0:
                        return {"scan_budget_exhausted": 1, "partial_lines": partial}
                    stat = path.stat()
                    with path.open("rb") as stream:
                        # Device+inode catches replacement; first line hash prevents inode reuse.
                        first = stream.readline(4097)
                        import hashlib

                        identity = (
                            f"{stat.st_dev}:{stat.st_ino}:"
                            + hashlib.sha256(first).hexdigest()
                        )
                        name = service + "/" + path.name
                        row = self.db.execute(
                            "SELECT identity,offset FROM files WHERE name=?", (name,)
                        ).fetchone()
                        offset = row[1] if row and row[0] == identity else 0
                        if row and (row[0] != identity or stat.st_size < offset):
                            # A renamed old file plus a new active inode is ordinary
                            # rotation. Rewriting/truncating consumed bytes is not.
                            same_file = (
                                row[0].rsplit(":", 1)[0] == identity.rsplit(":", 1)[0]
                            )
                            if same_file and row[1] > 0:
                                self.count("source_changed")
                            elif not same_file:
                                self.count("source_replacements")
                            offset = 0
                        stream.seek(offset)
                        while line_budget > 0:
                            position = stream.tell()
                            raw = stream.readline(4097)
                            if not raw:
                                break
                            if count >= self.max_events:
                                self.count("ledger_capacity_rejected")
                                return {
                                    "scan_budget_exhausted": 1,
                                    "partial_lines": partial,
                                }
                            line_budget -= 1
                            if not raw.endswith(b"\n") and len(raw) <= 4096:
                                partial += 1
                                stream.seek(position)
                                break
                            try:
                                event = self.policy.line(raw)
                                allowed = (
                                    {"memory", "memory-knowledge"}
                                    if service == "memory"
                                    else {service}
                                )
                                if event["service"] not in allowed:
                                    raise ValueError()
                                old = self.db.execute(
                                    "SELECT hash FROM events WHERE id=?",
                                    (event["event_id"],),
                                ).fetchone()
                                if old:
                                    self.count("source_duplicates")
                                    if old[0] != digest(event):
                                        self.count("identity_conflicts")
                                else:
                                    try:
                                        self.db.execute(
                                            "INSERT INTO events(id,service,instance,sequence,hash,first_seen) VALUES (?,?,?,?,?,?)",
                                            (
                                                event["event_id"],
                                                event["service"],
                                                event["instance_id"],
                                                event["sequence"],
                                                digest(event),
                                                time.time(),
                                            ),
                                        )
                                        count += 1
                                    except sqlite3.IntegrityError:
                                        self.count("identity_conflicts")
                            except ValueError:
                                self.count("invalid_source_lines")
                                while raw and not raw.endswith(b"\n"):
                                    raw = stream.readline(4097)
                            offset = stream.tell()
                        self.db.execute(
                            "INSERT INTO files VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET identity=excluded.identity,offset=excluded.offset",
                            (name, identity, offset),
                        )
        return {
            "scan_budget_exhausted": int(line_budget <= 0),
            "partial_lines": partial,
        }

    def reconcile(self, client, retention_seconds=30 * 86400, batch=128):
        now = time.time()
        pending = self.db.execute(
            "SELECT id,hash FROM events WHERE verified=0 ORDER BY last_checked,first_seen LIMIT ?",
            (max(1, batch // 2),),
        ).fetchall()
        # A prior query hit is not permanent proof. Revisit confirmed events while
        # they are inside retention so an empty/restored central store cannot go green.
        confirmed = self.db.execute(
            "SELECT id,hash FROM events WHERE verified>0 AND last_checked<? AND first_seen>? ORDER BY last_checked LIMIT ?",
            (now - 3600, now - retention_seconds, max(0, batch - len(pending))),
        ).fetchall()
        pending += confirmed
        if not pending:
            # Query even while idle so storage failure cannot look like an empty healthy log.
            client.range(
                '{stack="tianshu"} |= "__tianshu_monitor_no_business_event__"',
                int((now - 60) * 1e9),
                int(now * 1e9),
            )
            return
        expected = dict(pending)
        selector = '{stack="tianshu"} | json | event_id=~"' + "|".join(expected) + '"'
        rows = client.range(
            selector, int((now - retention_seconds) * 1e9), int(now * 1e9)
        )
        seen = set()
        with self.db:
            self.db.executemany(
                "UPDATE events SET last_checked=? WHERE id=?",
                [(now, ident) for ident in expected],
            )
            for _, line in rows:
                try:
                    event = self.policy.line(line.encode() + b"\n")
                    ident = event["event_id"]
                    if ident not in expected or digest(event) != expected[ident]:
                        self.count("retrieval_conflicts")
                    else:
                        if ident in seen:
                            self.count("retrieval_duplicates")
                        seen.add(ident)
                        self.db.execute(
                            "UPDATE events SET verified=? WHERE id=?", (now, ident)
                        )
                except ValueError:
                    self.count("invalid_retrieved_lines")
            self.db.executemany(
                "UPDATE events SET verified=0 WHERE id=?",
                [(ident,) for ident in expected.keys() - seen],
            )

    def metrics(self):
        total, pending, oldest = self.db.execute(
            "SELECT count(*),sum(verified=0),min(CASE WHEN verified=0 THEN first_seen END) FROM events"
        ).fetchone()
        gaps = self.db.execute(
            "SELECT coalesce(sum(gaps),0) FROM (SELECT max(sequence)-count(*) AS gaps FROM events GROUP BY service,instance)"
        ).fetchone()[0]
        return {
            "landed_events": total,
            "pending_events": pending or 0,
            "oldest_pending_seconds": time.time() - oldest if oldest else 0,
            "sequence_gaps": gaps,
            **{
                name + "_total": value
                for name, value in self.db.execute("SELECT name,value FROM counters")
            },
        }

    def close(self):
        self.db.close()


def capacities(roots, budgets):
    values = {}
    for service, path in roots.items():
        size = sum(segment.stat().st_size for segment in segments(path))
        disk = shutil.disk_usage(path)
        values[service] = {
            "bytes": size,
            "budget": budgets[service],
            "ratio": size / budgets[service],
            "free": disk.free,
            "filesystem_ratio": 1 - disk.free / disk.total,
        }
    return values
