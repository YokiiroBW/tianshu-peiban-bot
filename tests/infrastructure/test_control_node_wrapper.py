"""Run in Linux runtime validation; no Docker or external node is required."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import OUTPUT
from ops.infrastructure.control.lifecycle import WRAPPER


@unittest.skipUnless(sys.platform == "linux" and shutil.which("node"), "Linux and installed Node are required for Dockge process-group proof")
class NodeWrapperTest(unittest.TestCase):
    def test_timeout_kills_original_compose_group_and_writes_only_result(self):
        with tempfile.TemporaryDirectory(dir=OUTPUT) as directory:
            directory = Path(directory)
            marker = directory / "completion.json"
            child_pid = directory / "descendant.pid"
            code = "import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);Path(sys.argv[1]).write_text(str(p.pid));print('synthetic-sensitive-output',flush=True);time.sleep(30)"
            result = subprocess.run([shutil.which("node"), "-e", WRAPPER, str(marker), ".5", json.dumps([sys.executable, "-c", code, str(child_pid)])], input=b"synthetic-private-input", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            self.assertEqual(result.returncode, 124)
            value = json.loads(marker.read_text())
            self.assertEqual(value["state"], "complete")
            self.assertTrue(value["timed_out"])
            self.assertNotIn("synthetic-sensitive", marker.read_text())
            pid = int(child_pid.read_text())
            proc = Path(f"/proc/{pid}/stat")
            if proc.exists():
                self.assertEqual(proc.read_text().rsplit(")", 1)[1].split()[0], "Z")

    def test_closed_exec_output_still_produces_original_completion_receipt(self):
        with tempfile.TemporaryDirectory(dir=OUTPUT) as directory:
            marker = Path(directory) / "completion.json"
            code = "import sys,time;sys.stdin.buffer.read();time.sleep(.2);print('synthetic-secret-output',flush=True)"
            process = subprocess.Popen([shutil.which("node"), "-e", WRAPPER, str(marker), "2", json.dumps([sys.executable, "-c", code])], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            process.stdin.write(b"synthetic-input")
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(json.loads(marker.read_text()), {"state": "complete", "exit_code": 0, "timed_out": False})


if __name__ == "__main__":
    unittest.main()
