"""Produce honest synthetic-only evidence, optionally bind the fixed DEP-A interface."""

import argparse
import hashlib
import json
import platform
import sqlite3
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(HERE))

from ops.recovery.manifest import release  # noqa: E402 -- standalone test entry point
from ops.recovery.safety import file_hash, safe_path  # noqa: E402

DEP_A_COMMIT = "e86d622a813f116b059b62b820cbd1342722042d"
DEP_A_FILES = {
    "release-manifest.schema.json": "cf95607fa7252f806ae4ab4d919298c234d91f1007ce763ec12485091f4d0397",
    "release-manifest.example.json": "7566375a763440d9408d1ae6a3502aab5521bd996cec3dc93c523ebcd660f922",
}


class Evidence(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def addSuccess(self, test):
        self.records.append(
            {
                "case": test.id(),
                "status": "passed",
                "duration_seconds": round(time.monotonic() - self.started, 3),
            }
        )
        super().addSuccess(test)

    def addError(self, test, err):
        self.records.append({"case": test.id(), "status": "error"})
        super().addError(test, err)

    def addFailure(self, test, err):
        self.records.append({"case": test.id(), "status": "failed"})
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        self.records.append({"case": test.id(), "status": "skipped"})
        super().addSkip(test, reason)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []


def fixed_interface(repo, contracts_root):
    documents = {}
    for name, expected in DEP_A_FILES.items():
        data = subprocess.run(
            [
                "git",
                "-C",
                str(safe_path(repo)),
                "show",
                DEP_A_COMMIT + ":deploy/tianshu/" + name,
            ],
            capture_output=True,
            check=True,
        ).stdout
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("fixed DEP-A blob mismatch")
        documents[name] = json.loads(data)
    document = release(documents["release-manifest.example.json"])
    contracts = []
    for contract in document["contracts"]:
        for item in contract["files"]:
            path = (
                safe_path(contracts_root)
                / contract["path"].removeprefix("contracts/")
                / item["path"]
            )
            if file_hash(path) != item["sha256"]:
                raise ValueError("contract original bytes mismatch")
        contracts.append(
            {
                "id": contract["id"],
                "manifest_sha256": contract["manifest_sha256"],
                "files_checked": len(contract["files"]),
            }
        )
    return {
        "status": "passed",
        "scope": "read_only_interface_and_contract_original_bytes",
        "commit": DEP_A_COMMIT,
        "blobs": DEP_A_FILES,
        "contracts": contracts,
        "product_execution": "not_run",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--dep-a-repo")
    parser.add_argument("--contracts-root")
    args = parser.parse_args()
    output = safe_path(Path(args.output).absolute(), exists=False)
    if not output.is_relative_to(HERE) or output.exists():
        raise ValueError("report must be a new file under this test directory")
    started = datetime.now(timezone.utc).isoformat()
    result = unittest.TextTestRunner(verbosity=2, resultclass=Evidence).run(
        unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
    )
    interface = {"status": "not_run", "reason": "explicit_fixed_inputs_not_supplied"}
    if args.dep_a_repo and args.contracts_root:
        interface = fixed_interface(args.dep_a_repo, args.contracts_root)
    source_hashes = {
        path.relative_to(WORKSPACE).as_posix(): file_hash(path)
        for folder in (WORKSPACE / "ops/recovery", HERE)
        for path in sorted(folder.glob("*.py"))
    }
    document = {
        "report_version": "dep-c-synthetic/1",
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "os": platform.system(),
        },
        "tests": {
            "run": result.testsRun,
            "errors": len(result.errors),
            "failures": len(result.failures),
            "skipped": len(result.skipped),
            "results": result.records,
        },
        "source_sha256": source_hashes,
        "dep_a_interface": interface,
        "claims": {
            "synthetic_recovery": result.wasSuccessful(),
            "real_product_restore": False,
            "linux_container": False,
            "nas": False,
            "disaster_recovery_without_current_authority": False,
            "real_model": False,
            "production_restore_drill_evidence": False,
        },
        "status": "passed" if result.wasSuccessful() else "failed",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
