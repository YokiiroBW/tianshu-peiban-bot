"""Only the unified plugin's standard Linux venv alias is metadata-counted."""

from copy import copy
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.resident_capacity import guard


class PluginVenvCapacityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = (
            self.root / "data/platform-plugins/plugins" / ("a" * 64) / ".environment"
        )
        (self.environment / "lib").mkdir(parents=True)
        self.link = self.environment / "lib64"
        self.entry = SimpleNamespace(path=str(self.link))
        self.info = SimpleNamespace(
            st_mode=stat.S_IFLNK | 0o777, st_uid=10001, st_gid=10001
        )

    def allowed(self, profile=guard.UNIFIED_PROFILE):
        return guard._plugin_venv_link(self.entry, self.info, self.root, profile)

    def test_exact_alias_allows_incomplete_environment_without_reading_markers(self):
        self.assertFalse((self.environment / ".complete").exists())
        self.assertFalse((self.environment / "pyvenv.cfg").exists())
        with patch.object(guard.os, "readlink", return_value="lib") as readlink:
            self.assertTrue(self.allowed())
        readlink.assert_called_once_with(str(self.link))

    def test_other_profiles_paths_or_owners_are_not_whitelisted(self):
        with patch.object(
            guard.os,
            "readlink",
            side_effect=AssertionError("must not inspect unrelated link"),
        ):
            for profile in ("resident-nine", guard.KNOWLEDGE_PROFILE, "unknown"):
                with self.subTest(profile=profile):
                    self.assertFalse(self.allowed(profile))
            original = self.entry.path
            for name in (
                "data/other/plugins/" + "a" * 64 + "/.environment/lib64",
                "data/platform-plugins/plugins/short/.environment/lib64",
                "data/platform-plugins/plugins/" + "A" * 64 + "/.environment/lib64",
                "data/platform-plugins/plugins/" + "a" * 64 + "/other/lib64",
                "data/platform-plugins/plugins/" + "a" * 64 + "/.environment/python",
            ):
                self.entry.path = str(self.root / name)
                with self.subTest(path=name):
                    self.assertFalse(self.allowed())
            self.entry.path = original
            for field in ("st_uid", "st_gid"):
                self.info = copy(self.info)
                setattr(self.info, field, 0)
                with self.subTest(owner=field):
                    self.assertFalse(self.allowed())
                setattr(self.info, field, 10001)

    def test_target_must_be_literal_lib_and_lib_must_be_a_real_directory(self):
        for value in ("./lib", "lib/", "../lib", "/tmp/lib", "elsewhere"):
            with (
                self.subTest(target=value),
                patch.object(guard.os, "readlink", return_value=value),
            ):
                self.assertFalse(self.allowed())
        with patch.object(guard.os, "readlink", return_value="lib"):
            with patch.object(
                Path,
                "lstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFLNK | 0o777),
            ):
                self.assertFalse(self.allowed())
            (self.environment / "lib").rmdir()
            self.link.write_bytes(b"synthetic surviving alias metadata")
            self.assertFalse(self.allowed())

    @unittest.skipUnless(
        os.name == "posix" and getattr(os, "geteuid", lambda: -1)() == 0,
        "real UID10001 symlink metadata needs Linux root fixture",
    )
    def test_actual_link_counts_only_its_bytes_and_legacy_still_refuses(self):
        self.link.symlink_to("lib", target_is_directory=True)
        os.chown(self.link, 10001, 10001, follow_symlinks=False)
        (self.environment / "lib" / "payload").write_bytes(b"body")
        used, free = guard.capacity(self.root, service_profile=guard.UNIFIED_PROFILE)
        self.assertEqual(used, 4 + self.link.lstat().st_size)
        self.assertGreater(free, 0)
        for profile in ("resident-nine", guard.KNOWLEDGE_PROFILE):
            with (
                self.subTest(profile=profile),
                self.assertRaisesRegex(guard.Unsafe, "deployment_tree_invalid"),
            ):
                guard.capacity(self.root, service_profile=profile)

    @unittest.skipUnless(
        os.name == "posix" and getattr(os, "geteuid", lambda: -1)() == 0,
        "real UID10001 symlink metadata needs Linux root fixture",
    )
    def test_removed_link_during_readlink_uses_existing_scan_race_handling(self):
        self.link.symlink_to("lib", target_is_directory=True)
        os.chown(self.link, 10001, 10001, follow_symlinks=False)

        def removed(path):
            self.link.unlink()
            raise FileNotFoundError()

        with patch.object(guard.os, "readlink", side_effect=removed):
            used, free = guard.capacity(
                self.root, service_profile=guard.UNIFIED_PROFILE
            )
        self.assertEqual(used, 0)
        self.assertGreater(free, 0)


if __name__ == "__main__":
    unittest.main()
