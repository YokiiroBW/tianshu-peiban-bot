"""Exercise shared retention through real recovery inputs and release callers."""

import ast
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deploy.infrastructure import recovery_input, resident
from ops.recovery import resident_retention as retention, resident_snapshot as snapshot
from tests.deployment.recovery.test_resident_snapshot import owners


class RetentionWiringTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="resident-wiring-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "resident"
        self.root.mkdir()
        (self.root / "config").mkdir()
        with closing(sqlite3.connect(self.root / "state.sqlite")) as database:
            database.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY)")
            database.commit()
        self.rows = owners()
        for row in self.rows:
            row["mounts"] = [{"Type": "bind", "Source": (self.root / "config").as_posix(), "RW": True}]
        self.updates = self.base / "resident-updates"
        self.updates.mkdir()

    def test_actual_rollback_carries_old_guard_before_automatic_pruning(self):
        tooling = self.base / "tooling"
        guard = tooling / "ops/resident_capacity"
        guard.mkdir(parents=True)
        (guard / "guard.py").write_bytes(b"# synthetic old rollback guard\n")
        original = self.base / "temporary-old"
        snapshot.snapshot(self.root, original, self.rows, lambda: self.rows, controls=[guard])
        old = self.updates / "old" / "cold-snapshot"
        old.parent.mkdir()
        original.rename(old)
        (guard / "guard.py").write_bytes(b"# synthetic current candidate guard\n")
        expectation = {"controls": [str(guard)], "memory_guards": [], "media_staging_roots": []}
        (old.parent / "cold-expectation.json").write_text(json.dumps(expectation))
        paths = {}
        for name in ("core", "platform", "obs"):
            paths[name] = self.base / (name + ".json")
            paths[name].write_text("{}")
        capacity, unit, state = self.base / "capacity.json", self.base / "unit", self.base / "state"
        capacity.write_text("{}"); unit.write_text("synthetic unit"); state.mkdir()
        worker = ast.parse(Path(resident.__file__).with_name("resident_worker.py").read_text(encoding="utf-8"))
        function = next(node for node in worker.body if isinstance(node, ast.FunctionDef) and node.name == "rollback_release")
        starts = next(index for index, node in enumerate(function.body) if isinstance(node, ast.Assign)
                      and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "original_controls")
        ends = next(index for index, node in enumerate(function.body) if isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "user_state")

        def save(path, value):
            with Path(path).open("x") as stream:
                json.dump(value, stream)

        original_snapshot = snapshot.snapshot
        stop = Mock()
        scope = {"parent": old.parent, "read": lambda path: json.loads(Path(path).read_bytes()), "save": save,
                 "TOOLING": tooling, "paths": paths, "CAP": capacity, "UNIT": unit,
                 "cap": {"state_dir": str(state), "external_media_binds": []},
                 "cold_external_media_binds": [],
                 "copy": copy, "uuid": uuid, "Path": Path, "original": {"previous_images": {"platform": {}}},
                 "rows": self.rows, "R": self.root, "D": "synthetic", "stop_managed": stop,
                 "snapshot": snapshot, "require": snapshot.require}
        with patch.object(retention, "docker_references", return_value=[]), \
                patch.object(snapshot, "inspect_owners", return_value=self.rows):
            try:
                retention.install_snapshot_retention(snapshot, "synthetic")
                exec(compile(ast.Module(body=function.body[starts:ends], type_ignores=[]), "real-rollback-control-handoff", "exec"), scope)
            finally:
                snapshot.snapshot = original_snapshot
        stop.assert_called_once()
        self.assertFalse((old / "controls-backup").exists())
        self.assertTrue((old / "manifest.json").is_file())
        carried = scope["guard_backup"] / "guard.py"
        self.assertEqual(b"# synthetic old rollback guard\n", carried.read_bytes())
        self.assertTrue(carried.is_relative_to(scope["S"] / "cold-snapshot/controls-backup"))
        current_expectation = json.loads((scope["S"] / "cold-expectation.json").read_bytes())
        self.assertEqual(str(old / "controls-backup/0"), current_expectation["controls"][-1])

    def test_legacy_fixed_snapshot_direct_call_gets_one_shared_success_hook(self):
        calls = []

        def legacy(root, operation, expected, observe, *, controls=()):
            calls.append("backup_verified")
            return {"status": "offline_restore_verified"}

        def after(*args, **kwargs):
            calls.append("retention")
            return {"status": "applied"}

        module = SimpleNamespace(snapshot=legacy)
        scope = {"snapshot": module, "D": "synthetic"}
        with patch.object(retention, "after_verified_snapshot", side_effect=after):
            exec(resident.retention_runtime_source(install=True), scope)
            exec(resident.retention_runtime_source(install=True), scope)
            result = module.snapshot(self.root, self.base / "legacy", self.rows, lambda: self.rows, controls=[], docker="synthetic")
        self.assertEqual(["backup_verified", "retention"], calls)
        self.assertEqual("applied", result["retention"]["status"])
        failed = SimpleNamespace(snapshot=Mock(side_effect=RuntimeError("snapshot_failure")))
        with patch.object(retention, "after_verified_snapshot") as hook:
            retention.install_snapshot_retention(failed, "synthetic")
            with self.assertRaisesRegex(RuntimeError, "snapshot_failure"):
                failed.snapshot(self.root, self.base / "failed", self.rows, lambda: self.rows)
            hook.assert_not_called()

    def test_fixed_recovery_input_imports_and_runs_outside_source_repository(self):
        report = recovery_input.prepare(output=self.base / "fixed.json")
        exported = Path(report["files_directory"])
        control = self.base / "control.json"
        control.write_text("{}")
        expectation = self.base / "expectation.json"
        expectation.write_text(json.dumps({"root": str(self.root), "containers": self.rows,
                                          "controls": [str(control)], "memory_guards": []}))
        program = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from ops.recovery import resident_snapshot as snapshot, resident_retention as retention
expectation=json.loads(Path(sys.argv[2]).read_bytes())
result=snapshot.snapshot(Path(expectation['root']),Path(sys.argv[3]),expectation['containers'],lambda:expectation['containers'],controls=[Path(x) for x in expectation['controls']],docker='synthetic-unused')
assert result['status']=='offline_restore_verified'
assert result['retention']['status']=='outside_resident_release_namespace'
assert Path(retention.__file__).is_relative_to(Path(sys.argv[1]))
print(json.dumps({'status':result['status'],'policy_imported_from_fixed_input':True}))
raise SystemExit(snapshot.main(['--expectation',sys.argv[2],'--operation',sys.argv[3]+'-unused','--docker','synthetic-unused']))
'''
        process = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(exported), str(expectation),
                                  str(self.base / "package-outside-operation")], cwd=self.base,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(0, process.returncode, process.stderr)
        rows = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(["offline_restore_verified", "plan"], [row["status"] for row in rows])


if __name__ == "__main__":
    unittest.main()
