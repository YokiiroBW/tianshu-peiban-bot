"""Minimal external-host SQLite receipt/outbox, not a second TianShu backend."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(
            self.path, check_same_thread=False, timeout=5, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,semantics TEXT NOT NULL,state TEXT NOT NULL,response TEXT,created REAL NOT NULL,notified INTEGER NOT NULL DEFAULT 0); CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT,state TEXT NOT NULL,created REAL NOT NULL);"
        )
        self.db.execute("UPDATE calls SET state='unknown' WHERE state='submitted'")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def begin(self, identifier, value):
        semantics = hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT * FROM calls WHERE id=?", (identifier,)).fetchone()
                if row:
                    if row["semantics"] != semantics:
                        raise ValueError("bridge_call_conflict")
                    result = dict(row)
                    result["fresh"] = False
                else:
                    self.db.execute(
                        "INSERT INTO calls(id,semantics,state,response,created) VALUES(?,?,'submitted',NULL,?)",
                        (identifier, semantics, time.time()),
                    )
                    result = {"id": identifier, "state": "submitted", "fresh": True}
                self.db.execute("COMMIT")
                return result
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def finish(self, identifier, state, response):
        with self.lock:
            self.db.execute(
                "UPDATE calls SET state=?,response=?,notified=0 WHERE id=?",
                (state, json.dumps(response, ensure_ascii=False), identifier),
            )

    def get(self, identifier):
        with self.lock:
            row = self.db.execute("SELECT * FROM calls WHERE id=?", (identifier,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["response"] = json.loads(value["response"]) if value["response"] else None
        return value

    def acknowledge(self, identifier):
        value = self.get(identifier)
        if not value:
            return
        response = value.get("response") or {}
        data = response.get("data")
        ids = {
            key: data[key]
            for key in ("message_id", "forward_id", "res_id")
            if isinstance(data, dict) and key in data
        }
        retained = {
            "status": response.get("status"),
            "retcode": response.get("retcode"),
            "data": ids,
            "acknowledged": True,
        }
        with self.lock:
            self.db.execute(
                "UPDATE calls SET response=? WHERE id=?", (json.dumps(retained), identifier)
            )

    def pending_receipts(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,state,response FROM calls WHERE notified=0 AND state IN ('confirmed','rejected','unknown') ORDER BY created,id LIMIT 32"
            ).fetchall()
        return [
            {
                "call_id": row["id"],
                "state": row["state"],
                "response": json.loads(row["response"]) if row["response"] else None,
            }
            for row in rows
        ]

    def receipt_ack(self, identifier):
        self.acknowledge(identifier)
        with self.lock:
            self.db.execute("UPDATE calls SET notified=1 WHERE id=?", (identifier,))

    def event(self, identifier, payload):
        with self.lock:
            self.db.execute(
                "INSERT OR IGNORE INTO events VALUES(?,?,'pending',?)",
                (identifier, json.dumps(payload, ensure_ascii=False), time.time()),
            )

    def pending(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,payload FROM events WHERE state='pending' ORDER BY created,id LIMIT 32"
            ).fetchall()
        return [(row["id"], json.loads(row["payload"])) for row in rows]

    def event_ack(self, identifier):
        with self.lock:
            self.db.execute(
                "UPDATE events SET payload=NULL,state='acknowledged' WHERE id=?", (identifier,)
            )

    def close(self):
        with self.lock:
            self.db.close()
