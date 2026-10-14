import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from ops import media_directory_relocation as relocation


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.old = self.base / "old"
        self.new = self.base / "new"
        self.old.mkdir()
        self.new.mkdir()
        (self.old / "video.mp4").write_bytes(b"same video bytes")
        (self.old / "video.nfo").write_bytes(b"<movie><title>Title</title></movie>")
        self.plan = {
            "schema_version": 1, "source_root": str(self.old), "target_root": str(self.new),
            "directory_uid": os.getuid() if hasattr(os, "getuid") else 0,
            "directory_gid": os.getgid() if hasattr(os, "getgid") else 0,
            "files": [],
        }
        for name in ("video.mp4", "video.nfo"):
            row = {"old_relative_path": name, "new_relative_path": "UP主/Creator/" + name,
                   "kind": "video" if name.endswith("mp4") else "sidecar",
                   "observation": relocation.observation(self.old / name)}
            if row["kind"] == "sidecar":
                row["sha256"] = hashlib.sha256((self.old / name).read_bytes()).hexdigest()
            self.plan["files"].append(row)

    def test_staging_reuses_video_but_keeps_metadata_independent(self):
        result = relocation.stage(self.plan)
        self.assertEqual((result["linked"], result["copied"]), (1, 1))
        video = self.new / "UP主/Creator/video.mp4"
        self.assertTrue(video.samefile(self.old / "video.mp4"))
        nfo = self.new / "UP主/Creator/video.nfo"
        self.assertFalse(nfo.samefile(self.old / "video.nfo"))
        self.assertEqual(relocation.stage(self.plan)["existing"], 2)
        self.assertEqual(relocation.verify(self.plan)["files"], 2)

    def test_collision_is_checked_before_any_file_is_staged(self):
        parent = self.new / "UP主/Creator"
        parent.mkdir(parents=True)
        (parent / "video.nfo").write_bytes(b"unrelated")
        with self.assertRaisesRegex(ValueError, "target_content_changed"):
            relocation.stage(self.plan)
        self.assertFalse((parent / "video.mp4").exists())

    def test_source_changed_rejects_staging(self):
        (self.old / "video.nfo").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source_changed"):
            relocation.stage(self.plan)
        self.assertEqual(list(self.new.iterdir()), [])

    def test_cleanup_requires_actual_external_verification(self):
        relocation.stage(self.plan)
        with self.assertRaisesRegex(ValueError, "external_verification_missing"):
            relocation.retire_sources(self.plan, {"plan_digest": relocation.digest(self.plan)})
        self.assertTrue((self.old / "video.mp4").exists())

    def test_retire_only_explicit_files_and_replay(self):
        (self.old / "unlisted.txt").write_text("keep", encoding="utf-8")
        relocation.stage(self.plan)
        proof = {"plan_digest": relocation.digest(self.plan), "database_verified": True, "emby_verified": True}
        self.assertEqual(relocation.retire_sources(self.plan, proof)["removed"], 2)
        self.assertEqual((self.old / "unlisted.txt").read_text(), "keep")
        self.assertEqual(relocation.retire_sources(self.plan, proof)["already_absent"], 2)
        self.assertEqual(relocation.verify(self.plan, source_required=False)["files"], 2)

    def test_target_changed_prevents_source_retirement(self):
        relocation.stage(self.plan)
        (self.new / "UP主/Creator/video.nfo").write_bytes(b"new content")
        proof = {"plan_digest": relocation.digest(self.plan), "database_verified": True, "emby_verified": True}
        with self.assertRaisesRegex(ValueError, "target_content_changed"):
            relocation.retire_sources(self.plan, proof)
        self.assertTrue((self.old / "video.mp4").exists())

    def test_mapping_cannot_leave_target_or_overlap_roots(self):
        self.plan["files"][0]["new_relative_path"] = "../elsewhere.mp4"
        with self.assertRaisesRegex(ValueError, "invalid_relative"):
            relocation.stage(self.plan)
        self.plan["target_root"] = str(self.old)
        with self.assertRaisesRegex(ValueError, "overlapping_roots"):
            relocation.stage(self.plan)

    def test_preexisting_sidecar_hardlink_is_a_collision(self):
        parent = self.new / "UP主/Creator"
        parent.mkdir(parents=True)
        os.link(self.old / "video.nfo", parent / "video.nfo")
        with self.assertRaisesRegex(ValueError, "sidecar_is_hardlink"):
            relocation.stage(self.plan)
        self.assertFalse((parent / "video.mp4").exists())

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
                         "requires POSIX owner changes as root")
    def test_sidecar_permissions_and_owner_are_preserved(self):
        original = self.old / "video.nfo"
        os.chown(original, 65534, 65534)
        original.chmod(0o600)
        relocation.stage(self.plan)
        moved = (self.new / "UP主/Creator/video.nfo").stat()
        self.assertEqual((moved.st_uid, moved.st_gid, moved.st_mode & 0o777), (65534, 65534, 0o600))
