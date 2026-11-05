import importlib.util
import json
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "rbf_monitor_summary.py"
spec = importlib.util.spec_from_file_location("rbf_monitor_summary", path)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


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
