"""Real native exports, disabled restores and source-guard/file pairing."""

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tianshu_storage import FileStore, StorageOptions, StorageRuntime

from ops.recovery import postgres_backend, resident_retention, resident_snapshot
from ops.recovery.safety import RecoveryError
from tests.deployment.recovery.test_resident_snapshot import owners

PG_URL = os.environ.get("TIANSHU_TEST_POSTGRES_URL", "")
PG_BIN = os.environ.get("TIANSHU_TEST_PG_BIN", "")


class ConnectionEnvironmentTests(unittest.TestCase):
    def test_uri_credentials_and_tls_options_use_environment_without_shell_interpolation(
        self,
    ):
        value = postgres_backend.connection_environment(
            "postgresql://synthetic:p%40ss%3Aword@127.0.0.1:57415/test?sslmode=require",
            read_only=True,
        )
        self.assertEqual(value["PGPASSWORD"], "p@ss:word")
        self.assertEqual(value["PGDATABASE"], "test")
        self.assertEqual(value["PGPORT"], "57415")
        self.assertEqual(value["PGSSLMODE"], "require")
        self.assertIn("default_transaction_read_only=on", value["PGOPTIONS"])


class ActorMemorySnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="actor-cold-snapshot-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "source"
        self.data = self.root / "data/memory"
        self.data.mkdir(parents=True)
        self.rows = owners()
        next(row for row in self.rows if row["service"] == "memory")["mounts"] = [
            {"Type": "bind", "Source": str(self.data), "Destination": "/memory", "RW": True}
        ]
        self.config_path = self.root / "config/memory/settings.json"
        self.config_path.parent.mkdir(parents=True)
        self.config = {"database_path": "/memory/user.sqlite", "actor_memory": {
            "database_path": "/memory/actor.sqlite", "guard_path": "/memory/checkpoints/actor.json"}}
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.guard = self.data / "checkpoints/actor.json"
        self.guard.parent.mkdir()
        self.head = {"domain": "actor-memory", "schema": 2, "instance": uuid4().hex,
                     "revision": 7, "recovery": uuid4().hex}
        self.guard.write_text(json.dumps(self.head), encoding="utf-8")
        self.actor = self.data / "actor.sqlite"
        with closing(sqlite3.connect(self.actor)) as db:
            db.execute("CREATE TABLE actor_metadata(key TEXT PRIMARY KEY,value TEXT)")
            db.executemany("INSERT INTO actor_metadata VALUES (?,?)", self.metadata().items())
            db.commit()
        with closing(sqlite3.connect(self.data / "user.sqlite")) as db:
            db.execute("CREATE TABLE untouched(value TEXT)")
            db.execute("INSERT INTO untouched VALUES ('synthetic user domain')")
            db.commit()

    def metadata(self):
        return {key: str(self.head[key]) for key in ("schema", "instance", "revision", "recovery")}

    def snapshot(self, name):
        return resident_snapshot.snapshot(self.root, self.base / name, self.rows, lambda: self.rows)

    def postgres_binding(self):
        self.config["storage_backends"] = {"postgresql_url": "postgresql://synthetic@127.0.0.1/synthetic"}
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        binding = Path(str(self.actor) + ".storage") / ".tianshu-storage-actor-memory.json"
        binding.parent.mkdir()
        self.state = {"version": 1, "service": "actor-memory", "backend": "postgresql",
                      "state": "active", "namespace": "synthetic", "epoch": uuid4().hex,
                      "installation_id": uuid4().hex, "databases": {"main": "synthetic_actor"}}
        binding.write_text(json.dumps(self.state), encoding="utf-8")
        return binding

    def test_sqlite_actor_custom_guard_is_automatically_verified_in_disabled_restore(self):
        user_before = (self.data / "user.sqlite").read_bytes()
        result = self.snapshot("sqlite")
        self.assertEqual(result["memory_guards_verified"], 1)
        self.assertEqual(result["sqlite_databases_verified"], 2)
        manifest = json.loads((self.base / "sqlite/manifest.json").read_bytes())
        self.assertEqual(manifest["actor_memory"], {
            "backend": "sqlite", "database": "data/memory/actor.sqlite",
            "guard": "data/memory/checkpoints/actor.json", "binding": None, "guard_verified": True})
        self.assertEqual((self.base / "sqlite/restored-disabled/data/memory/checkpoints/actor.json").read_bytes(), self.guard.read_bytes())
        self.assertEqual((self.data / "user.sqlite").read_bytes(), user_before)

    def test_actor_missing_guard_and_mismatched_checkpoint_never_publish_receipt(self):
        self.guard.unlink()
        with self.assertRaisesRegex(RecoveryError, "actor_memory_guard_missing"):
            self.snapshot("missing-guard")
        self.guard.write_text(json.dumps({**self.head, "revision": 6}), encoding="utf-8")
        with self.assertRaisesRegex(RecoveryError, "actor_memory_guard_mismatch"):
            self.snapshot("wrong-guard")
        self.assertFalse((self.base / "wrong-guard/receipt.json").exists())

    def test_configured_actor_cannot_reuse_user_paths_or_unmounted_state(self):
        for change, error in (({"database_path": "/memory/user.sqlite"}, "user_domain_overlap"),
                              ({"guard_path": "/memory/user.sqlite.source-guard.json"}, "user_domain_overlap"),
                              ({"state_directory": "/memory/user.sqlite.storage"}, "user_domain_overlap"),
                              ({"guard_path": "/unmounted/guard.json"}, "mount_missing")):
            with self.subTest(change=change):
                config = {**self.config, "actor_memory": {**self.config["actor_memory"], **change}}
                self.config_path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaisesRegex(RecoveryError, error):
                    postgres_backend.actor_memory_domain(self.root, self.rows)

    def test_pg_actor_requires_its_exact_active_binding(self):
        binding = self.postgres_binding()
        binding.unlink()
        with self.assertRaisesRegex(RecoveryError, "actor_memory_binding_missing"):
            postgres_backend.actor_memory_domain(self.root, self.rows)
        for change in ({"service": "memory"}, {"state": "pending"}, {"backend": "sqlite"},
                       {"epoch": "missing"}, {"installation_id": "missing"},
                       {"databases": {"role_grants": "wrong"}}):
            with self.subTest(change=change):
                binding.write_text(json.dumps({**self.state, **change}), encoding="utf-8")
                with self.assertRaisesRegex(RecoveryError, "actor_memory_binding_mismatch"):
                    postgres_backend.actor_memory_domain(self.root, self.rows)

    def test_actor_connections_reuse_memory_private_backend_and_maintenance_owner(self):
        binding = self.postgres_binding()
        private = self.root / "private"
        private.mkdir()
        (private / "memory.env").write_text("TIANSHU_POSTGRES_URL='postgresql://synthetic@127.0.0.1/owner'\n", encoding="utf-8")
        with patch.dict(os.environ, {"TIANSHU_MEMORY_BACKUP_POSTGRES_URL": "synthetic-maintenance"}):
            result = postgres_backend.deployment_connections(self.root, [(str(binding), self.state), ("user", {"service": "memory"})])
        self.assertEqual(result["actor-memory"], result["memory"])
        self.assertEqual(result["actor-memory"]["maintenance_url_env"], "TIANSHU_MEMORY_BACKUP_POSTGRES_URL")
        self.assertTrue(result["actor-memory"]["postgresql_url"].endswith("/owner"))

    def test_pg_actor_verifies_source_and_restored_checkpoint_without_user_main_alias(self):
        binding = self.postgres_binding()
        domain = postgres_backend.actor_memory_domain(self.root, self.rows)
        check = resident_snapshot.Deadline(60)
        recorded = resident_snapshot.inventory(self.root, check)
        metadata, installation = self.metadata(), self.state["installation_id"]
        queries, statements = [], []

        class NativeTools:
            def __init__(self, paths, deadline):
                pass

            def query(self, environment, sql):
                queries.append((environment["PGDATABASE"], sql))
                if "__tianshu_storage_binding" in sql:
                    return [[installation, "main", "ready"]]
                return [metadata]

            def fingerprint(self, environment, schemas):
                return {schema: {"synthetic": True} for schema in schemas}

            def run(self, tool, arguments, environment, **kwargs):
                if tool == "pg_dump":
                    Path(next(arg.split("=", 1)[1] for arg in arguments if arg.startswith("--file="))).write_bytes(b"synthetic dump boundary")

            def statement(self, environment, sql):
                statements.append(sql)

        for mismatch in (False, True):
            with self.subTest(restored_guard_mismatch=mismatch):
                operation = self.base / ("pg-mismatch" if mismatch else "pg")
                operation.mkdir()
                restored = operation / "restored"
                resident_snapshot.copy_tree(self.root, restored, recorded, check)
                if mismatch:
                    (restored / domain["guard"]).write_text(json.dumps({**self.head, "revision": 6}), encoding="utf-8")
                with patch.object(postgres_backend, "Tools", NativeTools):
                    if mismatch:
                        with self.assertRaisesRegex(RecoveryError, "actor_memory_guard_mismatch"):
                            postgres_backend.export_and_verify(self.root, operation, recorded, None, {}, check,
                                actor_memory=domain, restored_root=restored)
                    else:
                        exported, guards = postgres_backend.export_and_verify(self.root, operation, recorded, None, {}, check,
                            actor_memory=domain, restored_root=restored)
                        self.assertEqual(exported[0]["service"], "actor-memory")
                        self.assertEqual(guards, {domain["guard"]})
        self.assertTrue(any(database.startswith("ts_restore_") and ".actor_metadata" in sql for database, sql in queries))
        self.assertEqual(sum(sql.startswith("CREATE DATABASE") for sql in statements), 2)
        self.assertEqual(sum(sql.startswith("DROP DATABASE") for sql in statements), 2)
        self.assertFalse((self.base / "pg-mismatch/postgresql/manifest.json").exists())
        user_binding = self.data / ".tianshu-storage-memory.json"
        user_binding.write_text(json.dumps({**self.state, "service": "memory"}), encoding="utf-8")
        recorded = resident_snapshot.inventory(self.root, check)
        with self.assertRaisesRegex(RecoveryError, "actor_memory_user_domain_overlap"):
            postgres_backend.export_and_verify(self.root, self.base / "alias", recorded, None, {}, check, actor_memory=domain)
        user_binding.write_text(json.dumps({**self.state, "service": "memory", "namespace": "other",
            "installation_id": uuid4().hex, "databases": {"main": "synthetic_user"}}), encoding="utf-8")
        recorded = resident_snapshot.inventory(self.root, check)
        with self.assertRaisesRegex(RecoveryError, "actor_memory_binding_mismatch"):
            postgres_backend.export_and_verify(self.root, self.base / "namespace", recorded, None, {}, check, actor_memory=domain)


