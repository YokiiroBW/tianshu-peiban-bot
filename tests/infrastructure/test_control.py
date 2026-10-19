from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from helpers import OUTPUT, ROOT, LoopbackSSH, inspect_value
from ops.infrastructure.config import Config, Node
from ops.infrastructure.control.client import execute as relay
from ops.infrastructure.control.config import RelayConfig, load_remote
from ops.infrastructure.control.engine import Engine, runtime
from ops.infrastructure.control.engine import LocalTransport
from ops.infrastructure.control.lifecycle import process_identity
from ops.infrastructure.control.files import content, document, fingerprint, redact, unmask
from ops.infrastructure.control.protocol import VERSION, Request, encode, parse_request, receipt
from ops.infrastructure.control.service import Service
from ops.infrastructure.protocol import Failure, utc_now
from ops.infrastructure.sampling import stable_id
from ops.infrastructure.transport import CommandResult, Deadline


class ControlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((ROOT / "contracts/infrastructure-control/candidate-v1/response.schema.json").read_text())
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=OUTPUT)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.stacks = self.directory / "stacks"
        self.stacks.mkdir()
        self.stack = self.stacks / "synthetic"
        self.stack.mkdir()
        self.compose = self.stack / "compose.yaml"
        self.raw = b"name: synthetic\nservices:\n  web:\n    image: nginx:alpine\n    environment:\n      PASSWORD: synthetic-original-secret\n"
        self.compose.write_bytes(self.raw)
        (self.stack / ".env").write_text("TOKEN=synthetic-dotenv-secret\n")
        self.daemon = self.directory / "daemon.json"
        self.identity = f"{1:064x}"
        item = inspect_value(project="synthetic")
        item[11:13] = [str(self.stack), str(self.compose)]
        item[14:16] = [{}, []]
        self.save_daemon({"containers": {self.identity: item}, "calls": []})
        command = [sys.executable, str(Path(__file__).parent / "control_fixture.py"), str(self.daemon)]
        self.config_path = self.directory / "remote.json"
        self.registration = {"version": "infrastructure-control.remote.v1", "node_id": "fixture-nas", "state_dir": str(self.directory / "state"),
            "stacks_root": str(self.stacks), "docker_command": command, "docker_socket": str(self.directory / "fixture.sock"), "compose_command": [*command, "compose"],
            "platform_stacks": ["synthetic"], "protected_projects": {"tianshu-v2-resident": {"owner": "resident-update", "reason": "capacity_guard_ownership", "maintenance_ref": "resident-update"}, "tianshu-v2-resident-obs": {"owner": "resident-update", "reason": "capacity_guard_ownership", "maintenance_ref": "resident-update"}},
            "protected_container_names": {"dockge": {"owner": "dockge", "reason": "control_execution_dependency", "maintenance_ref": None}}, "operation_timeout_seconds": 5}
        self.config_path.write_text(json.dumps(self.registration))
        self.config = load_remote(str(self.config_path))
        self.stack_id = stable_id("stack", "fixture-nas", "synthetic")

    def service(self, seconds=10):
        return Service(self.config, Deadline(seconds))

    def daemon_state(self):
        return json.loads(self.daemon.read_text())

    def save_daemon(self, value):
        self.daemon.write_text(json.dumps(value))

    def change_daemon(self, **fields):
        value = self.daemon_state()
        value.update(fields)
        self.save_daemon(value)

    def request(self, operation, params=None):
        return parse_request(encode({"version": VERSION, "request_id": "synthetic-request", "node_id": "fixture-nas", "operation": operation, "params": params or {}, "timeout_ms": 10000}))

    def dispatch(self, operation, params=None, launch=lambda identity: None):
        response = self.service().dispatch(self.request(operation, params), launch)
        self.validator.validate(response)
        return response

    def revision(self):
        return self.service().stack(self.stack_id)["revision"]

    def mutation(self, action="container.restart", operation_id="synthetic:operation:1", target=None):
        return {"operation_id": operation_id, "action": action, "target": target or {"container_id": self.identity}}

    def execute_now(self, params):
        accepted = self.dispatch("operation.submit", params)
        self.service().work(params["operation_id"])
        response = self.dispatch("operation.get", {"operation_id": params["operation_id"]})
        return accepted, response["data"]["operation"]

    def test_exact_identity_lifecycle_idempotence_conflict_and_health(self):
        params = self.mutation()
        accepted, finished = self.execute_now(params)
        self.assertEqual(accepted["data"]["operation"]["command"]["state"], "not_sent")
        self.assertEqual(finished["state"], "succeeded", finished)
        self.assertEqual(finished["runtime"]["health"], "not_configured")
        self.assertFalse(finished["execution_active"])
        replay = self.dispatch("operation.submit", params)
        self.assertTrue(replay["data"]["replayed"])
        self.assertEqual(self.daemon_state()["calls"], [["restart", self.identity]])
        with self.assertRaisesRegex(Failure, "operation_id_conflict"):
            self.dispatch("operation.submit", self.mutation("container.stop"))
        invalid = self.request("operation.submit", params).value()
        invalid["params"]["target"]["container_id"] = "abc123"
        with self.assertRaisesRegex(Failure, "invalid_params"):
            parse_request(encode(invalid))

    def test_stale_config_and_identical_content_inode_replacement_reject(self):
        expected = self.revision()
        replacement = self.stack / "replacement"
        replacement.write_bytes(self.raw)
        os.replace(replacement, self.compose)
        with self.assertRaisesRegex(Failure, "stack_revision_changed"):
            self.dispatch("operation.submit", self.mutation("stack.stop", target={"stack_id": self.stack_id, "expected_revision": expected}))
        self.assertEqual(self.daemon_state()["calls"], [])

    def test_new_and_existing_secrets_are_masked_and_preserved(self):
        inspected = self.dispatch("stack.inspect", {"stack_id": self.stack_id})["data"]["stack"]
        self.assertNotIn("synthetic-original-secret", json.dumps(inspected))
        self.assertNotIn("synthetic-dotenv-secret", json.dumps(inspected))
        edited = inspected["compose_content"].replace("nginx:alpine", "nginx:stable") + "    labels:\n      API_KEY: synthetic-new-api-key\n      endpoint: https://user:synthetic-url-pass@example.invalid/\n"
        plan = self.dispatch("stack.plan", {"action": "deploy", "stack_id": self.stack_id, "expected_revision": inspected["revision"], "compose_content": edited})["data"]["plan"]
        for value in ("synthetic-original-secret", "synthetic-new-api-key", "synthetic-url-pass", "synthetic-dotenv-secret"):
            self.assertNotIn(value, json.dumps(plan))
        self.assertEqual(plan["state"], "ready", plan)
        original_env = (self.stack / ".env").read_bytes()
        _, result = self.execute_now(self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))
        self.assertEqual(result["state"], "succeeded", result)
        self.assertIn(b"synthetic-original-secret", self.compose.read_bytes())
        self.assertEqual((self.stack / ".env").read_bytes(), original_env)
        invalid_image = self.dispatch("stack.plan", {"action": "create", "project_name": "invalid-image", "compose_content": "services:\n  web:\n    image: https://user:synthetic-image-password@example.invalid/image\n"})["data"]["plan"]
        self.assertEqual(invalid_image["state"], "blocked")
        self.assertNotIn("synthetic-image-password", json.dumps(invalid_image))

    def test_external_edit_env_and_target_replacement_invalidate_plan(self):
        revision = self.revision()
        plan = self.dispatch("stack.plan", {"action": "update", "stack_id": self.stack_id, "expected_revision": revision})["data"]["plan"]
        (self.stack / ".env").write_text("TOKEN=external-edit\n")
        with self.assertRaisesRegex(Failure, "stack_revision_changed"):
            self.dispatch("operation.submit", self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))
        (self.stack / ".env").write_text("TOKEN=synthetic-dotenv-secret\n")
        params = self.mutation()
        self.dispatch("operation.submit", params)
        state = self.daemon_state()
        del state["containers"][self.identity]
        state["containers"][f"{2:064x}"] = inspect_value(2)
        self.save_daemon(state)
        self.service().work(params["operation_id"])
        result = self.dispatch("operation.get", {"operation_id": params["operation_id"]})["data"]["operation"]
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(self.daemon_state()["calls"], [])

    def test_guard_and_dockge_dependency_are_project_and_exact_name_scoped(self):
        state = self.daemon_state()
        item = state["containers"][self.identity]
        item[9], item[1] = "tianshu-v2-resident", "/tianshu-platform"
        self.save_daemon(state)
        for protected in ("resident", "dockge"):
            if protected == "dockge":
                state["containers"][self.identity][9] = "ordinary"
                state["containers"][self.identity][1] = "/dockge"
                self.save_daemon(state)
            details = self.dispatch("container.inspect", {"container_id": self.identity})["data"]["container"]
            self.assertTrue(details["protection"]["protected"])
            self.assertEqual(details["allowed_actions"], [])
            with self.assertRaisesRegex(Failure, "maintenance_owned_target"):
                self.dispatch("operation.submit", self.mutation())
        state["containers"][self.identity][1] = "/dockge-user-app"
        self.save_daemon(state)
        self.assertFalse(self.dispatch("container.inspect", {"container_id": self.identity})["data"]["container"]["protection"]["protected"])
        self.assertEqual(self.daemon_state()["calls"], [])

    def test_pull_failure_partial_start_and_versions_restore_preserve_data(self):
        data = self.stack / "data"
        data.mkdir()
        marker = data / "untouched.txt"
        marker.write_text("synthetic-user-data")
        revision = self.revision()
        plan = self.dispatch("stack.plan", {"action": "update", "stack_id": self.stack_id, "expected_revision": revision})["data"]["plan"]
        self.change_daemon(pull_failure=True)
        _, failed = self.execute_now(self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))
        self.assertEqual(failed["command"]["state"], "failed")
        self.assertEqual(self.daemon_state()["calls"], [["compose.pull", "synthetic"]])
        self.change_daemon(pull_failure=False)
        params = self.mutation("stack.apply", "synthetic:update:2", {"plan_id": plan["plan_id"]})
        _, result = self.execute_now(params)
        self.assertEqual(result["state"], "succeeded", result)
        versions = self.dispatch("stack.versions", {"stack_id": self.stack_id})["data"]["versions"]
        before = next(version for version in versions if version["version_id"].endswith(":before"))
        restored = self.dispatch("stack.plan", {"action": "restore", "stack_id": self.stack_id, "expected_revision": self.revision(), "restore_version_id": before["version_id"]})["data"]["plan"]
        _, receipt = self.execute_now(self.mutation("stack.apply", "synthetic:restore", {"plan_id": restored["plan_id"]}))
        self.assertEqual(receipt["state"], "succeeded", receipt)
        self.assertEqual(self.daemon_state()["containers"][self.identity][3], "sha256:" + "b" * 64)
        self.assertEqual(marker.read_text(), "synthetic-user-data")
        self.assertEqual(self.compose.read_bytes(), self.raw)

    def test_port_conflict_and_external_ownership_plan_are_explicit(self):
        state = self.daemon_state()
        other = inspect_value(2, project="foreign")
        other[14] = {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18337"}]}
        state["containers"][other[0]] = other
        self.save_daemon(state)
        inspected = self.service().stack(self.stack_id, editable=True)
        plan = self.dispatch("stack.plan", {"action": "deploy", "stack_id": self.stack_id, "expected_revision": inspected["revision"],
                    "compose_content": inspected["compose_content"] + '    ports: ["18337:80"]\n'})["data"]["plan"]
        self.assertEqual(plan["state"], "blocked")
        self.assertIn("published_port_in_use", plan["blockers"])
        self.config = replace(self.config, platform_stacks=())
        with self.assertRaisesRegex(Failure, "stack_configuration_not_owned"):
            self.dispatch("stack.plan", {"action": "deploy", "stack_id": self.stack_id, "expected_revision": self.revision(), "compose_content": self.raw.decode()})

    def test_create_relative_bind_is_host_resolved_and_partial_start_is_failure(self):
        raw = 'services:\n  web:\n    image: nginx:alpine\n    volumes:\n      - type: bind\n        source: ./data\n        target: /data\n  db:\n    image: redis:alpine\n'
        plan = self.dispatch("stack.plan", {"action": "create", "project_name": "new-synthetic", "compose_content": raw})["data"]["plan"]
        self.assertEqual(plan["state"], "ready", plan)
        self.assertEqual(plan["mounts"][0]["source"], str(self.stacks / "new-synthetic" / "data"))
        self.change_daemon(partial_start=True)
        _, result = self.execute_now(self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(result["runtime"]["state"], "partial")
        self.assertTrue((self.stacks / "new-synthetic" / "compose.yaml").is_file())
        self.assertEqual(self.service().files.owner(self.stacks / "new-synthetic"), "platform")

    def test_protected_bind_roots_block_plan_and_actual_container_with_owner_code(self):
        protected = self.directory / "protected-resident"
        protected.mkdir()
        self.config = replace(self.config, protected_bind_roots=({"path": str(protected), "owner": "resident-update", "reason": "capacity_guard_mount_ownership"},))
        raw = 'services:\n  web:\n    image: nginx:alpine\n    volumes:\n      - type: bind\n        source: ' + str(protected / "data") + '\n        target: /data\n'
        plan = self.dispatch("stack.plan", {"action": "create", "project_name": "unsafe-fixture", "compose_content": raw})["data"]["plan"]
        self.assertEqual(plan["state"], "blocked")
        self.assertIn("capacity_guard_mount_ownership", plan["blockers"])
        state = self.daemon_state()
        state["containers"][self.identity][15] = [{"Type": "bind", "Source": str(protected / "data"), "Destination": "/data", "RW": True}]
        self.save_daemon(state)
        with self.assertRaisesRegex(Failure, "maintenance_owned_mount_target"):
            self.dispatch("operation.submit", self.mutation())
        self.assertEqual(self.daemon_state()["calls"], [])

    def test_old_plan_rejects_replaced_runtime_and_expiry(self):
        plan = self.dispatch("stack.plan", {"action": "update", "stack_id": self.stack_id, "expected_revision": self.revision()})["data"]["plan"]
        state = self.daemon_state()
        state["containers"][self.identity][3] = "sha256:" + "d" * 64
        self.save_daemon(state)
        with self.assertRaisesRegex(Failure, "stack_runtime_identity_changed"):
            self.dispatch("operation.submit", self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))
        service = self.service()
        saved = service.store.read("plan", plan["plan_id"])
        saved["public"]["expires_at"] = "2026-10-01T00:00:00Z"
        service.store.write("plan", plan["plan_id"], saved)
        with self.assertRaisesRegex(Failure, "plan_expired"):
            self.dispatch("operation.submit", self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))

    def test_restore_previews_saved_env_without_touching_current_and_pins_survive_refresh(self):
        self.compose.write_bytes(self.raw + b'    ports: ["${PORT}:80"]\n')
        (self.stack / ".env").write_text("PORT=18338\nTOKEN=first-secret\n")
        service = self.service()
        before = service.save_version("synthetic:saved", self.stack_id, self.stack, service.engine.project_items("synthetic", self.stack), suffix="before")
        (self.stack / ".env").write_text("PORT=18339\nTOKEN=second-secret\n")
        plan = self.dispatch("stack.plan", {"action": "restore", "stack_id": self.stack_id, "expected_revision": self.revision(), "restore_version_id": before})["data"]["plan"]
        self.assertEqual(plan["state"], "ready", plan)
        self.assertEqual(plan["ports"][0]["published"], "18338")
        self.assertIn("PORT=18339", (self.stack / ".env").read_text())
        self.assertEqual(list(self.stack.glob(".tianshu-control-preview-*")), [])
        _, result = self.execute_now(self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))
        self.assertEqual(result["state"], "succeeded", result)
        stack = self.dispatch("stack.inspect", {"stack_id": self.stack_id})["data"]["stack"]
        self.assertEqual(stack["runtime"]["state"], "running")
        self.assertIn("stack.deploy", stack["allowed_actions"])
        deploy = self.dispatch("stack.plan", {"action": "deploy", "stack_id": self.stack_id, "expected_revision": stack["revision"], "compose_content": stack["compose_content"]})["data"]["plan"]
        self.assertFalse(deploy["images"][0]["pull"])

    def test_bounded_mutation_output_is_unknown_and_never_exposed(self):
        self.change_daemon(flood=True)
        plan = self.dispatch("stack.plan", {"action": "update", "stack_id": self.stack_id, "expected_revision": self.revision()})["data"]["plan"]
        _, result = self.execute_now(self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))
        self.assertEqual(result["state"], "unknown", result)
        self.assertNotIn("synthetic-sensitive-output", json.dumps(result))

    def test_local_image_never_policy_and_absent_reference_restore_are_explicit(self):
        raw = 'services:\n  web:\n    image: local-fixture:test\n    pull_policy: never\n'
        plan = self.dispatch("stack.plan", {"action": "create", "project_name": "offline-fixture", "compose_content": raw})["data"]["plan"]
        self.assertEqual(plan["state"], "ready", plan)
        self.assertFalse(plan["images"][0]["pull"])
        self.assertTrue(plan["images"][0]["target_image_id"].startswith("sha256:"))
        self.change_daemon(missing_image=True)
        blocked = self.dispatch("stack.plan", {"action": "create", "project_name": "missing-fixture", "compose_content": raw})["data"]["plan"]
        self.assertIn("local_image_required", blocked["blockers"])
        self.change_daemon(missing_image=False)
        env = self.stack / ".env"
        env.unlink()
        service = self.service()
        version = service.save_version("without-env", self.stack_id, self.stack, service.engine.project_items("synthetic", self.stack), suffix="before")
        env.write_text("NEW_SECRET=synthetic-new-secret\n")
        restored = self.dispatch("stack.plan", {"action": "restore", "stack_id": self.stack_id, "expected_revision": self.revision(), "restore_version_id": version})["data"]["plan"]
        self.assertEqual(restored["state"], "blocked")
        self.assertIn("restore_would_remove_new_reference_file", restored["blockers"])
        self.assertEqual(env.read_text(), "NEW_SECRET=synthetic-new-secret\n")

    def test_late_launcher_cannot_be_timed_out_into_releasing_project(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(10)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: sleeper.wait(timeout=2) if sleeper.poll() is not None else (sleeper.kill(), sleeper.wait(timeout=2)))
        params = self.mutation()
        self.dispatch("operation.submit", params, launch=lambda identity: {"state": "launched", "identity": process_identity(sleeper.pid)})
        service = self.service()
        saved = service.store.read("operation", params["operation_id"])
        saved["receipt"]["created_at"] = "2026-10-01T00:00:00Z"
        service.store.write("operation", params["operation_id"], saved)
        self.assertTrue(service.get(params["operation_id"])["execution_active"])
        with self.assertRaisesRegex(Failure, "project_operation_active"):
            self.dispatch("operation.submit", self.mutation("container.stop", "new-client"))
        service.store.write("reservation", saved["resolved"]["lock"], {"operation_id": "different-owner"})
        service.work(params["operation_id"])
        self.assertEqual(self.daemon_state()["calls"], [])

    def test_docker_exec_client_timeout_preserves_remote_execution_lease(self):
        plan = self.dispatch("stack.plan", {"action": "update", "stack_id": self.stack_id, "expected_revision": self.revision()})["data"]["plan"]
        self.config = replace(self.config, compose_command=("synthetic-docker", "exec", "-i", self.identity, "docker", "compose"))
        params = self.mutation("stack.apply", target={"plan_id": plan["plan_id"]})
        service = self.service()
        local = LocalTransport(service.deadline)
        class LostExec:
            deadline = service.deadline
            def command(wire, argv, payload=b"", **kwargs):
                if argv[:1] == ["synthetic-docker"]:
                    if argv[-2:] == ["123", "fixture-created"]:
                        return CommandResult(stdout=b'{"active":true}', exit_code=0)
                    marker = Path(argv[-3])
                    marker.write_text(json.dumps({"state": "running", "pid": 123, "created": "fixture-created"}))
                    # Docker exec can exit nonzero after its connection drops
                    # while the original container-side Compose keeps running.
                    return CommandResult(exit_code=1, stderr=b"synthetic client connection closed")
                return local.command(argv, payload, **kwargs)
        service.engine.transport = LostExec()
        service.engine.docker.transport = service.engine.transport
        service.submit(params, lambda identity: None)
        service.work(params["operation_id"])
        saved = service.store.read("operation", params["operation_id"])
        self.assertEqual(saved["receipt"]["state"], "unknown")
        self.assertTrue(saved["receipt"]["execution_active"])
        service.store.write("launcher", params["operation_id"], {"state": "launched", "identity": False})
        self.assertTrue(service.get(params["operation_id"])["execution_active"])
        marker = Path(saved["leases"][0]["host_path"])
        marker.write_text(json.dumps({"state": "complete", "exit_code": 0, "timed_out": False}))
        final = service.get(params["operation_id"])
        self.assertFalse(final["execution_active"])
        self.assertEqual(final["command"]["state"], "unknown")

    def test_restore_extra_reference_external_edit_is_not_overwritten(self):
        extra = self.stack / "settings.env"
        extra.write_text("ORIGINAL=synthetic-old-value\n")
        self.compose.write_bytes(self.raw + b"    env_file: settings.env\n")
        service = self.service()
        version = service.save_version("with-extra-reference", self.stack_id, self.stack, service.engine.project_items("synthetic", self.stack), suffix="before")
        self.compose.write_bytes(self.raw)
        plan = self.dispatch("stack.plan", {"action": "restore", "stack_id": self.stack_id, "expected_revision": self.revision(), "restore_version_id": version})["data"]["plan"]
        self.assertEqual(plan["state"], "ready", plan)
        extra.write_text("EXTERNAL=synthetic-new-value\n")
        with self.assertRaisesRegex(Failure, "compose_input_changed"):
            self.dispatch("operation.submit", self.mutation("stack.apply", target={"plan_id": plan["plan_id"]}))
        self.assertEqual(extra.read_text(), "EXTERNAL=synthetic-new-value\n")

    def test_unavailable_after_remote_acceptance_stays_unknown_and_only_gets(self):
        seen = []
        class Wire:
            def __init__(self, *args):
                pass
            def command(wire, argv, payload, **kwargs):
                outgoing = parse_request(payload)
                seen.append(outgoing.operation)
                result = self.service().dispatch(outgoing, lambda identity: None)
                if outgoing.operation == "operation.submit":
                    result.update(status="unavailable", reason_codes=["control_state_unavailable"], data=None)
                return CommandResult(stdout=encode(result), exit_code=0)
        node = Node("fixture-nas", "fixture", "linux", "unused.invalid", 1, "unused", "unused", "unused")
        config = RelayConfig(Config(self.directory / "sampling", {node.id: node}), self.directory / "relay", {node.id: ("runner",)})
        params = self.mutation()
        first = relay(self.request("operation.submit", params), config, transport_factory=Wire)
        self.assertEqual(first["data"]["operation"]["state"], "unknown")
        relay(self.request("operation.submit", params), config, transport_factory=Wire)
        self.assertEqual(seen, ["operation.submit", "operation.get"])

    @unittest.skipUnless(shutil.which("ssh"), "OpenSSH loopback client unavailable")
    def test_exported_loopback_fixture_real_control_cli_joint(self):
        fixture_dir = self.directory / "joint-fixture"
        ready_path = self.directory / "joint-ready.json"
        server = subprocess.Popen([sys.executable, str(Path(__file__).with_name("control_ssh.py")), "--directory", str(fixture_dir), "--ready-file", str(ready_path)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
        try:
            expires = time.monotonic() + 10
            while not ready_path.exists() and server.poll() is None and time.monotonic() < expires:
                time.sleep(.02)
            self.assertTrue(ready_path.exists(), "synthetic control SSH fixture failed to start")
            ready = json.loads(ready_path.read_text())
            def cli(operation, params):
                request = self.request(operation, params)
                process = subprocess.run(ready["control_command"], input=encode(request.value()), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, timeout=15)
                self.assertEqual(process.returncode, 0, process.stderr.decode())
                response = json.loads(process.stdout)
                self.validator.validate(response)
                return response
            listed = cli("stack.list", {})
            self.assertEqual(listed["data"]["stacks"][0]["id"], ready["stack_id"])
            params = self.mutation(target={"container_id": ready["container_id"]})
            accepted = cli("operation.submit", params)
            self.assertEqual(accepted["data"]["operation"]["state"], "accepted")
            expires = time.monotonic() + 10
            while time.monotonic() < expires:
                result = cli("operation.get", {"operation_id": params["operation_id"]})["data"]["operation"]
                if result["execution_active"] is False:
                    break
                time.sleep(.05)
            self.assertEqual(result["state"], "succeeded", result)
            replay = cli("operation.submit", params)
            self.assertTrue(replay["data"]["replayed"])
            daemon = json.loads((fixture_dir / "synthetic-daemon.json").read_text())
            self.assertEqual(daemon["calls"], [["restart", ready["container_id"]]])
        finally:
            server.communicate(input=b"stop", timeout=10)

    def test_pending_same_project_is_rejected_and_worker_loss_never_resends(self):
        params = self.mutation()
        self.dispatch("operation.submit", params)
        with self.assertRaisesRegex(Failure, "project_operation_active"):
            self.dispatch("operation.submit", self.mutation("container.stop", "second"))
        service = self.service()
        original = service.store.read("operation", params["operation_id"])
        original["receipt"].update(state="running", phase="applying", created_at="2026-10-01T00:00:00Z", command={"state": "sent", "exit_code": None, "reason_codes": []})
        service.store.write("operation", params["operation_id"], original)
        service.store.write("launcher", params["operation_id"], {"state": "launched", "identity": False})
        public = service.get(params["operation_id"])
        self.assertEqual(public["state"], "unknown")
        self.assertFalse(public["execution_active"])
        replay = self.dispatch("operation.submit", params)
        self.assertTrue(replay["data"]["replayed"])
        self.assertEqual(self.daemon_state()["calls"], [])

    def test_symlink_and_path_escape_rejected(self):
        env = self.stack / ".env"
        env.unlink()
        outside = self.directory / "outside-secret"
        outside.write_text("synthetic-outside")
        try:
            env.symlink_to(outside)
        except OSError:
            self.skipTest("OS does not permit test symlinks")
        with self.assertRaisesRegex(Failure, "stack_path_invalid"):
            self.service().stack(self.stack_id)

    def remote_cli(self, operation, params=None):
        process = subprocess.run([sys.executable, "-B", "-m", "ops.infrastructure.control.remote", "--config", str(self.config_path)],
            input=encode(self.request(operation, params).value()), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(len(process.stdout.splitlines()), 1)
        response = json.loads(process.stdout)
        self.validator.validate(response)
        return response

    def wait_receipt(self, operation_id):
        expires = time.monotonic() + 10
        while time.monotonic() < expires:
            response = self.remote_cli("operation.get", {"operation_id": operation_id})
            value = response.get("data", {}).get("operation")
            if value and value["execution_active"] is False:
                return value
            time.sleep(.05)
        self.fail("detached synthetic worker did not settle")

    def test_actual_cli_detached_worker_survives_request_exit_and_concurrent_replay(self):
        self.change_daemon(delay=.7)
        params = self.mutation()
        accepted = self.remote_cli("operation.submit", params)
        self.assertEqual(accepted["data"]["operation"]["state"], "accepted")
        active = self.remote_cli("operation.get", {"operation_id": params["operation_id"]})
        self.assertIn(active["data"]["operation"]["execution_active"], (True, None))
        replay = self.remote_cli("operation.submit", params)
        self.assertTrue(replay["data"]["replayed"])
        final = self.wait_receipt(params["operation_id"])
        self.assertEqual(final["state"], "succeeded", final)
        self.assertEqual(self.daemon_state()["calls"], [["restart", self.identity]])

    def test_real_process_timeout_is_unknown_with_actual_readback_and_no_repeat(self):
        self.change_daemon(delay=10)
        params = self.mutation()
        self.dispatch("operation.submit", params)
        self.service(2).work(params["operation_id"])
        state = self.dispatch("operation.get", {"operation_id": params["operation_id"]})["data"]["operation"]
        self.assertEqual(state["state"], "unknown", state)
        self.assertEqual(state["command"]["state"], "unknown")
        self.dispatch("operation.submit", params)
        self.assertEqual(self.daemon_state()["calls"], [["restart", self.identity]])

    def test_relay_disconnect_send_window_recovers_original_without_resubmit(self):
        remote = self.service()
        seen = []
        class Wire:
            def __init__(self, *args):
                pass
            def command(wire, argv, payload, **kwargs):
                request = parse_request(payload)
                seen.append(request.operation)
                result = self.service().dispatch(request, lambda identity: None)
                if request.operation == "operation.submit":
                    return CommandResult(exit_code=255, stderr=b"synthetic lost connection after node accepted")
                return CommandResult(stdout=encode(result), exit_code=0)
        node = Node("fixture-nas", "fixture", "linux", "unused.invalid", 1, "unused", "unused", "unused")
        config = RelayConfig(Config(self.directory / "sampling", {node.id: node}), self.directory / "relay", {node.id: ("synthetic-runner",)})
        params = self.mutation()
        first = relay(self.request("operation.submit", params), config, transport_factory=Wire)
        self.validator.validate(first)
        self.assertEqual(first["data"]["operation"]["state"], "unknown")
        self.assertIsNone(first["data"]["operation"]["execution_active"])
        second = relay(self.request("operation.submit", params), config, transport_factory=Wire)
        self.validator.validate(second)
        self.assertTrue(second["data"]["replayed"])
        self.assertEqual(seen, ["operation.submit", "operation.get"])
        self.service().work(params["operation_id"])
        final = relay(self.request("operation.get", {"operation_id": params["operation_id"]}), config, transport_factory=Wire)
        self.assertEqual(final["data"]["operation"]["state"], "succeeded")
        self.assertEqual(self.daemon_state()["calls"], [["restart", self.identity]])


if __name__ == "__main__":
    unittest.main()
