"""Optional A1 prepare -> A3 real configure/export readback in isolated data."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def joint_child():
    a3 = Path(os.environ["TS_A3_ROOT"]).resolve()
    sys.path.insert(0, str(a3 / "deploy" / "tianshu"))
    sys.path.insert(0, str(a3 / "tests" / "deployment" / "packaging"))
    import test_resident_export as a3_test
    from manifest import digest, read_json, write_json
    from resident_export import export

    # Import A1 after A3's fixed adapter modules are loaded. The A1 wrapper
    # then calls those actual adapter functions without copying or patching
    # product configuration or dependency behavior.
    sys.path.insert(0, str(ROOT))
    from ops.resident_install import install

    test = a3_test.ResidentExportTests()
    test.setUp()
    try:
        fixture = test.f
        fixture.configs["platform"]["web"]["username"] = "resident-operator"

        def a1_initialize():
            credentials = dict(fixture.env)
            password = credentials.pop("TS_ADMIN_PASSWORD")
            credentials_path = fixture.root / "credentials-private.json"
            password_path = fixture.root / "admin-password-private.txt"
            write_json(credentials_path, credentials)
            password_path.write_bytes((password + "\n").encode("utf-8"))
            credentials_path.chmod(0o600)
            password_path.chmod(0o600)
            return install.prepare(
                fixture.manifestpath, fixture.sitepath, fixture.contracts,
                credentials_path, password_path, "resident-operator",
                fixture.output, isolated_windows_fixture=True,
            )

        fixture.init = a1_initialize
        test.prepare()
        bundle = fixture.output
        platform = read_json(bundle / "config/platform/settings.json")
        assert platform["web"]["username"] == "resident-operator"
        assert platform["web"]["password_hash"].startswith("scrypt-v1$")
        assert platform["providers"] == {}
        assert not (bundle / "data/platform/platform.sqlite").exists()
        install._exporter_ready(bundle, a3, fixture.root / "candidate-final")
        test.configure_observability()
        out = fixture.root / "joint-dockge-export"
        result = export(bundle, a3, "/volume1/tianshu-v2-resident", out)
        lock = read_json(out / "resident-export.lock.json")
        assert result["status"] == "resident_candidate"
        assert lock["manifest_sha256"] == digest(
            (bundle / "release-manifest.json").read_bytes()
        )
        assert lock["bundle_integrity_sha256"] == digest(
            (bundle / "bundle-integrity.json").read_bytes()
        )
        assert lock["observability_source"]["commit"] == install.FIXED_OBSERVABILITY
        assert lock["product_sources"] == {
            p: read_json(bundle / "release-manifest.json")["products"][p]["source"]
            for p in install.PRODUCTS
        }
        assert lock["projects"] == [install.CORE_PROJECT, install.OBS_PROJECT]
        assert len(lock["images"]) == 9
        assert len(lock["network_subnets"]) == 6
        inventory = read_json(bundle / "bundle-integrity.json")["files"]
        assert lock["config_and_private_sha256"] == {
            name: value for name, value in inventory.items()
            if name.startswith(
                ("config/", "private/", "observability/config/",
                 "observability-input/")
            )
        }
        for project in lock["projects"]:
            assert lock["compose_sha256"][project] == digest(
                (out / project / "compose.yaml").read_bytes()
            )
        # Use the installed fixed Platform public CLI against this bundle's own
        # new isolated DB, then prove A3 can export the changed private env
        # without changing either Compose service definition.
        local = read_json(bundle / "config/platform/settings.json")
        local["database_path"] = str(bundle / "data/platform/platform.sqlite")
        local["contract_directory"] = str(fixture.contracts / "text-dialogue/v1")
        local["diagnostics"]["contract_directory"] = str(
            fixture.contracts / "diagnostics/v1"
        )
        local["diagnostics"]["log_directory"] = str(bundle / "logs/platform")
        local["web"]["static_directory"] = str(bundle / "local-web")
        (bundle / "local-web/assets").mkdir(parents=True)
        (bundle / "local-web/index.html").write_text(
            '<!doctype html><script src="/assets/app.js"></script>'
        )
        (bundle / "local-web/assets/app.js").write_text("/* isolated */")
        local["core"]["ca_file"] = str(bundle / "config/platform/tls/ca.pem")
        local["tls"]["certificate_file"] = str(
            bundle / "config/platform/tls/server.pem"
        )
        local["tls"]["private_key_file"] = str(
            bundle / "config/platform/tls/server.key"
        )
        local["web_access"]["certificates"]["nas-web"] = local["tls"]
        local_path = fixture.root / "local-platform.json"
        write_json(local_path, local)
        issue_path = fixture.root / "issue.json"
        write_json(issue_path, {"entry_id": "config-entry"})
        environment = dict(os.environ)
        for line in (bundle / "private/platform.env").read_text().splitlines():
            name, value = line.split("=", 1)
            environment[name] = value[1:-1]
        issue = subprocess.run(
            [sys.executable, "-B", "-m", "services.platform", "--settings",
             str(local_path), "local", "--credential-env", "TS_ADMIN_TOKEN",
             "issue", "--input", str(issue_path)],
            capture_output=True, timeout=45, env=environment,
        )
        assert issue.returncode == 0, issue.stderr.decode(errors="replace")[-500:]
        receipt = json.loads(issue.stdout)
        origin = install._gateway_origin_precheck(
            bundle, read_json(bundle / "config/gateway/settings.json")
        )
        install._install_ref(bundle, receipt, origin)
        after = fixture.root / "joint-dockge-final"
        export(bundle, a3, "/volume1/tianshu-v2-resident", after)
        final_lock = read_json(after / "resident-export.lock.json")
        assert final_lock["bundle_integrity_sha256"] != lock["bundle_integrity_sha256"]
        assert final_lock["compose_sha256"] == lock["compose_sha256"]
        print(json.dumps({
            "status": "joint_a1_a3_passed",
            "prepare": "real_bundle_builder",
            "configure": "fixed_git_observability_cli",
            "export": "actual_pre_and_post_issue_dockge_locks",
            "provider": "not_configured",
            "release_ready": False,
        }))
    finally:
        test.tearDown()


class JointA3Tests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("TS_A3_ROOT")
        and os.environ.get("TIANSHU_CONTRACTS_ROOT")
        and os.environ.get("TIANSHU_PROJECTS_ROOT"),
        "A3 worktree, original contracts and fixed product repos required",
    )
    def test_a1_prepare_to_a3_actual_export(self):
        child = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--joint-child"],
            env=os.environ.copy(), capture_output=True, timeout=120,
        )
        self.assertEqual(
            child.returncode, 0,
            (child.stdout + child.stderr).decode(errors="replace")[-1500:],
        )
        report = json.loads(child.stdout.splitlines()[-1])
        self.assertEqual(report["status"], "joint_a1_a3_passed")


if __name__ == "__main__":
    if "--joint-child" in sys.argv:
        joint_child()
    else:
        unittest.main()
