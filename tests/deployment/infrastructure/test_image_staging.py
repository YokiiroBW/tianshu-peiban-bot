"""Execute the real stage payload against local synthetic build inputs."""

import ast
import base64
import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from deploy.infrastructure import image


class ImageStagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.remote_root = "/volume2/tianshu-v2-resident-updates"
        self.stage = self.base / "synthetic-build"
        self.config = {"stage": self.remote_root + "/synthetic-build"}
        self.files = {
            "build-inputs.json": (json.dumps(self.config).encode(), 0o644),
            "build-controller.py": (b"# fixed synthetic controller\n", 0o644),
            "contracts/example/v1/schema.json": (b"{}\n", 0o644),
            "services/platform/__init__.py": (b"# fixed platform\n", 0o644),
            "apps/web/main.js": (b"// fixed frontend\n", 0o644),
            "tools/music/run.sh": (b"#!/bin/sh\n", 0o755),
            "scripts/build/music_tools.py": (b"# fixed music build\n", 0o755),
            "node-package/ops/infrastructure/__main__.py": (b"# fixed node\n", 0o644),
        }
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, (raw, mode) in self.files.items():
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(raw), mode
                archive.addfile(member, io.BytesIO(raw))
        packed = buffer.getvalue()
        self.mkdirs, self.chmods = [], []
        local_root, remote_root = self.base, self.remote_root
        mkdirs, chmods = self.mkdirs, self.chmods

        class StagedPath(type(Path())):
            def __init__(self, *values):
                if len(values) == 1:
                    text = str(values[0])
                    if text == remote_root:
                        values = (local_root,)
                    elif text.startswith(remote_root + "/"):
                        values = (local_root / text[len(remote_root) + 1:],)
                super().__init__(*values)

            def mkdir(self, mode=0o777, parents=False, exist_ok=False):
                mkdirs.append((str(self), mode))
                return super().mkdir(mode=mode, parents=parents, exist_ok=exist_ok)

            def chmod(self, mode, **kwargs):
                chmods.append((str(self), mode))
                return super().chmod(mode, **kwargs)

        # Replace only the path constructor so the unchanged NAS-root restriction
        # targets this disposable local directory. The actual extraction runs.
        tree = ast.parse(image.BUILD_ACTIONS["stage"])
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or node.module != "pathlib"]
        self.code = compile(tree, "remote-build-stage", "exec")
        self.environment = {
            "CONFIG": self.config, "Path": StagedPath,
            "DATA": base64.b64encode(packed).decode(),
            "ARCHIVE_SHA256": hashlib.sha256(packed).hexdigest(),
            "CONTROLLER_SHA256": hashlib.sha256(self.files["build-controller.py"][0]).hexdigest(),
        }

    def execute(self):
        # The production payload deliberately keeps umask 077 for the process.
        # Restore it here because the tests share a process with other tests.
        previous = os.umask(0o077)
        try:
            with redirect_stdout(io.StringIO()) as output:
                exec(self.code, self.environment)
            return json.loads(output.getvalue())
        finally:
            os.umask(previous)

    def test_all_copy_source_directories_are_traversable_but_stage_stays_private(self):
        result = self.execute()
        self.assertEqual(result["status"], "staged")
        self.assertIn((str(self.stage), 0o700), self.mkdirs)
        self.assertNotIn(str(self.stage), {path for path, _ in self.chmods})
        directories = {parent for name in self.files for parent in (self.stage / name).parents if parent != self.stage and self.stage in parent.parents}
        for directory in directories:
            self.assertIn((str(directory), 0o755), self.mkdirs)
            self.assertIn((str(directory), 0o755), self.chmods)
        for name, (raw, mode) in self.files.items():
            self.assertEqual((self.stage / name).read_bytes(), raw)
            self.assertIn((str(self.stage / name), mode), self.chmods)
        # Windows does not model POSIX access bits; explicit chmod calls above
        # are still checked there. On POSIX also verify the actual resulting modes.
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.stage.stat().st_mode), 0o700)
            for directory in directories:
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
            for name, (_, mode) in self.files.items():
                self.assertEqual(stat.S_IMODE((self.stage / name).stat().st_mode), mode)

    def test_existing_stage_is_refused_without_replacing_files_or_permissions(self):
        self.execute()
        calls_before = list(self.mkdirs), list(self.chmods)
        with self.assertRaisesRegex(AssertionError, "candidate stage exists"):
            self.execute()
        self.assertEqual((self.mkdirs, self.chmods), calls_before)
        for name, (raw, _) in self.files.items():
            self.assertEqual((self.stage / name).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
