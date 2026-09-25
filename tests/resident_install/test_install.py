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

    def test_docker_occupancy_checks_stopped_and_other_project_bind(self):
        root = Path("/volume1/tianshu-v2-resident").resolve()
        platform_id = "a" * 64
        other_id = "b" * 64
        core = self.fixture.root / "core-compose.json"
        obs = self.fixture.root / "obs-compose.json"
        pinned = "example/platform@sha256:" + "c" * 64
        mount = {"type": "bind", "source": "/volume1/tianshu-v2-resident/data/platform",
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
                return json.dumps(visible).encode()
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
                "/volume1/tianshu-v2-resident/logs/platform:/host-logs:ro"
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
