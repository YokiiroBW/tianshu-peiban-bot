"""Affected final checks, preserving the first complete run and its cleanup error."""

import argparse
import json
import platform
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from run_verification import Evidence  # noqa: E402

from ops.recovery.safety import file_hash  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--pattern", action="append")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.parent != HERE or output.exists():
        raise ValueError("New report under recovery tests required")
    patterns = args.pattern or [
        "test_compose_lifecycle.py",
        "test_linux_recovery.py",
        "test_product_inventory.py",
        "test_runtime_identity.py",
        "test_runtime_image_boundary.py",
        "test_drill.py",
        "test_drill_http.py",
    ]
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.discover(str(HERE), pattern=p) for p in patterns
    )
    started = datetime.now(timezone.utc).isoformat()
    result = unittest.TextTestRunner(verbosity=2, resultclass=Evidence).run(suite)
    document = {
        "schema_version": "dep-j-verification/1",
        "patterns": patterns,
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
            "cases": result.records,
        },
        "earlier_full_run": {
            "path": "verification-depj-2026-09-22.json",
            "sha256": file_hash(HERE / "verification-depj-2026-09-22.json"),
            "tests": 93,
            "errors": 1,
            "failures": 0,
            "error": "test_drill authority-change fixture connection not closed before Windows cleanup; fixed",
        },
        "source_sha256": {
            p.relative_to(ROOT).as_posix(): file_hash(p)
            for folder in (ROOT / "ops/recovery", HERE)
            for p in sorted(folder.glob("*.py"))
        },
        "claims": {
            "boundary_and_orchestration_tests": result.wasSuccessful(),
            "docker_contract_double": True,
            "http_transport": "real_loopback_TLS_test_server",
            "real_product_http": "not_run",
            "linux_containers": "not_run",
            "nas": "not_run",
            "release_ready": False,
            "original_restore_activation": False,
        },
        "status": "passed" if result.wasSuccessful() else "failed",
    }
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
