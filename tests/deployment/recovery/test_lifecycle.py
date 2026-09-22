import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from fixtures import candidate, fixture, put
from lifecycle_fixtures import compose_fixture, interface

from ops.recovery.engine import Recovery
from ops.recovery.lifecycle import operate
from ops.recovery.manifest import release
from ops.recovery.safety import RecoveryError, file_hash, files

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
PROJECT = "tianshu-synthetic-depf"


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        runtime = HERE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "sandbox"
        self.scope, self.authority = fixture(self.root)
        self.source = self.root / "deployments/source"
        self.compose_files = compose_fixture(self.source)
        self.processes = []
        self.binding_hash = None

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                # Only this test's exact Popen handles. Never global process discovery/kill.
                process.kill()
            process.wait(timeout=10)
        self.assertTrue(
            self.base.resolve().is_relative_to((HERE / ".runtime").resolve())
        )
        self.temp.cleanup()

    def cli(self, command, *args, execute=False, expected=0):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "ops.recovery",
                "--root",
                str(self.root),
                "--scope-id",
                self.scope,
                *(["--execute"] if execute else []),
                command,
                *map(str, args),
            ],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def initialize(self, execute=True):
        options = []
        for file in self.compose_files:
            options.extend(["--compose-file", file])
        result = self.cli(
            "lifecycle-init",
            "--deployment-directory",
            self.source,
            "--project",
            PROJECT,
            "--backend",
            "local-process",
            *options,
            execute=execute,
        )
        self.binding_hash = result["binding_sha256"]
        return result

    def start(self, special=None):
        self.initialize()
        binding = json.loads((self.source / ".lifecycle/binding.json").read_bytes())
        for service in binding["services"]:
            options = (special or {}).get(service, [])
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(HERE / "lifecycle_worker.py"),
                    "--directory",
                    str(self.source),
                    "--service",
                    service,
                    *options,
                ],
                cwd=WORKSPACE,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.processes.append(process)
            deadline = time.monotonic() + 8
            while not (
                self.source / ".lifecycle/owners" / (service + ".json")
            ).exists():
                self.assertIsNone(process.poll(), service)
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
        return binding

    def action(self, name="backup", *args, execute=True, expected=0):
        return self.cli(
            "lifecycle-" + name,
            "--deployment-directory",
            self.source,
            "--project",
            PROJECT,
            "--binding-sha256",
            self.binding_hash,
            *args,
            execute=execute,
            expected=expected,
        )

    def test_plan_is_readonly_without_process_or_docker(self):
        before = {n: file_hash(self.root / n) for n in files(self.root)}
        self.initialize(execute=False)
        self.assertEqual(
            before, {n: file_hash(self.root / n) for n in files(self.root)}
        )
        self.initialize()
        before = {n: file_hash(self.root / n) for n in files(self.root)}
        plan = self.action("backup", "--backup", "copy", execute=False)
        self.assertEqual(plan["projects"], [PROJECT, PROJECT + "-obs"])
        self.assertEqual(len(plan["stop_order"]), 9)
        self.assertEqual(
            before, {n: file_hash(self.root / n) for n in files(self.root)}
        )

    def test_existing_backup_is_rejected_before_stopping_writers(self):
        self.start()
        (self.root / "backups/existing").mkdir()
        result = self.action("backup", "--backup", "existing", expected=2)
        self.assertEqual(result["reason"], "destination_exists")
        self.assertFalse((self.source / ".lifecycle/MAINTENANCE.json").exists())
        self.assertTrue(all(process.poll() is None for process in self.processes))

    def test_nine_real_writers_stop_backup_restore_and_remain_disabled(self):
        self.start()
        self.assertTrue(all(p.poll() is None for p in self.processes))
        result = self.action("backup", "--backup", "copy")
        self.assertEqual(result["runtime_owners_stopped"], 9)
        for process in self.processes:
            self.assertEqual(process.wait(timeout=2), 0)
        receipt = result["result"]["snapshot_sha256"]
        target = self.root / "deployments/restored"
        restored = self.action(
            "restore",
            "--backup",
            "copy",
            "--snapshot-sha256",
            receipt,
            "--target",
            "restored",
            "--authority-id",
            self.authority,
        )
        self.assertEqual(restored["result"]["verification"]["status"], "state_verified")
        self.assertEqual(restored["activation"], "disabled")
        for component in ("vector", "loki", "grafana", "prometheus", "guard"):
            rel = "observability/data/" + component
            for name in files(self.source / rel):
                self.assertEqual(
                    (self.source / rel / name).read_bytes(),
                    (target / rel / name).read_bytes(),
                )
            self.assertTrue((target / rel / "empty-subdirectory").is_dir())
        for product in ("platform", "companion", "memory", "gateway"):
            log = target / "logs" / product / "events.log"
            self.assertTrue(
                log.read_bytes().endswith(b'{"synthetic_shutdown":"complete"}\n')
            )
            with closing(sqlite3.connect(target / "data" / product / "main.db")) as db:
                self.assertGreater(
                    int(
                        db.execute(
                            "SELECT value FROM facts WHERE id='sequence'"
                        ).fetchone()[0]
                    ),
                    0,
                )
        # A restart attempt uses the public fixture CLI and must honor the persistent gate.
        restart = subprocess.run(
            [
                sys.executable,
                "-B",
                str(HERE / "lifecycle_worker.py"),
                "--directory",
                str(self.source),
                "--service",
                "platform",
            ],
            capture_output=True,
            timeout=5,
        )
        self.assertNotEqual(restart.returncode, 0)
        self.assertFalse((target / ".lifecycle").exists())

    def test_timeout_with_residual_writer_never_publishes(self):
        self.start({"companion": ["--ignore-stop"]})
        started = time.monotonic()
        result = self.action(
            "backup", "--backup", "copy", "--timeout", "0.5", expected=2
        )
        self.assertEqual(result["reason"], "lifecycle_timeout")
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse((self.root / "backups/copy").exists())
        self.assertTrue((self.source / ".lifecycle/MAINTENANCE.json").exists())
        self.assertTrue(any(p.poll() is None for p in self.processes))

    def test_exit_receipt_does_not_replace_kernel_exit(self):
        self.start({"platform": ["--exit-delay", "2"]})
        result = self.action(
            "backup", "--backup", "copy", "--timeout", "0.4", expected=2
        )
        self.assertEqual(result["reason"], "lifecycle_timeout")
        self.assertFalse((self.root / "backups/copy").exists())

    def test_cancel_during_stop_keeps_gate_and_no_backup(self):
        self.start({"platform": ["--ignore-stop"]})
        start = time.monotonic()
        with self.assertRaisesRegex(RecoveryError, "cancelled"):
            operate(
                Recovery(self.root, self.scope),
                self.source,
                PROJECT,
                self.binding_hash,
                "backup",
                backup="copy",
                execute=True,
                cancel=lambda: time.monotonic() - start > 0.15,
            )
        self.assertFalse((self.root / "backups/copy").exists())
        self.assertTrue((self.source / ".lifecycle/MAINTENANCE.json").exists())

    def test_wrong_birth_unknown_owner_and_binding_drift_rejected_before_stop(self):
        self.start()
        home = self.source / ".lifecycle"
        owner_path = home / "owners/platform.json"
        original = json.loads(owner_path.read_bytes())
        put(owner_path, original | {"birth": "wrong"})
        result = self.action("backup", "--backup", "copy", expected=2)
        self.assertEqual(result["reason"], "process_identity_mismatch")
        put(owner_path, original)
        put(home / "owners/rogue.json", {})
        result = self.action("backup", "--backup", "copy", expected=2)
        self.assertEqual(result["reason"], "unregistered_runtime_owner")
        self.assertFalse((home / "MAINTENANCE.json").exists())
        with (self.source / "compose.json").open("ab") as stream:
            stream.write(b" ")
        result = self.action("backup", "--backup", "copy", expected=2)
        self.assertEqual(result["reason"], "deployment_binding_drift")

    def test_direct_legacy_backup_cannot_bypass_registered_lifecycle(self):
        self.start()
        result = self.cli(
            "backup",
            "--deployment",
            "source",
            "--backup",
            "copy",
            execute=True,
            expected=2,
        )
        self.assertEqual(result["reason"], "observability_lifecycle_required")
        self.assertFalse((self.root / "backups/copy").exists())

    def test_current_authority_change_blocks_old_restore_and_target_not_overwritten(
        self,
    ):
        self.start()
        checksum = self.action("backup", "--backup", "copy")["result"][
            "snapshot_sha256"
        ]
        with closing(sqlite3.connect(self.source / "data/companion/main.db")) as db:
            db.execute(
                "UPDATE facts SET value='revoked-after-backup' WHERE id='visible'"
            )
            db.commit()
        result = self.action(
            "restore",
            "--backup",
            "copy",
            "--snapshot-sha256",
            checksum,
            "--target",
            "restored",
            "--authority-id",
            self.authority,
            expected=2,
        )
        self.assertEqual(result["reason"], "current_authority_diverged")
        self.assertFalse((self.root / "deployments/restored").exists())
        target = self.root / "deployments/existing"
        target.mkdir()
        (target / "retain").write_text("unchanged")
        result = self.action(
            "restore",
            "--backup",
            "copy",
            "--snapshot-sha256",
            checksum,
            "--target",
            "existing",
            "--authority-id",
            self.authority,
            expected=2,
        )
        self.assertEqual(result["reason"], "destination_exists")
        self.assertEqual((target / "retain").read_text(), "unchanged")

    def test_fixed_depe_interface_and_missing_or_misowned_volumes(self):
        original = json.loads(interface())
        self.assertIs(release(original), original)
        for mutator in (
            lambda value: value["volumes"].pop(),
            lambda value: value["volumes"][-1].update(owner_service="obs-loki"),
            lambda value: value["volumes"][-1].update(
                host_path="observability/data/elsewhere"
            ),
        ):
            document = deepcopy(original)
            mutator(document)
            with self.assertRaises(RecoveryError):
                release(document)

    def test_code_update_and_rollback_only_select_disabled_code(self):
        self.start()
        manifest, compatibility = candidate(self.root)
        result = self.action(
            "prepare-update",
            "--backup",
            "before-update",
            "--candidate-manifest",
            manifest,
            "--compatibility",
            compatibility,
            "--update-id",
            "update-one",
        )
        self.assertFalse(result["data_restore"])
        source_db = self.source / "data/companion/main.db"
        before = file_hash(source_db)
        result = self.action(
            "rollback-code",
            "--candidate-manifest",
            manifest,
            "--compatibility",
            compatibility,
            "--update-id",
            "rollback-one",
        )
        self.assertFalse(result["data_restore"])
        self.assertEqual(before, file_hash(source_db))
        self.assertTrue((self.root / "backups/before-update").exists())
