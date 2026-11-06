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
  name TEXT PRIMARY KEY, path TEXT NOT NULL, device TEXT, inode TEXT,
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


def _time(value):
    try:
        return parse_time(value).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _target(value):
    if not isinstance(value, dict):
        return None
    result = {key: identifier(value.get(key)) for key in ("domain", "kind", "id", "version", "data_generation")}
    return result if all(result.values()) else None


def _evidence(value):
    if isinstance(value, str):
        return identifier(value)
    if not isinstance(value, dict):
        return None
    kind = identifier(value.get("kind"))
    reference = identifier(value.get("ref")) or evidence_path(value.get("path"))
    return {"kind": kind, "ref": reference} if kind and reference else None


def _currency_cost(value):
    if (isinstance(value, dict) and type(value.get("amount")) in (int, float)
            and (type(value["amount"]) is int or math.isfinite(value["amount"])) and value["amount"] >= 0
            and identifier(value.get("currency"))
            and (value.get("knowledge") == "known" or value.get("metering") == "reported")):
        return {"knowledge": "known", "amount": value["amount"], "currency": value["currency"]}
    return {"knowledge": "unknown", "amount": None, "currency": None}


def _execution(value):
    if not isinstance(value, dict):
        return None
    result, invalid = {}, []
    if "live_job" in value:
        job = value["live_job"]
        if isinstance(job, dict):
            job_id = job.get("job_id")
            if type(job_id) is not int or job_id < 0:
                job_id = identifier(job_id)
            result["live_job"] = {"job_id": job_id, "state": identifier(job.get("state")),
                                  "valid": job.get("valid") if type(job.get("valid")) is bool else None}
            if (job_id is None or result["live_job"]["valid"] is None
                    or result["live_job"]["valid"] and result["live_job"]["state"] not in {"todo", "doing"}):
                invalid.append("live_job")
        else:
            result["live_job"] = None
            if job is not None:
                invalid.append("live_job")
    for name in ("valid_due", "consumer_lease_until"):
        if name in value:
            result[name] = _time(value[name])
            if value[name] is not None and result[name] is None:
                invalid.append(name)
    if "consumer" in value:
        item = value["consumer"]
        if isinstance(item, dict):
            result["consumer"] = {"id": identifier(item.get("id")), "state": identifier(item.get("state")),
                                  "revision": _scalar(item.get("revision")),
                                  "last_progress_at": _time(item.get("last_progress_at"))}
            if result["consumer"]["id"] is None or result["consumer"]["state"] is None:
                invalid.append("consumer")
        else:
            result["consumer"] = None
            if item is not None:
                invalid.append("consumer")
    if "observer_lease" in value:
        lease = value["observer_lease"]
        result["observer_lease"] = {"until": _time(lease.get("until"))} if isinstance(lease, dict) else {"until": _time(lease)}
    result["invalid_fields"] = invalid
    return result


def _business_observation(value):
    if not isinstance(value, dict):
        return None
    business = value.get("business") if isinstance(value.get("business"), dict) else {}
    states = {"blocked", "recovering", "recovered", "waiting_sample", "terminal", "unknown"}
    state = business.get("state")
    state = state if isinstance(state, str) and state in states else "unknown"
    receipt = value.get("original_receipt") if isinstance(value.get("original_receipt"), dict) else {}
    status = receipt.get("status")
    status = status if isinstance(status, str) and status in {
        "queued", "running", "succeeded", "failed", "cancelled", "unknown"} else None
    attempt = identifier(receipt.get("attempt_id"))
    return {"fingerprint": identifier(value.get("fingerprint")), "target": _target(value.get("target")),
            "business": {"state": state, "reason": identifier(business.get("reason")),
                         "evidence": _evidence(business.get("evidence"))},
            "original_receipt": {"attempt_id": attempt, "status": status if attempt else None,
                                 "unscoped_status": status if not attempt else None,
                                 "cost": _currency_cost(receipt.get("cost") if attempt else None)},
            "execution": _execution(value.get("execution"))}


