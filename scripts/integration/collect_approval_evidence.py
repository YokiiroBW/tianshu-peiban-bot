"""Collect the fixed transport/user-approval slice, preserving superseded runs and old Git evidence."""

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from collect_source_evidence import barrier_examples, compact

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/ts050-approved"
HISTORY = "f6354b6d1acaa8e9121985f6ea92b021babf45b2"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run_record(directory):
    environment = json.loads((directory / "environment.json").read_text("utf-8"))
    log = (directory / "unittest.txt").read_text("utf-8")
    match = re.search(r"Ran (\d+) tests? in ([\d.]+)s", log)
    names = re.findall(r"^(test_\w+) \(test_ts050_acceptance[^\n]+\) \.\.\.", log, re.M)
    assert match and re.search(r"^OK$", log, re.M) and environment["test_exit_code"] == 0
    assert len(names) == int(match[1]) and len(set(names)) == len(names)
    return {
        "environment": environment,
        "tests": int(match[1]),
        "seconds": float(match[2]),
        "names": names,
        "log_sha256": sha((directory / "unittest.txt").read_bytes()),
    }


def main():
    latest = run_record(RUNTIME)
    initial_path = RUNTIME / "diagnostics/initial-four"
    runs = [latest]
    retained = {}
    if initial_path.exists() and latest["tests"] == 1:
        initial = run_record(initial_path)
        assert initial["environment"]["pins"] == latest["environment"]["pins"]
        assert initial["environment"]["distributions"] == latest["environment"]["distributions"]
        manifest = {
            path.replace("\\", "/"): value
            for path, value in json.loads((initial_path / "sha256.json").read_text("utf-8")).items()
        }
        assert all(
            sha((initial_path / path).read_bytes()) == value for path, value in manifest.items()
        )
        for name in initial["names"]:
            if name not in latest["names"]:
                expected = manifest["results/" + name + ".json"]
                assert sha((RUNTIME / "results" / (name + ".json")).read_bytes()) == expected
                retained[name] = expected
        initial["retained_names"] = list(retained)
        initial["superseded_names"] = latest["names"]
        runs = [initial, latest]
    names = list(retained) + latest["names"]
    assert len(names) == 4
    cases, totals = (
        [],
        {
            "models": 0,
            "sends": 0,
            "blocked_generated_replies": 0,
            "owner_http": 0,
            "listener_checks": 0,
            "user_actions": 0,
        },
    )
    for name in names:
        path = RUNTIME / "results" / (name + ".json")
        raw = path.read_bytes()
        data = json.loads(raw)
        assert data["scenario"] == name and not data["cleanup"]["failures"]
        assert data["cleanup"]["gateway_process_stopped"]
        assert all(r["transport"] == "https" for r in data["wire"])
        model_texts = Counter(
            m["response"]["choices"][0]["message"]["content"] for m in data["models"]
        )
        channel_texts = Counter(r["text"] for r in data["channel_requests"])
        assert not (channel_texts - model_texts)
        blocked = sum((model_texts - channel_texts).values())
        assert blocked == (1 if "owner_curator" in name else 0)
        totals["models"] += len(data["models"])
        totals["sends"] += len(data["channel_requests"])
        totals["blocked_generated_replies"] += blocked
        totals["owner_http"] += len(data["wire"])
        totals["listener_checks"] += data["cleanup"]["listeners_checked"]
        totals["user_actions"] += len(data.get("local_user_actions", []))
        if "same_idle" in name:
            evidence = data["checks"][-1]
            events = evidence["transport"]

            def at(role, event):
                return next(e["at"] for e in events if e["role"] == role and e["event"] == event)

            assert (
                at("parallel", "http11.receive_response_headers.started")
                < at("target", "http11.receive_response_headers.failed")
                <= at("target", "http11.receive_response_headers.complete")
                <= at("parallel", "http11.receive_response_headers.complete")
            )
        cases.append(
            {
                "scenario": name,
                "result": "passed",
                "raw_result_sha256": sha(raw),
                "checks": data["checks"],
                "cleanup": data["cleanup"],
                "user_actions": data.get("local_user_actions", []),
                "models": data["models"],
                "channel_requests": data["channel_requests"],
                "channel_receipts": data["channel_receipts"],
                "core_turns": [
                    {
                        k: t.get(k)
                        for k in (
                            "id",
                            "scope",
                            "phase",
                            "delivery_state",
                            "failure",
                            "model_calls",
                            "scope_version",
                            "profile_checks",
                            "route_receipt",
                        )
                    }
                    for t in data["core"]["turns"]
                ],
                "barriers": barrier_examples(data["wire"]),
                "http_timeline": [
                    compact(r) for r in sorted(data["wire"], key=lambda r: r["started"])
                ],
            }
        )
    historical = {}
    old_files = subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-tree",
            "-r",
            "--name-only",
            HISTORY,
            "docs/development/evidence",
        ],
        text=True,
    ).splitlines()
    for relative in old_files:
        original = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", HISTORY + ":" + relative]
        )
        assert (ROOT / relative).read_bytes().replace(b"\r\n", b"\n") == original
        historical[relative] = sha(original)
    output = {
        "task": "TS-050",
        "slice": "fixed_query_and_real_user_approval",
        "status": "targeted_verification_passed_full_matrix_pending_coordinator",
        "distinct_scenarios": 4,
        "runs": runs,
        "environment": latest["environment"],
        "totals": totals,
        "historical_git_commit": HISTORY,
        "historical_git_bytes_sha256": historical,
        "redaction": "Assertion, approval and confirmation references are SHA256; credentials omitted. Wire hashes precede redaction.",
        "scenarios": cases,
    }
    encoded = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    for forbidden in ("Bearer ", "BEGIN PRIVATE KEY", "local-synthetic-user-", "synthetic-source-"):
        assert forbidden not in encoded
    target = ROOT / "docs/development/evidence/TS-050-approved-trace.json"
    target.write_text(encoded, "utf-8", newline="\n")
    print(
        json.dumps(
            {
                "totals": totals,
                "historical_files_unchanged": len(historical),
                "bytes": target.stat().st_size,
            }
        )
    )


if __name__ == "__main__":
    main()
