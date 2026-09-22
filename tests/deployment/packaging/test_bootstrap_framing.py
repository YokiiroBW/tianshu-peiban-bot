"""Exercise a real open pipe; mocks alone hide Docker stdin EOF deadlocks."""

import json
import subprocess
import sys
import tempfile
import unittest

import test_packaging  # noqa: F401 -- initializes the deployment import path
from linux_bootstrap import CLI, request_frame
from manifest import Refused


class FramingTests(unittest.TestCase):
    def test_json_frame_has_one_delimiter_and_preserves_unicode_and_newlines(self):
        value = {"nested": {"text": "中文\nsecond line"}, "items": [1, 2]}
        frame = request_frame(value)
        self.assertEqual(frame.count(b"\n"), 1)
        self.assertTrue(frame.endswith(b"\n"))
        self.assertEqual(json.loads(frame), value)

    def test_byte_limit_is_enforced_before_sending(self):
        with self.assertRaisesRegex(Refused, "bootstrap_input_too_large"):
            request_frame({"text": "中" * 400000})

    def test_actual_cli_transport_completes_while_stdin_remains_open(self):
        with tempfile.TemporaryDirectory() as folder:
            stub = "import subprocess\nsubprocess.run=lambda *a,**kw: subprocess.CompletedProcess(a,0,stdout=b'{\"ok\":true}')\n"
            code = stub + CLI.replace("dir='/tmp'", "dir=" + repr(folder))
            process = subprocess.Popen(
                [sys.executable, "-c", code, "issue"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                process.stdin.write(request_frame({"entry_id": "synthetic-probe"}))
                process.stdin.flush()
                # Intentionally do not close stdin or call communicate(), which would
                # mask the missing-EOF behavior observed on the real NAS.
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertFalse(process.stdin.closed)
                self.assertEqual(json.loads(process.stdout.read()), {"ok": True})
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
