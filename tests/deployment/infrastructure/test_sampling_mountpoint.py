"""Only the empty host mountpoint metadata is prepared; no recursive changes."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy.infrastructure.sampling_mountpoint import prepare_sampling_mountpoint


class SamplingMountpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.path = self.base / "infrastructure-sampling"

    def prepare(self):
        original = Path.chmod
        modes = []

        def chmod(path, mode, **kwargs):
            modes.append((path, mode))
            return original(path, mode, **kwargs)

        with patch("os.chown", create=True) as chown, patch.object(Path, "chmod", new=chmod):
            prepare_sampling_mountpoint(self.path)
            chown.assert_called_once_with(self.path, 10001, 10001)
        self.assertEqual(modes, [(self.path, 0o700)])
        self.assertTrue(self.path.is_dir())
        self.assertEqual(list(self.path.iterdir()), [])

    def test_new_underlay_is_created_empty_with_exact_owner_and_private_mode(self):
        self.prepare()

    def test_existing_empty_underlay_is_repaired_without_changing_parent(self):
        self.path.mkdir(mode=0o755)
        marker = self.base / "unrelated"
        marker.write_bytes(b"unchanged")
        self.prepare()
        self.assertEqual(marker.read_bytes(), b"unchanged")

    def test_nonempty_or_symlink_mountpoint_is_refused_before_metadata_changes(self):
        self.path.mkdir()
        marker = self.path / "must-preserve"
        marker.write_bytes(b"existing data")
        with patch("os.chown", create=True) as chown, patch.object(Path, "chmod") as chmod:
            with self.assertRaisesRegex(ValueError, "not_empty"):
                prepare_sampling_mountpoint(self.path)
            chown.assert_not_called()
            chmod.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"existing data")
        with patch.object(Path, "is_symlink", return_value=True), patch("os.chown", create=True) as chown:
            with self.assertRaisesRegex(ValueError, "mountpoint_invalid"):
                prepare_sampling_mountpoint(self.path)
            chown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
