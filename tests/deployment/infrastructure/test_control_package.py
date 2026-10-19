"""Control packaging and installation use synthetic Git/filesystem inputs only."""

import copy
import json
import os
import stat
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_package as fixtures
import test_production_preparation as production
from deploy.infrastructure import bundle, control_bundle, control_install, control_install_body as install, image, resident


class ControlPackageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base, self.repo = self.fixture.base, self.fixture.repo
        self.fixture.put("ops/infrastructure/acceptance.py", "# synthetic acceptance source\n")
        self.fixture.put("ops/infrastructure/ACCEPTANCE.md", "Synthetic acceptance documentation.\n")
        for name in control_bundle.CONTROL_FILES:
            self.fixture.put(name, "// synthetic JS\n" if name.endswith(".js") else "# synthetic control source\n")
        self.fixture.put("ops/infrastructure/control/protocol.py", f'VERSION = "{control_bundle.PROTOCOL}"\n')
        for name in control_bundle.CONTRACT_FILES:
            version = ("infrastructure-control.remote.v1" if name.endswith("remote-config.schema.json") else
                       "infrastructure-control.config.v1" if name.endswith("/config.schema.json") else control_bundle.PROTOCOL)
            self.fixture.put(name, json.dumps({"properties": {"version": {"const": version}}}) if name.endswith(".schema.json") else "{}\n")
        self.fixture.put("ops/infrastructure/control/config-private.json", "SYNTHETIC_PRIVATE_CONTENT_MUST_NOT_BE_PACKAGED")
        self.commit = self.fixture.save("synthetic control input")
        self.wheel = self.base / "pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.whl"
        self.wheel.write_bytes(b"synthetic wheel, never installed")
        assets = self.base / "assets"
        assets.mkdir()
        for name in control_bundle.ASSETS.values():
            (assets / name).write_bytes((control_bundle.HERE / name).read_bytes())
        lock = assets / "requirements.lock"
        lock.write_bytes(lock.read_bytes() + b"# synthetic lock member --hash=sha256:" + bundle.digest(self.wheel.read_bytes()).encode() + b"\n")
        self.assets = assets
        self.package = self.base / "control.zip"
        with patch.object(control_bundle, "HERE", self.assets):
            self.info = control_bundle.build(self.repo, self.commit, self.package)

    def configuration(self):
        root = "/synthetic/resident"
        code_root = "/synthetic/tooling/control"
        code = code_root + "/" + self.info["package_sha256"]
        runtime = code + ".venv"
        protection = {"owner": "resident-update", "reason": "capacity_guard_ownership", "maintenance_ref": "resident-update"}
        return {"version": control_install.VERSION, "remote_code_root": code_root, "remote_python": "/synthetic/registered-python",
                "remote_runtime_directory": runtime,
                "remote_config_path": root + "/config/platform/infrastructure/control-remote.json",
                "relay_config_path": root + "/config/platform/infrastructure/control-relay.json",
                "relay_config": {"version": "infrastructure-control.config.v1", "observation_config": "/etc/tianshu/infrastructure/executor.json",
                                 "state_dir": "/var/lib/tianshu/control-relay", "nodes": {"fixture-nas": {"command": [
                                     "sudo", "-n", "/synthetic/registered-python", "-B", code + "/run_remote.py", "--python", runtime + "/bin/python",
                                     "--config", root + "/config/platform/infrastructure/control-remote.json"]}}},
                "remote_config": {"version": "infrastructure-control.remote.v1", "node_id": "fixture-nas", "state_dir": root + "/data/platform/control-node",
                                  "stacks_root": "/synthetic/stacks", "docker_command": ["/synthetic/docker"], "docker_socket": "/synthetic/docker.sock",
                                  "compose_command": ["/synthetic/docker", "compose"], "platform_stacks": [],
                                  "protected_projects": {"tianshu-v2-resident": protection, "tianshu-v2-resident-obs": protection},
                                  "protected_container_names": {"dockge": {"owner": "dockge", "reason": "control_execution_dependency", "maintenance_ref": None}},
                                  "protected_bind_roots": [{"path": root, "owner": "resident-update", "reason": "capacity_guard_mount_ownership"}]},
                "platform_control": {"contract_directory": "/opt/tianshu-infrastructure-control/" + control_bundle.CONTRACT,
                                     "executor": {"directory": "/opt/tianshu-infrastructure-control", "command": ["/opt/tianshu-infrastructure-venv/bin/python", "-I", "-B", "/opt/tianshu-infrastructure-control/run_control.py",
                                                                                          "--config", "/etc/tianshu/infrastructure/control-relay.json"]}}}

    def prepared(self):
        path = self.base / "configuration.json"
        path.write_bytes(bundle.json_bytes(self.configuration()))
        output = self.base / "install.json"
        receipt = control_install.prepare(package=self.package, configuration=path, wheel=self.wheel, output=output)
        return json.loads(output.read_bytes()), receipt

    def validated(self, value):
        mounts = [{"Type": "bind", "Source": "/synthetic/resident/config/platform", "Destination": "/etc/tianshu", "RW": False},
                  {"Type": "bind", "Source": "/synthetic/resident/data/platform", "Destination": "/var/lib/tianshu", "RW": True},
                  {"Type": "tmpfs", "Destination": resident.SAMPLE_STATE}]
        return control_install.validate(value, resident_root="/synthetic/resident", mounts=mounts, node_ids=["fixture-nas"])

    def test_repeatable_fixed_source_exact_files_contract_pins_and_tamper(self):
        self.fixture.put("ops/infrastructure/control/engine.py", "UNCOMMITTED_ERROR")
        second = self.base / "second.zip"
        with patch.object(control_bundle, "HERE", self.assets):
            repeated = control_bundle.build(self.repo, self.commit, second)
        self.assertEqual(self.info, repeated)
        self.assertEqual(self.package.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(self.package) as archive:
            self.assertIn("ops/infrastructure/control/compose-wrapper.js", archive.namelist())
            self.assertIn("ops/infrastructure/control/compose-probe.js", archive.namelist())
            for name in archive.namelist():
                self.assertNotIn(b"SYNTHETIC_PRIVATE_CONTENT_MUST_NOT_BE_PACKAGED", archive.read(name))
            self.assertEqual(self.info["contract"]["schemas"]["response.schema.json"], bundle.digest(archive.read(control_bundle.CONTRACT + "/response.schema.json")))
        with zipfile.ZipFile(second, "a") as archive:
            archive.writestr("private.json", "synthetic")
        with self.assertRaisesRegex(bundle.Refused, "package_file_list_mismatch"):
            control_bundle.verify(second)

    def test_missing_node_script_and_mismatched_protocol_are_refused(self):
        (self.repo / "ops/infrastructure/control/compose-wrapper.js").unlink()
        missing = self.fixture.save("missing wrapper")
        with self.assertRaisesRegex(bundle.Refused, "snapshot_files_missing"):
            control_bundle.build(self.repo, missing, self.base / "missing.zip")
        self.fixture.put("ops/infrastructure/control/compose-wrapper.js", "// synthetic\n")
        self.fixture.put("ops/infrastructure/control/protocol.py", "VERSION='incorrect'\n")
        changed = self.fixture.save("wrong control version")
        with self.assertRaisesRegex(bundle.Refused, "control_protocol_version_mismatch"):
            control_bundle.build(self.repo, changed, self.base / "wrong.zip")

    def test_install_input_is_explicit_locked_and_only_uses_existing_persistent_binds(self):
        value, receipt = self.prepared()
        self.assertFalse(receipt["nas_changed"])
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(receipt))
        checked = self.validated(value)
        self.assertEqual(checked["relay_state_host"], "/synthetic/resident/data/platform/control-relay")
        for field, change, reason in (
            ("relay_config", lambda item: item.update(state_dir=resident.SAMPLE_STATE + "/control"), "control_state_must_be_persistent"),
            ("remote_config", lambda item: item.update(protected_bind_roots=[]), "control_bind_protection_missing"),
            ("relay_config", lambda item: item["nodes"]["fixture-nas"].update(command=["ssh", "arbitrary"]), "control_fixed_remote_command_mismatch"),
            ("remote_config", lambda item: item.update(state_dir="/unbacked/node"), "control_state_backup_scope_invalid"),
        ):
            bad = copy.deepcopy(value)
            change(bad["configuration"][field])
            with self.assertRaisesRegex(bundle.Refused, reason):
                self.validated(bad)
        self.wheel.write_bytes(b"unreviewed wheel")
        config = self.base / "configuration.json"
        with self.assertRaisesRegex(bundle.Refused, "control_wheel_lock_mismatch"):
            control_install.prepare(package=self.package, configuration=config, wheel=self.wheel, output=self.base / "untrusted.json")

    def test_settings_and_rollback_change_only_explicit_control_reference(self):
        infrastructure = {"directory": "/state/external", "poll": 30, "executor": {"command": ["/old/python"]}}
        settings = {"infrastructure": infrastructure, "music": {"period": 1500}, "books": {"keep": True}}
        control = self.configuration()["platform_control"]
        changed = resident.merge_settings(settings, infrastructure, preserve_existing=True, control=control)
        self.assertEqual(changed["music"], settings["music"])
        self.assertEqual({key: item for key, item in changed["infrastructure"].items() if key != "control"}, infrastructure)
        changed["books"]["later_edit"] = True
        restored = resident.restore_control_settings(changed, control, infrastructure)
        self.assertNotIn("control", restored["infrastructure"])
        self.assertTrue(restored["books"]["later_edit"])
        old = {**infrastructure, "control": {"contract_directory": "/old/contracts"}}
        self.assertEqual(resident.restore_control_settings(changed, control, old)["infrastructure"]["control"], old["control"])
        changed["infrastructure"]["control"] = {"later_control_edit": True}
        with self.assertRaisesRegex(bundle.Refused, "control_changed_after_release"):
            resident.restore_control_settings(changed, control, infrastructure)

    def test_image_contains_both_fixed_packages_without_changing_platform_recipe(self):
        node, old_info = self.fixture.build("old-node.zip")
        base, candidate = production.ProductionPreparationTests.platform(self)
        with patch.object(image, "NODE_COMMIT", self.fixture.commit), patch.object(image, "NODE_SHA256", old_info["package_sha256"]):
            receipt = image.prepare(platform_repo=self.repo, platform_commit=candidate, music_commit=base,
                                    music_image="sha256:" + "a" * 64, contracts_repo=self.repo, contracts_commit=self.commit,
                                    node_package=node, control_package=self.package,
                                    stage="/volume2/tianshu-v2-resident-updates/synthetic-control-build", docker="/synthetic/docker",
                                    remote_python="/synthetic/python", tag="synthetic/control:test", output=self.base / "image")
        import tarfile
        from deploy.infrastructure import image_controller
        self.assertEqual(receipt["control_package"], self.info)
        with tarfile.open(self.base / "image/build-context.tar.gz", "r:gz") as archive:
            recipe = archive.extractfile("Dockerfile.candidate").read().decode()
            self.assertIn("COPY control-package/ /opt/tianshu-infrastructure-control/", recipe)
            self.assertIn("USER 10001:10001", recipe)
            self.assertIn("node-package/run_node.py", archive.getnames())
            self.assertIn("control-package/ops/infrastructure/control/compose-wrapper.js", archive.getnames())
            config = json.loads(archive.extractfile("build-inputs.json").read())
        self.assertEqual(image_controller.candidate_labels(config)["org.tianshu.infrastructure-control.package-sha256"], self.info["package_sha256"])
        compile("CONFIG=" + repr(config) + "\n" + image_controller.LINUX_CHECKS, "installed-control-linux-checks", "exec")

    def test_installer_idempotence_preserves_journal_config_and_source(self):
        value, _ = self.prepared()
        value = self.validated(value)
        config = value["configuration"]
        local = self.base / "installation"
        local.mkdir()
        code_root = local / "tooling"
        private = local / "private"
        private.mkdir()
        state = local / "state"
        state.mkdir()
        config.update(remote_code_root=str(code_root), remote_runtime_directory=str(code_root / "runtime.venv"),
                      remote_config_path=str(private / "remote.json"), relay_config_path=str(private / "relay.json"))
        config["remote_config"]["state_dir"] = str(state / "node")
        value["code_directory"], value["relay_state_host"] = str(code_root / "fixed-package"), str(state / "relay")
        owners, modes = {}, {}

        class OwnedPath(type(Path())):
            def stat(self, *, follow_symlinks=True):
                raw = super().stat(follow_symlinks=follow_symlinks)
                fields = list(raw)
                fields[4] = owners.get(str(self), raw.st_uid)
                if str(self) in modes:
                    fields[0] = stat.S_IFMT(raw.st_mode) | modes[str(self)]
                return os.stat_result(fields)

            def chmod(self, mode, **kwargs):
                modes[str(self)] = mode

        def chown(path, uid, gid):
            owners[str(path)] = uid

        def run(argv, **kwargs):
            if "venv" in argv:
                runtime = OwnedPath(argv[-1])
                runtime.mkdir()
                (runtime / "bin").mkdir()
                (runtime / "bin/python").write_bytes(b"synthetic runtime")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        original_rename = os.rename
        original_link = os.link

        def rename(source, target):
            original_rename(source, target)
            for mapping in (owners, modes):
                for name in list(mapping):
                    if name == str(source) or name.startswith(str(source) + os.sep):
                        mapping[str(target) + name[len(str(source)):]] = mapping.pop(name)

        def link(source, target):
            original_link(source, target)
            for mapping in (owners, modes):
                if str(source) in mapping:
                    mapping[str(target)] = mapping[str(source)]

        with patch.object(install, "Path", OwnedPath), patch.object(install.os, "chown", side_effect=chown, create=True), patch.object(install.os, "rename", side_effect=rename), patch.object(install.os, "link", side_effect=link), patch.object(install.subprocess, "run", side_effect=run) as runtime:
            install.check_control_install(value)
            self.assertFalse(code_root.exists(), "preflight wrote code before a backup")
            result = install.install_control(value)
            node_marker = state / "node" / "operation-existing.json"
            node_marker.write_bytes(b"existing persistent journal")
            config_bytes = (private / "relay.json").read_bytes()
            source_file = Path(value["code_directory"]) / "ops/infrastructure/control/engine.py"
            source_identity = source_file.stat().st_ino
            repeated = install.install_control(value)
            self.assertEqual(result, repeated)
            self.assertEqual(node_marker.read_bytes(), b"existing persistent journal")
            self.assertEqual((private / "relay.json").read_bytes(), config_bytes)
            self.assertEqual(source_file.stat().st_ino, source_identity)
            self.assertEqual(sum("venv" in call.args[0] for call in runtime.call_args_list), 1)
            source_file.write_bytes(b"external change")
            with self.assertRaisesRegex(ValueError, "control_installed_source_changed"):
                install.install_control(value)
        self.assertNotIn("synthetic-runtime", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
