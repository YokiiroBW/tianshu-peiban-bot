"""Explicit external media mount fixtures; no Docker daemon or NAS access."""

import hashlib
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ops.resident_capacity import guard


class ExternalMediaBindTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "resident"
        self.root.mkdir()
        self.media = self.base / "媒体库" / "bilibili"
        self.media.mkdir(parents=True)
        docker = self.base / "docker"
        docker.write_text("fixture", encoding="utf-8")
        for filename in ("core.yaml", "obs.yaml", "platform.yaml"):
            (self.root / filename).write_text("{}\n", encoding="utf-8")
        self.config_path = self.base / "capacity.json"
        images = {
            service: "sha256:" + "a" * 64
            for names in guard.SERVICES.values() for service in names
        }
        binds = {}
        for service in images:
            (self.root / service).mkdir()
            binds[service] = [{
                "source": str(self.root / service), "target": f"/mnt/{service}", "read_only": False,
            }]
        self.value = {
            "deployment_root": str(self.root),
            "state_dir": str(self.base / "state"),
            "docker_binary": str(docker),
            "compose": {
                guard.CORE: {"workdir": str(self.root), "file": str(self.root / "core.yaml")},
                guard.OBS: {"workdir": str(self.root), "file": str(self.root / "obs.yaml")},
            },
            "platform_first_compose": {
                "workdir": str(self.root), "file": str(self.root / "platform.yaml"),
            },
            "images": images, "binds": binds, "free_paths": [str(self.root)],
            "min_free_bytes": 20 * guard.GIB, "max_deployment_bytes": 20 * guard.GIB,
            "poll_seconds": 5, "term_timeout_seconds": 10,
        }
        self.media_bind = {"source": str(self.media), "target": "/media/Bilibili", "read_only": False}

    def _register_media(self, entry=None):
        entry = dict(self.media_bind if entry is None else entry)
        self.value["external_media_binds"] = [entry]
        self.value["binds"]["platform"].append(dict(entry))

    def _load(self):
        self.config_path.write_text(json.dumps(self.value), encoding="utf-8")
        native_stat = Path.stat

        def config_stat(path, *args, **kwargs):
            metadata = native_stat(path, *args, **kwargs)
            if path == self.config_path:
                return SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o600)
            return metadata

        # Model only the Linux/root/config-mode gate on Windows. Source and
        # directory metadata, normalization and symlink checks remain real.
        linux_os = SimpleNamespace(name="posix", geteuid=lambda: 0, path=os.path)
        with patch.object(guard, "DEPLOYMENT_ROOT", str(self.root)), patch.object(
            guard, "os", linux_os
        ), patch.object(Path, "stat", config_stat):
            return guard.load_config(self.config_path)

    @staticmethod
    def _containers(config):
        return [
            guard.Container(
                f"{index:064x}", signature["project"], signature["service"],
                signature["workdir"], signature["compose_file"], signature["image"],
                "running", "unless-stopped",
                tuple(("bind", item["source"], item["target"], not item["read_only"])
                      for item in signature["binds"]),
            )
            for index, signature in enumerate(config.signatures().values(), 1)
        ]

    def test_existing_config_and_profiles_keep_internal_bind_constraint(self):
        for profile in (None, "resident-nine", guard.KNOWLEDGE_PROFILE):
            with self.subTest(profile=profile):
                if profile is not None:
                    self.value["service_profile"] = profile
                if profile == guard.KNOWLEDGE_PROFILE:
                    (self.root / "knowledge").mkdir()
                    self.value["images"]["knowledge"] = "sha256:" + "a" * 64
                    self.value["binds"]["knowledge"] = [{
                        "source": str(self.root / "knowledge"), "target": "/mnt/knowledge", "read_only": False,
                    }]
                config, digest = self._load()
                self.assertEqual(config.external_media_binds, ())
                self.assertEqual(digest, hashlib.sha256(self.config_path.read_bytes()).hexdigest())
                self.assertTrue(all("external_media_binds" not in item for item in config.signatures().values()))
                guard.assess(self._containers(config), config.signatures(), str(self.root))
        self.value["binds"]["platform"].append(self.media_bind)
        with self.assertRaisesRegex(guard.Unsafe, "capacity_config_invalid"):
            self._load()

    def test_declared_external_directory_has_exact_signature_and_free_floor(self):
        self._register_media()
        for read_only in (False, True):
            with self.subTest(read_only=read_only):
                self.value["external_media_binds"][0]["read_only"] = read_only
                self.value["binds"]["platform"][-1]["read_only"] = read_only
                config, _ = self._load()
                self.assertEqual(config.external_media_binds, tuple(self.value["external_media_binds"]))
                self.assertEqual(
                    config.signatures()[f"{guard.CORE}/platform"]["external_media_binds"],
                    config.external_media_binds,
                )
                self.assertIn(self.media, config.free_paths)
                guard.assess(self._containers(config), config.signatures(), str(self.root))
        (self.root / "platform" / "data").write_bytes(b"deployment")
        (self.media / "video").write_bytes(b"x" * 1024)
        expected_bytes = sum(item.stat().st_size for item in self.root.rglob("*") if item.is_file())
        with patch.object(
            guard.shutil, "disk_usage",
            side_effect=lambda path: SimpleNamespace(free=19 * guard.GIB if path == self.media else 30 * guard.GIB),
        ) as disk_usage:
            used, free = guard.capacity(config.root, config.free_paths)
            self.assertEqual(used, expected_bytes)
            self.assertEqual(free, 19 * guard.GIB)
            self.assertIn(self.media, [call.args[0] for call in disk_usage.call_args_list])
            containers = self._containers(config)
            with self.assertRaisesRegex(guard.Unsafe, "host_free_floor_reached"):
                guard.sample(config, {"signatures": config.signatures(), "root": str(self.root),
                                      "ids": {f"{item.project}/{item.service}": item.id for item in containers}},
                             SimpleNamespace(snapshot=lambda: containers))

    def test_media_bind_is_platform_only(self):
        self._register_media()
        self.value["binds"]["companion"].append(dict(self.media_bind))
        with self.assertRaisesRegex(guard.Unsafe, "capacity_config_invalid"):
            self._load()

    def test_target_must_be_normal_media_subdirectory(self):
        for target in ("/media", "/mnt/Bilibili", "/media/../other", "/media//Bilibili", "media/Bilibili"):
            with self.subTest(target=target):
                self.value["binds"]["platform"] = self.value["binds"]["platform"][:1]
                self._register_media({**self.media_bind, "target": target})
                with self.assertRaisesRegex(guard.Unsafe, "capacity_config_invalid"):
                    self._load()

    def test_external_source_must_be_existing_external_directory(self):
        file = self.base / "video.mp4"
        file.write_bytes(b"fixture")
        for source in (self.root / "platform", self.base / "missing", file):
            with self.subTest(source=source):
                self.value["binds"]["platform"] = self.value["binds"]["platform"][:1]
                self._register_media({**self.media_bind, "source": str(source)})
                with self.assertRaises(guard.Unsafe):
                    self._load()

    def test_symlink_metadata_at_source_and_parent_is_refused(self):
        link = self.base / "media-link"
        link.mkdir()
        (link / self.media.name).mkdir()
        native_lstat = Path.lstat

        def linked_metadata(path):
            if path == link:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
            return native_lstat(path)

        for source in (link, link / self.media.name):
            with self.subTest(source=source):
                self.value["binds"]["platform"] = self.value["binds"]["platform"][:1]
                self._register_media({**self.media_bind, "source": str(source)})
                with patch.object(Path, "lstat", linked_metadata), self.assertRaisesRegex(
                    guard.Unsafe, "symlink_path_refused"
                ):
                    self._load()

    def test_allowlist_must_match_platform_binds_in_full(self):
        self._register_media()
        replacement = self.base / "other-media"
        replacement.mkdir()
        for changed in (
            {**self.media_bind, "source": str(replacement)},
            {**self.media_bind, "target": "/media/Other"},
            {**self.media_bind, "read_only": True},
        ):
            with self.subTest(changed=changed):
                self.value["binds"]["platform"][-1] = changed
                with self.assertRaisesRegex(guard.Unsafe, "capacity_config_invalid"):
                    self._load()
        self.value["binds"]["platform"] = self.value["binds"]["platform"][:1]
        with self.assertRaisesRegex(guard.Unsafe, "capacity_config_invalid"):
            self._load()

    def test_owned_requires_registered_source_target_and_read_only(self):
        self._register_media()
        config, _ = self._load()
        platform = next(item for item in self._containers(config) if item.service == "platform")
        signatures = config.signatures()
        self.assertTrue(guard._owned(platform, signatures, str(self.root)))
        self.assertFalse(guard._owned(platform, replace(config, external_media_binds=()).signatures(), str(self.root)))
        for mount in (
            ("bind", str(self.base / "other-media"), "/media/Bilibili", True),
            ("bind", str(self.media), "/media/Other", True),
            ("bind", str(self.media), "/media/Bilibili", False),
        ):
            with self.subTest(mount=mount):
                self.assertFalse(guard._owned(replace(platform, mounts=(*platform.mounts[:-1], mount)), signatures, str(self.root)))
        companion = next(item for item in self._containers(config) if item.service == "companion")
        signatures[f"{guard.CORE}/companion"]["external_media_binds"] = config.external_media_binds
        signatures[f"{guard.CORE}/companion"]["binds"].append(self.media_bind)
        self.assertFalse(guard._owned(replace(companion, mounts=(*companion.mounts, platform.mounts[-1])), signatures, str(self.root)))

    def test_shared_emby_media_mount_is_foreign_only_for_deployment_root(self):
        self._register_media()
        config, _ = self._load()
        containers = self._containers(config)
        emby = guard.Container("f" * 64, "emby", "emby", "/emby", "/emby/compose.yaml", "emby:fixture",
                               "running", "unless-stopped", (("bind", str(self.media), "/video", False),))
        owned, conflicts = guard.classify([*containers, emby], config.signatures(), str(self.root))
        self.assertEqual(len(owned), 9)
        self.assertEqual(conflicts, [])
        _, conflicts = guard.classify(
            [*containers, replace(emby, mounts=(("bind", str(self.root), "/video", False),))],
            config.signatures(), str(self.root),
        )
        self.assertEqual(conflicts, ["foreign_deployment_mount"])


if __name__ == "__main__":
    unittest.main()
