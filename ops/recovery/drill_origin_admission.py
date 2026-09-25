"""A1 clone admission against sealed Platform issue output and stopped source state."""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .safety import child, read_bytes, read_json, require


MINIMUM_REMAINING_SECONDS = 180
ISSUE_FILES = {
    "config": (
        "private/a1-config-origin-request.json",
        "private/a1-config-origin-issue.json",
        "config-entry",
    ),
    "actor": (
        "private/a1-actor-origin-request.json",
        "private/a1-actor-origin-issue.json",
        "web-source-actor",
    ),
}
ORIGIN = re.compile(r"origin:[a-f0-9]{32}\Z")
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")


def _epoch(value):
    require(type(value) is str and UTC.fullmatch(value) is not None,
            "drill_origin_expiry_invalid")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").timestamp()
    except ValueError:
        require(False, "drill_origin_expiry_invalid")


def _env_origin(inputs):
    try:
        lines = read_bytes(child(inputs, "private/gateway.env"), limit=65536).decode(
            "utf-8"
        ).splitlines()
    except UnicodeError:
        require(False, "drill_origin_binding_invalid")
    values = [line.split("=", 1)[1] for line in lines
              if line.startswith("TS_GATEWAY_ORIGIN=")]
    require(len(values) == 1 and ORIGIN.fullmatch(values[0]) is not None,
            "drill_origin_binding_invalid")
    return values[0]


def _reference(assertion):
    request = assertion.get("request_json")
    query = request.get("query") if isinstance(request, dict) else None
    origin = query.get("origin") if isinstance(query, dict) else None
    return origin.get("assertion_ref") if isinstance(origin, dict) else None


def _source_rows(source, references):
    settings = read_json(child(source, "config/platform/settings.json"))
    require(isinstance(settings, dict), "drill_origin_source_invalid")
    entries = settings.get("entries")
    require(settings.get("mode") == "service_https"
            and settings.get("database_path") == "/var/lib/tianshu/platform.sqlite"
            and isinstance(entries, dict)
            and all(isinstance(entries.get(entry), dict)
                    and entries[entry].get("ttl_seconds") == 300
                    for _, _, entry in ISSUE_FILES.values()),
            "drill_origin_source_invalid")
    database = child(source, "data/platform/platform.sqlite")
    require(database.is_file(), "drill_origin_source_invalid")
    wal = child(source, "data/platform/platform.sqlite-wal", exists=False)
    require(not wal.exists() or (wal.is_file() and wal.stat().st_size == 0),
            "drill_origin_source_wal_unreadable")
    try:
        # The authority is stopped and its WAL must be empty. Immutable opens the
        # already bound database without touching SQLite lock or journal files.
        with closing(sqlite3.connect(database.as_uri() + "?immutable=1", uri=True)) as db:
            rows = {
                name: db.execute(
                    "SELECT entry_id, entry_digest, expires_at, revoked FROM origins WHERE ref=?",
                    (reference,),
                ).fetchone()
                for name, reference in references.items()
            }
            revoked_entries = {
                entry for _, _, entry in ISSUE_FILES.values()
                if db.execute("SELECT 1 FROM revoked_entries WHERE id=?", (entry,)).fetchone()
            }
            revoked_owners = {
                name for name, (_, _, entry) in ISSUE_FILES.items()
                if db.execute(
                    "SELECT 1 FROM revoked_principals WHERE id=?",
                    (entries[entry].get("owner"),),
                ).fetchone()
            }
    except sqlite3.Error:
        require(False, "drill_origin_source_invalid")
    require(not revoked_entries and not revoked_owners,
            "drill_origin_source_mismatch")
    return rows, entries


def check_origin_admission(source, inputs, index, *, now=None, minimum_remaining=0):
    """Return a controlled life summary; never expose or persist origin references."""
    admission = index.get("a1_origin_admission")
    if admission is None:
        return None
    require(admission == {"minimum_remaining_seconds": MINIMUM_REMAINING_SECONDS},
            "drill_origin_admission_invalid")
    require(type(minimum_remaining) is int
            and minimum_remaining in {0, MINIMUM_REMAINING_SECONDS},
            "drill_origin_admission_invalid")
    inputs = Path(inputs)
    references, expiries = {}, {}
    for name, (request_file, issue_file, entry_id) in ISSUE_FILES.items():
        require(request_file in index["files"] and issue_file in index["files"],
                "drill_origin_receipt_missing")
        require(read_json(child(inputs, request_file)) == {"entry_id": entry_id},
                "drill_origin_receipt_invalid")
        issued = read_json(child(inputs, issue_file))
        require(isinstance(issued, dict) and set(issued) == {"action", "receipt"}
                and issued["action"] == "issue" and isinstance(issued["receipt"], dict),
                "drill_origin_receipt_invalid")
        receipt = issued["receipt"]
        require(set(receipt) == {"assertion_ref", "expires_at", "mode"}
                and type(receipt["assertion_ref"]) is str
                and ORIGIN.fullmatch(receipt["assertion_ref"]) is not None
                and receipt["mode"] == "service_https",
                "drill_origin_receipt_invalid")
        references[name] = receipt["assertion_ref"]
        expiries[name] = _epoch(receipt["expires_at"])
    require(references["config"] != references["actor"],
            "drill_origin_binding_invalid")
    require(_env_origin(inputs) == references["config"],
            "drill_origin_binding_invalid")
    assertions = {row["id"]: row for row in index["assertions"]}
    for assertion_id, name in (
        ("model_revoked", "config"),
        ("forgotten", "actor"),
        ("source_revoked", "actor"),
        ("unknown_no_resend", "actor"),
    ):
        require(assertion_id in assertions
                and _reference(assertions[assertion_id]) == references[name],
                "drill_origin_binding_invalid")
    unknown = assertions["unknown_no_resend"].get("request_json")
    require(isinstance(unknown, dict) and "deadline_at" in unknown,
            "drill_origin_deadline_invalid")
    deadline = _epoch(unknown["deadline_at"])
    rows, entries = _source_rows(source, references)
    current = time.time() if now is None else now
    effective_expiries = {}
    for name, (_, _, entry_id) in ISSUE_FILES.items():
        row = rows[name]
        entry_digest = hashlib.sha256(json.dumps(
            entries[entry_id], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        require(row is not None and row[0] == entry_id
                and row[1] == entry_digest and row[3] == 0
                and type(row[2]) is float
                and row[2] + 0.00001 >= expiries[name]
                and row[2] - current <= 300.00001,
                "drill_origin_source_mismatch")
        effective_expiries[name] = row[2]
    remaining = min(*effective_expiries.values(), deadline) - current
    require(remaining > 0 and remaining >= minimum_remaining,
            "drill_origin_lifetime_insufficient")
    return {
        "minimum_remaining_seconds": MINIMUM_REMAINING_SECONDS,
        "remaining_at_check_seconds": int(remaining),
        "source_receipts_and_bindings_verified": True,
    }