@unittest.skipUnless(
    PG_URL and PG_BIN, "isolated real PostgreSQL and client tools required"
)
class PostgresSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="storage-native-backup-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self.database = self.root / "data/memory/memory.sqlite"
        self.database.parent.mkdir(parents=True)
        self.guard = self.database.with_name(self.database.name + ".source-guard.json")
        self.head = {
            "schema": 3,
            "instance": "synthetic-" + uuid4().hex,
            "revision": 1,
            "recovery": "verified",
        }
        with closing(sqlite3.connect(self.database)) as source:
            source.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
            source.executemany(
                "INSERT INTO metadata VALUES (?,?)",
                [
                    ("schema", "3"),
                    ("source_instance", self.head["instance"]),
                    ("source_revision", "1"),
                    ("source_recovery", "verified"),
                ],
            )
            source.commit()
        self.options = StorageOptions(
            postgresql_url=PG_URL, namespace="cold_backup_" + uuid4().hex[:12]
        )
        self.runtime = StorageRuntime(
            self.options,
            service="memory",
            state_directory=str(self.database) + ".storage",
        )
        self.addCleanup(self.runtime.close)

        def prepare(connection, source):
            connection.native("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
            connection.executemany(
                "INSERT INTO metadata VALUES (?,?)",
                [tuple(row) for row in source.execute("SELECT * FROM metadata")],
            )
            connection.native(
                "CREATE TABLE facts(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,body TEXT,blob BYTEA,tokens BIGINT)"
            )
            connection.native(
                "INSERT INTO facts(body,blob,tokens) VALUES(?,?,?)",
                ("记忆与资料", b"\x00\xfffile", 10**12),
            )

        self.handle = self.runtime.database("main", self.database, prepare=prepare)
        with closing(self.handle.connect()) as connection, connection:
            connection.execute(
                "UPDATE metadata SET value='2' WHERE key='source_revision'"
            )
        self.runtime.finalize()
        self.head["revision"] = 2
        self.guard.write_text(json.dumps(self.head), encoding="utf-8")
        self.files = FileStore(str(self.database) + ".files")
        self.reference = self.files.put_text("原件与完整正文保留文件，不进数据库。")
        self.rows = owners()
        suffix = ".exe" if os.name == "nt" else ""
        self.tools = {
            name: str(Path(PG_BIN) / (name + suffix))
            for name in ("psql", "pg_dump", "pg_restore")
        }

    def snapshot(self, name, **options):
        connection = {"postgresql_url": PG_URL}
        if os.environ.get("TIANSHU_TEST_POSTGRES_MAINTENANCE_URL"):
            connection["maintenance_url_env"] = "TIANSHU_TEST_POSTGRES_MAINTENANCE_URL"
        return resident_snapshot.snapshot(
            self.root,
            self.base / name,
            self.rows,
            lambda: self.rows,
            memory_guards=[self.guard.relative_to(self.root).as_posix()],
            postgresql={"memory": connection},
            postgresql_tools=self.tools,
            **options,
        )

    def test_real_dump_restore_preserves_guard_and_files_without_using_old_sqlite_revision(
        self,
    ):
        self.runtime.close()
        result = self.snapshot("operation")
        self.assertEqual(result["postgresql_databases_verified"], 1)
        self.assertEqual(result["memory_guards_verified"], 1)
        self.assertEqual(result["sqlite_databases_verified"], 1)
        manifest = json.loads((self.base / "operation/manifest.json").read_bytes())
        pg = manifest["postgresql"][0]
        self.assertTrue(pg["native_restore_verified"] and pg["scratch_removed"])
        self.assertNotIn(PG_URL, json.dumps(manifest))
        with closing(sqlite3.connect(self.database)) as old:
            self.assertEqual(
                old.execute(
                    "SELECT value FROM metadata WHERE key='source_revision'"
                ).fetchone()[0],
                "1",
            )
        restored_files = FileStore(
            self.base
            / "operation/restored-disabled"
            / (self.database.relative_to(self.root).as_posix() + ".files")
        )
        self.assertEqual(
            restored_files.read_text(self.reference),
            self.files.read_text(self.reference),
        )
        resident_retention.verify_kept_copies(
            self.base / "operation", manifest, resident_snapshot
        )
        prepared = self.base / "prepared-disabled"
        report = postgres_backend.prepare_restored_files(
            self.base / "operation", prepared
        )
        self.assertFalse(report["database_restore_or_activation"])
        binding = self.runtime.state_path.relative_to(self.root)
        source_state = json.loads(self.runtime.state_path.read_bytes())
        new_state = json.loads((prepared / binding).read_bytes())
        self.assertNotEqual(new_state["epoch"], source_state["epoch"])
        self.assertEqual(new_state["installation_id"], source_state["installation_id"])
        prepared_files = FileStore(
            prepared / (self.database.relative_to(self.root).as_posix() + ".files")
        )
        self.assertEqual(
            prepared_files.read_text(self.reference),
            self.files.read_text(self.reference),
        )
        with self.assertRaisesRegex(RecoveryError, "live_mount_overlap"):
            postgres_backend.prepare_restored_files(
                self.base / "operation", self.root / "bad-restore"
            )
        archive = self.base / "operation" / pg["archive"]
        archive.write_bytes(b"corrupt native archive")
        with self.assertRaisesRegex(RuntimeError, "postgresql_changed"):
            resident_retention.verify_kept_copies(
                self.base / "operation", manifest, resident_snapshot
            )

    def test_active_pg_cannot_claim_a_sqlite_only_backup_without_connection_inputs(
        self,
    ):
        self.runtime.close()
        with self.assertRaisesRegex(RecoveryError, "backup_configuration_required"):
            resident_snapshot.snapshot(
                self.root,
                self.base / "missing",
                self.rows,
                lambda: self.rows,
                postgresql_tools=self.tools,
            )
        self.assertFalse((self.base / "missing/receipt.json").exists())

    def test_guard_mismatch_stops_before_any_verified_receipt(self):
        self.runtime.close()
        self.head["revision"] = 1
        self.guard.write_text(json.dumps(self.head), encoding="utf-8")
        with self.assertRaisesRegex(RecoveryError, "postgresql_memory_guard_mismatch"):
            self.snapshot("wrong-guard")
        self.assertFalse((self.base / "wrong-guard/receipt.json").exists())

    def test_current_private_deployment_config_is_discovered_for_native_backup(self):
        self.runtime.close()
        config = self.root / "config/memory/settings.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"storage_backends": {"postgresql_url": PG_URL}}),
            encoding="utf-8",
        )
        result = resident_snapshot.snapshot(
            self.root,
            self.base / "discovered",
            self.rows,
            lambda: self.rows,
            memory_guards=[self.guard.relative_to(self.root).as_posix()],
            postgresql_tools=self.tools,
        )
        self.assertEqual(result["postgresql_databases_verified"], 1)

    def test_external_knowledge_files_are_copied_from_the_registered_service_mount(
        self,
    ):
        self.runtime.close()
        external = self.base / "external-knowledge"
        files = FileStore(external)
        reference = files.put_text("外置资料原件必须能随数据库恢复。")
        config = self.root / "config/memory/settings.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"knowledge_files_directory": "/knowledge-files"}),
            encoding="utf-8",
        )
        owner = next(row for row in self.rows if row["service"] == "memory")
        owner["mounts"].append(
            {
                "Type": "bind",
                "Source": str(external),
                "Destination": "/knowledge-files",
                "RW": True,
            }
        )
        self.snapshot(
            "external",
            external_data_binds={
                "memory": [
                    {
                        "source": str(external),
                        "target": "/knowledge-files",
                        "read_only": False,
                    }
                ]
            },
        )
        manifest = json.loads((self.base / "external/manifest.json").read_bytes())
        self.assertIn(str(external), manifest["external_controls"])
        copied = FileStore(self.base / "external/controls-backup/0")
        self.assertEqual(copied.read_text(reference), files.read_text(reference))
        owner["mounts"].clear()
        with self.assertRaisesRegex(RecoveryError, "knowledge_files_mount_missing"):
            self.snapshot("unmounted")

    def test_latest_verified_native_archive_is_retained_before_old_archive_pruning(
        self,
    ):
        self.runtime.close()
        updates = self.base / "updates"
        for row in self.rows:
            row["mounts"] = [
                {
                    "Type": "bind",
                    "Source": str(self.root / "data" / row["service"]),
                    "Destination": "/data",
                    "RW": True,
                }
            ]
        old, new = updates / "old/cold-snapshot", updates / "new/cold-snapshot"
        for path in (old, new):
            path.parent.mkdir(parents=True)
            self.snapshot(path.relative_to(self.base).as_posix())
        rows = [
            resident_retention.read_evidence(path, self.root) for path in (old, new)
        ]
        plan = resident_retention.make_plan(
            updates_root=updates.as_posix(),
            authority_root=self.root.as_posix(),
            keep=new.as_posix(),
            snapshots=rows,
            containers=[],
        )
        old_plan = next(
            row for row in plan["snapshots"] if row["path"] == old.as_posix()
        )
        new_plan = next(
            row for row in plan["snapshots"] if row["path"] == new.as_posix()
        )
        self.assertIn("postgresql", old_plan["remove"])
        self.assertNotIn("postgresql", new_plan["remove"])
        resident_retention.apply_plan(
            plan, snapshot=resident_snapshot, containers=[], execute=True
        )
        self.assertFalse((old / "postgresql").exists())
        self.assertTrue((new / "postgresql/0.dump").is_file())

    def test_real_actor_dump_restore_keeps_separate_identity_and_guard(self):
        actor = self.database.parent / "actor.sqlite"
        guard = actor.with_name("actor-checkpoint.json")
        head = {"domain": "actor-memory", "schema": 2, "instance": uuid4().hex,
                "revision": 3, "recovery": uuid4().hex}
        runtime = StorageRuntime(self.options, service="actor-memory", state_directory=str(actor) + ".storage")
        self.addCleanup(runtime.close)

        def prepare(connection, source):
            self.assertIsNone(source)
            connection.native("CREATE TABLE actor_metadata(key TEXT PRIMARY KEY,value TEXT)")
            connection.executemany("INSERT INTO actor_metadata VALUES (?,?)",
                [(key, str(head[key])) for key in ("schema", "instance", "revision", "recovery")])

        runtime.database("main", actor, prepare=prepare)
        runtime.finalize()
        guard.write_text(json.dumps(head), encoding="utf-8")
        runtime.close()
        self.runtime.close()
        config = self.root / "config/memory/settings.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"database_path": "/memory/memory.sqlite",
            "storage_backends": {"postgresql_url": PG_URL},
            "actor_memory": {"database_path": "/memory/actor.sqlite", "guard_path": "/memory/actor-checkpoint.json"}}), encoding="utf-8")
        next(row for row in self.rows if row["service"] == "memory")["mounts"] = [
            {"Type": "bind", "Source": str(self.database.parent), "Destination": "/memory", "RW": True}]
        result = self.snapshot("actor-operation")
        self.assertEqual(result["postgresql_databases_verified"], 2)
        self.assertEqual(result["memory_guards_verified"], 2)
        manifest = json.loads((self.base / "actor-operation/manifest.json").read_bytes())
        rows = {row["service"]: row for row in manifest["postgresql"]}
        self.assertNotEqual(rows["actor-memory"]["installation_id"], rows["memory"]["installation_id"])
        self.assertNotEqual(rows["actor-memory"]["schemas"]["main"], rows["memory"]["schemas"]["main"])
        self.assertEqual(rows["actor-memory"]["memory_guards"], [guard.relative_to(self.root).as_posix()])
        target = self.base / "actor-prepared-disabled"
        postgres_backend.prepare_restored_files(self.base / "actor-operation", target)
        self.assertEqual((target / guard.relative_to(self.root)).read_bytes(), guard.read_bytes())
        saved = json.loads(runtime.state_path.read_bytes())
        restored = json.loads((target / runtime.state_path.relative_to(self.root)).read_bytes())
        self.assertEqual(saved["installation_id"], restored["installation_id"])
        self.assertNotEqual(saved["epoch"], restored["epoch"])


if __name__ == "__main__":
    unittest.main()
