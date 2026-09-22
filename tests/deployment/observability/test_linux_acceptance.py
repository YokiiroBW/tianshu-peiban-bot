import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
import subprocess
from unittest.mock import patch

import helpers  # noqa: F401
from acceptance import (
    Evidence,
    LocalDocker,
    capacity_gate,
    contract_snapshot,
    volume_layout,
    verify_evidence,
)
from runtime_binding import load_identity
from linux_receiver import safe_alerts


class AcceptanceBoundaryTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "real flock requires Linux")
    def test_shared_lease_same_inode_and_nonblocking_contention(self):
        from runtime_binding import lifecycle_lease

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with lifecycle_lease(root):
                inode = (root / ".runtime-owner.lock").stat().st_ino
                script = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from runtime_binding import lifecycle_lease;\ntry:\n with lifecycle_lease(Path(sys.argv[2])): pass\nexcept ValueError as e:\n print(str(e)); sys.exit(3)"
                result = subprocess.run(
                    [sys.executable, "-c", script, str(helpers.PACKAGE), str(root)],
                    capture_output=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 3)
                self.assertEqual(result.stdout.strip(), b"runtime_owner_busy")
            with lifecycle_lease(root):
                self.assertEqual((root / ".runtime-owner.lock").stat().st_ino, inode)

    def test_pinned_g_identity_plan_and_tamper_rejection(self):
        fixture = Path(__file__).parent / "fixtures/runtime-identity.planned.json"
        raw = fixture.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(load_identity(fixture, digest)["state"], "planned")
        with self.assertRaisesRegex(ValueError, "identity_hash_mismatch"):
            load_identity(fixture, "0" * 64)
        with tempfile.TemporaryDirectory() as directory:
            bad = json.loads(raw)
            bad["projects"]["observability"]["name"] = "other-obs"
            changed = Path(directory) / "identity.json"
            changed.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "project_binding_mismatch"):
                load_identity(changed, hashlib.sha256(changed.read_bytes()).hexdigest())

    def test_default_plan_never_calls_docker_or_acquires_lease(self):
        from run_linux import main

        fixture = Path(__file__).parent / "fixtures/runtime-identity.planned.json"
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("run_linux.LocalDocker") as docker,
            patch("run_linux.lifecycle_lease") as lease,
        ):
            output = Path(directory) / "evidence"
            code = main(
                [
                    "--identity",
                    str(fixture),
                    "--identity-sha256",
                    hashlib.sha256(fixture.read_bytes()).hexdigest(),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 0)
            docker.assert_not_called()
            lease.assert_not_called()
            report = json.loads((output / "report.json").read_bytes())
            self.assertTrue(
                all(v["status"] == "not_run" for v in report["dimensions"].values())
            )

    def test_receiver_persists_only_closed_safe_fields(self):
        document = {
            "secret": "MUST_NOT_PERSIST",
            "alerts": [
                {
                    "labels": {
                        "alertname": "CollectorDiscardOrError",
                        "token": "SECRET",
                    },
                    "status": "firing",
                    "annotations": {"message": "SECRET"},
                },
                {"labels": {"alertname": "SECRET"}, "status": "firing"},
            ],
        }
        self.assertEqual(
            safe_alerts(document),
            [{"name": "CollectorDiscardOrError", "status": "firing"}],
        )

    def test_pass_requires_artifact_and_partial_stays_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Evidence(Path(directory) / "report")
            with self.assertRaisesRegex(ValueError, "pass_requires_evidence"):
                evidence.record("numbered_collection", "passed")
            artifact = evidence.artifact("real-result.json", {"count": 400})
            evidence.record("numbered_collection", "passed", artifacts=[artifact])
            evidence.finish()
            result = json.loads((evidence.directory / "report.json").read_bytes())
            self.assertEqual(result["status"], "partial")
            self.assertEqual(
                result["dimensions"]["physical_enospc"]["status"], "not_run"
            )
            self.assertFalse(result["release_ready"])
            self.assertFalse(result["application_reclamation_authorized"])

    def test_existing_evidence_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                Evidence(directory)

    def test_evidence_verifier_rejects_modified_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Evidence(Path(directory) / "evidence")
            artifact = evidence.artifact("retrieved.json", {"events": []})
            evidence.record("numbered_collection", "passed", artifacts=[artifact])
            evidence.finish()
            self.assertEqual(
                verify_evidence(evidence.directory)["run_status"], "partial"
            )
            (evidence.directory / artifact).write_text("{}")
            with self.assertRaisesRegex(ValueError, "evidence_artifact_changed"):
                verify_evidence(evidence.directory)

    def test_contract_raw_bytes_checked_before_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            payload = b"bytes\r\n"
            (source / "README.md").write_bytes(payload)
            raw = json.dumps(
                {"files": {"README.md": hashlib.sha256(payload).hexdigest()}}
            ).encode()
            (source / "manifest.json").write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            destination = Path(directory) / "copy"
            contract_snapshot(source, destination, digest)
            self.assertEqual((destination / "README.md").read_bytes(), payload)
            (source / "README.md").write_bytes(b"bytes\n")
            rejected = Path(directory) / "rejected"
            with self.assertRaisesRegex(ValueError, "contract_file_changed"):
                contract_snapshot(source, rejected, digest)
            self.assertFalse(rejected.exists())

    def test_remote_daemon_and_override_rejected_without_mutation(self):
        with (
            patch("acceptance.os.name", "posix"),
            patch.dict("os.environ", {}, clear=True),
            patch.object(
                LocalDocker,
                "raw",
                return_value=b'[{"Endpoints":{"docker":{"Host":"ssh://remote"}}}]',
            ) as call,
        ):
            with self.assertRaisesRegex(ValueError, "local_unix_daemon_required"):
                LocalDocker()
            self.assertEqual(call.call_count, 1)
        with (
            patch("acceptance.os.name", "posix"),
            patch.dict("os.environ", {"DOCKER_HOST": "unix:///tmp/test.sock"}),
            patch.object(LocalDocker, "raw") as call,
        ):
            with self.assertRaisesRegex(ValueError, "docker_override_refused"):
                LocalDocker()
            call.assert_not_called()

    def test_daemon_endpoint_is_pinned(self):
        with (
            patch("acceptance.os.name", "posix"),
            patch.dict("os.environ", {}, clear=True),
            patch.object(
                LocalDocker,
                "raw",
                side_effect=[
                    b'[{"Endpoints":{"docker":{"Host":"unix:///var/run/docker.sock"}}}]',
                    b"linux/x86_64\n",
                    b"[]",
                ],
            ) as call,
        ):
            client = LocalDocker()
            client.call(["ps"])
            self.assertEqual(
                call.call_args.args[0], ["--host", "unix:///var/run/docker.sock", "ps"]
            )

    def test_no_delete_or_global_cleanup_command(self):
        client = object.__new__(LocalDocker)
        with patch.object(client, "call") as call:
            for verb in ("down", "rm", "kill", "prune"):
                with self.assertRaisesRegex(ValueError, "compose_action_refused"):
                    client.compose(Path("."), "synthetic-obs", verb)
            call.assert_not_called()

    def test_capacity_never_authorizes_reclamation(self):
        result = capacity_gate(100, 1000, 100000, 100000, 24)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["source_days_until_rejection"], 10)
        self.assertFalse(result["application_reclamation_authorized"])
        with self.assertRaises(ValueError):
            capacity_gate(True, 1, 1, 1, 1)

    def test_exact_five_volume_owner_and_mount_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            roles = ("vector", "loki", "grafana", "prometheus", "guard")
            volumes = []
            services = {}
            for role in roles:
                relative = "observability/data/" + role
                path = root / relative
                path.mkdir(parents=True)
                volumes.append(
                    {
                        "id": "obs-" + role + "-state",
                        "product": "observability",
                        "category": "observability_state",
                        "host_path": relative,
                        "container_path": "/var/lib/" + role,
                        "owner_service": "obs-" + role,
                        "backup_group": "obs-" + role,
                        "mount": True,
                        "kind": "directory",
                    }
                )
                services["obs-" + role] = {
                    "user": "10001:10001",
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "volumes": [
                        {
                            "type": "bind",
                            "source": str(path),
                            "target": "/var/lib/" + role,
                            "read_only": False,
                        }
                    ],
                }
            manifest = {"schema_version": "1.1.0", "volumes": volumes}
            stack = {"services": services}
            self.assertEqual(len(volume_layout(root, manifest, stack)), 5)
            bad = copy.deepcopy(stack)
            bad["services"]["obs-vector"]["volumes"][0]["source"] = str(root.parent)
            with self.assertRaisesRegex(ValueError, "mount_outside_scope"):
                volume_layout(root, manifest, bad)
            bad = copy.deepcopy(manifest)
            bad["volumes"][0]["owner_service"] = "obs-guard"
            with self.assertRaisesRegex(ValueError, "five_volume_owner_mismatch"):
                volume_layout(root, bad, stack)


if __name__ == "__main__":
    unittest.main()
