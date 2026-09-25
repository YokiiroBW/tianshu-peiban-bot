"""A1 Gateway host-CLI lock and isolated pre-owner readiness checks."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ops.recovery import a1_clone_prepare, a1_gateway_cli, a1_prepare
from ops.recovery.safety import RecoveryError


class GatewayCliPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        repository = Path(__file__).resolve().parents[3]
        product = Path("C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-model-gateway")
        if not product.is_dir():
            self.skipTest("fixed Gateway source unavailable locally")
        self.gateway = self.root / "gateway-source"
        shutil.copytree(product / "src", self.gateway)
        self.tooling = self.root / "tooling"
        lock = self.tooling / "ops/recovery/a1_gateway_runtime.lock.json"
        lock.parent.mkdir(parents=True)
        shutil.copyfile(repository / "ops/recovery/a1_gateway_runtime.lock.json", lock)
        self.config = {"code_root": str(self.tooling), "python": sys.executable,
            "gateway_pythonpath": {str(self.gateway): a1_gateway_cli.GATEWAY_TREE_SHA256},
            "gateway_runtime_lock": str(lock),
            "gateway_runtime_lock_sha256": a1_gateway_cli.LOCK_SHA256,
            "scope_parent": str(self.root), "scope_name": "scope-a1-fixture",
            "run_label": "a1-fixture"}
        self.source = self.root / "scope-a1-fixture/deployments/source"
        for name in ("config/gateway", "private", "data/gateway", "reports/a1-fixture"):
            (self.source / name).mkdir(parents=True, exist_ok=True)
        settings = {
            "contract_directory": str(self.root / "unused-contracts"),
            "diagnostics_path": str(self.root / "unused-original-ledger.sqlite"),
            "platform_base_url": "http://127.0.0.1:39991",
            "platform_credential_ref": "secret-ref:fixture/platform",
            "platform_origin_env": "TS_A1_TEST_ORIGIN",
            "secret_references": {"secret-ref:fixture/client": "TS_A1_TEST_CLIENT",
                                  "secret-ref:fixture/platform": "TS_A1_TEST_PLATFORM"},
            "targets": [],
            "clients": [{"service": "companion",
                         "credential_ref": "secret-ref:fixture/client",
                         "provider_id": "provider-synthetic",
                         "config_version": 7, "internal": True,
                         "allowed_versions": [8]}],
        }
        (self.source / "config/gateway/settings.json").write_text(
            json.dumps(settings), encoding="utf-8")
        (self.source / "private/gateway.env").write_text(
            "TS_A1_TEST_CLIENT=fixture-client-token-041\n"
            "TS_A1_TEST_PLATFORM=fixture-platform-token-041\n",
            encoding="utf-8")
        files = {name: a1_gateway_cli.file_hash(self.source / name)
                 for name in ("config/gateway/settings.json", "private/gateway.env")}
        (self.source / "bundle-integrity.json").write_text(
            json.dumps({"schema_version": "1.0.0", "files": files}), encoding="utf-8")

    def test_portable_lock_binds_fixed_gateway_tree_and_bytes(self):
        lock, gateway = a1_gateway_cli.verify_preparation(self.config)
        self.assertEqual(gateway, self.gateway)
        self.assertEqual(len(lock["wheels"]), 14)
        self.config["gateway_pythonpath"][str(gateway)] = "0" * 64
        with self.assertRaisesRegex(RecoveryError, "a1_gateway_source_not_fixed"):
            a1_gateway_cli.verify_preparation(self.config)

    def test_runtime_failure_precedes_source_creation(self):
        new = dict(self.config, scope_name="scope-a1-not-created")
        def missing(_config):
            raise RecoveryError("a1_gateway_cli_preflight_unavailable")
        with self.assertRaisesRegex(RecoveryError, "a1_gateway_cli_preflight_unavailable"):
            a1_prepare.prepare_source(new, runtime_checker=missing)
        self.assertFalse((self.root / new["scope_name"]).exists())

    def test_real_usage_child_uses_same_sanitized_python_environment(self):
        with (self.source / "private/gateway.env").open("a", encoding="utf-8") as stream:
            stream.write("PYTHONHOME=fixture-override\nPYTHONOPTIMIZE=1\n")
        completed = SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")
        with patch.object(a1_clone_prepare.subprocess, "run", return_value=completed) as run:
            a1_clone_prepare._usage(self.source, self.config,
                "2026-09-25T00:00:00Z", "2026-09-26T00:00:00Z")
        argv = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(argv[:4], [self.config["python"], "-B", "-s", "-m"])
        self.assertEqual(environment["PYTHONPATH"], str(self.gateway))
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
        self.assertNotIn("PYTHONHOME", environment)
        self.assertNotIn("PYTHONOPTIMIZE", environment)

    def test_full_fixed_runtime_and_real_usage_on_temporary_ledger(self):
        full = os.environ.get("A1_GATEWAY_PREFLIGHT_FULL_PYTHON")
        if not full:
            self.skipTest("A1_GATEWAY_PREFLIGHT_FULL_PYTHON not configured")
        self.config["python"] = full
        with self.assertRaisesRegex(RecoveryError, "a1_gateway_cli_preflight_invalid"):
            a1_gateway_cli.verify_runtime(self.config)
        ready = a1_gateway_cli.verify_runtime(self.config, _allow_windows_fixture=True)
        self.assertEqual(ready["gateway_package_count"], 14)
        result = a1_gateway_cli.verify_usage_before_owners(
            self.config, self.source, _allow_windows_fixture=True)
        self.assertEqual(result["status"], "gateway_usage_cli_ready")
        self.assertEqual(result["synthetic_attempts"], 1)
        self.assertEqual(list((self.source / "data/gateway").iterdir()), [])
        self.assertFalse((self.source / "reports/a1-fixture/a1-source-attempt.json").exists())

    def test_real_missing_aiohttp_runtime_is_rejected_before_owners(self):
        missing = os.environ.get("A1_GATEWAY_PREFLIGHT_MISSING_AIOHTTP_PYTHON")
        if not missing:
            self.skipTest("A1_GATEWAY_PREFLIGHT_MISSING_AIOHTTP_PYTHON not configured")
        self.config["python"] = missing
        with self.assertRaisesRegex(RecoveryError, "a1_gateway_cli_preflight_unavailable"):
            a1_gateway_cli.verify_usage_before_owners(
                self.config, self.source, _allow_windows_fixture=True)
        self.assertEqual(list((self.source / "data/gateway").iterdir()), [])
        self.assertFalse((self.source / "reports/a1-fixture/a1-source-attempt.json").exists())


if __name__ == "__main__":
    unittest.main()
