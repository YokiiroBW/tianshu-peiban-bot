import json
import tempfile
import unittest
from pathlib import Path

from vector_checkpoints import fingerprint
from vector_source_retention import prune_sources


class VectorSourceRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "logs"
        self.root.mkdir()
        self.vector = self.base / "vector"
        (self.vector / "companion").mkdir(parents=True)

    def write(self, name, content):
        path = self.root / name
        path.write_bytes(content)
        return path

    def checkpoints(self, entries):
        rows = [{"fingerprint": {"first_lines_checksum": fingerprint(p)}, "position": n, "modified": "2099-01-01T00:00:00Z"}
                for p, n in entries]
        (self.vector / "companion" / "checkpoints.json").write_text(
            json.dumps({"version": "1", "checkpoints": rows}))

    def prune(self):
        return prune_sources({"companion": self.root}, self.vector, now=100000)

    def test_complete_closed_segment_reclaimed_active_segment_kept(self):
        old = self.write("companion.jsonl", b"old\n")
        active = self.write("companion.jsonl.1", b"active\n")
        self.checkpoints([(old, old.stat().st_size), (active, active.stat().st_size)])
        result = self.prune()
        self.assertEqual(result["source_retention_deleted_segments"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(active.exists())

    def test_incomplete_or_colliding_checkpoint_preserves_source(self):
        old = self.write("companion.jsonl", b"same\nmore\n")
        active = self.write("companion.jsonl.1", b"active\n")
        self.checkpoints([(old, 5), (active, active.stat().st_size)])
        self.assertEqual(self.prune()["source_retention_deleted_segments"], 0)
        duplicate = self.write("other.jsonl.1foreign", b"same\nother\n")
        self.checkpoints([(old, old.stat().st_size), (active, active.stat().st_size)])
        self.assertEqual(self.prune()["source_retention_deleted_segments"], 0)
        self.assertTrue(old.exists())
        self.assertTrue(duplicate.exists())

    def test_missing_checkpoint_preserves_source(self):
        old = self.write("companion.jsonl", b"old\n")
        self.write("companion.jsonl.1", b"new\n")
        self.assertEqual(self.prune()["source_retention_failures"], 1)
        self.assertTrue(old.exists())

    def test_checkpoint_older_than_source_creation_is_not_reused(self):
        old = self.write("companion.jsonl", b"old\n")
        active = self.write("companion.jsonl.1", b"new\n")
        self.checkpoints([(old, old.stat().st_size), (active, active.stat().st_size)])
        path = self.vector / "companion" / "checkpoints.json"
        data = json.loads(path.read_bytes())
        data["checkpoints"][0]["modified"] = "2001-01-01T00:00:00Z"
        path.write_text(json.dumps(data))
        self.assertEqual(self.prune()["source_retention_deleted_segments"], 0)
        self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