def _coverage_facts(value):
    value = value if isinstance(value, dict) else {}
    producer = value.get("producer") if isinstance(value.get("producer"), dict) else {}
    complete = producer.get("complete") if type(producer.get("complete")) is bool else None
    missing = producer.get("missing") if isinstance(producer.get("missing"), list) else []
    if missing and complete is True:
        complete = False
    producer = {"status": "complete" if complete is True else "partial" if complete is False else "unknown",
                "complete": complete, "missing": [name for item in missing if (name := identifier(item))]}
    samples = value.get("real_samples") if isinstance(value.get("real_samples"), dict) else {}
    count = samples.get("count") if type(samples.get("count")) is int and samples["count"] >= 0 else None
    samples = {"status": "observed" if count else "waiting_sample" if count == 0 else "unknown", "count": count}
    target_sets = {}
    for name, item in (value.get("target_sets") or {}).items() if isinstance(value.get("target_sets"), dict) else ():
        if not identifier(name) or not isinstance(item, dict):
            continue
        evidence = _evidence(item.get("evidence"))
        complete = item.get("complete") if type(item.get("complete")) is bool else None
        if complete is True and not evidence:
            complete = None
        target_sets[name] = {"complete": complete, "evidence": evidence}
    queries = {}
    for name, item in (value.get("queries") or {}).items() if isinstance(value.get("queries"), dict) else ():
        if not identifier(name) or not isinstance(item, dict):
            continue
        error = identifier(item.get("error_type"))
        status = item.get("status")
        if item.get("error_type") is not None or not isinstance(status, str) or status == "unknown":
            status = "unknown"
        elif status not in {"sampled", "found", "absent", "complete"}:
            status = "unknown"
        # A page or target lookup never proves whole-query coverage, even if
        # the page itself is complete or contains no rows.
        scope = item.get("scope")
        scope = scope if isinstance(scope, str) and scope in {"page", "target", "overall"} else "page"
        if status in {"found", "absent"}:
            scope = "target"
        if scope != "overall" and status == "complete":
            status = "sampled"
        evidence = _evidence(item.get("evidence"))
        overall = None
        if scope == "overall" and status != "unknown" and item.get("overall_complete") is False:
            overall = False
            if status == "complete":
                status = "sampled"
        elif scope == "overall" and status == "complete" and item.get("overall_complete") is True and evidence:
            overall = True
        elif scope == "overall" and status == "complete":
            status = "unknown"
        queries[name] = {"status": status, "scope": scope, "error_type": error,
                         "sqlstate": identifier(item.get("sqlstate")),
                         "overall_complete": overall, "evidence": evidence,
                         "page_complete": item.get("page_complete") if status != "unknown" and type(item.get("page_complete")) is bool else None}
    return {"producer": producer, "real_samples": samples, "queries": queries, "target_sets": target_sets}


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
    cost = _currency_cost(models.get("actual_currency_cost"))
    facts = value.get("business_observations") if isinstance(value.get("business_observations"), list) else []
    facts = [fact for item in facts if (fact := _business_observation(item)) is not None]
    payload = {"fact_projection_version": 2,
               "status": identifier(value.get("status")), "source_sha": identifier(value.get("source_sha")),
               "generation": identifier(value.get("generation")), "evidence_path": evidence_path(value.get("evidence_path")),
               "active_issue_fingerprints": active, "status_changes": changes,
               "business_progress": identifier(business.get("business_progress")) or "unknown",
               "original_receipts": {"unknown_samples": unknown, "sampled_attempt_counts": attempt_counts},
               "billing": {"actual_currency_cost": cost}, "business_observations": facts,
               "coverage": _coverage_facts(value.get("coverage"))}
    return stamp, None, None, payload


def _issue(value):
    stamp = parse_time(value.get("recorded_at") or value.get("observed_at"))
    fingerprint = identifier(value.get("fingerprint"))
    state = identifier(value.get("state"))
    if not fingerprint or not state:
        raise ValueError("issue_identity_required")
    kind = value.get("event_kind")
    payload = {"fact_projection_version": 2,
               "fingerprint": fingerprint, "state": state, "phase": identifier(value.get("phase")),
               "evidence_path": evidence_path(value.get("evidence_path")),
               "event_kind": kind if isinstance(kind, str) and kind in {"evaluated", "occurred", "recurred", "resolved"} else None,
               "last_evaluated": _time(value.get("last_evaluated")),
               "last_recurred": _time(value.get("last_recurred"))}
    for name in ("unscoped_occurrences", "uncorrelated_occurrences"):
        count = value.get(name)
        payload[name] = count if type(count) is int and count >= 0 else None
    return stamp, fingerprint, state, payload


def _tail_hash(stream, offset):
    stream.seek(max(0, offset - 256))
    return hashlib.sha256(stream.read(min(offset, 256))).hexdigest()


def _insert_bad(conn, source, digest, reason):
    conn.execute("INSERT OR IGNORE INTO bad_lines(digest,source,reason) VALUES(?,?,?)",
                 (source + ":" + digest, source, reason))


def _file_id(value):
    # The prefix prevents SQLite's INTEGER affinity in existing indexes from
    # coercing a large decimal string to an imprecise REAL value.
    return "id:" + str(value)


