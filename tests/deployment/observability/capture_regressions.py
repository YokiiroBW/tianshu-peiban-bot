"""Run the two final Ledger regressions and preserve their actual synthetic inputs."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from helpers import PACKAGE, ROOT
from monitor import Ledger
import test_observability as cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "kind": "dep-b-final-ledger-regressions",
        "real_ledger": True,
        "query_backend": "explicit_unittest_mock",
        "full_native_pipeline": False,
        "application_reclamation_authorized": False,
        "cases": [],
        "implementation_sha256": {},
    }
    for path in [*PACKAGE.glob("*.py"), Path(__file__), Path(cases.__file__)]:
        report["implementation_sha256"][path.relative_to(ROOT).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
        )
    original_emit, original_metrics = cases.emit, Ledger.metrics
    for name in (
        "test_previously_verified_central_loss_becomes_pending",
        "test_regular_rename_rotation_does_not_raise_mutation_alarm",
    ):
        evidence = {"case": name, "source_writes": [], "actual_metrics": []}

        def emit(path, events):
            evidence["source_writes"].append({"filename": path.name, "events": events})
            return original_emit(path, events)

        def metrics(ledger):
            result = original_metrics(ledger)
            evidence["actual_metrics"].append(result)
            return result

        stream = io.StringIO()
        with (
            patch.object(cases, "emit", emit),
            patch.object(Ledger, "metrics", metrics),
        ):
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
                unittest.TestSuite([cases.LedgerTests(name)])
            )
        evidence.update(
            status="passed" if result.wasSuccessful() else "failed",
            output=stream.getvalue(),
        )
        report["cases"].append(evidence)
    report["status"] = (
        "passed" if all(c["status"] == "passed" for c in report["cases"]) else "failed"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "cases": len(report["cases"])}))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
