"""Read-only R1 evaluation of an existing archive; never relabel its executing implementation."""

import argparse
import json
from pathlib import Path
import re

from acceptance.evidence import digest, read_json, verify_report
from acceptance.source_lease import completed_after_expiry
from acceptance.transport import check


def evaluate(archive, execution_commit):
    check(
        re.fullmatch(r"[0-9a-f]{40}", execution_commit) is not None,
        "execution_commit_required",
    )
    report_path = archive / "report.json"
    check(verify_report(report_path), "original_report_integrity_invalid")
    report = read_json(report_path)
    raw = (archive / "stress-delivery.jsonl").read_bytes()
    check(
        digest(raw) == report["disabled_long_run"]["delivery_evidence_sha256"],
        "original_delivery_hash_mismatch",
    )
    check(raw.endswith(b"\n"), "incomplete_delivery_line")
    events = [json.loads(line) for line in raw.splitlines()]
    check(
        len(events) == report["disabled_long_run"]["completed_turns"],
        "original_delivery_count_mismatch",
    )
    # The old runner checked each event against its accepted public submission. It did
    # not archive a separate submission ledger. Do not invent one in this reevaluation.
    facts = completed_after_expiry(
        events, report["bootstrap"]["expires_at"], [e["correlation_id"] for e in events]
    )
    source = Path(__file__).parent
    return {
        "kind": "dep-e-r1-existing-delivery-evaluation",
        "result": "passed",
        "evaluation_only": True,
        "new_product_execution": False,
        "original_execution_commit": execution_commit,
        "original_report_sha256": digest(report_path.read_bytes()),
        "original_delivery_sha256": digest(raw),
        "original_manifest_sha256": report["binding"]["manifest_sha256"],
        "validator_files_sha256": {
            name: digest((source / name).read_bytes())
            for name in ("recheck_renewal.py", "acceptance/source_lease.py")
        },
        "correlation_basis": "original_runner_matched_each_accepted_request; no_separate_submission_ledger",
        "facts": facts,
        "release_ready": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--execution-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(
        not args.output.resolve().is_relative_to(args.archive.resolve()),
        "separate_evaluation_output_required",
    )
    result = evaluate(args.archive, args.execution_commit)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