def _same_file_id(stored, current):
    # Previously created indexes stored in-range identifiers as integers.
    return stored == _file_id(current) or (type(stored) is int and stored == current)


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
        if old and old["device"] is not None and (old["path"] != str(path)
                    or not _same_file_id(old["device"], stat.st_dev) or not _same_file_id(old["inode"], stat.st_ino)
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
                     (name, str(path), _file_id(stat.st_dev), _file_id(stat.st_ino),
                      offset, tail_hash, resets, int(partial)))
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
    issues, legacy = {}, 0
    for row in rows:
        item = json.loads(row["payload"])
        legacy += item.get("fact_projection_version") != 2
        current = issues.setdefault(row["fingerprint"], {"fingerprint": row["fingerprint"],
                                                          "first_seen": row["occurred_at"], "records": 0,
                                                          "last_resolved": None, "last_evaluated": None,
                                                          "last_recurred": None, "unscoped_occurrences": None,
                                                          "uncorrelated_occurrences": None,
                                                          "unscoped_occurrences_as_of": None,
                                                          "uncorrelated_occurrences_as_of": None})
        current.update(state=row["state"], last_seen=row["occurred_at"], evidence_path=item.get("evidence_path"))
        current["records"] += 1
        evaluated = item.get("last_evaluated") or (row["occurred_at"] if item.get("event_kind") == "evaluated" else None)
        recurred = item.get("last_recurred") or (row["occurred_at"] if item.get("event_kind") in {"occurred", "recurred"} else None)
        for name, stamp in (("last_evaluated", evaluated), ("last_recurred", recurred)):
            if stamp and stamp <= row["occurred_at"] and (not current[name] or stamp > current[name]):
                current[name] = stamp
        for name in ("unscoped_occurrences", "uncorrelated_occurrences"):
            if item.get(name) is not None:
                current[name] = item[name]
                current[name + "_as_of"] = row["occurred_at"]
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
                                                "last_evaluated": None, "last_recurred": None,
                                                "unscoped_occurrences": None, "uncorrelated_occurrences": None,
                                                "unscoped_occurrences_as_of": None, "uncorrelated_occurrences_as_of": None,
                                                "evidence_path": latest.get("evidence_path")})
            if current["state"] == "resolved" and observed_at > current["last_seen"]:
                current["state"] = "conflicting_evidence"
        for name, item in issues.items():
            item["reported_in_latest_observation"] = name in active
    else:
        for item in issues.values():
            item["reported_in_latest_observation"] = None
    return sorted(issues.values(), key=lambda item: item["fingerprint"]), legacy


def _after(value, end):
    return bool(value and value > end)


def _business_state(fact, *, end, current):
    business, receipt, execution = fact["business"], fact["original_receipt"], fact.get("execution")
    reported = business["state"]
    if not current or not business.get("evidence"):
        return "unknown"
    if reported == "recovered":
        receipts = fact.get("original_receipts", ()) or (receipt,)
        return ("recovered_original_unknown" if any(item.get("attempt_id") and
                (item.get("status") in {None, "unknown"} or item.get("cost", {}).get("knowledge") != "known")
                for item in receipts)
                else "recovered")
    if reported != "recovering":
        return reported
    if not isinstance(execution, dict):
        return "unknown"
    job = execution.get("live_job")
    live_job = bool(isinstance(job, dict) and job.get("valid") is True
                    and job.get("state") in {"todo", "doing"} and job.get("job_id") is not None)
    due = _after(execution.get("valid_due"), end)
    consumer = execution.get("consumer")
    lease = bool(isinstance(consumer, dict) and consumer.get("id") and consumer.get("state") in {"running", "doing"} and
                 _after(execution.get("consumer_lease_until"), end))
    if live_job or due or lease:
        return "recovering"
    fields = {"live_job", "valid_due", "consumer", "consumer_lease_until"}
    if fields.issubset(execution) and not execution.get("invalid_fields"):
        return "blocked"
    return "unknown"


