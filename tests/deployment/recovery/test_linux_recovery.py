"""Executable orchestration with a Docker contract double, not Linux/product proof."""

import tempfile
import unittest
import uuid
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

from fixtures import fixture
from runtime_fixtures import RuntimeDocker, runtime_fixture

from ops.recovery import linux_recovery
from ops.recovery.engine import Recovery
from ops.recovery.lifecycle_binding import describe
from ops.recovery.product_inventory import inventory
from ops.recovery.runtime_identity import load_identity
from ops.recovery.safety import RecoveryError, canonical, digest, file_hash, read_json


class LinuxRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "scope"
        self.scope, _ = fixture(self.root)
        self.source = self.root / "deployments/source"
        self.identity, self.runtime = runtime_fixture(self.source)
        self.recovery = Recovery(self.root, self.scope)
        self.authority = str(uuid.uuid4())
        self.pin = file_hash(self.identity)
        marker = {"role": "authority", "deployment_id": self.authority}
        inv = inventory(self.source)
        binding = describe(
            self.recovery,
            self.source,
            self.runtime["project_name"],
            linux_recovery.COMPOSE_FILES,
            "compose",
            runtime_pin={"path": "reports/runtime-identity.json", "sha256": self.pin},
            registration=(
                marker,
                read_json(self.source / "release-manifest.json"),
                digest(canonical(inv)),
            ),
        )
        self.docker = RuntimeDocker(self.source, binding)
        (self.source / ".deployment.json").unlink()
        (self.source / "recovery-inventory.json").unlink()
        self.stack = ExitStack()
        self.stack.enter_context(
            patch.object(linux_recovery, "runtime_lease", lambda *_: nullcontext())
        )
        self.stack.enter_context(
            patch.object(
                linux_recovery,
                "load_identity",
                lambda d, p, h, **kw: load_identity(d, p, h),
            )
        )
        self.stack.enter_context(
            patch.object(linux_recovery, "DockerCLI", lambda *_: self.docker)
        )
        self.stack.enter_context(
            patch("ops.recovery.lifecycle.DockerCLI", lambda *_: self.docker)
        )

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def prepare(self, execute=True):
        return linux_recovery.prepare(
            self.recovery,
            self.source,
            self.identity,
            self.pin,
            self.authority,
            execute=execute,
            docker_executable=__file__,
        )

    def test_plan_has_no_registration_or_docker_mutation(self):
        result = self.prepare(False)
        self.assertEqual(result["mode"], "plan")
        self.assertFalse((self.source / ".deployment.json").exists())
        self.assertEqual(self.docker.calls, [])

    def test_prepare_stop_backup_disabled_restore(self):
        prepared = self.prepare()
        result = linux_recovery.rehearse(
            self.recovery,
            self.source,
            prepared["registration_sha256"],
            "backup",
            "restored",
            execute=True,
            docker_executable=__file__,
        )
        self.assertEqual(result["status"], "disabled_restore_complete")
        self.assertEqual(result["runtime_owners_stopped"], 9)
        self.assertFalse(result["product_functional_restore"])
        marker = read_json(self.root / "deployments/restored/.deployment.json")
        self.assertEqual(marker["status"], "restored_disabled")
        self.assertEqual(read_json(self.identity)["authority"], None)
        self.assertEqual(file_hash(self.identity), self.pin)
        self.assertFalse(
            any(
                call[:2] in {("compose", "up"), ("container", "start")}
                for call in self.docker.calls
            )
        )

    def test_old_container_after_registration_refused_before_stop(self):
        prepared = self.prepare()
        self.docker.containers[0]["Id"] = "f" * 64
        with self.assertRaisesRegex(RecoveryError, "runtime_owner_changed"):
            linux_recovery.rehearse(
                self.recovery,
                self.source,
                prepared["registration_sha256"],
                "backup",
                "restored",
                execute=True,
                docker_executable=__file__,
            )
        self.assertFalse(any(c[:2] == ("container", "kill") for c in self.docker.calls))

    def test_abnormal_exit_keeps_gate_and_never_publishes_backup(self):
        prepared = self.prepare()
        self.docker.containers[0]["State"]["ExitCode"] = 143
        with self.assertRaisesRegex(RecoveryError, "owner_exit_unconfirmed"):
            linux_recovery.rehearse(
                self.recovery,
                self.source,
                prepared["registration_sha256"],
                "backup",
                "restored",
                execute=True,
                docker_executable=__file__,
            )
        self.assertTrue((self.source / ".lifecycle/MAINTENANCE.json").exists())
        self.assertFalse((self.root / "backups/backup").exists())

    def test_repeat_registration_and_existing_target_refused(self):
        prepared = self.prepare()
        with self.assertRaisesRegex(RecoveryError, "authority_must_be_new"):
            self.prepare()
        (self.root / "deployments/restored").mkdir()
        with self.assertRaisesRegex(RecoveryError, "destination_exists"):
            linux_recovery.rehearse(
                self.recovery,
                self.source,
                prepared["registration_sha256"],
                "backup",
                "restored",
                execute=True,
            )
