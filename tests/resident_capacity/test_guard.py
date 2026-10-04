"""Isolated metadata/Docker fixtures; no Docker daemon or NAS access."""

import json
import subprocess
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
    def test_image_references_require_a_complete_immutable_digest(self):
        for reference in ("sha256:" + "a" * 64, "registry/product@sha256:" + "b" * 64):
            with self.subTest(reference=reference):
                self.assertIsNotNone(guard.IMAGE.fullmatch(reference))
        for reference in ("product:latest", "product:release", "sha256:abc", "a" * 64,
                          "sha256:" + "a" * 63, "sha256:" + "a" * 65,
                          "sha256:" + "A" * 64, "sha256:" + "a" * 64 + "\n"):
            with self.subTest(reference=reference):
                self.assertIsNone(guard.IMAGE.fullmatch(reference))

    def test_full_local_image_ids_keep_exact_ten_owner_arm_and_binding(self):
        self._enable_knowledge()
        images = {name: "sha256:" + f"{index:064x}" for index, name in enumerate(self.config.images, 1)}
        self.config = replace(self.config, images=images)
        self.containers = [replace(item, image=images[item.service]) for item in self.containers]
        self.docker = FakeDocker(self.containers)
        with patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path)), patch.object(
            guard, "capacity", return_value=(100, 30 * guard.GIB)
        ):
            self.assertEqual(guard.arm(self.config, "a" * 64, self.docker)["services"], 10)
            self.assertEqual(guard.sample(self.config, self._marker(), self.docker)["services"], 10)
            changed = self.containers[0]
            self.docker.containers[changed.id] = replace(changed, image="sha256:" + "f" * 64)
            with self.assertRaises(guard.Unsafe):
                guard.sample(self.config, self._marker(), self.docker)

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

    @staticmethod
    def _inspect_output(containers):
        return "\n".join(json.dumps([
            item.id, item.project, item.service, item.workdir, item.compose_file,
            item.image, item.status, item.restart,
            [{"Type": kind, "Source": source, "Destination": target, "RW": writable}
             for kind, source, target, writable in item.mounts],
        ]) for item in containers).encode()

    @staticmethod
    def _docker_result(output=b"", returncode=0):
        return subprocess.CompletedProcess([], returncode, output, b"fixture stderr")

    def _churn_results(self, final, first_failure=None):
        temporary = replace(self.containers[0], id="f" * 64, project="temporary-build")
        first = [*self.containers, temporary]
        return [
            self._docker_result("\n".join(item.id for item in first).encode()),
            first_failure or self._docker_result(returncode=1),
            self._docker_result("\n".join(item.id for item in final).encode()),
            self._docker_result(self._inspect_output(final)),
        ]

    def test_snapshot_reenumerates_after_temporary_container_disappears(self):
        self._enable_knowledge()
        with patch.object(guard.subprocess, "run", side_effect=self._churn_results(self.containers)) as run:
            inventory = guard.Docker("docker").snapshot()
        owned = guard.assess(inventory, self.config.signatures(), str(self.config.root), self._marker()["ids"])
        self.assertEqual(len(owned), 10)
        self.assertEqual([call.args[0][3] for call in run.call_args_list], ["ps", "inspect", "ps", "inspect"])
        self.assertNotIn("f" * 64, run.call_args_list[-1].args[0])

    def test_snapshot_reenumerates_for_partial_or_different_inspect_inventory(self):
        for first in (
            self._inspect_output(self.containers),
            self._inspect_output([*self.containers, replace(self.containers[0], id="e" * 64)]),
        ):
            with self.subTest(first=first), patch.object(
                guard.subprocess, "run", side_effect=self._churn_results(self.containers, self._docker_result(first))
            ) as run:
                self.assertEqual(guard.Docker("docker").snapshot(), self.containers)
                self.assertEqual(run.call_count, 4)

    def test_snapshot_retry_is_limited_to_one_complete_attempt(self):
        for failure, reason in (
            (self._docker_result(returncode=1), "docker_command_failed"),
            (self._docker_result(b""), "docker_inventory_changed"),
        ):
            results = self._churn_results(self.containers, failure)
            results[-1] = failure
            with self.subTest(reason=reason), patch.object(guard.subprocess, "run", side_effect=results) as run:
                with self.assertRaisesRegex(guard.Unsafe, reason):
                    guard.Docker("docker").snapshot()
                self.assertEqual(run.call_count, 4)

    def test_snapshot_retry_failure_still_latches_and_stops_all_ten_owners(self):
        self._enable_knowledge()
        self._write_marker()
        results = self._churn_results(self.containers)
        results[-1] = self._docker_result(returncode=1)
        original = guard.fail_close
        with patch.object(guard.subprocess, "run", side_effect=results) as run, patch.object(
            guard, "_state_dir", side_effect=lambda path, create=False: Path(path)
        ), patch.object(
            guard, "fail_close", side_effect=lambda state, reason, docker: original(state, reason, self.docker)
        ):
            exit_code = guard.run(self.config, "a" * 64, guard.Docker("docker"))
        self.assertEqual(exit_code, 2)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(len(self.docker.updated), 10)
        self.assertEqual(len(self.docker.termed), 10)
        failure = json.loads((self.config.state_dir / "failure.json").read_text())
        self.assertEqual(failure["reason"], "docker_command_failed")

    def test_snapshot_retry_never_accepts_missing_owner_changed_mount_or_new_id(self):
        self._enable_knowledge()
        original = self.containers[0]
        for final, reason in (
            (self.containers[1:], "resident_ten_services_required"),
            ([replace(original, mounts=()), *self.containers[1:]], "resident_identity_conflict"),
            ([replace(original, id="e" * 64), *self.containers[1:]], "resident_container_identity_changed"),
        ):
            with self.subTest(reason=reason), patch.object(
                guard.subprocess, "run", side_effect=self._churn_results(final)
            ) as run:
                inventory = guard.Docker("docker").snapshot()
                with self.assertRaisesRegex(guard.Unsafe, reason):
                    guard.assess(inventory, self.config.signatures(), str(self.config.root), self._marker()["ids"])
                self.assertEqual(run.call_count, 4)

    def test_snapshot_does_not_retry_malformed_metadata_or_unavailable_docker(self):
        for results, reason in (
            ([self._docker_result(b"not-an-id")], "docker_inventory_invalid"),
            ([self._docker_result(self.containers[0].id.encode()), self._docker_result(b"invalid-json")], "docker_metadata_invalid"),
            ([subprocess.TimeoutExpired(["docker"], 10)], "docker_unavailable"),
        ):
            with self.subTest(reason=reason), patch.object(guard.subprocess, "run", side_effect=results) as run:
                with self.assertRaisesRegex(guard.Unsafe, reason):
                    guard.Docker("docker").snapshot()
                self.assertEqual(run.call_count, len(results))

    def test_snapshot_retry_shares_original_budget_under_watchdog(self):
        elapsed = [0.0]
        responses = iter(self._churn_results(self.containers))
        durations = iter((9, 5, 9, 1))

        def execute(*args, **kwargs):
            elapsed[0] += next(durations)
            return next(responses)

        with patch.object(guard.time, "monotonic", side_effect=lambda: elapsed[0]), patch.object(
            guard.subprocess, "run", side_effect=execute
        ) as run:
            self.assertEqual(guard.Docker("docker").snapshot(), self.containers)
        self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [10, 15, 10, 2])

    def test_snapshot_exhausted_budget_fails_without_another_docker_call(self):
        elapsed = [0.0]
        responses = iter(self._churn_results(self.containers))
        durations = iter((10, 15))

        def execute(*args, **kwargs):
            elapsed[0] += next(durations)
            return next(responses)

        with patch.object(guard.time, "monotonic", side_effect=lambda: elapsed[0]), patch.object(
            guard.subprocess, "run", side_effect=execute
        ) as run:
            with self.assertRaisesRegex(guard.Unsafe, "docker_command_failed"):
                guard.Docker("docker").snapshot()
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
