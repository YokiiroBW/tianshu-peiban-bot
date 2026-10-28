"""Existing installations retain settings, mounts and rollback semantics."""
import ast
import copy
from pathlib import Path
import tempfile
import unittest

from deploy.infrastructure import resident
from deploy.infrastructure.bundle import Refused


class ResidentUpdateTests(unittest.TestCase):
    def setUp(self):
        self.media = {"source": "/volume1/Documents/Books", "target": "/media/Books", "read_only": False}
        self.music = {"source": "/volume1/Music", "target": "/media/Music", "read_only": False}
        self.ssh = {"source": "/resident/ssh/identity", "target": "/run/tianshu-infrastructure/identity", "read_only": True}
        self.service = {"image": "old", "mem_limit": "1g", "environment": {"KEEP": "yes"},
                        "volumes": [{"type": "bind", **value} for value in (self.music, self.ssh)],
                        "tmpfs": [resident.SAMPLE_STATE + ":size=128m,mode=0700", "/tmp:size=64m"]}
        self.templates = {"core": {"services": {"platform": copy.deepcopy(self.service), "memory": {"image": "untouched"}}},
                          "platform": {"services": {"platform": copy.deepcopy(self.service)}}, "obs": {"keep": True}}
        self.capacity = {"images": {"platform": "old", "memory": "untouched"}, "binds": {"platform": [self.music, self.ssh]},
                         "external_media_binds": [self.music], "min_free_bytes": 20 * 1024 ** 3}
        self.infrastructure = {"directory": "/state/external", "poll_seconds": 30}
        self.settings = {"infrastructure": self.infrastructure, "web_external": {"directory": "/state/external"},
                         "media": {"original_targets": ["video", "music"]}, "other": "keep"}

    def test_update_only_changes_image_and_explicit_new_media_binding(self):
        before = copy.deepcopy((self.templates, self.capacity, self.settings))
        templates, capacity = resident.patch_templates(self.templates, self.capacity, "new", [], preserve_existing=True, media_binds=[self.media])
        self.assertEqual((self.templates, self.capacity, self.settings), before)
        for name in ("core", "platform"):
            service = templates[name]["services"]["platform"]
            self.assertEqual(service["image"], "new")
            self.assertEqual(service["volumes"][:-1], self.service["volumes"])
            self.assertEqual(service["volumes"][-1]["target"], "/media/Books")
            for key in ("tmpfs", "mem_limit", "environment"):
                self.assertEqual(service[key], self.service[key])
        self.assertEqual(templates["core"]["services"]["memory"], self.templates["core"]["services"]["memory"])
        self.assertEqual(capacity["binds"]["platform"], [self.music, self.ssh, self.media])
        self.assertEqual(capacity["external_media_binds"], [self.music, self.media])
        self.assertEqual(resident.merge_settings(self.settings, self.infrastructure, preserve_existing=True), self.settings)
        with self.assertRaises(Refused):
            resident.patch_templates(templates, capacity, "other", [], preserve_existing=True, media_binds=[self.media])
        with self.assertRaises(Refused):
            resident.patch_templates(self.templates, self.capacity, "new", [self.ssh], preserve_existing=True)
        with self.assertRaises(Refused):
            resident.merge_settings(self.settings, {"directory": "/different"}, preserve_existing=True)

    def test_real_rollback_preparation_keeps_existing_infrastructure_and_book_files(self):
        templates, capacity = resident.patch_templates(self.templates, self.capacity, "new", [], preserve_existing=True, media_binds=[self.media])
        rows = [{"service": "platform", "mounts": [
            {"Type": "bind", "Source": bind["source"], "Destination": bind["target"], "RW": not bind["read_only"]}
            for bind in (self.music, self.ssh, self.media)] + [{"Type": "tmpfs", "Destination": resident.SAMPLE_STATE}]}]
        worker = ast.parse(Path(resident.__file__).with_name("resident_worker.py").read_text(encoding="utf-8"))
        function = next(n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name == "rollback_release")
        start = next(i for i,n in enumerate(function.body) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "settings_path")
        end = next(i for i,n in enumerate(function.body) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "before")
        scope = {"R": Path("/resident"), "read": lambda _: copy.deepcopy(self.settings), "preserve_infrastructure": True,
                 "original": {"infrastructure": {"credential_binds": [], "settings": self.infrastructure}, "media_binds": [self.media], "previous_images": {"platform": {"image_id": "old"}}},
                 "templates": templates, "cap": capacity, "rows": rows, "copy": copy, "require": resident.require, "SAMPLE_STATE": resident.SAMPLE_STATE}
        exec(compile(ast.Module(body=function.body[start:end], type_ignores=[]), "rollback-update-preparation", "exec"), scope)
        self.assertEqual(scope["settings"], self.settings)
        self.assertEqual(capacity["external_media_binds"], [self.music])
        self.assertEqual(capacity["binds"]["platform"], [self.music, self.ssh])
        self.assertTrue(scope["FAILURE_EXPECT_SAMPLING"])
        for name in ("core", "platform"):
            self.assertEqual(templates[name]["services"]["platform"]["tmpfs"], self.service["tmpfs"])
            self.assertEqual(templates[name]["services"]["platform"]["volumes"], self.service["volumes"])
        self.assertEqual(len(scope["expected"][0]["mounts"]), 3)

    def test_update_directory_preparation_does_not_rewrite_existing_directory_permissions(self):
        worker = ast.parse(Path(resident.__file__).with_name("resident_worker.py").read_text(encoding="utf-8"))
        functions = [n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name in {"check_private_inputs", "install_private_inputs"}]
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            existing = Path(folder) / "existing"; existing.mkdir()
            marker = existing / "book.epub"; marker.write_bytes(b"existing book")
            calls = []
            scope = {"Path": Path, "os": SimpleNamespace(chown=lambda *args: calls.append(args)), "require": resident.require,
                     "RELEASE": {"preserve_infrastructure": True, "media_binds": [{**self.media, "source": str(existing)}]}}
            exec(compile(ast.Module(body=functions, type_ignores=[]), "prepare-book-directory", "exec"), scope)
            scope["install_private_inputs"]()
            self.assertEqual(calls, [])
            self.assertEqual(marker.read_bytes(), b"existing book")
            scope["RELEASE"]["media_binds"][0]["source"] = str(Path(folder) / "new-books")
            scope["install_private_inputs"]()
            self.assertEqual(len(calls), 1)
            self.assertTrue((Path(folder) / "new-books").is_dir())

    def test_legacy_stage_accepts_current_recovery_policy_and_still_rejects_unlisted_files(self):
        body = "assert name in {'ops/__init__.py','ops/recovery/__init__.py','ops/recovery/resident_snapshot.py','ops/recovery/safety.py'}\n"
        actions = resident.existing_actions("STAGE_BODY=" + repr(body) + "\nLAUNCH_BODY='pass'\nPOLL_BODY='pass'\n")
        for name in resident.RECOVERY_FILES:
            exec(actions["STAGE_BODY"], {"name": name})
        with self.assertRaises(AssertionError):
            exec(actions["STAGE_BODY"], {"name": "unlisted.py"})


if __name__ == "__main__":
    unittest.main()
