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
        # This verifies command wiring and fail-closed control flow only, not any image.
        for index, (fail_issue, dialogue) in enumerate(
            ((False, False), (True, False), (False, True))
        ):
            if index:
                self.tearDown()
                self.setUp()
            active = {}
            calls = []
            report = {"results": []}
            path = self.root / "reports/fake-docker.json"
            composition = read_json(self.root / "compose.json")
            tags = {s["image"]: p for p, s in composition["services"].items()}
            ids = {p: str(i + 1) * 64 for i, p in enumerate(tags.values())}

            def invoke(argv, **kwargs):
                calls.append(argv)
                raw = b""
                if argv[:3] == ["docker", "ps", "-aq"]:
                    raw = "\n".join(active).encode()
                elif argv[:2] == ["docker", "inspect"]:
                    values = []
                    for cid in argv[2:]:
                        owner, running = active[cid]
                        values.append(
                            dict(
                                Id=cid,
                                Image="sha256:" + ids[owner],
                                Config=dict(
                                    Labels={
                                        "com.docker.compose.project": "tianshu-qa-packaging",
                                        "com.docker.compose.project.working_dir": str(
                                            self.root
                                        ),
                                        "com.docker.compose.service": owner,
                                    }
                                ),
                                State=dict(Running=running, ExitCode=0),
                            )
                        )
                    raw = json.dumps(values).encode()
                elif argv[:3] == ["docker", "image", "inspect"]:
                    raw = json.dumps(
                        [
                            dict(
                                Id="sha256:" + ids[tags[argv[-1]]],
                                Os="linux",
                                Architecture="amd64",
                                RepoDigests=[],
                                Config=dict(User="10001:10001"),
                            )
                        ]
                    ).encode()
                elif argv[:2] == ["docker", "exec"]:
                    raw = b"[10001,10001]"
                elif argv[:2] == ["docker", "kill"]:
                    owner, _ = active[argv[-1]]
                    active[argv[-1]] = (owner, False)
                elif "up" in argv:
                    active.update({cid: (owner, True) for owner, cid in ids.items()})
                elif argv[-1] == "issue":
                    if fail_issue:
                        return subprocess.CompletedProcess(argv, 1, b"", b"")
                    raw = json.dumps(self.receipt).encode()
                elif "distributions" in " ".join(argv):
                    raw = b'{"interpreter":"/synthetic/python","distributions":[["synthetic","1"]]}'
                return subprocess.CompletedProcess(argv, 0, raw, b"")

            with (
                patch("linux_runtime.preflight"),
                patch("linux_runtime.subprocess.run", side_effect=invoke),
            ):
                if fail_issue:
                    with self.assertRaisesRegex(
                        Refused, "runtime_step_failed_platform_issue"
                    ):
                        leased_execute(
                            self.root, None, [], report, path, dialogue=dialogue
                        )
                else:
                    leased_execute(self.root, None, [], report, path, dialogue=dialogue)
            self.assertTrue(report["stop_confirmed"])
            self.assertEqual(sum(argv[-1] == "issue" for argv in calls), 1)
            self.assertFalse(
                any("down" in argv or "--signal=SIGKILL" in argv for argv in calls)
            )
            if fail_issue:
                self.assertFalse(any("up" in argv for argv in calls))
            else:
                runtime = read_json(self.root / "reports/runtime-identity.json")
                self.assertEqual(runtime["state"], "stopped")
                self.assertTrue(
                    all(runtime["services"][p]["status"] == "observed" for p in ids)
                )

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


if __name__ == "__main__":
    unittest.main()
