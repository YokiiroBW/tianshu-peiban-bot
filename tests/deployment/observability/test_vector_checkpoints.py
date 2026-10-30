import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vector_checkpoints import checkpoint_positions, checksum, fingerprint, compatible_configuration


class VectorCheckpointTests(unittest.TestCase):
    def test_crc64_ecma_reference(self):
        self.assertEqual(checksum(b"123456789"), 0x6C40DF5F0B497347)

    def test_fingerprint_uses_first_complete_line_or_length_limit(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "log"
            path.write_bytes(b"one\ntwo\n")
            self.assertEqual(fingerprint(path), checksum(b"one\n"))
            path.write_bytes(b"incomplete")
            self.assertIsNone(fingerprint(path))
            path.write_bytes(b"x" * 4095 + b"\n")
            self.assertEqual(fingerprint(path), checksum(b"x" * 4095))

    def test_checkpoint_format_and_duplicates_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "checkpoints.json"
            checkpoint = {"fingerprint": {"first_lines_checksum": 13}, "position": 9, "modified": "2030-01-01T00:00:00Z"}
            path.write_text(json.dumps({"version": "1", "checkpoints": [checkpoint]}))
            self.assertEqual(checkpoint_positions(path)[13][0], 9)
            for data in [
                {"version": "2", "checkpoints": [checkpoint]},
                {"version": "1", "checkpoints": [checkpoint, checkpoint]},
                {"version": "1", "checkpoints": [{"fingerprint": {"dev_inode": [1, 2]}, "position": 9}]},
                {"version": "1", "checkpoints": [{"fingerprint": {"first_lines_checksum": 13}, "position": -1}]},
            ]:
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    checkpoint_positions(path)

    def test_source_cleanup_requires_matching_version_acknowledgements_and_buffer(self):
        from configs import vector
        from helpers import SNAPSHOT
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "vector.json"
            config = vector(SNAPSHOT)
            roots = {service: str(Path("/sources") / service) for service in ("platform", "companion", "memory", "gateway")}
            # Match path spelling used by this host; production uses POSIX paths.
            for service, source in roots.items():
                config["sources"][service]["include"] = [str(Path(source) / "*.jsonl"), str(Path(source) / "*.jsonl.[0-9]*")]
            def write():
                path.write_text(json.dumps(config))
            with patch("vector_checkpoints.subprocess.run") as version:
                version.return_value.stdout = "vector 0.58.0 (synthetic)"
                write()
                self.assertTrue(compatible_configuration(path, roots))
                config["sinks"]["loki"]["acknowledgements"]["enabled"] = False
                write()
                self.assertFalse(compatible_configuration(path, roots))
                config["sinks"]["loki"]["acknowledgements"]["enabled"] = True
                config["sources"]["platform"]["acknowledgements"] = {"enabled": False, "extra": 1}
                write()
                self.assertFalse(compatible_configuration(path, roots))
                del config["sources"]["platform"]["acknowledgements"]
                version.return_value.stdout = "vector 0.59.0 (synthetic)"
                write()
                self.assertFalse(compatible_configuration(path, roots))


if __name__ == "__main__":
    unittest.main()
