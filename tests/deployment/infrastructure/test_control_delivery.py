"""The explicit acceptance driver and cold-release boundary never pick production targets."""

import ast
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deploy.infrastructure import control_acceptance as acceptance, control_node_check, resident


class ControlDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.config = {"version": "infrastructure-control.acceptance.v1", "command": ["/explicit/control"], "directory": str(self.base),
                       "docker_command": ["/explicit/docker"], "node_id": "synthetic-node", "project_name": "tianshu-control-acceptance-fixture",
                       "image": "fixture:tag", "expected_image_id": "sha256:" + "a" * 64, "stacks_root": str(self.base / "stacks"),
                       "replacement_image": "fixture:other", "replacement_image_id": "sha256:" + "b" * 64,
                       "marker_directory": str(self.base / "marker"), "operation_timeout_seconds": 5}

    def test_no_default_command_and_existing_service_names_are_refused_before_execution(self):
        with patch.object(acceptance.subprocess, "run", side_effect=AssertionError("no Docker command permitted")):
            for name in ("tianshu-v2-resident", "dockge", "music", ""):
                config = {**self.config, "project_name": name}
                with self.assertRaisesRegex(ValueError, "explicit_isolated_acceptance_project_required"):
                    acceptance.Acceptance(config, self.base / "output")
            with self.assertRaisesRegex(ValueError, "acceptance_configuration_invalid"):
                acceptance.Acceptance({}, self.base / "output")

    def test_non_target_full_identity_state_mounts_and_configuration_are_retained(self):
        before = [{"id": "a" * 64, "project": "foreign", "state": {"Running": True}, "mounts": [{"RW": True}], "configuration_sha256": "fixed"}]
        after = copy.deepcopy(before) + [{"id": "b" * 64, "project": "new-foreign"}, {"id": "c" * 64, "project": self.config["project_name"]}]
        self.assertTrue(acceptance.compare_inventory(before, after, self.config["project_name"])["preserved"])
        for field, value in (("id", "d" * 64), ("state", {"Running": False}), ("mounts", [{"RW": False}]), ("configuration_sha256", "changed")):
            current = copy.deepcopy(after)
            current[0][field] = value
            self.assertFalse(acceptance.compare_inventory(before, current, self.config["project_name"])["preserved"])
        inspected = {"Id": "a" * 64, "Name": "/unlabelled", "Image": "sha256:" + "b" * 64,
                     "Config": {"Labels": None, "Env": ["TOKEN=SYNTHETIC_ENV_SECRET"]}, "HostConfig": {},
                     "State": {"Running": True, "Health": None}, "Mounts": []}
        with patch.object(acceptance.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout=("a" * 64 + "\n").encode()),
                                                                   SimpleNamespace(returncode=0, stdout=json.dumps([inspected]).encode())]):
            projected = acceptance.inventory(["/synthetic/docker"])
        self.assertIsNone(projected[0]["project"])
        self.assertNotIn("SYNTHETIC_ENV_SECRET", json.dumps(projected))

    def inspect_projection(self, method, mounts, *, project="foreign-compose-project"):
        """Real Docker inspect field shapes, emitted in caller-supplied mount order."""
        inspected = {"Id": "a" * 64, "Name": "/synthetic-container", "Image": "sha256:" + "b" * 64,
                     "Config": {"Labels": {"com.docker.compose.project": project}, "Env": ["ACCEPTANCE_REVISION=one"]},
                     "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
                     "State": {"Running": True, "Status": "running", "StartedAt": "2026-10-19T00:00:00Z", "FinishedAt": "0001-01-01T00:00:00Z"},
                     "Mounts": mounts}
        with patch.object(acceptance.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout=("a" * 64 + "\n").encode()),
                                                                   SimpleNamespace(returncode=0, stdout=json.dumps([inspected]).encode())]):
            return method()

    def mount_fixtures(self):
        bind = {"Type": "bind", "Source": "/synthetic/data", "Destination": "/data", "Mode": "rw", "RW": True,
                "Propagation": "rprivate", "observed_extension": {"kept": True}}
        volume = {"Type": "volume", "Name": "synthetic-volume", "Source": "/synthetic/volumes/data", "Destination": "/cache",
                  "Driver": "local", "Mode": "ro", "RW": False, "Propagation": ""}
        return [bind, volume, copy.deepcopy(bind)]

    def test_inventory_and_replay_target_ignore_only_mount_order_preserving_duplicate_objects(self):
        mounts = self.mount_fixtures()
        original = copy.deepcopy(mounts)
        reordered = [mounts[1], mounts[2], mounts[0]]
        before = self.inspect_projection(lambda: acceptance.inventory(["/synthetic/docker"]), mounts)
        after = self.inspect_projection(lambda: acceptance.inventory(["/synthetic/docker"]), reordered)
        self.assertEqual(before, after)
        self.assertTrue(acceptance.compare_inventory(before, after, self.config["project_name"])["preserved"])
        self.assertEqual(len(before[0]["mounts"]), 3)
        self.assertEqual(before[0]["mounts"].count(mounts[0]), 2)
        driver = acceptance.Acceptance(self.config, self.base / "output")
        target_before = self.inspect_projection(driver.target, mounts, project=self.config["project_name"])
        target_after = self.inspect_projection(driver.target, reordered, project=self.config["project_name"])
        self.assertEqual(target_before, target_after)
        self.assertEqual(mounts, original)

    def test_original_unsorted_evidence_compares_without_mutating_or_discarding_fields(self):
        before = [{"id": "a" * 64, "project": "foreign", "health": "healthy", "state": {"Running": True}, "mounts": self.mount_fixtures(),
                   "image_id": "sha256:" + "b" * 64, "additional_container_field": {"kept": True}}]
        after = json.loads(json.dumps(before))
        after[0]["mounts"] = [after[0]["mounts"][1], after[0]["mounts"][0], after[0]["mounts"][2]]
        original = copy.deepcopy((before, after))
        self.assertTrue(acceptance.compare_inventory(before, after, self.config["project_name"])["preserved"])
        self.assertEqual((before, after), original)
        self.assertEqual(acceptance.normalize_container(before[0])["additional_container_field"], {"kept": True})

    def test_reordered_mount_evidence_still_rejects_each_real_change_duplicate_loss_health_and_identity(self):
        before = [{"id": "a" * 64, "project": "foreign", "health": "healthy", "state": {"Running": True}, "mounts": self.mount_fixtures()}]
        for index, field, value in ((0, "Type", "volume"), (0, "Source", "/changed-source"), (0, "Destination", "/changed-target"),
                                    (0, "Mode", "ro,z"), (0, "RW", False), (0, "Propagation", "rshared"),
                                    (0, "observed_extension", {"kept": False}), (1, "Name", "changed-volume"), (1, "Driver", "changed-driver")):
            after = copy.deepcopy(before)
            after[0]["mounts"][index][field] = value
            after[0]["mounts"].reverse()
            with self.subTest(field=field):
                report = acceptance.compare_inventory(before, after, self.config["project_name"])
                self.assertFalse(report["preserved"])
                self.assertEqual(report["changed"], ["a" * 64])
        after = copy.deepcopy(before)
        after[0]["mounts"].pop()
        self.assertFalse(acceptance.compare_inventory(before, after, self.config["project_name"])["preserved"])
        for field, value in (("health", "unhealthy"), ("id", "c" * 64), ("state", {"Running": False})):
            after = copy.deepcopy(before)
            after[0]["mounts"].reverse()
            after[0][field] = value
            with self.subTest(field=field):
                self.assertFalse(acceptance.compare_inventory(before, after, self.config["project_name"])["preserved"])

    def test_send_disconnect_only_reads_original_before_proven_replay(self):
        driver = acceptance.Acceptance(self.config, self.base / "output")
        driver.output.mkdir()
        original = {"operation_id": "unknown", "state": "succeeded", "execution_active": False, "command": {"state": "succeeded"},
                    "runtime": {"state": "running", "health": "not_configured"}}
        seen = []

        def call(operation, params):
            seen.append(operation)
            if len(seen) == 1:
                original["operation_id"] = params["operation_id"]
                raise subprocess.TimeoutExpired(["synthetic"], 1)
            return {"replayed": True, "operation": original}

        def wait(operation_id):
            seen.append("operation.get")
            self.assertEqual(operation_id, original["operation_id"])
            return original

        driver.call, driver.wait, driver.target = call, wait, Mock(return_value={"id": "a" * 64})
        driver.submit("container.restart", {"container_id": "a" * 64}, "test")
        self.assertEqual(seen, ["operation.submit", "operation.get", "operation.submit"])
        self.assertTrue((driver.output / "intent-test.json").exists())

    def test_unknown_terminal_command_is_never_reported_as_success(self):
        driver = acceptance.Acceptance(self.config, self.base / "output")
        driver.call = Mock(return_value={"operation": {"execution_active": False, "state": "unknown", "command": {"state": "unknown"}}})
        with self.assertRaisesRegex(ValueError, "acceptance_operation_not_successful"):
            driver.wait("original:operation")
        self.assertEqual(driver.call.call_args.args, ("operation.get", {"operation_id": "original:operation", "reconcile": True}))

    def test_control_preflight_cannot_install_and_installer_uses_separate_post_backup_entry(self):
        worker = ast.parse(Path(resident.__file__).with_name("resident_worker.py").read_text(encoding="utf-8"))
        functions = [node for node in worker.body if isinstance(node, ast.FunctionDef) and node.name in {"check_private_inputs", "install_private_inputs"}]
        check, install, save = Mock(), Mock(return_value={"status": "installed"}), Mock()
        control = {"synthetic": True}
        scope = {"RELEASE": {"preserve_infrastructure": True, "control": control}, "S": self.base,
                 "check_control_install": check, "install_control": install, "save": save}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "control-cold-install-boundary", "exec"), scope)
        scope["check_private_inputs"]()
        install.assert_not_called()
        save.assert_not_called()
        scope["install_private_inputs"]()
        install.assert_called_once_with(control)
        save.assert_called_once_with(self.base / "control-installation.json", {"status": "installed"})

    def test_node_process_proof_requires_full_target_and_explicit_temporary_scope(self):
        with patch.object(control_node_check, "verify", return_value={}), patch.object(control_node_check.subprocess, "run", side_effect=AssertionError("no Docker exec permitted")):
            with self.assertRaisesRegex(ValueError, "full_dockge_container_id_required"):
                control_node_check.run(package="unused", docker_command=["/docker"], dockge_id="dockge", directory="/tmp/tianshu-control-proof-test", output=self.base / "result.json")
            with self.assertRaisesRegex(ValueError, "explicit_new_node_proof_directory_required"):
                control_node_check.run(package="unused", docker_command=["/docker"], dockge_id="a" * 64, directory="/production/data", output=self.base / "result.json")


if __name__ == "__main__":
    unittest.main()
