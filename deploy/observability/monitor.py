"""Local durable hash ledger and external observations, independent of product databases."""

import heapq
import json
import shutil
import sqlite3
import time
from contextlib import contextmanager

from ledger_schema import initialize, insert_summary
from policy import digest_bytes, event_time, segments
from transport import Deadline

RECONCILE_BATCH = 64
RECONCILE_WINDOW_SECONDS = 3600


class Ledger:
    def __init__(self, path, policy, max_events=10000, retention_days=30):
        self.policy, self.max_events = policy, max_events
        self.retention_days = retention_days
        self.capacity_rejected_current = 0
        self.clear_before = 0
        self.scan_cursor = None
        # Transient query progress is never proof. Restart simply searches again;
        # only verified original hashes in the durable ledger authorize retirement.
        self.reconcile_windows = {}
        self.reconcile_cursor = 0
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        try:
            initialize(self.db)
        except BaseException:
            self.db.close()
            raise

    def count(self, name, amount=1):
        self.db.execute(
            "INSERT INTO counters VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
            (name, amount),
        )

    def _duplicate(self, event, digest):
        old = self.db.execute(
            "SELECT hash,service,instance,sequence FROM events WHERE id=?",
            (event["event_id"],),
        ).fetchone()
        if old is None:
            return False
        self.count("source_duplicates")
        if old[0] != digest:
            self.count("identity_conflicts")
            self.db.execute(
                "INSERT OR IGNORE INTO sequence_conflicts VALUES (?,?,?)", old[1:]
            )
            self.db.execute(
                "INSERT OR IGNORE INTO sequence_conflicts VALUES (?,?,?)",
                (event["service"], event["instance_id"], event["sequence"]),
            )
        return True

    def _sequence_conflict(self, event):
        self.count("identity_conflicts")
        self.db.execute(
            "INSERT OR IGNORE INTO sequence_conflicts VALUES (?,?,?)",
            (event["service"], event["instance_id"], event["sequence"]),
        )

    def _checkpoint(self, name, identity, offset):
        self.db.execute(
            "INSERT INTO files VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET identity=excluded.identity,offset=excluded.offset",
            (name, identity, offset),
        )

    def scan(self, roots, line_budget=20000):
        self.apply_clear()
        self.capacity_rejected_current = 0
        retired = {
            (service, instance): through
            for service, instance, through in self.db.execute(
                "SELECT service,instance,through FROM retired_streams"
            )
        }
        partial, count = (
            0,
            self.db.execute("SELECT landed FROM totals WHERE singleton=1").fetchone()[
                0
            ],
        )
        items = list(roots.items())
        names = list(roots)
        if self.scan_cursor in names:
            start = names.index(self.scan_cursor) + 1
            items = items[start:] + items[:start]
        with (
            self.db,
            insert_summary(self.db) as inserted,
            self._scan_summary() as cleared,
        ):
            for service, root in items:
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
                            line_budget -= 1
                            if not raw.endswith(b"\n") and len(raw) <= 4096:
                                partial += 1
                                stream.seek(position)
                                break
                            try:
                                event = self.policy.line(raw)
                                if service == "memory":
                                    allowed = {"memory", "memory-knowledge"}
                                elif service == "knowledge":
                                    allowed = {"memory-knowledge"}
                                else:
                                    allowed = {service}
                                if event["service"] not in allowed:
                                    raise ValueError()
                                occurred = event_time(event)
                                if (
                                    self.clear_before > 0
                                    and occurred <= self.clear_before
                                ):
                                    cleared(
                                        event["service"],
                                        event["instance_id"],
                                        event["sequence"],
                                    )
                                    self.count("manual_cleared_replays")
                                    offset = stream.tell()
                                    self.scan_cursor = service
                                    continue
                                digest = digest_bytes(event)
                                if event["sequence"] <= retired.get(
                                    (event["service"], event["instance_id"]), 0
                                ):
                                    # The retention policy deliberately discarded this
                                    # old hash. Consume replay without claiming that
                                    # its expired contents were checked or reinserted.
                                    if not self._duplicate(event, digest):
                                        self.count("retired_replays")
                                    offset = stream.tell()
                                    self.scan_cursor = service
                                    continue
                                if count >= self.max_events:
                                    if not self._duplicate(event, digest):
                                        conflict = self.db.execute(
                                            "SELECT 1 FROM events WHERE service=? AND instance=? AND sequence=?",
                                            (
                                                event["service"],
                                                event["instance_id"],
                                                event["sequence"],
                                            ),
                                        ).fetchone()
                                        if conflict:
                                            self._sequence_conflict(event)
                                        else:
                                            self.count("ledger_capacity_rejected")
                                            self.capacity_rejected_current = 1
                                            # Keep prior duplicate/invalid progress,
                                            # but leave this new event unread.
                                            self._checkpoint(name, identity, position)
                                            return {
                                                "scan_budget_exhausted": 1,
                                                "partial_lines": partial,
                                            }
                                    offset = stream.tell()
                                    self.scan_cursor = service
                                    continue
                                try:
                                    written = self.db.execute(
                                        "INSERT INTO events(id,service,instance,sequence,hash,first_seen,occurred) "
                                        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                                        (
                                            event["event_id"],
                                            event["service"],
                                            event["instance_id"],
                                            event["sequence"],
                                            digest,
                                            time.time(),
                                            occurred,
                                        ),
                                    ).rowcount
                                except sqlite3.IntegrityError:
                                    written = 0
                                if written:
                                    inserted(
                                        event["service"],
                                        event["instance_id"],
                                        event["sequence"],
                                    )
                                    count += 1
                                else:
                                    # Normal new events need only the INSERT's
                                    # unique checks. On conflict, retain the
                                    # original ID-before-sequence classification.
                                    if not self._duplicate(event, digest):
                                        self._sequence_conflict(event)
                            except ValueError:
                                self.count("invalid_source_lines")
                                while raw and not raw.endswith(b"\n"):
                                    raw = stream.readline(4097)
                            offset = stream.tell()
                            self.scan_cursor = service
                        self._checkpoint(name, identity, offset)
        return {
            "scan_budget_exhausted": int(line_budget <= 0),
            "partial_lines": partial,
        }

    def reconcile(self, client, retention_seconds=30 * 86400, batch=128):
        now = time.time()
        batch = min(batch, RECONCILE_BATCH)
        pending = self.db.execute(
            # Compare a never-checked event's first_seen with the last real
            # check of an unmatched event, so continuous backfill cannot starve it.
            "SELECT id,hash,service,collected,verified,occurred,first_seen FROM events WHERE verified=0 "
            "ORDER BY CASE WHEN last_checked=0 THEN first_seen ELSE last_checked END,first_seen LIMIT ?",
            (max(1, batch // 2),),
        ).fetchall()
        # A prior query hit is not permanent proof. Revisit confirmed events while
        # they are inside retention so an empty/restored central store cannot go green.
        confirmed = self.db.execute(
            "SELECT id,hash,service,collected,verified,occurred,first_seen FROM events WHERE verified>0 "
            "AND (last_checked<? OR collected IS NULL) AND first_seen>? ORDER BY last_checked LIMIT ?",
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
        window = RECONCILE_WINDOW_SECONDS
        latest = int(now // window)
        oldest = now - retention_seconds
        groups = {}
        for ident, fingerprint, service, collected, verified, occurred, first_seen in pending:
            if verified > 0 and collected is not None:
                # Re-prove the actual previously observed collection position.
                # Its absence conservatively makes the record pending again.
                bucket = int(collected // window) if oldest <= collected <= now else latest
                recent = False
            else:
                progress = self.reconcile_windows.get(ident)
                if progress is None:
                    # Backfilled source timestamps are a useful first lookup,
                    # never a bound: a miss resumes the full collection window.
                    hint = occurred if occurred is not None and oldest <= occurred <= now else first_seen
                    bucket = int(hint // window) if oldest <= hint <= now else latest
                    recent = bucket != latest
                else:
                    history, recent = progress
                    if (history + 1) * window < oldest:
                        history = latest
                    bucket = latest if recent else history
            # Legacy confirmations may have no recorded collection position.
            # A partial page cannot disprove their earlier full-window receipt;
            # keep it unchanged until a hit or completion of the original window.
            complete = not recent and bucket * window - 1 <= oldest
            disprove_missing = verified > 0 and (collected is not None or complete)
            groups.setdefault((service, bucket, disprove_missing), []).append(
                (ident, fingerprint, recent)
            )
        groups = list(groups.items())
        start = self.reconcile_cursor % len(groups)
        groups = groups[start:] + groups[:start]
        # One failed source/page cannot permanently monopolize the next cycle.
        self.reconcile_cursor += 1
        deadline = Deadline()
        for (service, bucket, previously_verified), records in groups:
            expected = {ident: fingerprint for ident, fingerprint, _ in records}
            selector = (
                '{stack="tianshu",service=' + json.dumps("tianshu_" + service)
                + '} | json | event_id=~"' + "|".join(expected) + '"'
            )
            # Original event timestamps/first_seen are not collection bounds.
            # Unknown records try their timestamp hint, then search the entire
            # original collection window one page at a time, alternating the
            # current hour with older pages to find late delivery as well.
            lower = max(oldest, bucket * window - 1)
            upper = min(now, (bucket + 1) * window + 1)
            rows = client.range(
                selector, int(lower * 1e9), int(upper * 1e9),
                limit=max(1000, len(expected) + 1), deadline=deadline,
            )
            seen = self._confirm_query(expected, rows, now, previously_verified)
            for ident, _, recent in records:
                if ident in seen:
                    self.reconcile_windows.pop(ident, None)
                    continue
                if ident not in self.reconcile_windows and len(self.reconcile_windows) >= self.max_events:
                    self.reconcile_windows.pop(next(iter(self.reconcile_windows)))
                history = self.reconcile_windows.get(ident, (latest, False))[0]
                self.reconcile_windows[ident] = (
                    history if recent else bucket - 1,
                    not recent and bucket != latest,
                )

    def _confirm_query(self, expected, rows, now, previously_verified):
        seen = set()
        with self.db:
            self.db.executemany(
                "UPDATE events SET last_checked=? WHERE id=?",
                [(now, ident) for ident in expected],
            )
            for timestamp, line in rows:
                try:
                    event = self.policy.line(line.encode() + b"\n")
                    ident = event["event_id"]
                    if ident not in expected or digest_bytes(event) != expected[ident]:
                        self.count("retrieval_conflicts")
                    else:
                        if ident in seen:
                            self.count("retrieval_duplicates")
                        seen.add(ident)
                        self.db.execute(
                            "UPDATE events SET verified=?,collected=CASE WHEN collected IS NULL THEN ? "
                            "ELSE min(collected,?) END WHERE id=?",
                            (now, int(timestamp) / 1e9, int(timestamp) / 1e9, ident),
                        )
                except ValueError:
                    self.count("invalid_retrieved_lines")
            if previously_verified:
                self.db.executemany(
                    "UPDATE events SET verified=0 WHERE id=?",
                    [(ident,) for ident in expected.keys() - seen],
                )
        return seen

    def metrics(self):
        total, pending = self.db.execute(
            "SELECT landed,pending FROM totals WHERE singleton=1"
        ).fetchone()
        oldest = self.db.execute(
            "SELECT first_seen FROM events WHERE verified=0 ORDER BY first_seen LIMIT 1"
        ).fetchone()
        gaps = self.db.execute(
            "SELECT coalesce(sum(maximum-landed-coalesce(through,0)),0) "
            "FROM streams LEFT JOIN retired_streams USING(service,instance)"
        ).fetchone()[0]
        retired = self.db.execute(
            "SELECT coalesce(sum(through),0) FROM retired_streams"
        ).fetchone()[0]
        return {
            "landed_events": total,
            "ledger_max_events": self.max_events,
            "ledger_capacity_rejected_current": self.capacity_rejected_current,
            "pending_events": pending or 0,
            "oldest_pending_seconds": time.time() - oldest[0] if oldest else 0,
            "sequence_gaps": gaps,
            "retired_events": retired,
            **{
                name + "_total": value
                for name, value in self.db.execute("SELECT name,value FROM counters")
            },
        }

    def _retire(self, service, instance, through, start, end):
        self.db.execute(
            "INSERT INTO retired_streams VALUES (?,?,?) ON CONFLICT(service,instance) "
            "DO UPDATE SET through=max(through,excluded.through)",
            (service, instance, through),
        )
        self.db.execute(
            "INSERT INTO retention_deletes(service,instance,through,start,end) VALUES (?,?,?,?,?) "
            "ON CONFLICT(service,instance) DO UPDATE SET through=max(through,excluded.through),"
            "start=CASE WHEN submitted>=through THEN excluded.start ELSE min(start,excluded.start) END,"
            "end=max(end,excluded.end)",
            (service, instance, through, start, end),
        )

    @contextmanager
    def _scan_summary(self):
        streams = {}

        def cleared(service, instance, sequence):
            key = (service, instance)
            streams[key] = max(streams.get(key, 0), sequence)

        yield cleared
        self.db.execute(
            "UPDATE retention_control SET scan_after=? WHERE singleton=1",
            (self.scan_cursor,),
        )
        now = time.time()
        for (service, instance), through in streams.items():
            self._retire(
                service, instance, through, now - (self.retention_days + 1) * 86400, now
            )
            self.db.execute(
                "INSERT INTO streams VALUES (?,?,0,?) ON CONFLICT(service,instance) "
                "DO UPDATE SET maximum=max(maximum,excluded.maximum)",
                (service, instance, through),
            )

    def apply_clear(self):
        cutoff, self.scan_cursor = self.db.execute(
            "SELECT clear_before,scan_after FROM retention_control WHERE singleton=1"
        ).fetchone()
        if cutoff <= self.clear_before:
            return
        with self.db:
            old = self.db.execute(
                "SELECT service,instance,max(sequence),min(collected) FROM events "
                "WHERE first_seen<=? OR occurred<=? GROUP BY service,instance",
                (cutoff, cutoff),
            ).fetchall()
            now = time.time()
            for service, instance, through, collected in old:
                self._retire(
                    service,
                    instance,
                    through,
                    collected or now - (self.retention_days + 1) * 86400,
                    now,
                )
            deleted = self.db.execute(
                "DELETE FROM events WHERE first_seen<=? OR occurred<=?",
                (cutoff, cutoff),
            ).rowcount
            if deleted:
                self.count("manual_cleared_events", deleted)
        self.clear_before = cutoff

    def expire(
        self,
        retention_days=30,
        *,
        now=None,
        event_budget=20000,
        coverage=None,
        reserve=0,
    ):
        """Apply age, global count and operator clear to confirmed stream prefixes.

        The watermark and deletion share the transaction with trigger-maintained
        totals. A missing, pending or still retained event stops that stream;
        later events and their gap evidence stay intact. No source file is deleted.
        """
        from retention import days

        cutoff = (time.time() if now is None else now) - days(retention_days) * 86400
        deleted = 0
        with self.db:
            clear_before = self.db.execute(
                "SELECT clear_before FROM retention_control WHERE singleton=1"
            ).fetchone()[0]
            total = self.db.execute(
                "SELECT landed FROM totals WHERE singleton=1"
            ).fetchone()[0]
            target = max(0, self.max_events - reserve)
            streams = self.db.execute(
                "SELECT service,instance,coalesce(through,0) FROM streams "
                "LEFT JOIN retired_streams USING(service,instance) "
                "WHERE landed>0 ORDER BY service,instance"
            ).fetchall()
            ends = {
                (service, instance): through for service, instance, through in streams
            }

            def records(key, after):
                while True:
                    rows = self.db.execute(
                        "SELECT sequence,first_seen,verified FROM events WHERE service=? AND instance=? "
                        "AND sequence>? ORDER BY sequence LIMIT 128",
                        (*key, after),
                    ).fetchall()
                    if not rows:
                        return
                    yield from rows
                    after = rows[-1][0]

            waiting, readers = [], {}

            def offer(key):
                row = next(readers[key], None)
                if row is None:
                    return
                sequence, seen, verified = row
                if sequence != ends[key] + 1 or verified <= 0:
                    return
                if coverage is not None and sequence > coverage.get(key, ends[key]):
                    return
                heapq.heappush(waiting, (seen, *key, sequence))

            for key, through in ends.items():
                readers[key] = records(key, through)
                offer(key)
            changed = {}
            while waiting and deleted < event_budget:
                seen, service, instance, sequence = heapq.heappop(waiting)
                if not (
                    seen < cutoff or seen <= clear_before or total - deleted > target
                ):
                    break
                key = (service, instance)
                ends[key] = changed[key] = sequence
                deleted += 1
                offer(key)
            for (service, instance), end in changed.items():
                collected = self.db.execute(
                    "SELECT min(collected) FROM events "
                    "WHERE service=? AND instance=? AND sequence<=?",
                    (service, instance, end),
                ).fetchone()[0]
                current = time.time() if now is None else now
                self._retire(
                    service,
                    instance,
                    end,
                    collected or current - (days(retention_days) + 1) * 86400,
                    current,
                )
                self.db.execute(
                    "DELETE FROM events WHERE service=? AND instance=? AND sequence<=?",
                    (service, instance, end),
                )
            if deleted:
                self.count("expired_events", deleted)
                self.db.execute(
                    "DELETE FROM sequence_conflicts WHERE EXISTS (SELECT 1 FROM retired_streams r "
                    "WHERE r.service=sequence_conflicts.service AND r.instance=sequence_conflicts.instance "
                    "AND r.through>=sequence_conflicts.sequence)"
                )
        return {"retention_expired_events": deleted}

    def retained_window(self):
        return self.db.execute(
            "SELECT e.id,e.service,e.instance,e.sequence,(c.sequence IS NOT NULL) FROM "
            "(SELECT id,service,instance,sequence FROM events "
            "ORDER BY first_seen DESC,id DESC LIMIT ?) e LEFT JOIN sequence_conflicts c "
            "USING(service,instance,sequence) ORDER BY e.service,e.instance,e.sequence",
            (self.max_events,),
        ).fetchall()

    def submit_deletions(self, client, *, now=None):
        """Submit stable, coalesced batches without resetting their maturation time.

        The retained query window changes immediately. Physical cleanup is an
        asynchronous outbox: combine further retirements for five minutes rather
        than create another overlapping Loki delete filter every monitor cycle.
        A failed submission leaves both its watermark and submission time intact.
        """
        from datetime import datetime, timezone
        from urllib.parse import urlencode

        now = time.time() if now is None else now
        for service, instance, through, start, end in self.db.execute(
            "SELECT service,instance,through,start,end FROM retention_deletes "
            "WHERE submitted<through AND (submitted_at=0 OR submitted_at<=? OR submitted_at>?) LIMIT 32",
            (now - 300, now),
        ).fetchall():
            selector = (
                '{stack="tianshu",service="tianshu_' + service + '"} | json '
                '| instance_id="' + instance + '" | sequence<=' + str(through)
            )
            status, _, _ = client.request(
                "/loki/api/v1/delete?"
                + urlencode(
                    {
                        "query": selector,
                        "start": max(0, int(start) - 1),
                        "end": datetime.fromtimestamp(end, timezone.utc).isoformat(),
                    }
                ),
                "POST",
            )
            if status != 204:
                raise RuntimeError("loki_retention_delete_failed")
            with self.db:
                self.db.execute(
                    "UPDATE retention_deletes SET submitted=?,submitted_at=? WHERE service=? AND instance=?",
                    (through, now, service, instance),
                )

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
