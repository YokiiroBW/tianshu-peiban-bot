"""Safe, local alert transitions, durable even when Loki is unavailable."""

import sqlite3
import time


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
        "ledger_capacity": metrics.get("landed_events", 0) >= 900000
        or metrics.get("ledger_capacity_rejected_total", 0) > 0,
        "source_scan_behind": metrics.get("scan_budget_exhausted", 0) > 0,
    }


class AlertStore:
    def __init__(self, path):
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
            # Only this package's alert transitions, not application logs. Explicit 30d/10k retention.
            self.db.execute("DELETE FROM history WHERE time < ?", (now - 30 * 86400,))
            self.db.execute(
                "DELETE FROM history WHERE rowid NOT IN (SELECT rowid FROM history ORDER BY rowid DESC LIMIT 10000)"
            )
        return {"alert_" + name: int(active) for name, active in desired.items()}

    def close(self):
        self.db.close()
