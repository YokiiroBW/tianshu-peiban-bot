"""Snapshot integrity boundaries using a disposable Git repository, not application fakes."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ts050_runner", ROOT / "scripts/integration/ts050.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class SnapshotGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="snapshot-test-", dir=RUNNER.RUNTIME)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        self.snapshot = self.root / "snapshots/fixture"
        self.repo.mkdir()
        self.snapshot.mkdir(parents=True)
        self.files = {
            "src/example/__init__.py": b"VALUE = 1\n",
            "README.md": b"Synthetic snapshot\n",
        }
        for name, content in self.files.items():
            for directory in (self.repo, self.snapshot):
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        self.git("init", "--quiet")
        self.git("-c", "core.autocrlf=false", "add", ".")
        self.git(
            "-c",
            "user.name=Codex",
            "-c",
            "user.email=codex@localhost",
            "commit",
            "--quiet",
            "-m",
            "Synthetic snapshot fixture",
        )
        self.pin = self.git("rev-parse", "HEAD")
        (self.snapshot / ".ts050-commit").write_text(self.pin, "utf-8")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def verify(self):
        RUNNER.verify_snapshots(
            {"fixture": self.repo}, snapshot_root=self.snapshot.parent, pins={"fixture": self.pin}
        )

    def test_git_blobs_and_exact_marker_are_accepted(self):
        self.verify()

    def test_extra_import_sources_bytecode_native_and_archive_are_rejected(self):
        for name in (
            "src/sitecustomize.py",
            "src/injected.pyc",
            "src/injected.pyd",
            "src/injected.so",
            "src/startup.pth",
            "src/injected.zip",
            ".ts050-commit.py",
            "unexpected.txt",
        ):
            with self.subTest(name):
                target = self.snapshot / name
                target.write_bytes(b"Synthetic unexpected file; never imported")
                try:
                    with self.assertRaisesRegex(SystemExit, "Unexpected snapshot entry"):
                        self.verify()
                finally:
                    target.unlink()
        self.verify()

    def test_changed_missing_blob_and_wrong_marker_are_rejected(self):
        target = self.snapshot / "src/example/__init__.py"
        target.write_bytes(b"VALUE = 2\n")
        with self.assertRaisesRegex(SystemExit, "Snapshot changed"):
            self.verify()
        target.unlink()
        with self.assertRaisesRegex(SystemExit, "Snapshot file missing"):
            self.verify()
        target.write_bytes(self.files["src/example/__init__.py"])
        marker = self.snapshot / ".ts050-commit"
        marker.write_text("incorrect-marker", "utf-8")
        with self.assertRaisesRegex(SystemExit, "Snapshot commit marker mismatch"):
            self.verify()
