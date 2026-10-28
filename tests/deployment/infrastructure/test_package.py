"""Isolated Git/package/process tests; never read real node credentials."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile
from pathlib import Path

from deploy.infrastructure import bundle


FIXTURE_MAIN = '''"""Test-only JSON process, not the TS-176 executor."""
import argparse, json, sys
from .protocol import VERSION

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    with open(args.config, encoding='utf-8') as stream:
        config = json.load(stream)
    request = json.load(sys.stdin)
    print(json.dumps({
        'version': VERSION, 'request_id': request['request_id'],
        'node_id': request['node_id'], 'operation': request['operation'],
        'status': 'ok', 'data': {'nodes': sorted(config['nodes'])},
    }))
    return 0
'''


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "source"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic package test")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.autocrlf", "false")
        for name in bundle.SOURCE_FILES:
            self.put(name, '"""Synthetic source fixture."""\n')
        self.put("ops/infrastructure/protocol.py", f'VERSION = "{bundle.PROTOCOL}"\n')
        self.put("ops/infrastructure/__main__.py", FIXTURE_MAIN)
        self.put("ops/infrastructure/requirements.txt", "# Pinned synthetic input\nPyYAML==6.0.3\n")
        for name in bundle.CONTRACT_FILES:
            if name.endswith(".schema.json"):
                value = {"properties": {"version": {"const": bundle.PROTOCOL}}}
                self.put(name, json.dumps(value))
            elif name.endswith("config.example.json"):
                self.put(name, json.dumps({"version": bundle.PROTOCOL, "nodes": {}}))
            else:
                self.put(name, "Synthetic contract documentation.\n")
        self.put(".gitattributes", "ops/infrastructure/protocol.py export-ignore\n")
        for name in (
            "ops/infrastructure/executor-config.json", "ops/infrastructure/known_hosts",
            "ops/infrastructure/id_ed25519", "ops/infrastructure/state/sample.json",
            "tests/infrastructure/case.json", "projects/foreign/settings.json",
            bundle.CONTRACT + "/examples/snapshot.response.json",
        ):
            self.put(name, "SYNTHETIC_PRIVATE_CONTENT_MUST_NOT_BE_PACKAGED\n")
        self.commit = self.save("fixed source input")

    def git(self, *args, payload=None):
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-C", str(self.repo), *args],
            input=payload, capture_output=True, timeout=15, check=True,
        ).stdout

    def put(self, name, value):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8", newline="\n")

    def save(self, message):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD").decode().strip()

    def build(self, name="node.zip", commit=None):
        path = self.base / name
        result = bundle.build(self.repo, commit or self.commit, path)
        return path, result

    def unpack(self, path):
        target = self.base / "unpacked"
        with zipfile.ZipFile(path) as archive:
            archive.extractall(target)
        return target

    def request(self):
        return {
            "version": bundle.PROTOCOL, "request_id": "package:empty",
            "node_id": None, "operation": "node.catalog", "params": {}, "timeout_ms": 1000,
        }

    def process(self, python, root, *args):
        outside = self.base / "unrelated-working-directory"
        outside.mkdir(exist_ok=True)
        return subprocess.run(
            [str(python), "-I", "-B", str(root / "run_node.py"), *args],
            input=(json.dumps(self.request()) + "\n").encode(), cwd=outside,
            capture_output=True, timeout=10, check=False,
        )

    def test_fixed_git_input_ignores_dirty_index_worktree_and_export_attributes(self):
        first, initial = self.build("first.zip")
        self.put("ops/infrastructure/protocol.py", 'VERSION = "dirty"\n')
        self.git("add", "ops/infrastructure/protocol.py")
        self.put("ops/infrastructure/executor.py", 'raise RuntimeError("uncommitted")\n')
        self.put("ops/infrastructure/new-secret.json", "another private fixture")
        second, repeated = self.build("second.zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(initial, repeated)
        self.assertEqual(initial["source_commit"], self.commit)
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(archive.read("ops/infrastructure/protocol.py"), f'VERSION = "{bundle.PROTOCOL}"\n'.encode())

    def test_manifest_hashes_cover_exact_declared_files_and_exclude_private_inputs(self):
        path, result = self.build()
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(bundle.MANIFEST))
            self.assertEqual(result["file_count"], len(manifest["files"]) + 1)
            self.assertEqual(set(archive.namelist()), set(manifest["files"]) | {bundle.MANIFEST})
            for name, metadata in manifest["files"].items():
                raw = archive.read(name)
                self.assertEqual(metadata["sha256"], bundle.digest(raw))
                self.assertEqual(metadata["bytes"], len(raw))
                self.assertNotIn(b"SYNTHETIC_PRIVATE_CONTENT_MUST_NOT_BE_PACKAGED", raw)
            self.assertEqual(manifest["contract"]["source_commit"], self.commit)
            self.assertEqual(manifest["runtime"]["dependencies"], [{"name": "PyYAML", "version": "6.0.3"}])
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist()))

    def test_local_git_replace_ref_cannot_change_the_fixed_commit(self):
        first, original = self.build("original.zip")
        self.put("ops/infrastructure/protocol.py", 'VERSION = "replacement"\n')
        replacement = self.save("replacement source")
        self.git("replace", self.commit, replacement)
        second, repeated = self.build("with-replace-ref.zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(original, repeated)

    def test_git_refs_and_abbreviated_or_unavailable_commits_are_rejected(self):
        for value, code in (("HEAD", "full_commit_required"), (self.commit[:12], "full_commit_required"), ("f" * 40, "git_snapshot_unavailable")):
            with self.subTest(commit=value):
                with self.assertRaisesRegex(bundle.Refused, code):
                    self.build(commit=value)
        self.assertFalse((self.base / "node.zip").exists())

    def test_missing_declared_code_is_not_replaced_from_worktree(self):
        self.git("rm", "--quiet", "ops/infrastructure/transport.py")
        changed = self.save("missing runtime module")
        self.put("ops/infrastructure/transport.py", "working tree cannot repair the fixed commit")
        with self.assertRaisesRegex(bundle.Refused, "snapshot_files_missing"):
            self.build(commit=changed)

    def test_git_symlink_mode_cannot_leak_local_file_contents(self):
        blob = self.git("hash-object", "-w", "--stdin", payload=b"../../secret-local-file").decode().strip()
        self.git("update-index", "--cacheinfo", f"120000,{blob},ops/infrastructure/transport.py")
        self.git("commit", "--quiet", "-m", "synthetic link entry")
        changed = self.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(bundle.Refused, "source_regular_files_required"):
            self.build(commit=changed)

    def test_protocol_change_cannot_be_mislabeled_as_original_contract(self):
        self.put("ops/infrastructure/protocol.py", 'VERSION = "infrastructure-node.v2"\n')
        changed = self.save("different executor protocol")
        with self.assertRaisesRegex(bundle.Refused, "protocol_version_mismatch"):
            self.build(commit=changed)

    def test_contract_change_cannot_be_mislabeled_as_original_protocol(self):
        self.put(bundle.CONTRACT + "/response.schema.json", json.dumps({"properties": {"version": {"const": "different"}}}))
        changed = self.save("different response contract")
        with self.assertRaisesRegex(bundle.Refused, "contract_version_mismatch"):
            self.build(commit=changed)

    def test_unpinned_runtime_dependency_cannot_silently_change_package(self):
        self.put("ops/infrastructure/requirements.txt", "PyYAML>=6\n")
        changed = self.save("unlocked dependency")
        with self.assertRaisesRegex(bundle.Refused, "runtime_dependency_lock_mismatch"):
            self.build(commit=changed)

    def test_existing_output_is_preserved(self):
        path = self.base / "node.zip"
        path.write_bytes(b"existing package")
        with self.assertRaisesRegex(bundle.Refused, "new_output_required"):
            self.build()
        self.assertEqual(path.read_bytes(), b"existing package")

    def test_changed_archive_payload_fails_manifest_verification(self):
        path, _ = self.build()
        changed = self.base / "tampered.zip"
        with zipfile.ZipFile(path) as original, zipfile.ZipFile(changed, "w") as archive:
            for info in original.infolist():
                raw = original.read(info.filename)
                archive.writestr(info, raw + b"changed" if info.filename == "ops/infrastructure/linux.py" else raw)
        with self.assertRaisesRegex(bundle.Refused, "package_hash_mismatch"):
            bundle.verify(changed)

    def test_declared_git_fixture_runs_with_isolated_python_outside_package(self):
        path, _ = self.build()
        root = self.unpack(path)
        config = self.base / "explicit-config.json"
        config.write_text(json.dumps({"nodes": {}, "state_dir": str(self.base / "explicit-state")}), encoding="utf-8")
        result = self.process(sys.executable, root, "--config", str(config))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        response = json.loads(result.stdout)
        self.assertEqual(response["version"], bundle.PROTOCOL)
        self.assertEqual(response["request_id"], self.request()["request_id"])
        self.assertEqual(response["operation"], "node.catalog")
        self.assertIsNone(response["node_id"])
        self.assertEqual(response["data"], {"nodes": []})

    def test_missing_dependency_reports_exact_requirement_without_importing_executor(self):
        path, _ = self.build()
        root = self.unpack(path)
        empty = self.base / "without-parser"
        venv.EnvBuilder(with_pip=False).create(empty)
        python = empty / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        result = self.process(python, root, "--config", str(self.base / "not-read.json"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"infrastructure_dependency_unavailable", result.stderr)
        self.assertIn(b"PyYAML==6.0.3", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        metadata = self.process(python, root, "--version")
        self.assertEqual(metadata.returncode, 0)
        self.assertEqual(json.loads(metadata.stdout)["source_commit"], self.commit)


if __name__ == "__main__":
    unittest.main()
