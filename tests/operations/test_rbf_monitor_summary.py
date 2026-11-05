import importlib.util
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


path = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "rbf_monitor_summary.py"
spec = importlib.util.spec_from_file_location("rbf_monitor_summary", path)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
LEGACY_SOURCES_SCHEMA = (
    "CREATE TABLE sources(name TEXT PRIMARY KEY,path TEXT NOT NULL,device INTEGER,inode INTEGER,"
    "offset INTEGER NOT NULL DEFAULT 0,tail_hash TEXT,resets INTEGER NOT NULL DEFAULT 0,"
    "partial INTEGER NOT NULL DEFAULT 0,error TEXT)"
)


def append(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def test_incremental_views_deduplicate_and_keep_recovery_evidence(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("observations.jsonl", "issues.jsonl", "summary.sqlite"))
    first = {"observed_at": "2026-11-05T00:00:00Z", "status": "healthy",
             "models": {"charged_tokens": 100, "unknown_original_receipts": [
                 {"attempt_id": "original-1", "status": "unknown"}]}}
    append(observations, first)
    append(observations, first)
    append(observations, {"observed_at": "2026-11-05T02:30:00Z", "status": "healthy",
                          "active_issue_fingerprints": ["issue-1"],
                          "models": {"actual_currency_cost": {"amount": 7, "currency": "USD"}}})
    append(issues, {"recorded_at": "2026-11-05T01:00:00Z", "fingerprint": "issue-1", "state": "open"})
    append(issues, {"recorded_at": "2026-11-05T02:00:00Z", "fingerprint": "issue-1", "state": "resolved",
                    "evidence_path": "details/resolved.json"})
    original = (observations.read_bytes(), issues.read_bytes())
    summary = monitor.summarize(observations, issues, state, as_of="2026-11-05T03:00:00Z", window_hours=1)
    assert summary["cumulative"]["observation_records"] == 2
    assert summary["rolling"]["observation_records"] == 1
    assert summary["new"]["observations"]["duplicates"] == 1
    assert summary["new"]["issues"]["new_fingerprints"] == ["issue-1"]
    assert summary["issues"][0]["state"] == "conflicting_evidence"
    assert summary["recovery_evidence"][0]["evidence_path"] == "details/resolved.json"
    assert summary["latest_observation"]["business_progress"] == "unknown"
    assert summary["latest_observation"]["billing"]["actual_currency_cost"]["knowledge"] == "unknown"
    assert summary["input_coverage"]["complete"]
    assert (observations.read_bytes(), issues.read_bytes()) == original
    again = monitor.summarize(observations, issues, state, as_of="2026-11-05T03:00:00Z", window_hours=1)
    assert again["new"]["observations"]["read"] == 0
    assert again["new"]["issues"]["read"] == 0
    assert monitor.last_summary(state) == again

    append(issues, {"recorded_at": "2026-11-05T02:45:00Z", "fingerprint": "issue-1", "state": "open"})
    reopened = monitor.summarize(observations, issues, state, as_of="2026-11-05T03:00:00Z")
    assert reopened["issues"][0]["state"] == "open"
    assert reopened["recovery_evidence"][0]["current_state"] == "open"
    assert reopened["new"]["issues"]["new_fingerprints"] == []


def test_partial_lines_and_rotations_are_reported_without_losing_original_data(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    issues.write_text("", encoding="utf-8")
    observations.write_text('{"observed_at":"2026-11-05T00:00:00Z"}', encoding="utf-8")
    first = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert first["input_coverage"]["sources"]["observations"]["partial_line"]
    assert first["cumulative"]["observation_records"] == 0
    with observations.open("a", encoding="utf-8") as stream:
        stream.write("\n")
    second = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert second["cumulative"]["observation_records"] == 1
    assert second["input_coverage"]["complete"]
    observations.write_text("{}\n", encoding="utf-8")
    rotated = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert rotated["input_coverage"]["sources"]["observations"]["resets"] == 1
    assert rotated["input_coverage"]["sources"]["observations"]["invalid_records"] == 1
    assert rotated["cumulative"]["observation_records"] == 1
    assert not rotated["input_coverage"]["complete"]


def test_last_summary_cannot_overwrite_a_remembered_journal(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    observations.write_text("", encoding="utf-8")
    issues.write_text("", encoding="utf-8")
    monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    with pytest.raises(SystemExit):
        monitor.main(["--state", str(state), "--show-last", "--output", str(issues)])
    assert issues.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("legacy_schema", (False, True))
def test_large_file_ids_are_lossless_and_identity_changes_reset_the_cursor(tmp_path, monkeypatch, legacy_schema):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    append(observations, {"observed_at": "2026-11-05T00:00:00Z"})
    issues.write_text("", encoding="utf-8")
    original = (observations.read_bytes(), issues.read_bytes())
    ids = {"device": 2**80 + 19, "inode": 2**120 + 31}
    if legacy_schema:
        with sqlite3.connect(state) as conn:
            conn.execute(LEGACY_SOURCES_SCHEMA)

    def fstat(fd):
        return SimpleNamespace(st_dev=ids["device"], st_ino=ids["inode"], st_size=os.fstat(fd).st_size)

    monkeypatch.setattr(monitor, "os", SimpleNamespace(fstat=fstat, replace=os.replace))
    first = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert first["cumulative"]["observation_records"] == 1
    with sqlite3.connect(state) as conn:
        rows = conn.execute("SELECT device,inode,typeof(device),typeof(inode) FROM sources").fetchall()
    assert rows == [(f"id:{ids['device']}", f"id:{ids['inode']}", "text", "text")] * 2
    again = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert again["new"]["observations"]["read"] == 0
    assert again["input_coverage"]["sources"]["observations"]["resets"] == 0

    # These adjacent values cannot be distinguished by floating-point storage.
    ids["inode"] += 1
    rotated = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert rotated["input_coverage"]["sources"]["observations"]["resets"] == 1
    assert rotated["new"]["observations"]["duplicates"] == 1
    assert (observations.read_bytes(), issues.read_bytes()) == original


def test_legacy_integer_cache_resumes_without_false_rotation(tmp_path, monkeypatch):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    append(observations, {"observed_at": "2026-11-05T00:00:00Z"})
    issues.write_text("", encoding="utf-8")
    # Match the old schema's INTEGER affinity, including its other columns.
    with sqlite3.connect(state) as conn:
        conn.execute(LEGACY_SOURCES_SCHEMA)

    def fstat(fd):
        return SimpleNamespace(st_dev=7, st_ino=11, st_size=os.fstat(fd).st_size)

    monkeypatch.setattr(monitor, "os", SimpleNamespace(fstat=fstat, replace=os.replace))
    monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    with sqlite3.connect(state) as conn:
        conn.execute("UPDATE sources SET device=7,inode=11")
        assert conn.execute("SELECT DISTINCT typeof(device),typeof(inode) FROM sources").fetchall() == [("integer", "integer")]
    append(observations, {"observed_at": "2026-11-05T00:30:00Z"})
    resumed = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert resumed["new"]["observations"]["read"] == 1
    assert resumed["new"]["observations"]["duplicates"] == 0
    assert resumed["input_coverage"]["sources"]["observations"]["resets"] == 0
    assert resumed["cumulative"]["observation_records"] == 2
    with sqlite3.connect(state) as conn:
        assert conn.execute("SELECT DISTINCT device,inode,typeof(device),typeof(inode) FROM sources").fetchall() == [
            ("id:7", "id:11", "text", "text")]
