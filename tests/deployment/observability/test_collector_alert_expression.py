"""Evaluate the actual collector alert with native Prometheus missing-series semantics."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from configs import ALERTS


class CollectorAlertExpressionTests(unittest.TestCase):
    def test_observed_errors_alert_when_the_other_counter_family_is_absent(self):
        tool = os.environ.get("TIANSHU_PROMTOOL") or shutil.which("promtool")
        if not tool:
            self.skipTest("native promtool is required")
        error = {
            "series": 'vector_component_errors_total{component_id="loki"}',
            "values": "0 0 0 0 0 1",
        }
        discarded = {
            "series": 'vector_component_discarded_events_total{component_id="loki"}',
            "values": "0 0 0 0 0 2",
        }
        cases = [
            ([error], 1),
            ([discarded], 2),
            ([error, discarded], 3),
            ([], None),
            ([{**error, "values": "0 0 0 0 0 0"}], 0),
        ]
        tests = []
        for series, value in cases:
            tests.append(
                {
                    "interval": "1m",
                    "input_series": series,
                    "promql_expr_test": [
                        {
                            "expr": ALERTS["CollectorDiscardOrError"][0],
                            "eval_time": "5m",
                            "exp_samples": []
                            if value is None
                            else [{"labels": "{}", "value": value}],
                        }
                    ],
                }
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector-alert.json"
            path.write_text(
                json.dumps({"evaluation_interval": "1m", "tests": tests}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [tool, "test", "rules", str(path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
