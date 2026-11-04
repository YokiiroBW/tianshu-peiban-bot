"""Import a host watcher's durable incidents without changing their identity."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from psycopg.types.json import Jsonb

from .notifications import message_for, notification_id


@contextmanager
def spool_lock(path):
    """Both host and application hold this lock while processing one file."""
    with path.with_name(path.name + ".lock").open("a+b") as stream:
        try:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class HostSpool:
    def __init__(self, directory, incidents, database, outbox=None):
        self.directory, self.incidents, self.database = Path(directory), incidents, database
        self.outbox = outbox

    async def import_pending(self, *, limit=100):
        if not self.directory.is_dir() or self.directory.is_symlink():
            return
        for path in sorted(self.directory.glob("event-*.json"))[:limit]:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                with spool_lock(path) as locked:
                    if not locked:
                        continue
                    data = json.loads(path.read_text(encoding="utf-8"))
                    event = data["event"]
                    notice = data.get("notification") or {}
                    if notice.get("state") in {"pending", "sending"}:
                        try:
                            updated = datetime.fromisoformat(notice["updated_at"])
                        except (ValueError, KeyError, TypeError):
                            updated = datetime.min.replace(tzinfo=UTC)
                        if updated.tzinfo is not None and updated > datetime.now(UTC)-timedelta(minutes=2):
                            continue
                    incident = await self.incidents.ingest(event)
                    if notice.get("state") in {"accepted", "unknown", "failed", "sending"} and notice.get("target"):
                        state = "unknown" if notice["state"] == "sending" else notice["state"]
                        if state == "failed" and int(notice.get("attempts", 0)) < 5:
                            state = "pending"
                        nid = notification_id(incident["incident_id"], "opened", "smtp", notice["target"],
                                              episode=incident["episode"])
                        async with self.database.transaction(control=True) as conn:
                            await conn.execute(
                                "INSERT INTO operations.notification_outbox(notification_id,incident_id,kind,episode,incident_revision,"
                                "channel,target,payload,status,attempts,receipt,last_error) "
                                "VALUES(%s,%s,'opened',%s,%s,'smtp',%s,%s,%s,%s,%s,%s) "
                                "ON CONFLICT(notification_id) DO UPDATE SET "
                                "status=CASE WHEN operations.notification_outbox.status='pending' "
                                "AND excluded.status IN ('accepted','unknown') THEN excluded.status ELSE operations.notification_outbox.status END,"
                                "receipt=CASE WHEN operations.notification_outbox.status='pending' "
                                "AND excluded.status IN ('accepted','unknown') THEN excluded.receipt ELSE operations.notification_outbox.receipt END",
                                (nid, incident["incident_id"], incident["episode"], incident["revision"], notice["target"],
                                 Jsonb(message_for(incident, "opened")), state, int(notice.get("attempts", 0)),
                                 Jsonb({"state": state, "origin": "host_spool", "meaning": notice.get("meaning")}),
                                 notice.get("code")))
                    # Commit incident and receipt before removing the spool file.
                    if self.outbox:
                        await self.outbox.enqueue(incident, "opened")
                    path.unlink()
            except (OSError, ValueError, TypeError, KeyError):
                continue
