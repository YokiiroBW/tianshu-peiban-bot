"""Safe, local alert transitions, durable even when Loki is unavailable."""

import sqlite3
import time

MONITOR_STALE_SECONDS = 120


def conditions(metrics):
    def high(pattern, threshold):
        return any(
            value > threshold for name, value in metrics.items() if pattern(name)
        )

    return {
        "monitor_unavailable": metrics.get("monitor_success", 0) != 1,
        "delivery_backlog": metrics.get("oldest_pending_seconds", 0) > 120,
        "sequence_gap": metrics.get("sequence_gaps", 0) > 0,
        "source_invalid": high(
            lambda k: (
                k
                in {
                    "invalid_source_lines_total",
                    "identity_conflicts_total",
                    "source_changed_total",
                }
            ),
            0,
        ),
        "retrieval_invalid": high(
            lambda k: (
                k in {"retrieval_conflicts_total", "invalid_retrieved_lines_total"}
            ),
            0,
        ),
        "application_capacity": high(
            lambda k: (
                k.startswith("source_")
                and k.endswith("_ratio")
                and "filesystem" not in k
            ),
            0.8,
        ),
        "disk_capacity": high(
            lambda k: (
                k.endswith("_ratio") and (k.startswith("storage_") or "filesystem" in k)
            ),
            0.85,
        ),
        "ledger_capacity": any(
            metrics.get(name, 0) > 0
            for name in (
                "ledger_capacity_rejected_current",
                "source_retention_failures",
                "retention_delete_failures_current",
            )
        ),
        "source_scan_behind": metrics.get("scan_budget_exhausted", 0) > 0,
    }


class AlertStore:
    def __init__(self, path, retention_days=30):
        if __package__:
            from .retention import days
        else:
            from retention import days

        self.retention_days = days(retention_days)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS current (name TEXT PRIMARY KEY, active INTEGER, changed REAL);
            CREATE TABLE IF NOT EXISTS history (time REAL, name TEXT, active INTEGER);
        """)

    def update(self, metrics, now=None):
        now = time.time() if now is None else now
        desired = conditions(metrics)
        with self.db:
            for name, active in desired.items():
                old = self.db.execute(
                    "SELECT active FROM current WHERE name=?", (name,)
                ).fetchone()
                if old is None or bool(old[0]) != active:
                    self.db.execute(
                        "INSERT INTO current VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET active=excluded.active,changed=excluded.changed",
                        (name, int(active), now),
                    )
                    self.db.execute(
                        "INSERT INTO history VALUES (?,?,?)", (now, name, int(active))
                    )
            # Only local alert transitions. The 10k bound and current state stay.
            self.db.execute(
                "DELETE FROM history WHERE time < ?",
                (now - self.retention_days * 86400,),
            )
            self.db.execute(
                "DELETE FROM history WHERE rowid NOT IN (SELECT rowid FROM history ORDER BY rowid DESC LIMIT 10000)"
            )
        return {"alert_" + name: int(active) for name, active in desired.items()}

    def close(self):
        self.db.close()
