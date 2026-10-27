"""Immutable source export and one portable, verified distribution environment."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from deploy.single_container.build import application_files, archive
from deploy.single_container import dependencies
from deploy.single_container.dependencies import PRODUCTS, lock, sha256, wheel_metadata
from deploy.single_container.image import COMPONENTS, dockerfile


def release():
    return {
        "version": "synthetic-release",
        "images": {
            name: "sha256:" + str(index) * 64
            for index, name in enumerate(COMPONENTS, 1)
        },
    }


def wheel(directory, name, *, extra=None, requires=(), version="0.1.0"):
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    path = Path(directory) / filename
    folder = filename.removesuffix("-py3-none-any.whl") + ".dist-info/"
    metadata = f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\nRequires-Python: >=3.12\n"
    if extra:
        metadata += "Provides-Extra: " + extra + "\n"
    for requirement in requires:
        metadata += "Requires-Dist: " + requirement + "\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(folder + "METADATA", metadata)
        archive.writestr(folder + "WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
    return path


class DistributionTests(unittest.TestCase):
    def test_archive_uses_exact_tracked_commit_and_preserves_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(repo), *args], text=True
                ).strip()

            git("init", "--quiet")
            (repo / "module.py").write_text("ORIGINAL = True\n", encoding="utf-8")
            git("add", "module.py")
            git(
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "fixture",
            )
            commit = git("rev-parse", "HEAD")
            (repo / "module.py").write_text("DIRTY = True\n", encoding="utf-8")
            (repo / "private.env").write_text(
                "SYNTHETIC=must-not-export\n", encoding="utf-8"
            )
            before = git("status", "--porcelain")
            receipt = archive({"repo": str(repo), "commit": commit}, root / "exported")
            self.assertEqual(receipt["commit"], commit)
            self.assertEqual(
                (root / "exported/module.py").read_text(), "ORIGINAL = True\n"
            )
            self.assertFalse((root / "exported/private.env").exists())
            self.assertEqual(git("status", "--porcelain"), before)
            with self.assertRaisesRegex(ValueError, "exact_source_commit"):
                archive({"repo": str(repo), "commit": "HEAD"}, root / "refused")

    def test_runtime_export_preserves_static_observability_schema_without_private_inputs(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            files = {
                "deploy/single_container/runtime.py": "runtime",
                "deploy/single_container/process.py": "process",
                "deploy/single_container/health.py": "health",
                "deploy/single_container/host.py": "host",
                "deploy/single_container/applications.py": "apps",
                "deploy/single_container/threaded_exchange.py": "threads",
                "deploy/single_container/host_settings.py": "settings",
                "deploy/single_container/edge.py": "edge",
                "deploy/tianshu/runtime_guard.py": "guard",
                "deploy/observability/guard.py": "observer",
                "deploy/observability/runtime-identity.schema.json": "{}",
                "deploy/observability/private.env": "SYNTHETIC=excluded",
                ".runtime/database.sqlite": "excluded",
            }
            for name, body in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
            result = application_files(source, output)
            self.assertIn("deploy/observability/runtime-identity.schema.json", result)
            self.assertFalse((output / "deploy/observability/private.env").exists())
            self.assertFalse((output / ".runtime").exists())
            (source / "deploy/single_container/host.py").unlink()
            with self.assertRaisesRegex(ValueError, "runtime_source_incomplete"):
                application_files(source, root / "incomplete")

    def test_image_has_five_pinned_inputs_one_clean_environment_and_original_runtime(
        self,
    ):
        definition = dockerfile(release())
        self.assertEqual(
            sum(line.startswith("FROM ") for line in definition.splitlines()), 5
        )
        self.assertIn("rm -rf /opt/tianshu-venv", definition)
        self.assertIn(
            "--no-index --find-links=./wheels --only-binary=:all: --require-hashes",
            definition,
        )
        self.assertIn("-m pip check", definition)
        self.assertIn('"deploy.single_container.runtime"', definition)
        self.assertNotIn("/opt/tianshu/modules", definition)
        self.assertNotIn("site-packages", definition)
        self.assertIn("/usr/share/grafana/ /usr/share/grafana/", definition)
        changed = release()
        changed["images"]["onebot"] = "sha256:" + "a" * 64
        with self.assertRaisesRegex(ValueError, "image_set"):
            dockerfile(changed)
        changed = release()
        changed["images"]["platform"] = "platform:latest"
        with self.assertRaisesRegex(ValueError, "immutable_image"):
            dockerfile(changed)

    def test_wheel_metadata_keeps_actual_extra_and_dependency_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            requirement = 'nonebot2[fastapi]==2.5.0; extra == "nonebot"'
            path = wheel(
                directory, "tianshu-companion", extra="nonebot", requires=[requirement]
            )
            result = wheel_metadata(path)
            self.assertEqual(result["requires_dist"], [requirement])
            self.assertEqual(result["provides_extra"], ["nonebot"])
            self.assertEqual(
                result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
            )

    def test_lock_output_is_relative_hashed_and_linux_download_does_not_resolve_windows(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "wheels").mkdir()
            wheels = {
                name: wheel(
                    root / "wheels",
                    package,
                    extra="nonebot" if name == "companion" else None,
                )
                for name, package in PRODUCTS.items()
            }
            wheels["storage"] = wheel(
                root / "wheels", "tianshu-storage", version="0.1.3"
            )
            commands = []

            def resolver(argv, *, cwd):
                commands.append([str(value) for value in argv])
                if "compile" in argv:
                    (root / "runtime.lock").write_text("", encoding="utf-8")

            with patch.object(dependencies, "run", resolver):
                constraints = root / "validated.constraints"
                constraints.write_text("starlette==1.6.0\n", encoding="utf-8")
                result = lock(
                    wheels,
                    root,
                    uv="explicit-uv",
                    python="explicit-python",
                    constraints=constraints,
                )
            raw = (root / "runtime.lock").read_text()
            for path in wheels.values():
                self.assertIn("./wheels/" + path.name, raw)
                self.assertIn("--hash=sha256:" + sha256(path), raw)
            self.assertIn(".whl[nonebot]", raw)
            self.assertNotIn(str(root), raw)
            self.assertIn("x86_64-manylinux_2_36", commands[0])
            self.assertIn("--generate-hashes", commands[0])
            self.assertIn("--constraint", commands[0])
            self.assertIn("constraints.txt", commands[0])
            self.assertNotIn(str(constraints), commands[0])
            self.assertEqual(
                result["constraints_sha256"], sha256(root / "constraints.txt")
            )
            self.assertIn("# constraints-sha256: " + result["constraints_sha256"], raw)
            self.assertIn("--no-deps", commands[1])
            self.assertIn("--require-hashes", commands[1])
            self.assertIn("manylinux_2_36_x86_64", commands[1])
            self.assertEqual(len(result["wheels"]), 5)
            self.assertEqual(result["products"]["storage"]["version"], "0.1.3")

    @unittest.skipUnless(
        os.environ.get("TS345_DISTRIBUTION_CONTEXT"), "explicit built context required"
    )
    def test_actual_cross_linux_context_is_portable_complete_and_hash_verified(self):
        root = Path(os.environ["TS345_DISTRIBUTION_CONTEXT"])
        receipt = json.loads((root / "release.json").read_bytes())
        metadata = json.loads((root / "dependencies.json").read_bytes())
        self.assertEqual(set(receipt["sources"]), {*PRODUCTS, "application"})
        self.assertEqual(len(metadata["products"]), 5)
        self.assertEqual(metadata["lock_sha256"], sha256(root / "runtime.lock"))
        raw = (root / "runtime.lock").read_text()
        self.assertNotIn("file:", raw)
        self.assertNotIn("C:/", raw)
        self.assertNotIn("C:\\", raw)
        names = []
        for info in metadata["wheels"]:
            self.assertEqual(info["sha256"], sha256(root / "wheels" / info["file"]))
            names.append(info["name"])
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("nonebot2", names)
        self.assertIn("nonebot-adapter-onebot", names)
        self.assertEqual(names.count("tianshu-memory"), 1)
        self.assertEqual(names.count("tianshu-storage"), 1)
        self.assertIn("av", names)
        self.assertIn("quickjs", names)
        with zipfile.ZipFile(
            root / "wheels" / metadata["products"]["companion"]["file"]
        ) as artifact:
            self.assertIn("tianshu_nonebot/onebot.py", artifact.namelist())
        with zipfile.ZipFile(
            root / "wheels" / metadata["products"]["memory"]["file"]
        ) as artifact:
            self.assertTrue(any("knowledge" in name for name in artifact.namelist()))


if __name__ == "__main__":
    unittest.main()
