"""Isolated metadata/Docker fixtures; no Docker daemon or NAS access."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ops.resident_capacity import guard


class FakeDocker:
    def __init__(self, containers):
        self.containers = {item.id: item for item in containers}
        self.updated = []
        self.termed = []
        self.events = []
        self.fail = False

    def snapshot(self):
        if self.fail:
            raise guard.Unsafe("docker_unavailable")
        return list(self.containers.values())

    def disable_restart(self, container_id):
        if self.fail:
            raise guard.Unsafe("docker_unavailable")
        self.updated.append(container_id)
        self.events.append(("update", container_id))
        self.containers[container_id] = replace(self.containers[container_id], restart="no")

    def inspect_one(self, container_id):
        if self.fail:
            raise guard.Unsafe("docker_unavailable")
        return self.containers[container_id]

    def terminate(self, container_id):
        if self.fail:
            raise guard.Unsafe("docker_unavailable")
        self.termed.append(container_id)
        self.events.append(("term", container_id))
        self.containers[container_id] = replace(self.containers[container_id], status="exited")


class ResidentCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "resident"
        state = base / "state"
        root.mkdir()
        state.mkdir()
        core = base / "final-core"
        obs = base / "final-obs"
        first = base / "first-core"
        for directory in (core, obs, first):
            directory.mkdir()
            (directory / "compose.yaml").write_text("{}\n", encoding="utf-8")
        compose = {
            guard.CORE: {"workdir": str(core), "file": str(core / "compose.yaml")},
            guard.OBS: {"workdir": str(obs), "file": str(obs / "compose.yaml")},
        }
        platform_first = {"workdir": str(first), "file": str(first / "compose.yaml")}
        images = {service: service + "@sha256:" + "a" * 64 for names in guard.SERVICES.values() for service in names}
        self.config = guard.Config(root, state, Path("/usr/bin/docker"), compose, platform_first, images, (root,), 20 * guard.GIB, 20 * guard.GIB, 5, 10)
        self.containers = []
        for index, key in enumerate(sorted(guard.ALL_KEYS), 1):
            signature = self.config.signatures()[key]
            self.containers.append(guard.Container(
                f"{index:064x}", signature["project"], signature["service"],
                signature["workdir"], signature["compose_file"], signature["image"],
                "running", "unless-stopped", (("bind", str(root / signature["service"])),),
            ))
        self.docker = FakeDocker(self.containers)

    def _marker(self):
        return {
            "config_sha256": "a" * 64,
            "root": str(self.config.root),
            "docker": str(self.config.docker),
            "ids": {f"{item.project}/{item.service}": item.id for item in self.containers},
            "signatures": self.config.signatures(),
            "term_timeout_seconds": 10,
        }

    def _write_marker(self):
        (self.config.state_dir / "armed.json").write_text(json.dumps(self._marker()), encoding="utf-8")

    def test_arm_locks_exact_nine_ids_after_full_start(self):
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard, "capacity", return_value=(100, 30 * guard.GIB)
        ):
            result = guard.arm(self.config, "a" * 64, self.docker)
        self.assertEqual(result["status"], "armed")
        self.assertEqual(guard._armed(self.config.state_dir)["ids"], self._marker()["ids"])
        self.assertNotEqual(
            self.config.signatures()[f"{guard.CORE}/platform"]["compose_file"],
            self.config.compose[guard.CORE]["file"],
        )
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            with self.assertRaisesRegex(guard.Unsafe, "capacity_guard_already_armed_or_latched"):
                guard.arm(self.config, "a" * 64, self.docker)

    def test_first_stage_missing_obs_refuses_without_marker(self):
        self.docker = FakeDocker(self.containers[:4])
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            with self.assertRaisesRegex(guard.Unsafe, "resident_nine_services_required"):
                guard.arm(self.config, "a" * 64, self.docker)
        self.assertFalse((self.config.state_dir / "armed.json").exists())

    def test_project_name_alone_never_authorizes_foreign_container(self):
        wrong = replace(self.containers[0], workdir="/another/project", id="f" * 64)
        with self.assertRaisesRegex(guard.Unsafe, "resident_identity_conflict"):
            guard.assess([*self.containers, wrong], self.config.signatures(), str(self.config.root))

    def test_changed_id_or_failed_service_refuses_running_sample(self):
        self.docker.containers[self.containers[0].id] = replace(self.containers[0], status="exited")
        with patch.object(guard, "capacity", return_value=(100, 30 * guard.GIB)):
            with self.assertRaisesRegex(guard.Unsafe, "resident_service_not_running"):
                guard.sample(self.config, self._marker(), self.docker)
        self.docker.containers.pop(self.containers[0].id)
        self.docker.containers["e" * 64] = replace(self.containers[0], id="e" * 64)
        with patch.object(guard, "capacity", return_value=(100, 30 * guard.GIB)):
            with self.assertRaisesRegex(guard.Unsafe, "resident_container_identity_changed"):
                guard.sample(self.config, self._marker(), self.docker)

    def test_deployment_budget_and_each_free_floor(self):
        with patch.object(guard, "capacity", return_value=(20 * guard.GIB, 40 * guard.GIB)):
            with self.assertRaisesRegex(guard.Unsafe, "deployment_budget_reached"):
                guard.sample(self.config, self._marker(), self.docker)
        with patch.object(guard, "capacity", return_value=(1, 20 * guard.GIB)):
            with self.assertRaisesRegex(guard.Unsafe, "host_free_floor_reached"):
                guard.sample(self.config, self._marker(), self.docker)

    def test_fail_close_updates_restart_then_terms_only_exact_owned(self):
        self._write_marker()
        foreign = replace(self.containers[0], id="f" * 64, workdir="/other/workdir")
        self.docker.containers[foreign.id] = foreign
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            result = guard.fail_close(self.config.state_dir, "threshold", self.docker)
        self.assertEqual(result["status"], "unconfirmed")  # foreign label conflict remains visible
        self.assertEqual(set(self.docker.updated), {item.id for item in self.containers})
        self.assertEqual(set(self.docker.termed), {item.id for item in self.containers})
        self.assertNotIn(foreign.id, self.docker.updated)
        self.assertEqual(self.docker.containers[foreign.id].status, "running")
        self.assertTrue((self.config.state_dir / "failure.json").exists())

    def test_clean_fail_close_confirmed_and_latched(self):
        self._write_marker()
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            result = guard.fail_close(self.config.state_dir, "service_exit", self.docker)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(len(result["restart_disabled_ids"]), 9)
        self.assertEqual(len(result["exited_or_absent_ids"]), 9)
        self.assertEqual([event for event, _ in self.docker.events[:9]], ["update"] * 9)
        self.assertEqual([event for event, _ in self.docker.events[9:]], ["term"] * 9)
        self.assertEqual(len(list(self.config.state_dir.glob("stop-*.json"))), 1)

    def test_replacement_is_reported_but_never_stopped_by_project_name(self):
        self._write_marker()
        old = self.containers[0]
        self.docker.containers.pop(old.id)
        replacement = replace(old, id="e" * 64)
        self.docker.containers[replacement.id] = replacement
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            result = guard.fail_close(self.config.state_dir, "identity_changed", self.docker)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("new_resident_container_after_arm", result["errors"])
        self.assertNotIn(replacement.id, self.docker.updated)
        self.assertNotIn(replacement.id, self.docker.termed)
        self.assertEqual(self.docker.containers[replacement.id].status, "running")

    def test_status_requires_fresh_heartbeat_and_same_nine_ids(self):
        self._write_marker()
        (self.config.state_dir / "heartbeat.json").write_text(
            json.dumps({"status": "healthy", "time": guard.time.time()}), encoding="utf-8"
        )
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard, "capacity", return_value=(100, 30 * guard.GIB)
        ):
            self.assertEqual(guard.status(self.config, "a" * 64, self.docker)["status"], "ready")
        (self.config.state_dir / "heartbeat.json").write_text(
            json.dumps({"status": "healthy", "time": guard.time.time() - 100}), encoding="utf-8"
        )
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            with self.assertRaisesRegex(guard.Unsafe, "capacity_heartbeat_stale"):
                guard.status(self.config, "a" * 64, self.docker)

    def test_docker_loss_never_claims_stop(self):
        self._write_marker()
        self.docker.fail = True
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            result = guard.fail_close(self.config.state_dir, "service_exit", self.docker)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("docker_unavailable", result["errors"])

    def test_missing_failure_receipt_never_claims_confirmed_stop(self):
        self._write_marker()
        original = guard._write

        def fail_first(path, value, *, exclusive=False):
            if path.name == "failure.json":
                raise OSError("fixture write failure")
            return original(path, value, exclusive=exclusive)

        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard, "_write", side_effect=fail_first
        ):
            result = guard.fail_close(self.config.state_dir, "service_exit", self.docker)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("failure_receipt_write_failed", result["errors"])
        self.assertEqual(len(self.docker.termed), 9)

    def test_running_guard_latches_and_stops_on_unsafe_sample(self):
        self._write_marker()
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard, "sample", side_effect=guard.Unsafe("host_free_floor_reached")
        ):
            exit_code = guard.run(self.config, "a" * 64, self.docker)
        self.assertEqual(exit_code, 2)
        self.assertEqual(len(self.docker.termed), 9)
        self.assertEqual(json.loads((self.config.state_dir / "failure.json").read_text())["reason"], "host_free_floor_reached")

    def test_config_change_uses_armed_docker_identity_for_stop(self):
        self._write_marker()
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard, "Docker", return_value=self.docker
        ) as constructor:
            exit_code = guard.run(self.config, "b" * 64, object())
        self.assertEqual(exit_code, 2)
        constructor.assert_called_once_with(str(self.config.docker))
        self.assertEqual(len(self.docker.termed), 9)

    def test_capacity_reads_metadata_and_selected_free_paths(self):
        (self.config.root / "data").mkdir()
        (self.config.root / "data" / "sample.bin").write_bytes(b"abc")
        used, free = guard.capacity(self.config.root, (self.config.root,))
        self.assertEqual(used, 3)
        self.assertGreater(free, 0)

    def test_docker_template_excludes_secret_fields_and_unit_has_watchdog(self):
        self.assertNotIn(".Config.Env", guard.INSPECT_FORMAT)
        unit = (Path(__file__).parents[2] / "ops/resident_capacity/tianshu-resident-capacity.service.in").read_text(encoding="utf-8")
        self.assertIn("Type=notify", unit)
        self.assertIn("WatchdogSec=30s", unit)
        self.assertIn("ExecStopPost=", unit)
        schema = json.loads((Path(__file__).parents[2] / "ops/resident_capacity/config.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["deployment_root"]["const"], guard.DEPLOYMENT_ROOT)


if __name__ == "__main__":
    unittest.main()
