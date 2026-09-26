"""Isolated real-product CLI checks; never starts Docker or touches the NAS."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FIXED_CONTRACTS = os.environ.get("TS_FIXED_CONTRACTS")
sys.path.insert(0, str(ROOT / "tests" / "deployment" / "packaging"))
sys.path.insert(0, str(ROOT))

import test_packaging as packaging  # noqa: E402
from bundle import initialize, verify_integrity  # noqa: E402
from manifest import Refused, digest, load_manifest, read_json, write_json  # noqa: E402
from ops.resident_install import install  # noqa: E402


class ResidentInstallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = packaging.PackagingTests()
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.fixture.configs["platform"]["web"]["username"] = "resident-operator"
        self.fixture.save_configs()
        self.fixture.manifest = load_manifest(
            packaging.PACKAGE / "release-manifest.example.json"
        )
        self.fixture.manifest["observability"]["source"]["commit"] = (
            install.FIXED_OBSERVABILITY
        )
        write_json(self.fixture.manifestpath, self.fixture.manifest)

    def tearDown(self):
        self.fixture.tearDown()

    def bundle(self):
        if not FIXED_CONTRACTS:
            self.skipTest("TS_FIXED_CONTRACTS original-byte contract directory required")
        initialize(
            self.fixture.manifestpath,
            self.fixture.sitepath,
            Path(FIXED_CONTRACTS),
            self.fixture.output,
            self.fixture.env,
        )
        return self.fixture.output

    def test_resident_gate_refuses_qa_inputs_before_writing_target(self):
        with self.assertRaisesRegex(Refused, "resident_lan_and_tls_inputs_required"):
            install.prepare(
                self.fixture.manifestpath, self.fixture.sitepath,
                ROOT / "contracts", self.fixture.root / "absent-credentials.json",
                self.fixture.root / "absent-password.txt", "resident-operator",
                self.fixture.output,
            )
        self.assertFalse(self.fixture.output.exists())

    def test_private_cli_wrapper_compiles(self):
        compile(install.LOCAL_CLI, "<fixed-product-local-cli>", "exec")

    def test_image_reference_normalizes_tag_but_keeps_registry_port(self):
        pinned = "registry.example:5000/tianshu/platform:fixed@sha256:" + "a" * 64
        self.assertEqual(
            install._repo_digest(pinned),
            "registry.example:5000/tianshu/platform@sha256:" + "a" * 64,
        )

    def test_failed_product_command_records_bounded_private_diagnostics(self):
        work = self.fixture.root / "private-command-evidence"
        work.mkdir()
        with self.assertRaisesRegex(Refused, "product_command_failed"):
            install._step(
                work, "memory_schema_2_resume",
                [sys.executable, "-c",
                 "import sys; sys.stdout.buffer.write(b'a'*70000); "
                 "sys.stderr.buffer.write(b'compose run rejected'); sys.exit(16)"],
                cwd=self.fixture.root,
            )
        result = read_json(work / "memory_schema_2_resume-result.json")
        self.assertEqual(result["state"], "command_failed")
        self.assertEqual(result["returncode"], 16)
        self.assertEqual(result["stdout_bytes"], 70000)
        self.assertEqual(result["stored_tail_bytes_limit"], 65536)
        self.assertEqual(len((work / "memory_schema_2_resume.stdout").read_bytes()), 65536)
        self.assertEqual((work / "memory_schema_2_resume.stderr").read_bytes(),
                         b"compose run rejected")

    def test_resume_activation_uses_supported_compose_run_without_pull_flag(self):
        captured = []

        def stop_after_schema2(_work, stage, command, **_kwargs):
            captured.append((stage, command))
            if stage == "memory_schema_2_resume":
                raise Refused("deliberate_stop")

        with (mock.patch.object(install, "_step", side_effect=stop_after_schema2),
              mock.patch.object(install, "_local_images_present")):
            with self.assertRaisesRegex(Refused, "deliberate_stop"):
                install._activation_steps(
                    self.fixture.root, self.fixture.root,
                    {"memory": "pinned"},
                    {install.CORE_PROJECT: self.fixture.root / "compose.yaml"},
                    None, None, None, None, resuming=True,
                )
        self.assertEqual([stage for stage, _ in captured],
                         ["compose_config_resume", "memory_schema_2_resume"])
        self.assertIn("run", captured[-1][1])
        self.assertNotIn("--pull", captured[-1][1])
        self.assertIn("--no-deps", captured[-1][1])

    def test_platform_preflight_exec_checks_running_container_first(self):
        events = []

        def capture_step(_work, stage, command, **_kwargs):
            events.append((stage, command))
            if stage == "platform_preflight":
                raise Refused("deliberate_stop")

        def capture_platform(*_args, **kwargs):
            events.append(("platform_identity", kwargs))
            return "a" * 64

        with (mock.patch.object(install, "_step", side_effect=capture_step),
              mock.patch.object(install, "_local_images_present"),
              mock.patch.object(install, "_platform_only", side_effect=capture_platform)):
            with self.assertRaisesRegex(Refused, "deliberate_stop"):
                install._activation_steps(
                    self.fixture.root, self.fixture.root,
                    {"memory": "pinned", "platform": "pinned"},
                    {install.CORE_PROJECT: self.fixture.root / "compose.yaml"},
                    None, None, None, None,
                )
        self.assertEqual(events[-2][0], "platform_identity")
        self.assertEqual(events[-1][0], "platform_preflight")
        self.assertEqual(events[-1][1][-10:], [
            "exec", "-T", "platform", "python", "-B", "-m",
            "services.platform", "--settings", "/etc/tianshu/settings.json",
            "preflight",
        ])

    def test_local_public_cli_exec_uses_stdin_on_same_platform(self):
        compose = self.fixture.root / "compose-local.json"
        write_json(compose, {"services": {"platform": {"image": "pinned"}}})
        base = ["docker", "compose", "-f", str(compose)]
        captured = []

        def capture_identity(*_args, **kwargs):
            captured.append(("identity", kwargs.get("expected_id")))
            return "a" * 64

        def capture_step(_work, stage, command, **kwargs):
            captured.append((stage, command, kwargs["input_bytes"]))
            return b'{"status":"ok"}'

        with (mock.patch.object(install, "_platform_only", side_effect=capture_identity),
              mock.patch.object(install, "_local_images_present"),
              mock.patch.object(install, "_step", side_effect=capture_step)):
            result = install._local(
                base, self.fixture.root, "issue", {"entry_id": "config-entry"},
                self.fixture.root, expected_id="a" * 64,
            )
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(captured[0], ("identity", "a" * 64))
        self.assertEqual(captured[1][1][-7:-3], ["exec", "-T", "platform", "python"])
        self.assertTrue(captured[1][2].endswith(b"\n"))

    def test_platform_tail_reuses_exact_container_and_new_preflight_marker(self):
        seen = []

        def capture_platform(*_args, **kwargs):
            seen.append(("identity", kwargs["expected_id"]))
            return "b" * 64

        def stop_at_preflight(_work, stage, command, **_kwargs):
            seen.append((stage, command))
            raise Refused("deliberate_stop")

        with (mock.patch.object(install, "_platform_only", side_effect=capture_platform),
              mock.patch.object(install, "_local_images_present"),
              mock.patch.object(install, "_step", side_effect=stop_at_preflight)):
            with self.assertRaisesRegex(Refused, "deliberate_stop"):
                install._platform_activation_tail(
                    self.fixture.root, self.fixture.root,
                    {"platform": "pinned"},
                    {install.CORE_PROJECT: self.fixture.root / "compose.yaml"},
                    None, None, None, None,
                    preflight_marker="platform_preflight_resume",
                    expected_id="b" * 64,
                )
        self.assertEqual(seen[0], ("identity", "b" * 64))
        self.assertEqual(seen[1][0], "platform_preflight_resume")
        self.assertEqual(seen[1][1][-10:-7], ["exec", "-T", "platform"])

    def test_docker_occupancy_checks_stopped_and_other_project_bind(self):
        root = (self.fixture.root / "tianshu-v2-resident").resolve()
        slash_root = "/" + root.as_posix().split(":/", 1)[-1].lstrip("/")
        platform_id = "a" * 64
        other_id = "b" * 64
        core = self.fixture.root / "core-compose.json"
        obs = self.fixture.root / "obs-compose.json"
        pinned = "example/platform@sha256:" + "c" * 64
        mount = {"type": "bind", "source": slash_root + "/data/platform",
                 "target": "/srv/tianshu", "read_only": False}
        write_json(core, {"services": {"platform": {"image": pinned,
                                                     "volumes": [mount]}}})
        write_json(obs, {})
        platform = {
            "Id": platform_id,
            "Config": {"Image": pinned, "Labels": {
                "com.docker.compose.project": install.CORE_PROJECT,
                "com.docker.compose.service": "platform",
                "com.docker.compose.project.working_dir": str(root),
                "com.docker.compose.project.config_files": str(core),
            }},
            "State": {"Running": True, "Status": "running"},
            "HostConfig": {"Privileged": False},
            "Mounts": [{"Type": "bind", "Source": mount["source"],
                        "Destination": mount["target"], "RW": True}],
        }
        unrelated = {
            "Id": other_id, "Config": {"Labels": {
                "com.docker.compose.project": "unrelated"}},
            "State": {"Running": False, "Status": "exited"},
            "HostConfig": {}, "Mounts": [],
        }
        visible = [platform, unrelated]

        def fake_run(command, **_kwargs):
            if command[:2] == ["docker", "ps"]:
                return ("".join(item["Id"] + "\n" for item in visible)).encode()
            if command[:2] == ["docker", "inspect"]:
                self.assertEqual(command[2], "--format")
                self.assertNotIn("Env", command[3])
                return ("\n".join(json.dumps(item) for item in visible) + "\n").encode()
            raise AssertionError(command)

        with mock.patch.object(install, "_run", side_effect=fake_run):
            self.assertEqual(
                install._platform_only(root, {
                    install.CORE_PROJECT: core, install.OBS_PROJECT: obs,
                }, expected_id=platform_id), platform_id,
            )
            visible[:] = [unrelated]
            unrelated["Config"]["Labels"] = None
            install._projects_empty(root)
            unrelated["HostConfig"]["Binds"] = [
                slash_root + "/logs/platform:/host-logs:ro"
            ]
            with self.assertRaisesRegex(Refused, "resident_deployment_root_occupied"):
                install._projects_empty(root)
            visible[:] = [platform, unrelated]
            with self.assertRaisesRegex(Refused, "resident_start_stage_changed"):
                install._platform_only(root, {
                    install.CORE_PROJECT: core, install.OBS_PROJECT: obs,
                }, expected_id=platform_id)
            visible[:] = [platform]
            with self.assertRaisesRegex(Refused, "resident_deployment_root_occupied"):
                install._projects_empty(root)
            platform["Mounts"][0]["RW"] = False
            with self.assertRaisesRegex(Refused, "platform_container_identity_changed"):
                install._platform_only(root, {
                    install.CORE_PROJECT: core, install.OBS_PROJECT: obs,
                }, expected_id=platform_id)
            platform["Mounts"][0]["RW"] = True
            platform["State"] = {"Running": False, "Status": "exited"}
            with self.assertRaisesRegex(Refused, "platform_container_identity_changed"):
                install._platform_only(root, {
                    install.CORE_PROJECT: core, install.OBS_PROJECT: obs,
                }, expected_id=platform_id)

    def test_absent_resident_root_inventory_uses_existing_parent_for_both_commands(self):
        root = self.fixture.root / "fresh-resident-root"
        self.assertFalse(root.exists())
        with self.assertRaisesRegex(Refused, "product_command_unavailable_or_timeout"):
            install._run([sys.executable, "-c", "print('ok')"], cwd=root)
        self.assertEqual(
            install._run([sys.executable, "-c", "print('ok')"], cwd=root.parent).strip(),
            b"ok",
        )
        container_id = "a" * 64
        inspected = {"Id": container_id, "Config": {"Image": "fixed", "Labels": {
            "com.docker.compose.project": install.CORE_PROJECT}},
            "State": {"Running": False, "Status": "exited"},
            "HostConfig": {"Binds": None, "Mounts": None, "Privileged": False},
            "Mounts": []}
        calls = []

        def fake_run(command, *, cwd, **_kwargs):
            if not Path(cwd).is_dir():
                raise FileNotFoundError("nonexistent inventory cwd")
            calls.append((command, cwd))
            if command[:2] == ["docker", "ps"]:
                return (container_id + "\n").encode()
            if command[:3] == ["docker", "inspect", "--format"]:
                self.assertNotIn("Env", command[3])
                return (json.dumps(inspected) + "\n").encode()
            raise AssertionError(command)

        with mock.patch.object(install, "_run", side_effect=fake_run):
            self.assertEqual(install._docker_occupants(root), [inspected])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(cwd == root.parent for _, cwd in calls))

    def test_export_lock_refuses_unpinned_image_before_docker(self):
        root = self.bundle()
        manifest = read_json(root / "release-manifest.json")
        projects = (install.CORE_PROJECT, install.OBS_PROJECT)
        core = read_json(root / "compose.json")
        core["name"] = install.CORE_PROJECT
        obs = {
            "name": install.OBS_PROJECT,
            "services": {
                p: {"image": "example/" + p + "@sha256:" + "a" * 64}
                for p in (
                    "obs-vector", "obs-loki", "obs-grafana",
                    "obs-prometheus", "obs-guard",
                )
            },
        }
        for project, value in zip(projects, (core, obs)):
            directory = self.fixture.root / project
            directory.mkdir()
            write_json(directory / "compose.yaml", value)
        protected = {
            name: value
            for name, value in read_json(root / "bundle-integrity.json")["files"].items()
            if name.startswith(
                ("config/", "private/", "observability/config/",
                 "observability-input/")
            )
        }
        lock = {
            "schema_version": "nas-a3-r1-resident-export/1",
            "status": "resident_candidate",
            "acceptance": "pending_live_acceptance",
            "release_ready": False,
            "projects": list(projects),
            "config_and_private_sha256": protected,
            "compose_sha256": {
                p: digest((self.fixture.root / p / "compose.yaml").read_bytes())
                for p in projects
            },
            "manifest_sha256": digest((root / "release-manifest.json").read_bytes()),
            "bundle_integrity_sha256": digest(
                (root / "bundle-integrity.json").read_bytes()
            ),
            "deployment_root": str(root),
            "product_sources": {
                p: manifest["products"][p]["source"] for p in install.PRODUCTS
            },
            "observability_source": {"commit": install.FIXED_OBSERVABILITY},
            "images": {
                p: "example/" + p + "@sha256:" + "a" * 64
                for p in (
                    *install.PRODUCTS, "obs-vector", "obs-loki", "obs-grafana",
                    "obs-prometheus", "obs-guard",
                )
            },
        }
        path = self.fixture.root / "export-lock.json"
        write_json(path, lock)
        with self.assertRaisesRegex(Refused, "resident_image_digest_required"):
            install._export_lock(root, path, manifest, read_json(root / "compose.json"))

    def test_real_memory_migrations_and_platform_issue_from_empty_databases(self):
        root = self.bundle()
        self.assertFalse((root / "data/memory/memory.sqlite").exists())
        self.assertFalse((root / "data/platform/platform.sqlite").exists())
        platform = read_json(root / "config/platform/settings.json")
        self.assertEqual(platform["web"]["username"], "resident-operator")
        self.assertTrue(platform["web"]["password_hash"].startswith("scrypt-v1$"))
        from services.platform.web_console import password_hash

        _, salt, _ = platform["web"]["password_hash"].split("$")
        self.assertEqual(
            password_hash(
                self.fixture.env["TS_ADMIN_PASSWORD"], bytes.fromhex(salt)
            ),
            platform["web"]["password_hash"],
        )
        self.assertEqual(platform["providers"], {})
        self.assertFalse(platform["web"]["dialogue_enabled"])
        self.assertIsNone(install._provider(root, None))
        with self.assertRaisesRegex(Refused, "first_install_mutable_layout_changed"):
            install._clean_install(root)

        memory = read_json(root / "config/memory/settings.json")
        memory["database_path"] = str(root / "data/memory/memory.sqlite")
        memory["contract_directory"] = str(Path(FIXED_CONTRACTS) / "text-dialogue/v1")
        memory["source_sync"]["recovery_path"] = str(
            root / "data/memory/memory.sqlite.source-guard.json"
        )
        memory_path = self.fixture.root / "local-memory.json"
        write_json(memory_path, memory)
        for operation, backup in (
            ("migrate-profiles", "before-profiles.sqlite"),
            ("migrate-sources", "before-sources.sqlite"),
        ):
            result = subprocess.run(
                [sys.executable, "-B", "-m", "tianshu_memory.cli", "--config",
                 str(memory_path), operation, "--backup",
                 str(root / "data/memory" / backup)],
                capture_output=True, timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stderr[:300])
        self.assertTrue((root / "data/memory/memory.sqlite").is_file())

        platform["database_path"] = str(root / "data/platform/platform.sqlite")
        platform["contract_directory"] = str(
            Path(FIXED_CONTRACTS) / "text-dialogue/v1"
        )
        platform["diagnostics"]["contract_directory"] = str(
            Path(FIXED_CONTRACTS) / "diagnostics/v1"
        )
        platform["diagnostics"]["log_directory"] = str(root / "logs/platform")
        platform["web"]["static_directory"] = str(root / "local-web")
        (root / "local-web/assets").mkdir(parents=True)
        (root / "local-web/index.html").write_text(
            '<!doctype html><script src="/assets/app.js"></script>'
        )
        (root / "local-web/assets/app.js").write_text("/* isolated test */")
        platform["core"]["ca_file"] = str(root / "config/platform/tls/ca.pem")
        platform["tls"]["certificate_file"] = str(
            root / "config/platform/tls/server.pem"
        )
        platform["tls"]["private_key_file"] = str(
            root / "config/platform/tls/server.key"
        )
        platform_path = self.fixture.root / "local-platform.json"
        write_json(platform_path, platform)
        environment = dict(os.environ)
        for line in (root / "private/platform.env").read_text().splitlines():
            name, value = line.split("=", 1)
            environment[name] = value[1:-1]
        issue_path = self.fixture.root / "issue.json"
        write_json(issue_path, {"entry_id": "config-entry"})
        result = subprocess.run(
            [sys.executable, "-B", "-m", "services.platform", "--settings",
             str(platform_path), "local", "--credential-env", "TS_ADMIN_TOKEN",
             "issue", "--input", str(issue_path)],
            capture_output=True, timeout=45, env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr[:300])
        receipt = json.loads(result.stdout)
        self.assertTrue((root / "data/platform/platform.sqlite").is_file())
        origin = install._gateway_origin_precheck(
            root, read_json(root / "config/gateway/settings.json")
        )
        expiry = install._install_ref(root, receipt, origin)
        self.assertEqual(expiry, receipt["expires_at"])
        verify_integrity(root)
        self.assertIn(
            receipt["assertion_ref"],
            (root / "private/gateway.env").read_text(),
        )
        current, _ = install._current_ref(
            root, digest(receipt["assertion_ref"].encode())
        )
        self.assertEqual(current, receipt["assertion_ref"])
        with self.assertRaisesRegex(Refused, "nonempty_first_install_mutable_state_refused"):
            install._clean_install(root)
        with self.assertRaisesRegex(Refused, "gateway_origin_already_issued"):
            install._gateway_origin_precheck(
                root, read_json(root / "config/gateway/settings.json")
            )


if __name__ == "__main__":
    unittest.main()
