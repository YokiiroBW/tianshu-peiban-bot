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


def business_fact(identifier, version, state, *, fingerprint="memory-gap", execution=None, evidence=True):
    return {"fingerprint": fingerprint,
            "target": {"domain": "memory", "kind": "batch", "id": identifier,
                       "version": version, "data_generation": "generation-1"},
            "business": {"state": state, "evidence": {"kind": "domain_commit", "ref": "record:" + identifier}
                         if evidence else None},
            "original_receipt": {"attempt_id": "attempt-" + identifier, "status": "unknown", "cost": None},
            "execution": execution}


def complete_targets():
    return {"target_sets": {"memory-gap": {"complete": True,
        "evidence": {"kind": "domain_scan", "ref": "target-set-1"}}}}


def test_business_closure_requires_the_exact_target_and_preserves_original_unknown(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    append(issues, {"recorded_at": "2026-11-05T00:00:00Z", "fingerprint": "memory-gap", "state": "open",
                    "event_kind": "occurred", "unscoped_occurrences": 0, "uncorrelated_occurrences": 0})
    append(observations, {"observed_at": "2026-11-05T00:10:00Z",
                          "business_observations": [business_fact("old-batch", "v1", "blocked")]})
    append(observations, {"observed_at": "2026-11-05T00:20:00Z", "business_observations": [
        business_fact("old-batch", "v1", "blocked"), business_fact("old-batch", "v2", "recovered"),
        business_fact("new-batch", "v2", "recovered")]})
    first = monitor.summarize(observations, issues, state, as_of="2026-11-05T00:25:00Z")
    issue = first["issues"][0]
    assert issue["business_state"] == "blocked"
    assert {(item["target"]["id"], item["target"]["version"]): item["business_state"]
            for item in issue["targets"]} == {("old-batch", "v1"): "blocked",
            ("old-batch", "v2"): "recovered_original_unknown", ("new-batch", "v2"): "recovered_original_unknown"}

    append(observations, {"observed_at": "2026-11-05T00:30:00Z",
        "coverage": {"target_sets": {"memory-gap": {"complete": True}}}, "business_observations": [
        business_fact("old-batch", "v1", "recovered"), business_fact("old-batch", "v2", "recovered"),
        business_fact("new-batch", "v2", "recovered")]})
    one_page = monitor.summarize(observations, issues, state, as_of="2026-11-05T00:32:00Z")
    assert one_page["issues"][0]["business_state"] == "unknown"
    assert one_page["issues"][0]["target_coverage"]["complete"] is None
    append(observations, {"observed_at": "2026-11-05T00:33:00Z", "coverage": complete_targets(),
        "business_observations": [business_fact("old-batch", "v1", "recovered"),
                                  business_fact("old-batch", "v2", "recovered"),
                                  business_fact("new-batch", "v2", "recovered")]})
    append(issues, {"recorded_at": "2026-11-05T00:35:00Z", "fingerprint": "memory-gap", "state": "open",
                    "event_kind": "evaluated"})
    recovered = monitor.summarize(observations, issues, state, as_of="2026-11-05T00:40:00Z")
    issue = recovered["issues"][0]
    assert issue["business_state"] == "recovered_original_unknown"
    old = next(item for item in issue["targets"] if item["target"]["id"] == "old-batch"
               and item["target"]["version"] == "v1")
    assert old["original_receipt"]["status"] == "unknown"
    assert old["original_receipt"]["cost"] == {"knowledge": "unknown", "amount": None, "currency": None}
    assert issue["last_evaluated"] == "2026-11-05T00:35:00+00:00"
    assert issue["last_recurred"] == "2026-11-05T00:00:00+00:00"


def test_observer_lease_does_not_count_as_execution_or_coverage(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    issues.write_text("", encoding="utf-8")
    idle = {"live_job": None, "valid_due": None, "consumer": None, "consumer_lease_until": None,
            "observer_lease": "2026-11-05T02:00:00Z"}
    due = {**idle, "valid_due": "2026-11-05T01:30:00Z"}
    job = {**idle, "live_job": {"job_id": 42, "state": "doing", "valid": True}}
    lease = {**idle, "consumer": {"id": "memory", "state": "running", "revision": 4,
                                  "last_progress_at": "2026-11-05T00:29:00Z"},
             "consumer_lease_until": "2026-11-05T01:30:00Z"}
    completed_consumer = {**lease, "consumer": {"id": "memory", "state": "done", "revision": 4}}
    invalid_due = {**idle, "valid_due": "not-a-time"}
    known_result = business_fact("known-result", "1", "recovered")
    known_result["original_receipt"]["status"] = "succeeded"
    known_cost = business_fact("known-cost", "1", "recovered")
    known_cost["original_receipt"].update(status="succeeded", cost={"knowledge": "known", "amount": 1, "currency": "USD"})
    append(observations, {"observed_at": "2026-11-05T00:30:00Z", "business_observations": [
        business_fact("observer", "1", "recovering", execution=idle),
        business_fact("due", "1", "recovering", execution=due),
        business_fact("job", "1", "recovering", execution=job),
        business_fact("consumer", "1", "recovering", execution=lease),
        business_fact("done-consumer", "1", "recovering", execution=completed_consumer),
        business_fact("invalid-due", "1", "recovering", execution=invalid_due),
        business_fact("waiting-for-sample", "1", "waiting_sample"),
        business_fact("withdrawn", "1", "terminal"),
        known_result, known_cost,
        business_fact("unproven", "1", "recovered", evidence=False),
        business_fact("unsampled", "1", "recovering")]})
    result = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    targets = {item["target"]["id"]: item for item in result["business"]["targets"]}
    assert targets["observer"]["business_state"] == "blocked"
    assert targets["done-consumer"]["business_state"] == "blocked"
    assert targets["invalid-due"]["business_state"] == "unknown"
    assert targets["waiting-for-sample"]["business_state"] == "waiting_sample"
    assert targets["withdrawn"]["business_state"] == "terminal"
    assert targets["known-result"]["business_state"] == "recovered_original_unknown"  # Cost remains unknown.
    assert targets["known-result"]["original_receipt"]["status"] == "succeeded"
    assert targets["known-cost"]["business_state"] == "recovered"
    assert targets["observer"]["observer_lease_is_business_progress"] is False
    assert {targets[name]["business_state"] for name in ("due", "job", "consumer")} == {"recovering"}
    assert targets["consumer"]["execution"]["consumer"]["last_progress_at"] == "2026-11-05T00:29:00+00:00"
    assert targets["unproven"]["business_state"] == targets["unsampled"]["business_state"] == "unknown"
    assert result["input_coverage"]["complete"] is True
    assert result["coverage"]["producer"]["status"] == "unknown"
    assert result["coverage"]["real_samples"]["status"] == "unknown"


def test_query_cancellation_is_not_overridden_by_a_target_or_empty_page(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    issues.write_text("", encoding="utf-8")
    append(observations, {"observed_at": "2026-11-05T00:00:00Z", "coverage": {
        "producer": {"complete": False, "missing": ["memory"]}, "real_samples": {"count": 0},
        "queries": {"memory_original_observation": {"scope": "overall", "error_type": "QueryCanceled",
                                                       "overall_complete": True}}}})
    append(observations, {"observed_at": "2026-11-05T00:30:00Z", "coverage": {
        "real_samples": {"count": 0}, "queries": {
            "memory_original_observation": {"scope": "target", "status": "found"},
            "memory_original_target": {"scope": "overall", "status": "found", "overall_complete": True},
            "memory_original_history": {"scope": "page", "status": "complete", "page_complete": True}}}})
    earlier = monitor.summarize(observations, issues, state, as_of="2026-11-05T00:15:00Z")
    assert earlier["coverage"]["producer"]["status"] == "partial"
    assert earlier["coverage"]["producer"]["missing"] == ["memory"]
    result = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    coverage = result["coverage"]
    assert coverage["ledger_input"]["complete"] is True
    assert coverage["producer"]["status"] == "unknown"
    assert coverage["real_samples"]["status"] == "waiting_sample"
    assert coverage["queries"]["memory_original_observation"]["status"] == "unknown"
    assert coverage["queries"]["memory_original_observation"]["overall_complete"] is None
    assert coverage["queries_by_scope"]["memory_original_observation"]["target"]["status"] == "found"
    assert coverage["queries"]["memory_original_target"]["status"] == "found"
    assert coverage["queries"]["memory_original_target"]["scope"] == "target"
    assert coverage["queries"]["memory_original_target"]["overall_complete"] is None
    assert coverage["queries"]["memory_original_history"]["page_complete"] is True
    assert coverage["queries"]["memory_original_history"]["overall_complete"] is None


def test_unscoped_and_uncorrelated_events_cannot_close_an_issue(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    append(issues, {"recorded_at": "2026-11-05T00:00:00Z", "fingerprint": "memory-gap", "state": "resolved",
                    "unscoped_occurrences": 0, "uncorrelated_occurrences": 0})
    unscoped = business_fact("other", "1", "blocked")
    del unscoped["target"]
    uncorrelated = business_fact("another", "1", "blocked", fingerprint=None)
    append(observations, {"observed_at": "2026-11-05T00:30:00Z", "coverage": complete_targets(),
                          "business_observations": [business_fact("original", "1", "recovered"),
                                                    unscoped, uncorrelated]})
    result = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert result["issues"][0]["business_state"] == "unknown"
    assert len(result["business"]["unscoped"]) == 1
    assert len(result["business"]["uncorrelated"]) == 1
    assert result["business"]["uncorrelated"][0]["fingerprint"] is None
    append(issues, {"recorded_at": "2026-11-05T00:40:00Z", "fingerprint": "memory-gap", "state": "resolved",
                    "unscoped_occurrences": 0, "uncorrelated_occurrences": 0})
    resolved = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert resolved["issues"][0]["business_state"] == "recovered_original_unknown"
    assert len(resolved["business"]["unscoped"]) == 1  # Preserve the historical, unattributed fact.


def test_recovery_observation_does_not_erase_or_merge_original_receipt_ids(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    issues.write_text("", encoding="utf-8")
    original = business_fact("batch-a", "1", "blocked")
    recovered = business_fact("batch-a", "1", "recovered")
    del recovered["original_receipt"]  # No new physical observation is not a known result.
    append(observations, {"observed_at": "2026-11-05T00:00:00Z", "business_observations": [original]})
    append(observations, {"observed_at": "2026-11-05T00:30:00Z", "business_observations": [recovered]})
    summary = monitor.summarize(observations, issues, state, as_of="2026-11-05T00:45:00Z")
    item = summary["business"]["targets"][0]
    assert item["business_state"] == "recovered_original_unknown"
    assert item["original_receipt"]["attempt_id"] == "attempt-batch-a"
    assert item["original_receipt"]["status_as_of"] == "2026-11-05T00:00:00+00:00"

    conflict = business_fact("batch-a", "1", "recovered")
    conflict["original_receipt"]["attempt_id"] = "different-original"
    append(observations, {"observed_at": "2026-11-05T00:50:00Z", "business_observations": [conflict]})
    summary = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    item = summary["business"]["targets"][0]
    assert item["original_receipt_identity_conflict"] is True
    assert {receipt["attempt_id"] for receipt in item["original_receipts"]} == {
        "attempt-batch-a", "different-original"}
    assert item["original_receipt"]["attempt_id"] is None
    assert item["business_state"] == "recovered_original_unknown"


def test_old_derived_cache_is_honest_and_can_be_rebuilt_from_unchanged_journals(tmp_path):
    observations, issues, state = (tmp_path / name for name in ("o.jsonl", "i.jsonl", "state.sqlite"))
    append(issues, {"recorded_at": "2026-11-05T00:00:00Z", "fingerprint": "memory-gap", "state": "open",
                    "event_kind": "recurred", "unscoped_occurrences": 0, "uncorrelated_occurrences": 0})
    append(observations, {"observed_at": "2026-11-05T00:30:00Z", "coverage": complete_targets(),
                          "business_observations": [business_fact("original", "1", "recovered")]})
    originals = (observations.read_bytes(), issues.read_bytes())
    monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    with sqlite3.connect(state) as conn:
        for sequence, payload in conn.execute("SELECT sequence,payload FROM records").fetchall():
            item = json.loads(payload)
            for name in ("fact_projection_version", "business_observations", "coverage", "event_kind",
                         "unscoped_occurrences", "uncorrelated_occurrences"):
                item.pop(name, None)
            conn.execute("UPDATE records SET payload=? WHERE sequence=?", (json.dumps(item), sequence))
    old = monitor.summarize(observations, issues, state, as_of="2026-11-05T01:00:00Z")
    assert old["input_coverage"]["complete"] is True
    assert old["coverage"]["fact_projection"]["complete"] is False
    assert old["coverage"]["fact_projection"]["legacy_issue_records"] == 1
    assert old["coverage"]["fact_projection"]["legacy_observation_records"] == 1
    assert old["issues"][0]["business_state"] == "unknown"
    rebuilt = monitor.summarize(observations, issues, tmp_path / "rebuilt.sqlite", as_of="2026-11-05T01:00:00Z")
    assert rebuilt["coverage"]["fact_projection"]["complete"] is True
    assert rebuilt["issues"][0]["business_state"] == "recovered_original_unknown"
    assert (observations.read_bytes(), issues.read_bytes()) == originals
