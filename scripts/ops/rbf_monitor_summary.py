"""Incremental, read-only summaries of existing monitor JSONL journals.

The SQLite file is a disposable derived index. It contains hashes, source byte
positions and selected diagnostic facts, never a copy of the full observations.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from uuid import uuid4


MAX_LINE = 8 * 1024 * 1024
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  name TEXT PRIMARY KEY, path TEXT NOT NULL, device INTEGER, inode INTEGER,
  offset INTEGER NOT NULL DEFAULT 0, tail_hash TEXT, resets INTEGER NOT NULL DEFAULT 0,
  partial INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS records (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT, digest TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL, occurred_at TEXT NOT NULL, fingerprint TEXT, state TEXT,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_source_time ON records(source,occurred_at);
CREATE INDEX IF NOT EXISTS records_issue_time ON records(fingerprint,occurred_at);
CREATE TABLE IF NOT EXISTS bad_lines (
  digest TEXT PRIMARY KEY, source TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp_required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone_required")
    return result.astimezone(UTC)


def identifier(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def evidence_path(value):
    return value if isinstance(value, str) and len(value) <= 4096 and not any(ord(c) < 32 for c in value) else None


def _scalar(value):
    if type(value) is float and not math.isfinite(value):
        return None
    if value is None or type(value) in (bool, int, float):
        return value
    return identifier(value)


def _observation(value):
    stamp = parse_time(value.get("observed_at") or value.get("recorded_at"))
    active = value.get("active_issue_fingerprints")
    active = sorted({v for item in active if (v := identifier(item))}) if isinstance(active, list) else None
    changes = []
    fields = ("fingerprint", "change", "business_completion", "business_effect", "current_model_status",
              "in_budget", "previous", "current", "previous_occurrences", "current_occurrences",
              "reported_input_tokens", "reported_output_tokens")
    for change in value.get("status_changes", ()) if isinstance(value.get("status_changes"), list) else ():
        if not isinstance(change, dict):
            continue
        item = {key: safe for key in fields if key in change and (safe := _scalar(change[key])) is not None}
        ids = change.get("sampled_retried_succeeded_jobs")
        if isinstance(ids, list):
            item["sampled_retried_succeeded_jobs"] = [v for v in ids if type(v) is int][:100]
        if item:
            changes.append(item)
    business = value.get("business_samples") if isinstance(value.get("business_samples"), dict) else {}
    models = value.get("models") if isinstance(value.get("models"), dict) else {}
    unknown = []
    for item in models.get("unknown_original_receipts", ()) if isinstance(models.get("unknown_original_receipts"), list) else ():
        if isinstance(item, dict):
            unknown.append({key: safe for key in ("attempt_id", "function", "status", "dispatch", "error_code")
                            if (safe := identifier(item.get(key))) is not None})
    attempt_counts = []
    for item in models.get("last_35_minutes_at_snapshot", ()) if isinstance(models.get("last_35_minutes_at_snapshot"), list) else ():
        if (isinstance(item, dict) and identifier(item.get("function")) and identifier(item.get("status"))
                and type(item.get("count")) is int and item["count"] >= 0):
            attempt_counts.append({"function": item["function"], "status": item["status"], "count": item["count"]})
    cost = models.get("actual_currency_cost")
    # A quota/charged token count or an estimate is never a currency charge.
    # Preserve only an explicitly reported actual amount and currency.
    if (isinstance(cost, dict) and type(cost.get("amount")) in (int, float)
            and math.isfinite(cost["amount"]) and cost["amount"] >= 0
            and identifier(cost.get("currency"))
            and (cost.get("knowledge") == "known" or cost.get("metering") == "reported")):
        cost = {"knowledge": "known", "amount": cost["amount"], "currency": cost["currency"]}
    else:
        cost = {"knowledge": "unknown", "amount": None, "currency": None}
    payload = {"status": identifier(value.get("status")), "source_sha": identifier(value.get("source_sha")),
               "generation": identifier(value.get("generation")), "evidence_path": evidence_path(value.get("evidence_path")),
               "active_issue_fingerprints": active, "status_changes": changes,
               "business_progress": identifier(business.get("business_progress")) or "unknown",
               "original_receipts": {"unknown_samples": unknown, "sampled_attempt_counts": attempt_counts},
               "billing": {"actual_currency_cost": cost}}
    return stamp, None, None, payload


def _issue(value):
    stamp = parse_time(value.get("recorded_at") or value.get("observed_at"))
    fingerprint = identifier(value.get("fingerprint"))
    state = identifier(value.get("state"))
    if not fingerprint or not state:
        raise ValueError("issue_identity_required")
    payload = {"fingerprint": fingerprint, "state": state, "phase": identifier(value.get("phase")),
               "evidence_path": evidence_path(value.get("evidence_path"))}
    return stamp, fingerprint, state, payload


def _tail_hash(stream, offset):
    stream.seek(max(0, offset - 256))
    return hashlib.sha256(stream.read(min(offset, 256))).hexdigest()


def _insert_bad(conn, source, digest, reason):
    conn.execute("INSERT OR IGNORE INTO bad_lines(digest,source,reason) VALUES(?,?,?)",
                 (source + ":" + digest, source, reason))


def _ingest(conn, name, path):
    path = Path(path).resolve()
    old = conn.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
    try:
        stream = path.open("rb")
    except OSError:
        conn.execute("INSERT INTO sources(name,path,error) VALUES(?,?,?) "
                     "ON CONFLICT(name) DO UPDATE SET error=excluded.error", (name, str(path), "source_unavailable"))
        return {"read": 0, "duplicates": 0, "invalid": 0, "new_fingerprints": [],
                "requested_path": str(path)}
    with stream:
        stat = os.fstat(stream.fileno())
        offset = old["offset"] if old else 0
        resets = old["resets"] if old else 0
        if old and old["device"] is not None and (old["path"] != str(path) or old["device"] != stat.st_dev or old["inode"] != stat.st_ino
                    or stat.st_size < offset or old["tail_hash"] != _tail_hash(stream, offset)):
            offset, resets = 0, resets + 1
        stream.seek(offset)
        remaining = stat.st_size - offset
        inserted, duplicates, invalid, fingerprints, partial = 0, 0, 0, set(), False
        while remaining:
            line_start = stream.tell()
            line = stream.readline(min(remaining, MAX_LINE + 1))
            if not line:
                partial = True
                break
            remaining -= len(line)
            if not line.endswith(b"\n"):
                digest = hashlib.sha256(line)
                too_large = len(line) > MAX_LINE
                while remaining and not line.endswith(b"\n"):
                    line = stream.readline(min(remaining, MAX_LINE + 1))
                    if not line:
                        break
                    digest.update(line)
                    remaining -= len(line)
                    too_large = True
                if not line.endswith(b"\n"):
                    stream.seek(line_start)
                    partial = True
                    break
                _insert_bad(conn, name, digest.hexdigest(), "oversized_line" if too_large else "invalid_line")
                invalid += 1
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("object_required")
                stamp, fingerprint, state, payload = (_observation(value) if name == "observations" else _issue(value))
                normalized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                digest = hashlib.sha256((name + "\0" + normalized).encode()).hexdigest()
                prior_issue = (conn.execute("SELECT 1 FROM records WHERE source='issues' AND fingerprint=? LIMIT 1",
                                            (fingerprint,)).fetchone() if fingerprint else None)
                result = conn.execute("INSERT OR IGNORE INTO records(digest,source,occurred_at,fingerprint,state,payload) "
                                      "VALUES(?,?,?,?,?,?)", (digest, name, stamp.isoformat(), fingerprint, state,
                                      json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
                if result.rowcount:
                    inserted += 1
                    if fingerprint and prior_issue is None:
                        fingerprints.add(fingerprint)
                else:
                    duplicates += 1
            except (ValueError, TypeError, UnicodeError, OverflowError):
                _insert_bad(conn, name, hashlib.sha256(line).hexdigest(), "invalid_record")
                invalid += 1
        offset = stream.tell()
        tail_hash = _tail_hash(stream, offset)
        conn.execute("INSERT INTO sources(name,path,device,inode,offset,tail_hash,resets,partial,error) "
                     "VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(name) DO UPDATE SET path=excluded.path,"
                     "device=excluded.device,inode=excluded.inode,offset=excluded.offset,tail_hash=excluded.tail_hash,"
                     "resets=excluded.resets,partial=excluded.partial,error=NULL",
                     (name, str(path), stat.st_dev, stat.st_ino, offset, tail_hash, resets, int(partial)))
        return {"read": inserted, "duplicates": duplicates, "invalid": invalid,
                "new_fingerprints": sorted(fingerprints)}


def _counts(conn, start, end):
    where = "occurred_at<=?" if start is None else "occurred_at>? AND occurred_at<=?"
    args = (end,) if start is None else (start, end)
    rows = conn.execute("SELECT source,count(*) AS count,count(DISTINCT fingerprint) AS fingerprints "
                        "FROM records WHERE " + where + " GROUP BY source", args).fetchall()
    values = {row["source"]: dict(row) for row in rows}
    return {"observation_records": values.get("observations", {}).get("count", 0),
            "issue_records": values.get("issues", {}).get("count", 0),
            "issue_fingerprints": values.get("issues", {}).get("fingerprints", 0)}


def _latest_issues(conn, end, latest):
    rows = conn.execute("SELECT occurred_at,fingerprint,state,payload FROM records "
                        "WHERE source='issues' AND occurred_at<=? ORDER BY occurred_at,sequence", (end,)).fetchall()
    issues = {}
    for row in rows:
        item = json.loads(row["payload"])
        current = issues.setdefault(row["fingerprint"], {"fingerprint": row["fingerprint"],
                                                          "first_seen": row["occurred_at"], "records": 0,
                                                          "last_resolved": None})
        current.update(state=row["state"], last_seen=row["occurred_at"], evidence_path=item.get("evidence_path"))
        current["records"] += 1
        if row["state"] == "resolved":
            current["last_resolved"] = {"recorded_at": row["occurred_at"], "evidence_path": item.get("evidence_path"),
                                        "source": "monitor_issue_record"}
    active = latest.get("active_issue_fingerprints") if latest else None
    observed_at = latest.get("observed_at") if latest else None
    if active is not None:
        for name in active:
            current = issues.setdefault(name, {"fingerprint": name, "state": "reported_active",
                                                "first_seen": None, "last_seen": observed_at,
                                                "records": 0, "last_resolved": None,
                                                "evidence_path": latest.get("evidence_path")})
            if current["state"] == "resolved" and observed_at > current["last_seen"]:
                current["state"] = "conflicting_evidence"
        for name, item in issues.items():
            item["reported_in_latest_observation"] = name in active
    else:
        for item in issues.values():
            item["reported_in_latest_observation"] = None
    return sorted(issues.values(), key=lambda item: item["fingerprint"])


def summarize(observations, issues, state, *, as_of=None, window_hours=24):
    state, observations, issues = Path(state).resolve(), Path(observations).resolve(), Path(issues).resolve()
    if state in {observations, issues} or observations == issues:
        raise ValueError("state_and_sources_must_be_distinct")
    if type(window_hours) not in (int, float) or not 0 < window_hours <= 24 * 365:
        raise ValueError("invalid_window_hours")
    now = parse_time(as_of) if as_of else datetime.now(UTC)
    end, start = now.isoformat(), (now - timedelta(hours=window_hours)).isoformat()
    state.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(state, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        new = {"observations": _ingest(conn, "observations", observations),
               "issues": _ingest(conn, "issues", issues)}
        row = conn.execute("SELECT occurred_at,payload FROM records WHERE source='observations' AND occurred_at<=? "
                           "ORDER BY occurred_at DESC,sequence DESC LIMIT 1", (end,)).fetchone()
        latest = json.loads(row["payload"]) if row else None
        if latest is not None:
            latest["observed_at"] = row["occurred_at"]
        views = _latest_issues(conn, end, latest)
        inputs = {}
        for row in conn.execute("SELECT * FROM sources ORDER BY name"):
            invalid = conn.execute("SELECT count(*) FROM bad_lines WHERE source=?", (row["name"],)).fetchone()[0]
            inputs[row["name"]] = {"path": row["path"], "offset": row["offset"], "partial_line": bool(row["partial"]),
                                    "resets": row["resets"], "invalid_records": invalid, "error": row["error"]}
            if new[row["name"]].get("requested_path"):
                inputs[row["name"]]["requested_path"] = new[row["name"]]["requested_path"]
        complete = len(inputs) == 2 and all(not (item["partial_line"] or item["resets"] or item["invalid_records"] or item["error"])
                                          for item in inputs.values())
        summary = {"schema_version": "rbf.monitor-summary.v1", "as_of": end,
                   "window": {"start_exclusive": start, "end_inclusive": end, "hours": window_hours},
                   "input_coverage": {"complete": complete, "sources": inputs},
                   "cumulative": {"basis": "deduplicated_records_through_as_of", **_counts(conn, None, end)},
                   "rolling": {"basis": "record_timestamps_in_window", **_counts(conn, start, end)},
                   "new": {"basis": "newly_ingested_this_run_regardless_of_timestamp", **new},
                   "latest_observation": latest, "issues": views,
                   "recovery_evidence": [{"fingerprint": item["fingerprint"], "current_state": item["state"],
                                          **item["last_resolved"]} for item in views if item["last_resolved"]]}
        conn.execute("INSERT INTO meta(key,value) VALUES('last_summary',?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),))
        conn.commit()
        return summary


def last_summary(state):
    state = Path(state).resolve()
    if not state.is_file():
        raise ValueError("no_previous_summary")
    with sqlite3.connect(state) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='last_summary'").fetchone()
        if not row:
            raise ValueError("no_previous_summary")
        return json.loads(row[0])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations")
    parser.add_argument("--issues")
    parser.add_argument("--state", required=True, help="separate derived SQLite index")
    parser.add_argument("--output", help="optional separate JSON summary; stdout when omitted")
    parser.add_argument("--window-hours", type=float, default=24)
    parser.add_argument("--as-of", help="timezone-aware ISO timestamp; defaults to current UTC")
    parser.add_argument("--show-last", action="store_true", help="recover the last committed summary without advancing")
    args = parser.parse_args(argv)
    if not args.show_last and (not args.observations or not args.issues):
        parser.error("--observations and --issues are required unless --show-last is used")
    if args.output:
        output = Path(args.output).resolve()
        protected = {Path(args.state).resolve()}
        protected.update(Path(path).resolve() for path in (args.observations, args.issues) if path)
        if output in protected:
            parser.error("output, state and source files must be distinct")
    summary = (last_summary(args.state) if args.show_last else
               summarize(args.observations, args.issues, args.state, as_of=args.as_of, window_hours=args.window_hours))
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        # --show-last need not receive source arguments; protect the remembered
        # journal paths too, even on that recovery-only invocation.
        protected.update(Path(item["path"]).resolve() for item in
                         summary.get("input_coverage", {}).get("sources", {}).values() if item.get("path"))
        protected.update(Path(item["requested_path"]).resolve() for item in
                         summary.get("input_coverage", {}).get("sources", {}).values() if item.get("requested_path"))
        if output in protected:
            parser.error("output, state and source files must be distinct")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
            os.replace(temporary, output)
        finally:
            if temporary.exists():
                temporary.unlink()
    else:
        sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
