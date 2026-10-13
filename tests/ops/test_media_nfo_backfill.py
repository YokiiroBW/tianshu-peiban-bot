import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.media_nfo_backfill import execute, observation


class NfoBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "library"
        self.root.mkdir()
        self.backup = base / "backup"
        self.video = self.root / "original.mp4"
        self.video.write_bytes(b"unchanged-media-evidence")
        self.files = []
        for name in ("movie.nfo", "tvshow.nfo"):
            path = self.root / name
            old = b"<movie><title>Original</title></movie>"
            new = b"<movie><title>Original</title><plot>Updated</plot></movie>"
            path.write_bytes(old)
            self.files.append(
                {
                    "relative_path": name,
                    "before_sha256": hashlib.sha256(old).hexdigest(),
                    "after_sha256": hashlib.sha256(new).hexdigest(),
                    "content_base64": base64.b64encode(new).decode(),
                }
            )
        info = self.root.stat()
        self.plan = {
            "schema_version": 1,
            "root_identity": [info.st_dev, info.st_ino],
            "files": self.files,
            "videos": [
                {
                    "relative_path": "original.mp4",
                    "observation": observation(self.video),
                }
            ],
        }

    def run_action(self, action):
        return execute(self.plan, root=self.root, backup=self.backup, action=action)

    def test_apply_resume_and_video_preservation(self):
        before = observation(self.video)
        self.assertEqual("ready", self.run_action("inspect")["status"])
        self.assertFalse(self.backup.exists())
        self.assertEqual(2, self.run_action("apply")["changed"])
        self.assertEqual(before, observation(self.video))
        self.assertEqual(b"unchanged-media-evidence", self.video.read_bytes())
        result = self.run_action("apply")
        self.assertEqual(0, result["changed"])
        self.assertEqual(2, result["already_applied"])
        self.assertEqual("verified", self.run_action("verify")["status"])
        self.assertEqual(
            b"<movie><title>Original</title></movie>",
            (self.backup / "original/movie.nfo").read_bytes(),
        )

    def test_changed_input_fails_before_any_output(self):
        unchanged = (self.root / "movie.nfo").read_bytes()
        (self.root / "tvshow.nfo").write_bytes(b"<movie>User edit</movie>")
        with self.assertRaisesRegex(ValueError, "original_nfo_changed"):
            self.run_action("apply")
        self.assertEqual(unchanged, (self.root / "movie.nfo").read_bytes())
        self.assertFalse(self.backup.exists())

    def test_rejects_video_write_and_changed_video_identity(self):
        self.files[0]["relative_path"] = "original.mp4"
        with self.assertRaisesRegex(ValueError, "only_nfo_allowed"):
            self.run_action("apply")
        self.files[0]["relative_path"] = "movie.nfo"
        self.video.write_bytes(b"changed-outside-maintenance")
        with self.assertRaisesRegex(ValueError, "video_observation_changed"):
            self.run_action("apply")
        self.assertFalse(self.backup.exists())

    def test_partial_publication_resumes_from_verified_backup(self):
        self.run_action("apply")
        restored = self.backup / "original/tvshow.nfo"
        (self.root / "tvshow.nfo").write_bytes(restored.read_bytes())
        self.assertEqual(1, self.run_action("apply")["changed"])
        self.assertEqual("verified", self.run_action("verify")["status"])

    def test_interrupted_backup_does_not_publish_partial_saved_file(self):
        before = (self.root / "movie.nfo").read_bytes()

        def interrupted_copy(source, target, **kwargs):
            Path(target).write_bytes(b"partial-backup")
            raise OSError("simulated disk full")

        with patch("ops.media_nfo_backfill.shutil.copy2", side_effect=interrupted_copy):
            with self.assertRaisesRegex(OSError, "simulated disk full"):
                self.run_action("apply")
        self.assertFalse((self.backup / "original/movie.nfo").exists())
        self.assertEqual(before, (self.root / "movie.nfo").read_bytes())
        self.assertEqual(2, self.run_action("apply")["changed"])
        self.assertEqual("verified", self.run_action("verify")["status"])


if __name__ == "__main__":
    unittest.main()
