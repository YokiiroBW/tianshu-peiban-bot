"""Persist actual runner test counts and per-test results, without failure bodies."""

import argparse
import sys
import unittest
from pathlib import Path

from acceptance.evidence import Report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = Report("local", {}, "synthetic")
    report.data["kind"] = "runner_selftest"

    class Result(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            report.add(test.id(), "pass", "assertions_satisfied")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            report.add(test.id(), "fail", "assertion_failed")

        def addError(self, test, err):
            super().addError(test, err)
            report.add(test.id(), "fail", "test_error")

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            report.add(test.id(), "skipped", "dependency_missing")

    suite = unittest.defaultTestLoader.discover(
        str(Path(__file__).parent), pattern="test_runner.py"
    )
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    report.data["test_counts"] = {
        "executed": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
    }
    report.save(args.output)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
