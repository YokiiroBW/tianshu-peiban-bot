"""Preserve the deployed music/media contract while enabling TS-175's guard."""

import copy
import json
import os
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import test_guard as fixtures
from ops.resident_capacity import guard


class DeployedContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResidentCapacityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base = Path(self.fixture.temp.name)
        external = self.base / "external-music"
        external.mkdir()
        self.media = {"source": str(external), "target": "/media/Music", "read_only": False}
        binds = copy.deepcopy(self.fixture.config.binds)
        binds["platform"].append(self.media)
        images = {**self.fixture.config.images, "platform": "sha256:" + "b" * 64}
        self.fixture.config = replace(self.fixture.config, images=images, binds=binds, external_media_binds=(self.media,))
        self.fixture.containers = [
            replace(item, image=images["platform"], mounts=(*item.mounts, ("bind", self.media["source"], self.media["target"], True)))
            if item.service == "platform" else item
            for item in self.fixture.containers
        ]
        self.fixture.docker = fixtures.FakeDocker(self.fixture.containers)

    def test_registered_media_and_local_image_id_remain_exact_owned_identity(self):
        config = self.fixture.config
        owned = guard.assess(self.fixture.containers, config.signatures(), str(config.root))
        self.assertEqual(len(owned), 9)
        platform = next(item for item in self.fixture.containers if item.service == "platform")
        for mount in (
            ("bind", self.media["source"], "/media/Other", True),
            ("bind", self.media["source"], self.media["target"], False),
            ("bind", str(self.base / "unregistered"), self.media["target"], True),
        ):
            with self.subTest(mount=mount), self.assertRaisesRegex(guard.Unsafe, "resident_identity_conflict"):
                changed = replace(platform, mounts=(*platform.mounts[:-1], mount))
                guard.assess([changed if row.id == platform.id else row for row in self.fixture.containers], config.signatures(), str(config.root))
        self.assertNotIn("external_media_binds", config.signatures()[f"{guard.CORE}/memory"])

    def test_failure_stops_registered_platform_but_never_foreign_media_user(self):
        self.fixture._write_marker()
        foreign = guard.Container(
            "e" * 64, "other-product", "music", "/elsewhere", "/elsewhere/compose.json",
            "sha256:" + "f" * 64, "running", "unless-stopped",
            (("bind", self.media["source"], self.media["target"], True),),
        )
        docker = fixtures.FakeDocker([*self.fixture.containers, foreign])
        with patch.object(guard, "_state_dir", return_value=self.fixture.config.state_dir):
            receipt = guard.fail_close(self.fixture.config.state_dir, "deployment_budget_reached", docker)
        self.assertEqual(receipt["status"], "stopped")
        self.assertEqual(set(docker.termed), {item.id for item in self.fixture.containers})
        self.assertNotIn(foreign.id, docker.updated)
        self.assertEqual(docker.containers[foreign.id].status, "running")

    def configuration(self):
        config = self.fixture.config
        compose = {}
        for project, suffix in ((guard.CORE, "core"), (guard.OBS, "obs")):
            path = config.root / (suffix + ".json")
            path.write_text("{}")
            compose[project] = {"workdir": str(config.root), "file": str(path)}
        first = config.root / "platform.json"
        first.write_text("{}")
        return {
            "deployment_root": str(config.root), "state_dir": str(config.state_dir),
            "docker_binary": str(first), "compose": compose,
            "platform_first_compose": {"workdir": str(config.root), "file": str(first)},
            "images": config.images, "binds": copy.deepcopy(config.binds),
            "external_media_binds": [self.media], "free_paths": [str(config.root)],
            "min_free_bytes": 20 * guard.GIB, "max_deployment_bytes": 20 * guard.GIB,
            "poll_seconds": 5, "term_timeout_seconds": 10,
        }

    def load(self, value):
        # Model only Linux ownership checks on Windows; all directories/files
        # and every bind decision still use the real isolated filesystem.
        path = self.base / "capacity.json"
        path.write_text(json.dumps(value))
        config_file = Mock()
        config_file.stat.return_value = types.SimpleNamespace(st_uid=0, st_mode=0o600)
        config_file.read_bytes.side_effect = path.read_bytes
        linux_os = types.SimpleNamespace(name="posix", geteuid=lambda: 0, path=os.path)

        def safe(raw, **kwargs):
            return config_file if str(raw) == str(path) else Path(raw)

        with patch.object(guard, "os", linux_os), patch.object(guard, "_safe_path", side_effect=safe), patch.object(guard, "DEPLOYMENT_ROOT", str(self.fixture.config.root)):
            return guard.load_config(path)[0]

    def test_load_preserves_external_free_space_sampling_and_image_id(self):
        config = self.load(self.configuration())
        self.assertEqual(config.external_media_binds, (self.media,))
        self.assertIn(Path(self.media["source"]), config.free_paths)
        self.assertEqual(config.images["platform"], "sha256:" + "b" * 64)
        self.assertIsNotNone(guard.IMAGE.fullmatch(config.images["memory"]))

    def test_data_mounts_keep_internal_paths_and_are_owned_per_service(self):
        value = self.configuration()
        directory = self.base / "hdd-logs"
        directory.mkdir()
        entry = {"source": str(directory), "target": "/var/log/tianshu", "read_only": False}
        value["binds"]["memory"].append(entry)
        value["external_data_binds"] = {"memory": [copy.deepcopy(entry)]}
        config = self.load(value)
        self.assertIn(directory, config.free_paths)
        self.assertEqual(config.max_deployment_bytes, 20 * guard.GIB)
        rows = []
        for row in self.fixture.containers:
            signature = config.signatures()[f"{row.project}/{row.service}"]
            row = replace(row, compose_file=signature["compose_file"], workdir=signature["workdir"])
            if row.service == "memory":
                row = replace(row, mounts=(*row.mounts, ("bind", str(directory), entry["target"], True)))
            rows.append(row)
        self.assertEqual(len(guard.assess(rows, config.signatures(), str(config.root))), 9)
        for changed in ("owner", "read_only", "unused", "source"):
            bad = copy.deepcopy(value)
            if changed == "owner":
                bad["external_data_binds"] = {"companion": [entry]}
            elif changed == "read_only":
                bad["external_data_binds"]["memory"][0]["read_only"] = True
            elif changed == "unused":
                bad["binds"]["memory"].pop()
            else:
                bad["external_data_binds"]["memory"][0]["source"] = str(config.root.parent)
            with self.subTest(changed=changed), self.assertRaises(guard.Unsafe):
                self.load(bad)

    def test_load_rejects_unregistered_unused_nonplatform_or_nonmedia_bind(self):
        original = self.configuration()
        cases = []
        value = copy.deepcopy(original)
        value["external_media_binds"] = []
        cases.append(value)
        value = copy.deepcopy(original)
        value["binds"]["platform"] = value["binds"]["platform"][:-1]
        cases.append(value)
        value = copy.deepcopy(original)
        value["binds"]["memory"].append(self.media)
        cases.append(value)
        value = copy.deepcopy(original)
        value["external_media_binds"][0]["target"] = "/etc/foreign"
        cases.append(value)
        for value in cases:
            with self.subTest(value=value["external_media_binds"]), self.assertRaisesRegex(guard.Unsafe, "capacity_config_invalid"):
                self.load(value)


if __name__ == "__main__":
    unittest.main()
