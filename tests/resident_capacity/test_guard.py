"""Isolated metadata/Docker fixtures; no Docker daemon or NAS access."""

import json
import subprocess
import sys
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

    def snapshot(self, *, diagnostics=None, deadline=None):
        if self.fail:
            raise guard.Unsafe("docker_unavailable")
        return list(self.containers.values())

    def disable_restart(self, container_id, *, diagnostics=None):
        if self.fail:
            raise guard.Unsafe("docker_unavailable")
        self.updated.append(container_id)
        self.events.append(("update", container_id))
        self.containers[container_id] = replace(self.containers[container_id], restart="no")

    def inspect_one(self, container_id, *, diagnostics=None):
        if self.fail:
            raise guard.Unsafe("docker_unavailable")
        return self.containers[container_id]

    def terminate(self, container_id, *, diagnostics=None):
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
        binds = {}
        for service in images:
            (root / service).mkdir()
            binds[service] = [{"source": str(root / service), "target": f"/mnt/{service}", "read_only": False}]
        self.config = guard.Config(root, state, Path("/usr/bin/docker"), compose, platform_first, images, binds, (root,), 20 * guard.GIB, 20 * guard.GIB, 5, 10)
        self.containers = []
        for index, key in enumerate(sorted(guard.ALL_KEYS), 1):
            signature = self.config.signatures()[key]
            self.containers.append(guard.Container(
                f"{index:064x}", signature["project"], signature["service"],
                signature["workdir"], signature["compose_file"], signature["image"],
                "running", "unless-stopped", (("bind", str(root / signature["service"]), f"/mnt/{signature['service']}", True),),
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

    def _enable_knowledge(self):
        root = self.config.root
        (root / "knowledge").mkdir()
        images = {**self.config.images, "knowledge": "memory@sha256:" + "a" * 64}
        binds = {**self.config.binds, "knowledge": [
            {"source": str(root / "knowledge"), "target": "/mnt/knowledge", "read_only": False},
        ]}
        self.config = replace(
            self.config, images=images, binds=binds, service_profile=guard.KNOWLEDGE_PROFILE,
        )
        signature = self.config.signatures()[f"{guard.CORE}/knowledge"]
        knowledge = guard.Container(
            f"{10:064x}", signature["project"], signature["service"],
            signature["workdir"], signature["compose_file"], signature["image"],
            "running", "unless-stopped", (("bind", str(root / "knowledge"), "/mnt/knowledge", True),),
        )
        self.containers.append(knowledge)
        self.docker.containers[knowledge.id] = knowledge
        return knowledge

    @staticmethod
    def _raw(container):
        return [
            container.id, container.project, container.service, container.workdir,
            container.compose_file, container.image, container.status, container.restart,
            [
                {"Type": kind, "Source": source, "Destination": target, "RW": writable}
                for kind, source, target, writable in container.mounts
            ],
        ]

    def _cli(self, *, containers=None, faults=None):
        index = getattr(self, "cli_index", 0)
        self.cli_index = index + 1
        state = Path(self.temp.name) / f"docker-{index}.json"
        selected = self.containers if containers is None else containers
        state.write_text(json.dumps({
            "containers": [self._raw(item) for item in selected],
            "faults": faults or {},
        }), encoding="utf-8")
        docker = guard.Docker(sys.executable)
        docker.prefix = [
            sys.executable, "-B", str(Path(__file__).with_name("fake_docker.py")),
            str(state), *docker.prefix[1:],
        ]
        return docker, state

    @staticmethod
    def _cli_events(state):
        return json.loads(state.read_text(encoding="utf-8"))["events"]

    def _run_with_clock(
        self, commands, capacity_seconds, *, notify_seconds=2,
        heartbeat_seconds=(), recovery_seconds=0, stop_after_notifications=None,
    ):
        self.config = replace(self.config, poll_seconds=10)
        self._write_marker()
        clock, command_limits, sample_deadlines, notifications, stops = [0], [], [], [], []
        capacity_calls, heartbeat_calls = [0], [0]
        original_sample, original_write, original_stop = guard.sample, guard._write, guard.fail_close
        docker = guard.Docker("synthetic-docker")
        ids = "\n".join(item.id for item in self.containers).encode()
        inspected = "\n".join(json.dumps(self._raw(item)) for item in self.containers).encode()

        def invoke(args, *, capture_output, timeout, check):
            seconds, returncode = commands[len(command_limits)]
            command_limits.append(timeout)
            if seconds > timeout:
                clock[0] += timeout
                raise subprocess.TimeoutExpired(args, timeout)
            clock[0] += seconds
            output = ids if args[len(docker.prefix)] == "ps" else inspected
            return subprocess.CompletedProcess(args, returncode, stdout=output, stderr=b"")

        def measure(*args):
            clock[0] += capacity_seconds[capacity_calls[0]]
            capacity_calls[0] += 1
            return 100, 30 * guard.GIB

        def sample(config, marker, actual_docker, *, deadline=None):
            sample_deadlines.append(deadline)
            if stop_after_notifications is not None and len(notifications) >= stop_after_notifications:
                raise guard.Unsafe("fixture_loop_end")
            return original_sample(config, marker, actual_docker, deadline=deadline)

        def write(path, value, *, exclusive=False, deadline=None):
            if path.name == "heartbeat.json":
                index = heartbeat_calls[0]
                heartbeat_calls[0] += 1
                if index < len(heartbeat_seconds):
                    clock[0] += heartbeat_seconds[index]
            elif path.name == "docker-resample.json":
                clock[0] += recovery_seconds
            return original_write(path, value, exclusive=exclusive, deadline=deadline)

        def notify(message):
            notifications.append((clock[0], message, (self.config.state_dir / "heartbeat.json").read_bytes()))
            clock[0] += notify_seconds

        def stop(state, reason, actual_docker, *, diagnostics=()):
            stops.append(clock[0])
            return original_stop(state, reason, self.docker, diagnostics=diagnostics)

        def sleep(seconds):
            clock[0] += seconds

        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard.time, "monotonic", side_effect=lambda: clock[0]
        ), patch.object(guard.time, "sleep", side_effect=sleep), patch.object(
            guard.subprocess, "run", side_effect=invoke
        ), patch.object(guard, "capacity", side_effect=measure), patch.object(
            guard, "sample", side_effect=sample
        ), patch.object(guard, "_write", side_effect=write), patch.object(
            guard, "notify", side_effect=notify
        ), patch.object(guard, "fail_close", side_effect=stop):
            exit_code = guard.run(self.config, "a" * 64, docker)
        return exit_code, command_limits, sample_deadlines, notifications, stops

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

    def test_explicit_knowledge_profile_arms_ten_and_rejects_missing_service(self):
        knowledge = self._enable_knowledge()
        self.docker.containers.pop(knowledge.id)
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            with self.assertRaisesRegex(guard.Unsafe, "resident_ten_services_required"):
                guard.arm(self.config, "a" * 64, self.docker)
        self.assertFalse((self.config.state_dir / "armed.json").exists())
        self.docker.containers[knowledge.id] = knowledge
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard, "capacity", return_value=(100, 30 * guard.GIB)
        ):
            self.assertEqual(guard.arm(self.config, "a" * 64, self.docker)["services"], 10)
            self.assertEqual(guard.sample(self.config, self._marker(), self.docker)["services"], 10)
            (self.config.state_dir / "heartbeat.json").write_text(
                json.dumps({"status": "healthy", "time": guard.time.time()}), encoding="utf-8"
            )
            self.assertEqual(guard.status(self.config, "a" * 64, self.docker)["services"], 10)
            self.docker.containers.pop(knowledge.id)
            with self.assertRaisesRegex(guard.Unsafe, "resident_ten_services_required"):
                guard.status(self.config, "a" * 64, self.docker)
            self.docker.containers[knowledge.id] = knowledge
        self.assertEqual(len(guard._armed(self.config.state_dir)["ids"]), 10)

    def test_knowledge_profile_identity_and_fail_close_include_tenth_id(self):
        knowledge = self._enable_knowledge()
        with patch.object(guard, "capacity", return_value=(100, 30 * guard.GIB)):
            for changed in (
                replace(knowledge, image="wrong@sha256:" + "b" * 64),
                replace(knowledge, mounts=(("bind", str(self.config.root / "knowledge"), "/other", True),)),
            ):
                self.docker.containers[knowledge.id] = changed
                with self.assertRaisesRegex(guard.Unsafe, "resident_identity_conflict"):
                    guard.sample(self.config, self._marker(), self.docker)
            self.docker.containers[knowledge.id] = knowledge
        self._write_marker()
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            result = guard.fail_close(self.config.state_dir, "service_exit", self.docker)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(len(result["target_ids"]), 10)
        self.assertIn(knowledge.id, result["restart_disabled_ids"])
        self.assertIn(knowledge.id, result["term_sent_ids"])
        self.assertEqual(len(result["exited_or_absent_ids"]), 10)

    def test_legacy_profile_refuses_extra_knowledge_container(self):
        knowledge = replace(self.containers[0], id=f"{10:064x}", service="knowledge")
        with self.assertRaisesRegex(guard.Unsafe, "resident_identity_conflict"):
            guard.assess([*self.containers, knowledge], self.config.signatures(), str(self.config.root))
        with self.assertRaisesRegex(guard.Unsafe, "capacity_config_invalid"):
            guard._service_map("arbitrary")

    def test_project_workdir_may_differ_from_export_file_directory(self):
        entry = {"workdir": str(self.config.root), "file": self.config.compose[guard.CORE]["file"]}
        self.assertEqual(guard._compose_entry(entry, self.config.root), entry)
        with self.assertRaises(ValueError):
            guard._compose_entry(self.config.compose[guard.CORE], self.config.root)

    def test_project_name_alone_never_authorizes_foreign_container(self):
        wrong = replace(self.containers[0], workdir="/another/project", id="f" * 64)
        with self.assertRaisesRegex(guard.Unsafe, "resident_identity_conflict"):
            guard.assess([*self.containers, wrong], self.config.signatures(), str(self.config.root))

    def test_missing_or_wrong_bind_is_identity_conflict(self):
        original = self.containers[0]
        for mounts in (
            (),
            (("bind", str(self.config.root / original.service), "/wrong-target", True),),
            (("bind", str(self.config.root / original.service), f"/mnt/{original.service}", False),),
        ):
            altered = replace(original, mounts=mounts)
            with self.assertRaisesRegex(guard.Unsafe, "resident_identity_conflict"):
                guard.assess([altered, *self.containers[1:]], self.config.signatures(), str(self.config.root))

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

    def test_weaker_than_fixed_allocation_is_rejected(self):
        values = {"min_free_bytes": 20 * guard.GIB, "max_deployment_bytes": 20 * guard.GIB,
                  "poll_seconds": 5, "term_timeout_seconds": 120}
        guard._validate_limits(values)
        for key, weak in (("min_free_bytes", guard.GIB),
                          ("max_deployment_bytes", 1024 * guard.GIB),
                          ("term_timeout_seconds", 300)):
            with self.subTest(key=key):
                with self.assertRaisesRegex(guard.Unsafe, "capacity_config_invalid"):
                    guard._validate_limits({**values, key: weak})

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
        self.assertIn("TimeoutStopSec=700s", unit)
        self.assertLess(guard.MAX_FAIL_CLOSE_SECONDS, 700)
        schema = json.loads((Path(__file__).parents[2] / "ops/resident_capacity/config.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["deployment_root"]["const"], guard.DEPLOYMENT_ROOT)
        self.assertGreaterEqual(schema["properties"]["min_free_bytes"]["minimum"], 20 * guard.GIB)
        self.assertLessEqual(schema["properties"]["max_deployment_bytes"]["maximum"], 20 * guard.GIB)
        self.assertLessEqual(schema["properties"]["term_timeout_seconds"]["maximum"], guard.MAX_TERM_SECONDS)
        self.assertEqual(schema["properties"]["service_profile"]["enum"], ["resident-nine", "knowledge-ten"])

    def test_subprocess_disappearing_foreign_container_reenumerates_every_id(self):
        temporary = replace(self.containers[0], id="f" * 64, project="external", mounts=())
        docker, state = self._cli(
            containers=[*self.containers, temporary],
            faults={"ps": [{"remove_after": [temporary.id]}]},
        )
        with patch.object(guard, "capacity", return_value=(100, 30 * guard.GIB)):
            result = guard.sample(self.config, self._marker(), docker)
        self.assertEqual(result["status"], "healthy")
        events = self._cli_events(state)
        self.assertEqual([item["stage"] for item in events], ["ps", "batch_inspect"] * 2)
        self.assertEqual(set(events[1]["ids"]), {item.id for item in [*self.containers, temporary]})
        self.assertEqual(set(events[3]["ids"]), {item.id for item in self.containers})
        diagnostics = result["docker_diagnostics"]
        self.assertEqual([item["attempt"] for item in diagnostics], [1, 1, 2, 2])
        self.assertEqual(diagnostics[1]["outcome"], "nonzero_exit")
        self.assertEqual(diagnostics[1]["returncode"], 1)
        self.assertEqual(diagnostics[1]["batch_size"], 10)
        self.assertEqual(diagnostics[-1]["batch_size"], 9)
        self.assertEqual(diagnostics[-1]["outcome"], "ok")

    def test_subprocess_resample_still_applies_every_existing_protection(self):
        original = self.containers[0]
        temporary = replace(original, id="f" * 64, project="external", mounts=())
        replacement = replace(original, id="e" * 64)
        foreign = replace(
            temporary, id="d" * 64,
            mounts=(("bind", str(self.config.root / "unapproved"), "/data", True),),
        )
        cases = [
            ("resident_nine_services_required", [original.id], [], (100, 30 * guard.GIB)),
            ("resident_container_identity_changed", [original.id], [replacement], (100, 30 * guard.GIB)),
            ("foreign_deployment_mount", [], [foreign], (100, 30 * guard.GIB)),
            ("deployment_budget_reached", [], [], (20 * guard.GIB, 30 * guard.GIB)),
            ("host_free_floor_reached", [], [], (100, 20 * guard.GIB)),
        ]
        for reason, remove, add, capacity in cases:
            with self.subTest(reason=reason):
                docker, state = self._cli(
                    containers=[*self.containers, temporary],
                    faults={"ps": [{
                        "remove_after": [temporary.id, *remove],
                        "add_after": [self._raw(item) for item in add],
                    }]},
                )
                with patch.object(guard, "capacity", return_value=capacity):
                    with self.assertRaisesRegex(guard.Unsafe, reason) as refused:
                        guard.sample(self.config, self._marker(), docker)
                self.assertEqual(len(self._cli_events(state)), 4)
                self.assertEqual(refused.exception.docker_diagnostics[1]["returncode"], 1)
                self.assertEqual(refused.exception.docker_diagnostics[-1]["outcome"], "ok")

    def test_subprocess_host_inventory_above_sixty_four_is_complete(self):
        external = [
            replace(self.containers[0], id=f"{index:064x}", project="external", mounts=())
            for index in range(100, 498)
        ]
        docker, state = self._cli(containers=[*self.containers, *external])
        with patch.object(guard, "capacity", return_value=(100, 30 * guard.GIB)):
            result = guard.sample(self.config, self._marker(), docker)
        self.assertEqual(result["services"], 9)
        self.assertEqual(result["docker_diagnostics"][-1]["batch_size"], 407)
        events = self._cli_events(state)
        self.assertEqual(set(events[1]["ids"]), {item.id for item in [*self.containers, *external]})

    def test_subprocess_nonzero_is_bounded_and_preserved_in_failure_and_stop_receipts(self):
        self._write_marker()
        secret_stdout = "RAW_STDOUT_MUST_NOT_BE_SAVED credential=value command=/private/docker"
        secret_stderr = "RAW_STDERR_MUST_NOT_BE_SAVED /private/mount token=value"
        fault = {"returncode": 13, "stdout": secret_stdout, "stderr": secret_stderr}
        docker, state = self._cli(faults={"batch_inspect": [dict(fault), dict(fault)]})
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            result = guard.run(self.config, "a" * 64, docker)
        self.assertEqual(result, 2)
        failure = json.loads((self.config.state_dir / "failure.json").read_text())
        self.assertEqual(failure["reason"], "docker_command_failed")
        diagnostics = failure["docker_diagnostics"]
        self.assertEqual(len(diagnostics), 4)
        self.assertEqual([item["returncode"] for item in diagnostics], [0, 13, 0, 13])
        self.assertEqual([item["attempt"] for item in diagnostics], [1, 1, 2, 2])
        stop_path = next(self.config.state_dir.glob("stop-*.json"))
        stopped = json.loads(stop_path.read_text())
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["trigger_docker_diagnostics"], diagnostics)
        self.assertEqual(set(stopped["term_sent_ids"]), set(self._marker()["ids"].values()))
        self.assertEqual(
            set(item["stage"] for item in stopped["docker_diagnostics"]),
            {"ps", "batch_inspect", "update", "inspect_one", "term"},
        )
        for path in self.config.state_dir.glob("*.json"):
            text = path.read_text()
            self.assertNotIn(secret_stdout, text)
            self.assertNotIn(secret_stderr, text)
            self.assertNotIn(str(state), text)
        old_failure = (self.config.state_dir / "failure.json").read_bytes()
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            self.assertEqual(guard.run(self.config, "a" * 64, docker), 0)
        self.assertEqual((self.config.state_dir / "failure.json").read_bytes(), old_failure)
        self.assertEqual(len(list(self.config.state_dir.glob("stop-*.json"))), 2)

    def test_subprocess_successful_resample_receipt_survives_next_normal_heartbeat(self):
        self._write_marker()
        temporary = replace(self.containers[0], id="f" * 64, project="external", mounts=())
        docker, _ = self._cli(
            containers=[*self.containers, temporary],
            faults={"ps": [{"remove_after": [temporary.id]}]},
        )
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard, "capacity", return_value=(100, 30 * guard.GIB)
        ), patch.object(guard, "notify", side_effect=[None, guard.Unsafe("fixture_loop_end")]), patch.object(
            guard.time, "sleep"
        ):
            self.assertEqual(guard.run(self.config, "a" * 64, docker), 2)
        recovered = json.loads((self.config.state_dir / "docker-resample.json").read_text())
        heartbeat = json.loads((self.config.state_dir / "heartbeat.json").read_text())
        self.assertEqual(recovered["status"], "healthy")
        self.assertEqual(len(recovered["docker_diagnostics"]), 4)
        self.assertEqual(recovered["docker_diagnostics"][1]["returncode"], 1)
        self.assertEqual(recovered["docker_diagnostics"][-1]["outcome"], "ok")
        self.assertEqual(len(heartbeat["docker_diagnostics"]), 2)

    def test_subprocess_stop_errors_preserve_unconfirmed_receipt(self):
        self._write_marker()
        docker, _ = self._cli(faults={
            "update": [{"returncode": 13, "stderr": "private update error"}],
            "ps": [{}, {"returncode": 17, "stderr": "private final inventory error"}],
        })
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)):
            result = guard.fail_close(self.config.state_dir, "fixture_failure", docker)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("docker_command_failed", result["errors"])
        self.assertEqual(len(result["term_sent_ids"]), 8)
        self.assertNotIn(sorted(self._marker()["ids"].values())[0], result["term_sent_ids"])
        failures = [item for item in result["docker_diagnostics"] if item["outcome"] == "nonzero_exit"]
        self.assertEqual([(item["stage"], item["returncode"]) for item in failures], [("update", 13), ("ps", 17)])
        self.assertEqual(json.loads(next(self.config.state_dir.glob("stop-*.json")).read_text()), result)

    def test_subprocess_ps_and_single_id_command_errors_do_not_retry(self):
        original = self.containers[0]
        for stage, action in (
            ("ps", lambda docker: docker.snapshot()),
            ("inspect_one", lambda docker: docker.inspect_one(original.id)),
            ("update", lambda docker: docker.disable_restart(original.id)),
            ("term", lambda docker: docker.terminate(original.id)),
        ):
            with self.subTest(stage=stage):
                docker, state = self._cli(faults={stage: [{"returncode": 7, "stderr": "private text"}]})
                with self.assertRaisesRegex(guard.Unsafe, "docker_command_failed") as refused:
                    action(docker)
                self.assertEqual(len(self._cli_events(state)), 1)
                diagnostics = refused.exception.docker_diagnostics
                self.assertEqual(len(diagnostics), 1)
                self.assertEqual(diagnostics[0]["stage"], stage)
                self.assertEqual(diagnostics[0]["returncode"], 7)
                self.assertFalse(diagnostics[0]["timed_out"])

    def test_subprocess_batch_timeout_is_distinct_and_never_resampled(self):
        docker, state = self._cli(faults={"batch_inspect": [{"sleep_seconds": 3}]})
        started = guard.time.monotonic()
        with patch.object(guard, "DOCKER_SNAPSHOT_SECONDS", 0.75):
            with self.assertRaisesRegex(guard.Unsafe, "docker_unavailable") as refused:
                docker.snapshot()
        self.assertLess(guard.time.monotonic() - started, 2.5)
        self.assertEqual(len(self._cli_events(state)), 2)
        diagnostics = refused.exception.docker_diagnostics
        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(diagnostics[-1]["stage"], "batch_inspect")
        self.assertEqual(diagnostics[-1]["outcome"], "timeout")
        self.assertTrue(diagnostics[-1]["timed_out"])
        self.assertIsNone(diagnostics[-1]["returncode"])
        self.assertGreater(diagnostics[-1]["elapsed_seconds"], 0)

    def test_subprocess_missing_binary_records_spawn_failure(self):
        docker = guard.Docker(str(Path(self.temp.name) / "missing-docker"))
        with self.assertRaisesRegex(guard.Unsafe, "docker_unavailable") as refused:
            docker.snapshot()
        diagnostic, = refused.exception.docker_diagnostics
        self.assertEqual(diagnostic["stage"], "ps")
        self.assertEqual(diagnostic["outcome"], "spawn_failed")
        self.assertFalse(diagnostic["timed_out"])
        self.assertIsNone(diagnostic["returncode"])
        self.assertIsInstance(diagnostic["os_error_number"], int)

    def test_subprocess_incomplete_inventory_retries_but_invalid_metadata_does_not(self):
        cases = [
            ({"batch_inspect": [{"stdout": json.dumps(self._raw(self.containers[0]))}]}, None, 4),
            ({"batch_inspect": [{"stdout": "invalid-json\n" * len(self.containers)}]}, "docker_metadata_invalid", 2),
            ({"ps": [{"stdout": "invalid-id"}]}, "docker_inventory_invalid", 1),
            ({"ps": [{"stdout": "\n".join([self.containers[0].id] * 2)}]}, "docker_inventory_invalid", 1),
        ]
        for faults, reason, expected_calls in cases:
            with self.subTest(reason=reason, expected_calls=expected_calls):
                docker, state = self._cli(faults=faults)
                if reason:
                    with self.assertRaisesRegex(guard.Unsafe, reason):
                        docker.snapshot()
                else:
                    self.assertEqual(len(docker.snapshot()), len(self.containers))
                self.assertEqual(len(self._cli_events(state)), expected_calls)

    def test_snapshot_retry_shares_original_monotonic_budget(self):
        for durations, expected_limits, expected_calls, final_outcome in (
            ([6, 8, 8], [10, 15, 10, 3], 4, "timeout"),
            ([10, 15], [10, 15], 2, "deadline_exhausted"),
        ):
            with self.subTest(durations=durations):
                clock, limits = [0], []
                ids = "\n".join(item.id for item in self.containers).encode()

                def invoke(args, *, capture_output, timeout, check):
                    limits.append(timeout)
                    index = len(limits) - 1
                    if index >= len(durations):
                        clock[0] += timeout
                        raise subprocess.TimeoutExpired(args, timeout, output=b"private output", stderr=b"private error")
                    clock[0] += durations[index]
                    return subprocess.CompletedProcess(args, 13 if index == 1 else 0, stdout=ids, stderr=b"private error")

                docker = guard.Docker("synthetic-docker")
                with patch.object(guard.time, "monotonic", side_effect=lambda: clock[0]), patch.object(
                    guard.subprocess, "run", side_effect=invoke
                ) as run:
                    with self.assertRaisesRegex(guard.Unsafe, "docker_unavailable") as refused:
                        docker.snapshot()
                self.assertEqual(limits, expected_limits)
                self.assertEqual(run.call_count, expected_calls)
                self.assertEqual(clock[0], guard.DOCKER_SNAPSHOT_SECONDS)
                self.assertEqual(refused.exception.docker_diagnostics[1]["returncode"], 13)
                self.assertEqual(refused.exception.docker_diagnostics[-1]["outcome"], final_outcome)

    def test_run_cycle_allows_slow_resample_with_poll_and_previous_notify_time(self):
        result, limits, deadlines, notifications, stops = self._run_with_clock(
            [(0.2, 0), (0.3, 0), (2, 0), (2, 13), (2, 0), (5, 0)],
            [0.5, 0.5], heartbeat_seconds=[0.25, 0.25], recovery_seconds=0.25,
            stop_after_notifications=2,
        )
        self.assertEqual(result, 2)  # controlled end after two healthy cycles
        self.assertEqual(limits, [10, 15, 10, 11, 9, 7])
        self.assertEqual([item[0] for item in notifications], [1.25, 25.25])
        self.assertEqual(deadlines, [25, 26.25, 50.25])
        self.assertLess(notifications[1][0] - notifications[0][0], 30)
        self.assertIn("READY=1", notifications[0][1])
        self.assertNotIn("READY=1", notifications[1][1])
        recovered = json.loads((self.config.state_dir / "docker-resample.json").read_text())
        self.assertEqual([item["returncode"] for item in recovered["docker_diagnostics"]], [0, 13, 0, 0])
        self.assertEqual(len(stops), 1)

    def test_run_cycle_docker_timeout_latches_before_watchdog_without_new_heartbeat(self):
        result, limits, deadlines, notifications, stops = self._run_with_clock(
            [(0.2, 0), (0.3, 0), (2, 0), (2, 13), (2, 0), (9, 0)], [0, 0],
        )
        self.assertEqual(result, 2)
        self.assertEqual(limits, [10, 15, 10, 11, 9, 7])
        self.assertEqual(deadlines, [25, 25.5])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(stops, [25.5])
        self.assertLess(stops[0] - notifications[0][0], 30)
        self.assertEqual((self.config.state_dir / "heartbeat.json").read_bytes(), notifications[0][2])
        failure = json.loads((self.config.state_dir / "failure.json").read_text())
        self.assertEqual(failure["reason"], "docker_unavailable")
        self.assertEqual(failure["docker_diagnostics"][-1]["outcome"], "timeout")
        self.assertEqual(len(self.docker.termed), 9)

    def test_run_cycle_capacity_timeout_does_not_publish_health_or_notify(self):
        result, _, deadlines, notifications, stops = self._run_with_clock(
            [(0.2, 0), (0.3, 0), (1, 0), (1, 0)], [0, 12],
        )
        self.assertEqual(result, 2)
        self.assertEqual(deadlines, [25, 25.5])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(stops, [26.5])
        self.assertLess(stops[0] - notifications[0][0], 30)
        self.assertEqual((self.config.state_dir / "heartbeat.json").read_bytes(), notifications[0][2])
        failure = json.loads((self.config.state_dir / "failure.json").read_text())
        self.assertEqual(failure["reason"], "capacity_sample_timeout")
        self.assertEqual(failure["docker_diagnostics"][-1]["outcome"], "ok")

    def test_run_cycle_state_write_timeout_does_not_publish_health_or_notify(self):
        result, _, _, notifications, stops = self._run_with_clock(
            [(0.2, 0), (0.3, 0), (1, 0), (1, 0)], [0, 0], heartbeat_seconds=[0, 12],
        )
        self.assertEqual(result, 2)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(stops, [26.5])
        self.assertLess(stops[0] - notifications[0][0], 30)
        self.assertEqual((self.config.state_dir / "heartbeat.json").read_bytes(), notifications[0][2])
        self.assertFalse(list(self.config.state_dir.glob("*.tmp")))
        failure = json.loads((self.config.state_dir / "failure.json").read_text())
        self.assertEqual(failure["reason"], "capacity_sample_timeout")


if __name__ == "__main__":
    unittest.main()
