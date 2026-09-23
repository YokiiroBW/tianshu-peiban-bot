"""Synthetic boundary tests. Fake Docker orchestration is never Linux execution evidence."""

import copy
import json
import os
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_packaging as packaging
from bundle import verify_integrity, preflight
from linux_bootstrap import first_install_container, prepare, update
from linux_runtime import (
    Runner,
    image_fact,
    local_daemon,
    owned_container,
    leased_execute,
)
from manifest import Refused, digest, read_json, write_json
from runtime_identity import identity, validate

PACKAGE = packaging.PACKAGE


class LinuxBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.fixture = packaging.PackagingTests()
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.fixture.init()
        self.root = self.fixture.output
        (self.root / "reports").mkdir()
        self.receipt = dict(
            assertion_ref="origin:" + "a" * 32,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=299)
            ).isoformat(),
        )

    def tearDown(self):
        self.fixture.tearDown()

    def test_plan_identity_has_nine_unknown_owners_and_thirteen_mounts(self):
        value = validate(identity(self.root))
        self.assertEqual(len(value["services"]), 9)
        self.assertEqual(len(value["mounts"]), 13)
        self.assertIsNone(value["authority"])
        self.assertIsNone(value["recovery_inventory"])
        self.assertFalse(value["scope"]["nas_acceptance"])
        self.assertEqual(value["lease"]["path"], str(self.root / ".runtime-owner.lock"))
        self.assertTrue(all(v["image_id"] is None for v in value["services"].values()))

    def test_identity_rejects_wrong_project_and_canonical_hash(self):
        for field in ("name", "directory", "compose_canonical_sha256"):
            value = identity(self.root)
            value["projects"]["core"][field] = "a" * 64
            with self.assertRaises(Refused):
                validate(value)

    def test_identity_missing_authority_field_is_invalid(self):
        value = identity(self.root)
        del value["authority"]
        with self.assertRaises(Refused):
            validate(value)

    def test_only_inspected_image_does_not_claim_container_observed(self):
        value = validate(
            identity(
                self.root,
                observed={
                    "obs-loki": dict(
                        image_id="sha256:" + "b" * 64,
                        repo_digests=[],
                        platform="linux/amd64",
                    )
                },
            )
        )
        self.assertEqual(value["services"]["obs-loki"]["status"], "not_observed")
        self.assertIsNone(value["services"]["obs-loki"]["uid"])

    def test_liveness_removes_placeholder_without_enabling_model_or_web(self):
        publication, password = prepare(self.root, False)
        self.assertIsNone(publication)
        self.assertGreater(len(password), 12)
        self.assertNotIn(
            "TS_GATEWAY_ORIGIN=", (self.root / "private/gateway.env").read_text()
        )
        config = read_json(self.root / "config/platform/settings.json")
        self.assertEqual(config["providers"], {})
        self.assertIs(config["web"]["dialogue_enabled"], False)
        verify_integrity(self.root)

    def test_explicit_real_provider_keeps_secret_out_of_publication_and_config(self):
        provider = dict(
            base_url="https://provider.example/v1",
            model_id="test-text",
            addresses=["8.8.8.8"],
            secret="test-secret-only",
            text_probe_passed=True,
        )
        publication, _ = prepare(self.root, False, real_provider=provider)
        model = publication["providers"][0]
        self.assertEqual(model["base_url"], provider["base_url"])
        self.assertEqual(model["verified_capabilities"], ["text"])
        self.assertNotIn(provider["secret"], json.dumps(publication))
        self.assertNotIn(
            provider["secret"],
            (self.root / "config/platform/settings.json").read_text(),
        )
        self.assertIn(
            "TS_ACCEPTANCE_MODEL='test-secret-only'",
            (self.root / "private/gateway.env").read_text(),
        )
        verify_integrity(self.root)

    def test_real_provider_rejects_private_targets_and_secret_interpolation_before_mutation(
        self,
    ):
        original = (self.root / "bundle-integrity.json").read_bytes()
        baseline = dict(
            base_url="https://provider.example/v1",
            model_id="test-text",
            addresses=["8.8.8.8"],
            secret="test-secret-only",
            text_probe_passed=True,
        )
        for changes in (
            {"addresses": ["127.0.0.1"]},
            {"secret": "bad'\\nKEY=x"},
            {"secret": "${TOKEN}"},
            {"text_probe_passed": False},
            {"base_url": "http://provider.example/v1"},
        ):
            with self.subTest(changes=changes), self.assertRaises(Refused):
                prepare(self.root, False, real_provider={**baseline, **changes})
        with self.assertRaises(Refused):
            prepare(self.root, True, real_provider=baseline)
        self.assertEqual(original, (self.root / "bundle-integrity.json").read_bytes())
        verify_integrity(self.root)

    def test_synthetic_dialogue_only_modifies_private_candidate(self):
        original = (PACKAGE / "release-manifest.example.json").read_bytes()
        publication, _ = prepare(self.root, True)
        self.assertEqual(
            publication["providers"][0]["provider_id"], "provider-synthetic"
        )
        self.assertEqual(
            publication["providers"][0]["base_url"], "https://gateway.internal:9443/v1"
        )
        manifest = read_json(self.root / "release-manifest.json")
        self.assertEqual(manifest["status"], "candidate")
        self.assertEqual(
            [v["id"] for v in manifest["features"] if v["enabled"]],
            ["web_text_dialogue"],
        )
        self.assertEqual(
            original, (PACKAGE / "release-manifest.example.json").read_bytes()
        )
        verify_integrity(self.root)

    def test_private_origin_from_real_receipt_is_only_added_to_gateway(self):
        prepare(self.root, False)
        calls = []

        def run(name, argv, seconds, **kwargs):
            calls.append((name, argv, kwargs))
            return json.dumps(self.receipt).encode()

        first_install_container(self.root, ["docker", "compose"], run, None)
        self.assertEqual([c[0] for c in calls], ["platform_issue"])
        self.assertNotIn(self.receipt["assertion_ref"], json.dumps(calls[0][1]))
        self.assertIn(
            self.receipt["assertion_ref"],
            (self.root / "private/gateway.env").read_text(),
        )
        self.assertNotIn(
            "TS_ADMIN_TOKEN", (self.root / "private/gateway.env").read_text()
        )
        for f in (self.root / "reports").rglob("*.json"):
            self.assertNotIn(self.receipt["assertion_ref"], f.read_text())
        verify_integrity(self.root)

    def test_unknown_cli_result_is_never_replayed(self):
        prepare(self.root, False)
        with patch("linux_bootstrap.json.loads", side_effect=ValueError):
            with self.assertRaises(ValueError):
                first_install_container(self.root, [], lambda *a, **k: b"?", None)
        with self.assertRaisesRegex(Refused, "bootstrap_already_attempted"):
            first_install_container(
                self.root, [], lambda *a, **k: self.fail("replay"), None
            )

    def test_expired_receipt_cannot_be_injected(self):
        prepare(self.root, False)
        self.receipt["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        with self.assertRaisesRegex(Refused, "assertion_expired"):
            first_install_container(
                self.root, [], lambda *a, **k: json.dumps(self.receipt), None
            )
        self.assertNotIn("origin:", (self.root / "private/gateway.env").read_text())

    def test_publish_failure_does_not_issue(self):
        publication, _ = prepare(self.root, True)
        calls = []

        def fail(name, *a, **k):
            calls.append(name)
            raise Refused("synthetic_failure")

        with self.assertRaises(Refused):
            first_install_container(self.root, [], fail, publication)
        self.assertEqual(calls, ["platform_publish"])

    def test_unlisted_update_refused_without_writes(self):
        with self.assertRaisesRegex(Refused, "out_of_scope"):
            update(self.root, {"compose.json": b"{}"})
        verify_integrity(self.root)

    def test_failed_update_keeps_incomplete_marker(self):
        with patch("linux_bootstrap.write_json", side_effect=OSError):
            with self.assertRaises(OSError):
                update(self.root, {"private/gateway.env": b"changed"})
        with self.assertRaisesRegex(Refused, "initialization_incomplete"):
            verify_integrity(self.root)

    def test_remote_docker_context_rejected_before_any_command(self):
        for key in (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        ):
            with (
                patch("linux_runtime.sys.platform", "linux"),
                patch("linux_runtime.shutil.which", return_value="docker"),
                patch.dict(os.environ, {key: "synthetic-remote"}, clear=True),
                patch("linux_runtime.subprocess.run") as run,
            ):
                with self.assertRaisesRegex(Refused, "custom_docker_endpoint"):
                    local_daemon()
                run.assert_not_called()

    def test_selected_remote_context_rejected(self):
        with (
            patch("linux_runtime.sys.platform", "linux"),
            patch("linux_runtime.shutil.which", return_value="docker"),
            patch.dict(os.environ, {}, clear=True),
            patch(
                "linux_runtime.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, b"ssh://remote", b""),
            ),
        ):
            with self.assertRaisesRegex(Refused, "local_docker_endpoint"):
                local_daemon()

    def test_image_id_is_not_registry_digest_and_wrong_arch_refused(self):
        value = dict(
            Id="sha256:" + "a" * 64, Os="linux", Architecture="amd64", RepoDigests=[]
        )
        self.assertEqual(image_fact(value)["repo_digests"], [])
        for key, bad in [
            ("Os", "windows"),
            ("Architecture", "arm64"),
            ("Id", "unknown"),
            ("RepoDigests", ["sha256:" + "a" * 64]),
        ]:
            with self.assertRaises(Refused):
                image_fact({**value, key: bad})

    def test_foreign_container_and_same_project_other_directory_refused(self):
        labels = {
            "com.docker.compose.project": "tianshu-qa-packaging",
            "com.docker.compose.project.working_dir": str(self.root),
            "com.docker.compose.service": "platform",
        }
        value = dict(Id="a" * 64, Config=dict(Labels=labels))
        self.assertEqual(
            owned_container(value, "tianshu-qa-packaging", self.root, ["platform"]),
            "platform",
        )
        for key in labels:
            bad = copy.deepcopy(value)
            bad["Config"]["Labels"][key] = "foreign"
            with self.assertRaises(Refused):
                owned_container(bad, "tianshu-qa-packaging", self.root, ["platform"])

    def test_runner_timeout_saves_static_evidence_without_output_or_input(self):
        report = {"results": []}
        path = self.root / "reports/runner.json"
        with patch(
            "linux_runtime.subprocess.run",
            side_effect=subprocess.TimeoutExpired("SECRET_CANARY", 1),
        ):
            with self.assertRaises(Refused):
                Runner(report, path)(
                    "test", ["secret-argv"], 1, input=b"SECRET_CANARY", capture=True
                )
        self.assertEqual(report["results"][0]["status"], "timeout")
        self.assertNotIn("SECRET", path.read_text())

    def test_complete_docker_wiring_fake_and_failed_issue_stop_without_retry(self):
        from fake_linux_docker import Docker, clock_patches

        for index, (fail_issue, dialogue) in enumerate(
            ((False, False), (True, False), (False, True))
        ):
            if index:
                self.tearDown()
                self.setUp()
            fake = Docker(self.root, self.receipt, fail_issue=fail_issue)
            report = {"results": []}
            with (
                patch("linux_runtime.preflight"),
                patch("linux_runtime.subprocess.run", side_effect=fake),
                clock_patches(),
            ):
                if fail_issue:
                    with self.assertRaisesRegex(
                        Refused, "runtime_step_failed_platform_issue"
                    ):
                        leased_execute(
                            self.root,
                            None,
                            [],
                            report,
                            self.root / "reports/fake-docker.json",
                            dialogue=dialogue,
                        )
                else:
                    leased_execute(
                        self.root,
                        None,
                        [],
                        report,
                        self.root / "reports/fake-docker.json",
                        dialogue=dialogue,
                    )
            self.assertEqual(report["stop_confirmed"], not fail_issue)
            self.assertFalse(
                any(
                    "down" in argv or "--signal=SIGKILL" in argv or "--force" in argv
                    for argv in fake.calls
                )
            )
            if not fail_issue:
                self.assertEqual(len(fake.active), 4)
                self.assertTrue(
                    all(v["State"]["Status"] == "exited" for v in fake.active.values())
                )
                self.assertEqual(len(report["completed_oneoffs"]), 8 if dialogue else 7)

    def test_core_only_cannot_weaken_release_preflight(self):
        with self.assertRaisesRegex(Refused, "release_requires_all_owners"):
            preflight(self.root, release=True, runtime=True, core_only=True)

    def test_rehashed_old_executor_cannot_run_with_new_code(self):
        from linux_validate import plan

        tool = self.root / "tools/linux_runtime.py"
        tool.write_bytes(tool.read_bytes() + b"\n# different executor\n")
        index = read_json(self.root / "bundle-integrity.json")
        index["files"]["tools/linux_runtime.py"] = digest(tool.read_bytes())
        write_json(self.root / "bundle-integrity.json", index)
        with self.assertRaisesRegex(Refused, "executor_bundle_version_mismatch"):
            plan(self.root, self.fixture.root / "unused-contexts")

    def test_synthetic_init_logs_tls_has_loopback_san(self):
        from synthetic_init import create
        from cryptography import x509
        import ipaddress

        scope = self.fixture.root / "scope"
        root = create(
            scope,
            self.fixture.manifestpath,
            self.fixture.contracts,
            "tianshu-qa-generated",
            "172.30.90.0/24",
            20443,
        )
        self.assertEqual(root, scope / "deployments/source")
        cert = x509.load_pem_x509_certificate(
            (scope / "inputs/tls/logs/server.pem").read_bytes()
        )
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn(
            ipaddress.ip_address("127.0.0.1"), san.get_values_for_type(x509.IPAddress)
        )
        self.assertIn("logs.internal", san.get_values_for_type(x509.DNSName))

    def test_joint_recovery_scope_is_new_and_preserves_bundle_identity(self):
        from synthetic_init import create
        import uuid

        scope = self.fixture.root / "joint-scope"
        scope_id = str(uuid.uuid4())
        args = (
            scope,
            self.fixture.manifestpath,
            self.fixture.contracts,
            "tianshu-qa-joint",
            "172.30.91.0/24",
            20443,
        )
        root = create(*args, recovery_scope_id=scope_id)
        from ops.recovery.engine import Recovery

        recovery = Recovery(scope, scope_id)
        self.assertEqual(recovery.root, scope)
        self.assertEqual(root, scope / "deployments/source")
        verify_integrity(root)
        self.assertTrue((scope / "backups").is_dir())
        self.assertFalse((root / ".deployment.json").exists())
        with self.assertRaises(Refused):
            create(*args, recovery_scope_id=scope_id)

    def test_lan_qa_tls_and_port_are_bound_to_explicit_address(self):
        from synthetic_init import create
        from cryptography import x509
        import ipaddress

        profile = {
            "kind": "nas-cpuset-lan-qa-v1",
            "cpus": [6, 7],
            "pid_limit": "unsupported",
        }
        scope = self.fixture.root / "lan-scope"
        root = create(
            scope,
            self.fixture.manifestpath,
            self.fixture.contracts,
            "tianshu-qa-lan-test",
            "172.30.91.0/24",
            20443,
            resource_profile=profile,
            lan_address="192.168.31.210",
        )
        cert = x509.load_pem_x509_certificate(
            (root / "config/platform/tls/server.pem").read_bytes()
        )
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn(
            ipaddress.ip_address("192.168.31.210"),
            san.get_values_for_type(x509.IPAddress),
        )
        self.assertEqual(
            read_json(root / "compose.json")["services"]["platform"]["ports"],
            ["192.168.31.210:20443:8443"],
        )
        self.assertEqual(preflight(root, core_only=True)["status"], "package_valid")
        with self.assertRaisesRegex(Refused, "nas_qa_profile_not_release_approved"):
            preflight(root, release=True)

    def test_browser_window_closes_without_claim_and_removes_private_login(self):
        import browser_lease

        metadata = read_json(self.root / "deployment.json")
        metadata["compose_inputs"]["resource_profile"] = {
            "kind": "nas-cpuset-lan-qa-v1"
        }
        write_json(self.root / "deployment.json", metadata)

        def finish(_):
            self.assertEqual(
                read_json(self.root / "reports/browser-private/login.json")["password"],
                "private-test",
            )
            write_json(self.root / "reports/browser-finish.json", {"finished": True})

        with patch.object(browser_lease.time, "sleep", finish), patch("builtins.print"):
            self.assertEqual(
                browser_lease.wait(self.root, "private-test", 10), "closed_by_operator"
            )
        self.assertFalse((self.root / "reports/browser-private/login.json").exists())


if __name__ == "__main__":
    unittest.main()
