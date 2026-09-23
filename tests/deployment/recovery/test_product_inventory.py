"""Inventory adapters use synthetic SQLite only; these are not product execution tests."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import fixture, put
from lifecycle_fixtures import compose_fixture

from ops.recovery.engine import Recovery
from ops.recovery.manifest import load_deployment
from ops.recovery.product_inventory import inventory
from ops.recovery.safety import RecoveryError
from ops.recovery.snapshot import enumerate_inputs, verify_guards


class ProductInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "scope"
        self.scope, _ = fixture(self.root)
        self.source = self.root / "deployments/source"
        compose_fixture(self.source)
        self.manifest = json.loads((self.source / "release-manifest.json").read_bytes())
        for service in self.manifest["services"]:
            put(self.source / service["config_path"], {"fixture": service["id"]})
        for product in ("platform", "companion", "memory", "gateway"):
            (self.source / "data" / product / ".deployment-owner.lock").write_bytes(
                b"original-lock"
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_all_state_files_migration_backups_and_runtime_locks(self):
        shutil.copyfile(
            self.source / "data/memory/main.db",
            self.source / "data/memory/first-install.sqlite",
        )
        (self.source / "data/platform/future.state").write_bytes(b"must retain")
        document = inventory(self.source)
        put(self.source / "recovery-inventory.json", document)
        manifest, inv, resources = load_deployment(self.source)
        verify_guards(self.source, inv, resources)
        indexed = {r["path"]: r["kind"] for r in document["resources"]}
        self.assertEqual(indexed["data/memory/first-install.sqlite"], "sqlite")
        self.assertEqual(indexed["data/platform/future.state"], "file")
        self.assertEqual(sum(kind == "owner_lock" for kind in indexed.values()), 5)
        inputs = enumerate_inputs(
            self.source, manifest, resources, max_bytes=10**7, max_files=1000
        )
        self.assertIn("data/platform/future.state", inputs)
        self.assertEqual(len(document["config_references"]), 9)
        # Existing lifecycle gate still applies: inventory cannot authorize an offline bypass.
        with self.assertRaisesRegex(RecoveryError, "observability_lifecycle_required"):
            Recovery(self.root, self.scope).backup("source", "backup", execute=True)

    def test_inventory_is_read_only_and_deterministic(self):
        before = {
            p.relative_to(self.source): p.read_bytes()
            for p in self.source.rglob("*")
            if p.is_file()
        }
        self.assertEqual(inventory(self.source), inventory(self.source))
        after = {
            p.relative_to(self.source): p.read_bytes()
            for p in self.source.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)

    def test_directory_config_pins_every_nested_file_and_rejects_empty_tree(self):
        service = next(s for s in self.manifest["services"] if s["id"] == "obs-grafana")
        service["config_path"] = "observability/config/provisioning"
        put(self.source / "release-manifest.json", self.manifest)
        directory = self.source / service["config_path"]
        self.assertTrue(directory.is_file())
        directory.unlink()
        directory.mkdir(parents=True)
        with self.assertRaisesRegex(RecoveryError, "empty_service_config_tree"):
            inventory(self.source)
        put(directory / "datasources/main.yaml", {"synthetic": True})
        put(directory / "alerting/rules.yaml", {"synthetic": True})
        original = inventory(self.source)
        refs = [
            r
            for r in original["config_references"]
            if r["id"].startswith("obs-grafana-")
        ]
        self.assertEqual(len(refs), 2)
        put(directory / "alerting/rules.yaml", {"synthetic": "changed"})
        self.assertNotEqual(
            inventory(self.source)["config_references"], original["config_references"]
        )

    def test_missing_guard_or_settings_rejected(self):
        guard = self.source / "data/memory/main.db.source-guard.json"
        raw = guard.read_bytes()
        guard.unlink()
        with self.assertRaisesRegex(RecoveryError, "memory_guard_required"):
            inventory(self.source)
        guard.write_bytes(raw)
        (self.source / self.manifest["services"][0]["config_path"]).unlink()
        with self.assertRaisesRegex(RecoveryError, "path_missing"):
            inventory(self.source)

    def test_fake_nested_owner_lock_not_exempted(self):
        nested = self.source / "data/platform/nested"
        nested.mkdir()
        (nested / ".deployment-owner.lock").write_bytes(b"preserve me")
        document = inventory(self.source)
        indexed = {r["path"]: r["kind"] for r in document["resources"]}
        self.assertEqual(indexed["data/platform/nested/.deployment-owner.lock"], "file")
        put(self.source / "recovery-inventory.json", document)
        load_deployment(self.source)

    def test_new_state_after_inventory_rejected(self):
        put(self.source / "recovery-inventory.json", inventory(self.source))
        (self.source / "data/gateway/new-ledger").write_bytes(b"new fact")
        manifest, _, resources = load_deployment(self.source)
        with self.assertRaisesRegex(RecoveryError, "unregistered_state_file"):
            enumerate_inputs(
                self.source, manifest, resources, max_bytes=10**7, max_files=1000
            )

    def test_file_limit_rejected(self):
        with self.assertRaises(RecoveryError):
            inventory(self.source, max_files=1)


if __name__ == "__main__":
    unittest.main()
