"""One-container generation with thirteen exact, cold rollback containers."""

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_deployed_contract as deployed
import test_guard as fixtures
from ops.resident_capacity import guard


class UnifiedGuardTests(unittest.TestCase):
    def setUp(self):
        self.loader = deployed.DeployedContractTests()
        self.loader.setUp()
        self.fixture = self.loader.fixture
        self.addCleanup(self.fixture.doCleanups)
        self.base = Path(self.fixture.temp.name)
        root = self.fixture.config.root
        install = root / "unified-20261027"
        install.mkdir()
        compose_file = install / "compose.json"
        compose_file.write_text("{}")
        self.entry = {"workdir": str(install), "file": str(compose_file)}
        data = self.base / "external-data"
        data.mkdir()
        self.data = {
            "source": str(data),
            "target": "/data",
            "read_only": False,
        }
        self.media = self.loader.media
        binds = [
            {"source": str(install), "target": "/config", "read_only": True},
            self.media,
            self.data,
        ]
        self.retired = [
            replace(
                self.fixture.containers[0],
                id=f"{index:064x}",
                project=project,
                service=service,
                status="exited",
                restart="no",
            )
            for index, (project, service) in enumerate(
                [
                    (guard.CORE, name)
                    for name in (
                        "platform",
                        "companion",
                        "memory",
                        "knowledge",
                        "gateway",
                    )
                ]
                + [(guard.OBS, name) for name in sorted(guard.SERVICES[guard.OBS])]
                + [
                    ("tianshu-onebot", "onebot"),
                    ("tianshu-comics-io", "inputs"),
                    ("tianshu-comics-io", "publisher"),
                ],
                1,
            )
        ]
        self.config = replace(
            self.fixture.config,
            compose={guard.UNIFIED_PROJECT: self.entry},
            platform_first_compose=self.entry,
            images={"tianshu": "sha256:" + "c" * 64},
            binds={"tianshu": binds},
            service_profile=guard.UNIFIED_PROFILE,
            external_media_binds=(self.media,),
            external_data_binds={"tianshu": (self.data,)},
            retired_ids=tuple(row.id for row in self.retired),
        )
        self.fixture.config = self.config
        signature = self.config.signatures()[f"{guard.UNIFIED_PROJECT}/tianshu"]
        self.owner = guard.Container(
            "e" * 64,
            signature["project"],
            signature["service"],
            signature["workdir"],
            signature["compose_file"],
            signature["image"],
            "running",
            "unless-stopped",
            tuple(
                ("bind", row["source"], row["target"], not row["read_only"])
                for row in binds
            ),
        )
        self.fixture.containers = [self.owner]
        self.containers = [self.owner, *self.retired]

    def configuration(self):
        value = {
            "deployment_root": str(self.config.root),
            "state_dir": str(self.config.state_dir),
            "docker_binary": self.entry["file"],
            "service_profile": guard.UNIFIED_PROFILE,
            "compose": {guard.UNIFIED_PROJECT: self.entry},
            "platform_first_compose": self.entry,
            "images": self.config.images,
            "binds": self.config.binds,
            "external_media_binds": [self.media],
            "external_data_binds": {"tianshu": [self.data]},
            "retired_ids": list(self.config.retired_ids),
            "free_paths": [str(self.config.root)],
            "min_free_bytes": 20 * guard.GIB,
            "max_deployment_bytes": 20 * guard.GIB,
            "poll_seconds": 5,
            "term_timeout_seconds": 10,
        }
        return json.loads(json.dumps(value))

    def sample(self, docker):
        with patch.object(guard, "capacity", return_value=(100, 30 * guard.GIB)):
            return guard.sample(self.config, self.fixture._marker(), docker)

    def private_state(self):
        return patch.object(guard, "_state_dir", return_value=self.config.state_dir)

    def test_load_inside_installation_keeps_exact_image_and_external_floors(self):
        config = self.loader.load(self.configuration())
        self.assertEqual(config.compose, self.config.compose)
        self.assertEqual(config.platform_first_compose, self.entry)
        self.assertEqual(config.images, self.config.images)
        self.assertEqual(config.retired_ids, self.config.retired_ids)
        self.assertEqual(
            set(config.free_paths),
            {
                config.root,
                Path(self.entry["workdir"]),
                Path(self.media["source"]),
                Path(self.data["source"]),
            },
        )
        self.assertEqual(
            set(guard.assess(self.containers, config.signatures(), str(config.root))),
            guard.UNIFIED_KEYS,
        )

    def test_retired_set_is_required_exact_and_new_profile_only(self):
        for change in ("missing", "short", "duplicate", "invalid", "legacy"):
            value = copy.deepcopy(self.configuration())
            if change == "missing":
                value.pop("retired_ids")
            elif change == "short":
                value["retired_ids"].pop()
            elif change == "duplicate":
                value["retired_ids"][-1] = value["retired_ids"][0]
            elif change == "invalid":
                value["retired_ids"][0] = "short-id"
            else:
                value["service_profile"] = "resident-nine"
            with self.subTest(change=change), self.assertRaises(guard.Unsafe):
                self.loader.load(value)

    def test_workdir_and_file_cannot_escape_installation_or_change_legacy_policy(self):
        other_file = self.config.root / "outside-installation.json"
        other_file.write_text("{}")
        for entry in (
            {"workdir": str(self.base), "file": self.entry["file"]},
            {"workdir": self.entry["workdir"], "file": str(other_file)},
        ):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                guard._compose_entry(entry, self.config.root, inside_root=True)
        with self.assertRaises(ValueError):
            guard._compose_entry(self.entry, self.config.root)
        self.assertEqual(
            guard._compose_entry(self.entry, self.config.root, inside_root=True),
            self.entry,
        )
        another_file = Path(self.entry["workdir"]) / "different.json"
        another_file.write_text("{}")
        value = copy.deepcopy(self.configuration())
        value["platform_first_compose"]["file"] = str(another_file)
        with self.assertRaises(guard.Unsafe):
            self.loader.load(value)

    def test_workdir_symlink_is_not_a_root_internal_directory(self):
        link = self.config.root / "linked-installation"
        try:
            link.symlink_to(self.base, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks requires an unavailable Windows privilege")
        with self.assertRaisesRegex(guard.Unsafe, "symlink_path_refused"):
            guard._compose_entry(
                {"workdir": str(link), "file": self.entry["file"]},
                self.config.root,
                inside_root=True,
            )

    def test_only_exact_cold_ids_are_exempt_and_unknown_old_projects_conflict(self):
        for status in ("exited", "dead", "created"):
            rows = [self.owner, *(replace(row, status=status) for row in self.retired)]
            self.assertEqual(
                len(
                    guard.assess(rows, self.config.signatures(), str(self.config.root))
                ),
                1,
            )
        original = self.retired[0]
        for status, restart in (
            ("running", "no"),
            ("paused", "no"),
            (None, "no"),
            ("exited", None),
            ("exited", "unless-stopped"),
        ):
            rows = [
                self.owner,
                replace(original, status=status, restart=restart),
                *self.retired[1:],
            ]
            with (
                self.subTest(status=status, restart=restart),
                self.assertRaisesRegex(
                    guard.Unsafe,
                    "retired_container_not_cold",
                ),
            ):
                guard.assess(rows, self.config.signatures(), str(self.config.root))
        for project in sorted(guard.RETIRED_PROJECTS):
            unknown = replace(original, id="f" * 64, project=project, mounts=())
            with (
                self.subTest(project=project),
                self.assertRaisesRegex(
                    guard.Unsafe,
                    "resident_identity_conflict",
                ),
            ):
                guard.assess(
                    [*self.containers, unknown],
                    self.config.signatures(),
                    str(self.config.root),
                )

    def test_unified_owner_still_requires_all_exact_metadata(self):
        for field, changed, reason in (
            ("id", "f" * 64, "resident_container_identity_changed"),
            ("status", "exited", "resident_service_not_running"),
            ("restart", "no", "resident_restart_policy_changed"),
            ("image", "sha256:" + "d" * 64, "resident_identity_conflict"),
            (
                "compose_file",
                str(self.config.root / "other.json"),
                "resident_identity_conflict",
            ),
            ("mounts", self.owner.mounts[:-1], "resident_identity_conflict"),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(guard.Unsafe, reason),
            ):
                guard.assess(
                    [replace(self.owner, **{field: changed}), *self.retired],
                    self.config.signatures(),
                    str(self.config.root),
                    self.fixture._marker()["ids"],
                )
        with self.assertRaisesRegex(guard.Unsafe, "unified_one_service_required"):
            guard.assess(self.retired, self.config.signatures(), str(self.config.root))

    def test_external_media_and_data_remain_exact_and_per_owner(self):
        value = self.configuration()
        for change in (
            "unregistered-media",
            "unregistered-data",
            "different-owner",
            "unused",
            "rw",
        ):
            bad = copy.deepcopy(value)
            if change == "unregistered-media":
                bad["external_media_binds"] = []
            elif change == "unregistered-data":
                bad["external_data_binds"] = {}
            elif change == "different-owner":
                bad["external_data_binds"] = {"platform": [self.data]}
            elif change == "unused":
                bad["binds"]["tianshu"].pop()
            else:
                bad["external_data_binds"]["tianshu"][0]["read_only"] = True
            with self.subTest(change=change), self.assertRaises(guard.Unsafe):
                self.loader.load(bad)
        foreign = replace(
            self.owner,
            id="f" * 64,
            project="other-product",
            mounts=(self.owner.mounts[1],),
        )
        self.assertEqual(
            len(
                guard.assess(
                    [*self.containers, foreign],
                    self.config.signatures(),
                    str(self.config.root),
                )
            ),
            1,
        )
        foreign = replace(foreign, mounts=(self.owner.mounts[0],))
        with self.assertRaisesRegex(guard.Unsafe, "foreign_deployment_mount"):
            guard.assess(
                [*self.containers, foreign],
                self.config.signatures(),
                str(self.config.root),
            )

    def test_real_cli_refresh_detects_old_comics_resurrection_after_warm_cache(self):
        docker, path = self.fixture._cli(containers=self.containers)
        self.assertEqual(self.sample(docker)["services"], 1)
        initial = self.fixture._cli_events(path)
        self.assertEqual(docker._foreign_creation, {})
        self.sample(docker)
        events = self.fixture._cli_events(path)[len(initial) :]
        self.assertEqual(set(events[-1]["ids"]), {row.id for row in self.containers})
        value = json.loads(path.read_text())
        comics = next(
            row for row in value["containers"] if row[1] == "tianshu-comics-io"
        )
        comics[6] = "running"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(guard.Unsafe, "retired_container_not_cold"):
            self.sample(docker)

    def test_arm_and_stop_one_new_owner_preserve_old_generation_and_other_media_users(
        self,
    ):
        old_state = self.base / "old-capacity-generation"
        old_state.mkdir()
        preserved = {
            name: (name + " old evidence").encode()
            for name in ("armed.json", "failure.json", "stop-old.json")
        }
        for name, data in preserved.items():
            (old_state / name).write_bytes(data)
        unrelated = replace(
            self.owner,
            id="f" * 64,
            project="other-product",
            mounts=(self.owner.mounts[1],),
        )
        docker, path = self.fixture._cli(containers=[*self.containers, unrelated])
        # A reused Docker object must refresh both the new owner and cold
        # OneBot/comic-files IDs whose creation metadata was cached earlier.
        docker.snapshot()
        self.assertNotIn(self.owner.id, docker._foreign_creation)
        self.assertTrue(set(self.config.retired_ids) & set(docker._foreign_creation))
        with (
            self.private_state(),
            patch.object(guard, "capacity", return_value=(100, 30 * guard.GIB)),
        ):
            self.assertEqual(guard.arm(self.config, "a" * 64, docker)["services"], 1)
            marker = guard._armed(self.config.state_dir)
            self.assertEqual(set(marker["ids"].values()), {self.owner.id})
            self.assertEqual(
                guard._retired_ids(marker["signatures"]), set(self.config.retired_ids)
            )
            receipt = guard.fail_close(
                self.config.state_dir, "deployment_budget_reached", docker
            )
        self.assertEqual(receipt["status"], "stopped")
        self.assertEqual(receipt["target_ids"], [self.owner.id])
        effects = [
            item
            for item in self.fixture._cli_events(path)
            if item["stage"] in {"update", "term"}
        ]
        self.assertEqual(
            effects,
            [
                {"stage": "update", "ids": [self.owner.id]},
                {"stage": "term", "ids": [self.owner.id]},
            ],
        )
        current = {row[0]: row for row in json.loads(path.read_text())["containers"]}
        self.assertEqual(current[unrelated.id][6:8], ["running", "unless-stopped"])
        self.assertTrue(
            all(current[row.id][6:8] == ["exited", "no"] for row in self.retired)
        )
        self.assertEqual(
            {name: (old_state / name).read_bytes() for name in preserved}, preserved
        )

    def test_retired_resurrection_fail_close_never_stops_the_old_id(self):
        self.fixture._write_marker()
        revived = replace(self.retired[-1], status="running")
        docker = fixtures.FakeDocker([self.owner, *self.retired[:-1], revived])
        with self.private_state():
            receipt = guard.fail_close(
                self.config.state_dir, "retired_container_not_cold", docker
            )
        self.assertEqual(receipt["status"], "unconfirmed")
        self.assertIn("retired_container_not_cold", receipt["errors"])
        self.assertEqual(docker.updated, [self.owner.id])
        self.assertEqual(docker.termed, [self.owner.id])
        self.assertEqual(docker.containers[revived.id].status, "running")

    def test_marker_cannot_promote_a_retired_id_to_a_stop_target(self):
        marker = self.fixture._marker()
        marker["ids"][f"{guard.UNIFIED_PROJECT}/tianshu"] = self.retired[0].id
        (self.config.state_dir / "armed.json").write_text(json.dumps(marker))
        docker = fixtures.FakeDocker(self.containers)
        with (
            self.private_state(),
            self.assertRaisesRegex(guard.Unsafe, "armed_marker_invalid"),
        ):
            guard.fail_close(self.config.state_dir, "service_exit", docker)
        self.assertEqual(docker.events, [])


if __name__ == "__main__":
    unittest.main()
