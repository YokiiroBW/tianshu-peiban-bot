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
