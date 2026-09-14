"""Summarize the latest successful source-sync run without re-running applications."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/ts050-source"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def barrier_examples(records):
    """Find actual nested C1/P1/C2 sequences with equal Core heads and exact admissions."""
    examples = []
    for outer in records:
        if outer["owner"] != "memory" or outer["status"] != 200 or outer["request"] is None:
            continue
        nested = sorted(
            (
                r
                for r in records
                if r["owner"] in {"core", "platform"}
                and r["started"] >= outer["started"]
                and r["ended"] <= outer["ended"]
            ),
            key=lambda r: r["started"],
        )
        for c1, p1, c2 in zip(nested, nested[1:], nested[2:]):
            if not (
                c1["path"].endswith("/source-facts/read")
                and c1["request"].get("mode") == "snapshot"
                and p1["path"].endswith("/source-access/read")
                and p1["request"].get("operation") == "current"
                and c2["path"].endswith("/source-facts/read")
                and c2["request"].get("mode") == "head"
                and all(r["status"] == 200 for r in (c1, p1, c2))
            ):
                continue
            if not (
                c1["ended"] <= p1["started"]
                and p1["ended"] <= c2["started"]
                and c1["response"]["head"] == c2["response"]["head"]
                and c1["response"]["admissions"] == p1["request"]["admissions"]
            ):
                continue
            item = {
                "memory_operation": outer["path"],
                "budget": outer["request"].get("budget"),
                "memory_request_id": outer["request"]
                .get("query", outer["request"])
                .get("request_id"),
                "C1": c1,
                "P1": p1,
                "C2": c2,
                "nonempty_admissions": bool(c1["response"]["admissions"]),
            }
            if not any(
                e["nonempty_admissions"] == item["nonempty_admissions"]
                and e["budget"] == item["budget"]
                for e in examples
            ):
                examples.append(item)
        if len(examples) >= 3:
            break
    return examples[:3]


def compact(record):
    body, response = record["request"] or {}, record["response"] or {}
    envelope = body.get("command", body.get("query", body))
    result = {
        key: record[key]
        for key in ("owner", "path", "transport", "status", "request_sha256", "response_sha256")
    }
    result.update(
        request_id=envelope.get("request_id", body.get("event_id")),
        duration_ms=round((record["ended"] - record["started"]) * 1000, 3),
    )
    for key in ("operation", "mode", "budget"):
        if key in body:
            result[key] = body[key]
    for key in ("head", "scope_version", "version_domain", "code", "state", "candidate_job_ref"):
        if key in response:
            result[key] = response[key]
    if "grants" in response:
        result["grant_states"] = [g["state"] for g in response["grants"]]
    return result


def main(*, archive=None, prior=()):
    environment = json.loads((RUNTIME / "environment.json").read_text("utf-8"))
    log = (RUNTIME / "unittest.txt").read_text("utf-8")
    matched = re.search(r"Ran (\d+) tests? in ([\d.]+)s", log)
    names = re.findall(r"^(test_\w+) \(test_ts050_source[^\n]+\) \.\.\.", log, re.M)
    if environment["test_exit_code"] != 0 or not matched or not re.search(r"^OK$", log, re.M):
        raise SystemExit("Only a completed successful run can be collected")
    if len(names) != int(matched[1]) or len(set(names)) != len(names):
        raise SystemExit("Test-name inventory mismatch; do not mix stale scenario results")
    current_run = {
        "environment": environment,
        "validation": {
            "tests": int(matched[1]),
            "passed": int(matched[1]),
            "duration_seconds": float(matched[2]),
            "pattern": environment["test_pattern"],
            "unittest_log_sha256": digest((RUNTIME / "unittest.txt").read_bytes()),
        },
        "result_hashes": {
            name: digest((RUNTIME / "results" / (name + ".json")).read_bytes()) for name in names
        },
    }
    if archive:
        archive = Path(archive).resolve()
        if not archive.is_relative_to(RUNTIME.resolve()):
            raise SystemExit("Archive must stay within this slice runtime directory")
        archive.write_text(json.dumps(current_run, indent=2) + "\n", "utf-8", newline="\n")
        print(str(archive))
        return
    runs = [json.loads(Path(path).read_text("utf-8")) for path in prior] + [current_run]
    names = []
    for run in runs:
        for key in ("pins", "distributions", "source_manifest_sha256", "profile_manifest_sha256"):
            assert run["environment"][key] == environment[key], (
                "Do not mix versions or environments"
            )
        for name, expected in run["result_hashes"].items():
            assert name not in names, "Disjoint verified batches are required"
            assert digest((RUNTIME / "results" / (name + ".json")).read_bytes()) == expected, (
                "Stale result file"
            )
            names.append(name)
    scenarios, totals = (
        [],
        {
            "models": 0,
            "channel_requests": 0,
            "sent": 0,
            "unknown": 0,
            "recorded_owner_http": 0,
            "closed_listener_checks": 0,
        },
    )
    for name in names:
        path = RUNTIME / "results" / (name + ".json")
        raw = path.read_bytes()
        source = json.loads(raw)
        assert source["scenario"] == name and not source["cleanup"]["failures"]
        assert source["cleanup"]["gateway_process_stopped"]
        assert all(r["transport"] == "https" for r in source["wire"])
        models = [m["response"]["choices"][0]["message"]["content"] for m in source["models"]]
        sent_text = [r["text"] for r in source["channel_requests"]]
        assert sorted(models) == sorted(sent_text), (
            "Native response text must reach the channel unchanged"
        )
        totals["models"] += len(models)
        totals["channel_requests"] += len(sent_text)
        totals["recorded_owner_http"] += len(source["wire"])
        totals["closed_listener_checks"] += source["cleanup"]["listeners_checked"]
        for receipt in source["channel_receipts"]:
            totals[receipt["state"]] += 1
        scenarios.append(
            {
                "scenario": name,
                "result": "passed_expected_assertions",
                "raw_result_file": path.name,
                "raw_result_sha256": digest(raw),
                "checks": source["checks"],
                "cleanup": source["cleanup"],
                "fanouts": source["fanouts"],
                "model_wire": source["models"],
                "channel_requests": source["channel_requests"],
                "channel_receipts": source["channel_receipts"],
                "barrier_examples": barrier_examples(source["wire"]),
                "http_timeline": [
                    compact(r) for r in sorted(source["wire"], key=lambda r: r["started"])
                ],
            }
        )
    historical = {}
    for commit, names in (
        (
            "dc357e2cefb3e3df7c427938da38dd2c67d28341",
            ["TS-050-report.md", "TS-050-trace.json", "TS-050-wiring.md", "TS-050-next-ports.md"],
        ),
        (
            "3c4e5920af8b3c5f236c2e8959cfadb8154bda6d",
            ["TS-050-tls-report.md", "TS-050-tls-trace.json"],
        ),
    ):
        for name in names:
            relative = "docs/development/evidence/" + name
            previous = subprocess.check_output(
                ["git", "-C", str(ROOT), "show", commit + ":" + relative]
            )
            current = (ROOT / relative).read_bytes().replace(b"\r\n", b"\n")
            assert current == previous, "Historical evidence must remain unchanged"
            historical[name] = {"commit": commit, "git_bytes_sha256": digest(previous)}
    result = {
        "task": "TS-050",
        "slice": "real_source_sync",
        "full_requested_L0_matrix": "partial",
        "scope_note": "Actual source-sync conversation chain passed; real confirmation/profile publication absent",
        "validation": {
            "tests": len(scenarios),
            "passed": len(scenarios),
            "duration_seconds": sum(run["validation"]["duration_seconds"] for run in runs),
            "runs": [run["validation"] for run in runs],
            "command": ".runtime/ts050/venv/Scripts/python.exe -B scripts/integration/ts050.py run",
        },
        "environment": environment,
        "historical_evidence": historical,
        "totals": totals,
        "redaction": "Usable assertion refs replaced by SHA256; HTTP hashes are of original wire bytes. Timeline is a projection, not replayable wire.",
        "scenarios": scenarios,
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    for forbidden in ("Bearer ", "BEGIN PRIVATE KEY", "synthetic-source-"):
        assert forbidden not in serialized
    output = ROOT / "docs/development/evidence/TS-050-source-trace.json"
    output.write_text(serialized, "utf-8", newline="\n")
    print(json.dumps({"output": str(output), "totals": totals, "bytes": output.stat().st_size}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive")
    parser.add_argument("--prior", action="append", default=[])
    args = parser.parse_args()
    main(archive=args.archive, prior=args.prior)