def _business_views(conn, end, latest):
    rows = conn.execute("SELECT occurred_at,payload FROM records WHERE source='observations' AND occurred_at<=? "
                        "ORDER BY occurred_at,sequence", (end,)).fetchall()
    current = latest.get("observed_at") if latest else None
    targets, unscoped, query_scopes, legacy = {}, [], {}, 0
    for row in rows:
        payload = json.loads(row["payload"])
        legacy += payload.get("fact_projection_version") != 2
        for name, item in (payload.get("coverage") or {}).get("queries", {}).items():
            query_scopes.setdefault(name, {})[item["scope"]] = {**item, "observed_at": row["occurred_at"]}
        for fact in payload.get("business_observations", ()):
            target, fingerprint = fact.get("target"), fact.get("fingerprint")
            if not target:
                unscoped.append({**fact, "last_evaluated": row["occurred_at"], "unscoped": True,
                                 "uncorrelated": fingerprint is None})
                continue
            key = (fingerprint, *(target[name] for name in ("domain", "kind", "id", "version", "data_generation")))
            previous = targets.get(key, {})
            receipts = {item["attempt_id"]: item for item in previous.get("original_receipts", ())}
            incoming = fact["original_receipt"]
            attempt = incoming.get("attempt_id")
            if attempt:
                prior = receipts.get(attempt)
                receipt = {**incoming, "status_as_of": row["occurred_at"] if incoming.get("status") else None}
                if prior and incoming.get("status") is None:
                    receipt["status"] = prior["status"]
                    receipt["status_as_of"] = prior.get("status_as_of")
                receipts[attempt] = receipt
            originals = [receipts[name] for name in sorted(receipts)]
            merged = {**fact, "last_evaluated": row["occurred_at"], "original_receipts": originals,
                      "original_receipt_identity_conflict": len(originals) > 1,
                      "uncorrelated": fingerprint is None, "observer_lease_is_business_progress": False}
            if len(originals) == 1:
                merged["original_receipt"] = originals[0]
            elif len(originals) > 1:
                merged["original_receipt"] = {"attempt_id": None, "status": None,
                    "unscoped_status": None, "cost": _currency_cost(None)}
            merged["business_state"] = _business_state(merged, end=end, current=row["occurred_at"] == current)
            targets[key] = merged
    ordered = sorted(targets.values(), key=lambda item: tuple(
        item["target"][key] for key in ("domain", "kind", "id", "version", "data_generation")) + (item["fingerprint"] or "",))
    queries = {name: scopes.get("overall") or max(scopes.values(), key=lambda item: item["observed_at"])
               for name, scopes in query_scopes.items()}
    return {"targets": ordered, "unscoped": unscoped,
            "uncorrelated": [item for item in [*ordered, *unscoped] if item["uncorrelated"]]}, queries, query_scopes, legacy


def _attach_business(issues, business, target_sets):
    for issue in issues:
        issue["targets"] = [item for item in business["targets"] if item["fingerprint"] == issue["fingerprint"]]
        issue["target_coverage"] = target_sets.get(issue["fingerprint"], {"complete": None, "evidence": None})
        times = [item["last_evaluated"] for item in issue["targets"]]
        if issue.get("last_evaluated"):
            times.append(issue["last_evaluated"])
        issue["last_evaluated"] = max(times) if times else None
        unscoped_times = [item["last_evaluated"] for item in business["unscoped"]
                          if item.get("fingerprint") == issue["fingerprint"]]
        last_unscoped = max(unscoped_times) if unscoped_times else None
        unscoped_as_of = issue.get("unscoped_occurrences_as_of")
        unresolved_unscoped = bool(last_unscoped and (not unscoped_as_of or unscoped_as_of < last_unscoped))
        states = {item["business_state"] for item in issue["targets"]}
        if not states:
            issue["business_state"] = "unknown"
        elif "blocked" in states:
            issue["business_state"] = "blocked"
        elif "unknown" in states:
            issue["business_state"] = "unknown"
        elif (issue["target_coverage"]["complete"] is not True
              or issue.get("unscoped_occurrences") != 0 or issue.get("uncorrelated_occurrences") != 0
              or unresolved_unscoped):
            # Matching one scoped target cannot close other or unlinked events.
            issue["business_state"] = "unknown"
        elif "recovering" in states:
            issue["business_state"] = "recovering"
        elif "terminal" in states:
            issue["business_state"] = "terminal"
        elif "waiting_sample" in states:
            issue["business_state"] = "waiting_sample"
        elif "recovered_original_unknown" in states:
            issue["business_state"] = "recovered_original_unknown"
        else:
            issue["business_state"] = "recovered"


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
        views, legacy_issues = _latest_issues(conn, end, latest)
        business, queries, query_scopes, legacy_observations = _business_views(conn, end, latest)
        coverage = _coverage_facts(latest.get("coverage") if latest else None)
        _attach_business(views, business, coverage.get("target_sets", {}))
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
                   "coverage": {"ledger_input": {"complete": complete}, "producer": coverage["producer"],
                                "real_samples": coverage["real_samples"], "queries": queries,
                                "queries_by_scope": query_scopes,
                                "target_sets": coverage.get("target_sets", {}),
                                "fact_projection": {"complete": not (legacy_issues or legacy_observations),
                                    "scope": "indexed_records_only", "legacy_issue_records": legacy_issues,
                                    "legacy_observation_records": legacy_observations}},
                   "cumulative": {"basis": "deduplicated_records_through_as_of", **_counts(conn, None, end)},
                   "rolling": {"basis": "record_timestamps_in_window", **_counts(conn, start, end)},
                   "new": {"basis": "newly_ingested_this_run_regardless_of_timestamp", **new},
                   "latest_observation": latest, "issues": views, "business": business,
                   "recovery_evidence": [{"fingerprint": item["fingerprint"], "current_state": item["state"],
                                          "business_state": item["business_state"],
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
