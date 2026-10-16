"""Local fixed-input/rendering tests; no network, Docker or production files."""

import ast
import copy
import json
import re
import tarfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_package as fixtures
from deploy.infrastructure import bundle, image, resident


class ProductionPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base, self.repo = self.fixture.base, self.fixture.repo

    def platform(self):
        for name in image.DEPENDENCY_FILES:
            self.fixture.put(name, "synthetic locked input\n")
        self.fixture.put("Dockerfile", "FROM node AS console\nRUN npm run build\nFROM python AS music-tools\nFROM python AS runtime\nUSER 10001:10001\nENTRYPOINT [\"python\",\"-I\",\"-m\",\"services.platform\"]\n")
        for name in ("services/platform/__init__.py", "apps/web/main.js", "tools/music/main.go"):
            self.fixture.put(name, "synthetic committed source\n")
        self.fixture.put("tools/music/run.sh", "#!/bin/sh\nexit 0\n")
        self.fixture.git("add", "--all")
        self.fixture.git("update-index", "--chmod=+x", "tools/music/run.sh")
        self.fixture.git("commit", "--quiet", "-m", "music baseline")
        music = self.fixture.git("rev-parse", "HEAD").decode().strip()
        self.fixture.put("services/platform/infrastructure/__init__.py", "synthetic new infrastructure source\n")
        return music, self.fixture.save("merged platform")

    def test_image_preparation_uses_fixed_objects_preserves_recipe_modes_and_excludes_private_files(self):
        node_zip, info = self.fixture.build()
        music, platform = self.platform()
        self.fixture.put("services/platform/__init__.py", "UNCOMMITTED_CONTENT")
        self.fixture.put("settings.json", "SYNTHETIC_PRIVATE_CONTENT")
        arguments = dict(platform_repo=self.repo, platform_commit=platform, music_commit=music,
                         music_image="sha256:" + "a" * 64, contracts_repo=self.repo,
                         contracts_commit=self.fixture.commit, node_package=node_zip,
                         stage="/volume2/tianshu-v2-resident-updates/synthetic-build", docker="/usr/bin/docker", remote_python="/usr/bin/python3", tag="synthetic/platform:test")
        with patch.object(image, "NODE_COMMIT", self.fixture.commit), patch.object(image, "NODE_SHA256", info["package_sha256"]):
            first = image.prepare(**arguments, output=self.base / "first")
            second = image.prepare(**arguments, output=self.base / "second")
        self.assertEqual(first["archive_sha256"], second["archive_sha256"])
        for action in ("stage", "launch", "poll"):
            compile((self.base / "first" / ("remote-build-" + action + ".py")).read_text(encoding="utf-8"), "build-" + action, "exec")
        with tarfile.open(self.base / "first/build-context.tar.gz", "r:gz") as archive:
            self.assertEqual(archive.extractfile("services/platform/__init__.py").read(), b"synthetic committed source\n")
            self.assertNotIn("settings.json", archive.getnames())
            self.assertEqual(archive.getmember("tools/music/run.sh").mode, 0o755)
            recipe = archive.extractfile("Dockerfile.candidate").read().decode()
            self.assertIn("FROM python AS music-tools", recipe)
            self.assertIn("--no-install-recommends openssh-client", recipe)
            self.assertIn("/opt/tianshu-infrastructure-venv", recipe)
            self.assertNotIn("ENV PATH", recipe)
            self.assertIn('ENTRYPOINT ["python","-I","-m","services.platform"]', recipe)

    def test_image_preparation_refuses_music_dependency_drift(self):
        music, _ = self.platform()
        self.fixture.put("scripts/build/runtime.lock", "changed dependency\n")
        changed = self.fixture.save("unexpected lock change")
        with self.assertRaisesRegex(bundle.Refused, "music_dependency_input_changed"):
            image.prepare(platform_repo=self.repo, platform_commit=changed, music_commit=music,
                          music_image="sha256:" + "a" * 64, contracts_repo=self.repo,
                          contracts_commit=self.fixture.commit, node_package=self.base / "not-needed.zip",
                          stage="/volume2/tianshu-v2-resident-updates/synthetic-build", docker="/usr/bin/docker", remote_python="/usr/bin/python3", tag="synthetic/platform:test", output=self.base / "bad")
        self.assertFalse((self.base / "bad").exists())

    def test_template_patch_preserves_music_all_other_services_limits_and_existing_tmpfs(self):
        service = {"image": "old", "mem_limit": "512m", "environment": {"MUSIC_TOOL": "unchanged"},
                   "volumes": [{"type": "bind", "source": "/music", "target": "/media/Music", "read_only": False}],
                   "tmpfs": ["/tmp:rw,size=16m"]}
        templates = {"core": {"services": {"platform": copy.deepcopy(service), "memory": {"image": "keep"}}},
                     "platform": {"services": {"platform": copy.deepcopy(service)}}, "obs": {"services": {"observer": {"image": "keep"}}}}
        cap = {"images": {"platform": "old", "memory": "keep"}, "binds": {"platform": [{"source": "/music", "target": "/media/Music", "read_only": False}]}, "external_media_binds": [{"source": "/music", "target": "/media/Music", "read_only": False}], "min_free_bytes": 20 * 1024**3}
        credential_binds = [{"source": "/resident/identity", "target": "/run/identity", "read_only": True}, {"source": "/resident/known_hosts", "target": "/run/known_hosts", "read_only": True}]
        before = copy.deepcopy((templates, cap))
        updated, capacity = resident.patch_templates(templates, cap, "new", credential_binds)
        self.assertEqual((templates, cap), before)
        self.assertEqual(updated["obs"], templates["obs"])
        self.assertEqual(updated["core"]["services"]["memory"], {"image": "keep"})
        target = updated["core"]["services"]["platform"]
        self.assertEqual(target["mem_limit"], "512m")
        self.assertEqual(target["environment"], service["environment"])
        self.assertEqual(target["volumes"][0], service["volumes"][0])
        self.assertEqual(target["tmpfs"][0], service["tmpfs"][0])
        self.assertIn("size=128m,uid=10001,gid=10001,mode=0700", target["tmpfs"][1])
        self.assertEqual(capacity["external_media_binds"], cap["external_media_binds"])
        self.assertEqual(capacity["min_free_bytes"], cap["min_free_bytes"])

    def test_settings_merge_changes_only_infrastructure_and_reuses_existing_directory(self):
        settings = {"web_external": {"directory": "/state/external", "other": 1}, "music": {"scheduler": 12}, "provider_secret": "synthetic"}
        infrastructure = {"directory": "/state/external", "executor": {"command": ["/explicit/python"]}}
        changed = resident.merge_settings(settings, infrastructure)
        self.assertEqual({key: value for key, value in changed.items() if key != "infrastructure"}, settings)
        self.assertNotIn("infrastructure", settings)
        with self.assertRaisesRegex(bundle.Refused, "external_directory_mismatch"):
            resident.merge_settings(settings, {"directory": "/different"})

    def test_preflight_refusal_never_stops_a_previous_successful_release(self):
        # A retained started.json from an earlier run must not turn a fresh
        # preflight refusal into an instruction to stop production.
        worker = ast.parse((Path(resident.__file__).with_name("resident_worker.py")).read_text(encoding="utf-8"))
        handler = ast.Module(body=[worker.body[-1]], type_ignores=[])
        stop, save = Mock(), Mock()
        failure = RuntimeError("release_already_started")
        env = {"release": Mock(side_effect=failure), "TRANSACTION_STARTED": False,
               "S": self.base, "stop_managed": stop, "save": save, "re": re, "json": json}
        (self.base / "started.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "release_already_started"):
            exec(compile(handler, "controlled-worker-entry", "exec"), env)
        stop.assert_not_called()
        self.assertEqual(save.call_args.args[1]["cleanup"], "not_started")

    def test_helper_rebinding_precedes_function_defaults_and_does_not_execute_source(self):
        source = "\n".join(["R='old'", "D='old'", "CAP='old'", "UNIT='old'", "TOOLING='old'", "PYTHON='old'", "def host_path(root=R): return root", "raise RuntimeError('never execute during rendering')"])
        rendered = resident.helpers_source(source)
        compile(rendered, "helpers", "exec")
        self.assertLess(rendered.index("RELEASE['paths']['root']"), rendered.index("def host_path"))
        self.assertIn("raise RuntimeError('never execute during rendering')", rendered)

    def test_resident_renderer_compiles_all_operator_payloads_without_executing_mechanism(self):
        for name in resident.GUARD_FILES:
            self.fixture.put(name, "# synthetic immutable guard source\n")
        guard_commit = self.fixture.save("guard input")
        mechanism = self.base / "mechanism"
        mechanism.mkdir()
        helpers = "\n".join(["R='old'", "D='old'", "CAP='old'", "UNIT='old'", "TOOLING='old'", "PYTHON='old'", "raise RuntimeError('never execute local preparation')"])
        (mechanism / "resident_helpers.py.txt").write_text(helpers, encoding="utf-8")
        body = "\n".join("def " + name + "(): pass" for name in resident.BODY_FUNCTIONS)
        (mechanism / "resident_update_body.py.txt").write_text(body, encoding="utf-8")
        (mechanism / "resident_release_launcher.py").write_text("STAGE_BODY='pass\\nprint(123)'\nLAUNCH_BODY='pass'\nPOLL_BODY='pass'\n", encoding="utf-8")
        baseline = {"owners": [{"service": "platform", "running": True, "image_id": "sha256:" + "a" * 64,
                                "mounts": [{"Destination": "/contracts", "Source": "/synthetic/contracts"}]}],
                    "guard_status": {"status": "ready"}, "owner_runtime": {"platform": {"revision": self.fixture.commit}},
                    "owner_contract_pins": {"platform": {}}}
        metadata = {"resident_root": "/volume2/tianshu-v2-resident", "external_directory": "/var/lib/tianshu/external",
                    "infrastructure_configured": False, "docker_path": "/usr/bin/docker", "capacity_path": "/synthetic/capacity.json",
                    "guard_code_path": "/synthetic/repo/ops/resident_capacity/guard.py", "tool_python": "/usr/bin/python3"}
        build = {"status": "built", "production_changed": False, "node_commit": image.NODE_COMMIT,
                 "node_package_sha256": image.NODE_SHA256, "base_revision": self.fixture.commit, "base_image_id": "sha256:" + "a" * 64,
                 "image_id": "sha256:" + "b" * 64, "revision": guard_commit, "contracts_revision": self.fixture.commit}
        executor = {"version": bundle.PROTOCOL, "state_dir": resident.SAMPLE_STATE, "ssh_command": ["/usr/bin/ssh"],
                    "nodes": {"nas-main": {"label": "主 NAS", "kind": "synology", "host": "nas.invalid", "port": 77,
                                           "user": "synthetic", "identity_file": resident.CONTAINER_SSH + "/identity",
                                           "known_hosts_file": resident.CONTAINER_SSH + "/known_hosts"}}}
        recovery = {"files": {"ops/recovery/resident_snapshot.py": {"base64": "IyBmaXh0dXJlCg==", "sha256": bundle.digest(b"# fixture\n")}}}
        for name, value in (("baseline", baseline), ("metadata", metadata), ("build", build), ("executor", executor), ("recovery", recovery)):
            (self.base / (name + ".json")).write_bytes(bundle.json_bytes(value))
        arguments = dict(baseline=self.base / "baseline.json", metadata=self.base / "metadata.json", build_result=self.base / "build.json",
                         mechanism_directory=mechanism, recovery=self.base / "recovery.json", executor_config=self.base / "executor.json",
                         guard_repo=self.repo, guard_commit=guard_commit, stage="/volume2/tianshu-v2-resident-updates/synthetic-render")
        receipt = resident.prepare(**arguments, output=self.base / "rendered")
        self.assertFalse(receipt["nas_changed"])
        inputs = json.loads((self.base / "rendered/resident-release.json").read_bytes())
        settings = inputs["release"]["infrastructure"]["settings"]
        self.assertEqual((settings["timeout_ms"], settings["poll_interval_seconds"], settings["stale_after_seconds"]), (30000, 30, 120))
        for name in ("resident-update.py", "remote-stage.py", "remote-launch.py", "remote-poll.py", "rollback.py"):
            compile((self.base / "rendered" / name).read_text(encoding="utf-8"), name, "exec")
        baseline["owners"][0]["running"] = False
        (self.base / "baseline.json").write_bytes(bundle.json_bytes(baseline))
        with self.assertRaisesRegex(bundle.Refused, "final_ready_baseline_required"):
            resident.prepare(**arguments, output=self.base / "must-not-render")
        self.assertFalse((self.base / "must-not-render").exists())

    def test_failed_disabled_restore_prevents_every_production_input_write(self):
        root, stage = self.base / "resident", self.base / "stage"
        stage.mkdir()
        settings = {"web_external": {"directory": "/state/external"}, "music": {"keep": True}}
        for name in ("platform", "memory", "knowledge"):
            path = root / "config" / name / "settings.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(bundle.json_bytes(settings if name == "platform" else {"database_path": "/db.sqlite"}))
            (root / (name + ".sqlite.source-guard.json")).write_text("{}")
        paths = {}
        for name in ("core", "platform", "obs"):
            path = root / (name + ".json")
            path.write_text("{}")
            paths[name] = str(path)
        unit = self.base / "unit"
        unit.write_text("old unit")
        rows = [{"id": "a" * 64, "service": "platform"}]
        cap = {"state_dir": str(self.base / "old-state"), "external_media_binds": []}
        writes, installed, stopped = [], Mock(), Mock()
        snapshot = types.SimpleNamespace(require_stopped=Mock(), inspect_owners=Mock(return_value=rows),
                                         snapshot=Mock(return_value={"status": "not_verified", "restored_disabled": False, "authority_unchanged": False}))
        env = {"R": root, "S": stage, "D": "synthetic-docker", "CAP": self.base / "cap", "UNIT": unit, "TOOLING": self.base / "tooling",
               "RELEASE": {"infrastructure": {"settings": {"directory": "/state/external"}}, "baseline": {"media_staging_host": str(root / "staging")}},
               "Path": Path, "json": json, "TRANSACTION_STARTED": False, "snapshot": snapshot,
               "require": bundle.require, "inspect_inputs": Mock(return_value=(rows, cap, paths)), "check_private_inputs": Mock(),
               "load_candidate_guard": Mock(), "metadata_inventory": Mock(return_value=[]),
               "network_inventory": Mock(return_value={}), "network_definitions": Mock(return_value={}), "declared_addresses": Mock(return_value={}),
               "read": lambda path: json.loads(Path(path).read_bytes()),
               "save": lambda path, value: Path(path).write_bytes(bundle.json_bytes(value)),
               "atomic_bytes": lambda path, *args, **kwargs: writes.append(Path(path)), "merge_settings": resident.merge_settings,
               "current_settings_hashes": Mock(return_value={}),
               "command": Mock(return_value=types.SimpleNamespace(stdout=json.dumps([{"HostConfig": {"Memory": 512 * 1024**2}}]))),
               "host_path": lambda rows, name, value: root / (name + ".sqlite"),
               "media_state": Mock(return_value={"configuration_sha256": "synthetic"}), "stop_managed": stopped,
               "install_private_inputs": installed}
        tree = ast.parse(Path(resident.__file__).with_name("resident_worker.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "release")
        exec(compile(ast.Module(body=[function], type_ignores=[]), "controlled-release", "exec"), env)
        with self.assertRaisesRegex(bundle.Refused, "offline_restore_not_verified"):
            env["release"]()
        stopped.assert_called_once()
        installed.assert_not_called()
        self.assertEqual(writes, [stage / "original-unit"])
        self.assertEqual(json.loads((root / "config/platform/settings.json").read_bytes()), settings)

    def test_owner_readback_allows_legacy_tmpfs_representation_but_no_other_mount_change(self):
        names = {"platform", "memory", "knowledge", "companion", "gateway", "obs-vector", "obs-loki", "obs-grafana", "obs-prometheus", "obs-guard"}
        before = [{"service": name, "id": str(index), "image_id": "old", "project": "core",
                   "mounts": [{"Type": "bind", "Source": "/synthetic/" + name, "Destination": "/data", "RW": True}]}
                  for index, name in enumerate(sorted(names))]
        binds = [{"source": "/synthetic/key", "target": "/run/identity", "read_only": True}]
        after = copy.deepcopy(before)
        platform = next(row for row in after if row["service"] == "platform")
        platform.update(id="new-id", image_id="new")
        platform["mounts"].append({"Type": "bind", "Source": "/synthetic/key", "Destination": "/run/identity", "RW": False})
        env = {"RELEASE": {"images": {"platform": {"image_id": "new"}}}, "GATEWAY_REISSUED": False,
               "SAMPLE_STATE": resident.SAMPLE_STATE, "snapshot": types.SimpleNamespace(OWNERS=names), "require": bundle.require, "json": json}
        tree = ast.parse(Path(resident.__file__).with_name("resident_worker.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "expect_owners")
        exec(compile(ast.Module(body=[function], type_ignores=[]), "controlled-ownership", "exec"), env)
        env["expect_owners"](after, before, binds)
        platform["mounts"].append({"Type": "tmpfs", "Source": "", "Destination": resident.SAMPLE_STATE, "RW": True})
        env["expect_owners"](after, before, binds)
        platform["mounts"][-1]["Destination"] = "/unexpected"
        with self.assertRaisesRegex(bundle.Refused, "platform_tmpfs_changed"):
            env["expect_owners"](after, before, binds)


if __name__ == "__main__":
    unittest.main()
