"""Immutable source export and one portable, verified distribution environment."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from deploy.single_container import build, dependencies
from deploy.single_container.build import application_files, archive
from deploy.single_container.dependencies import PRODUCTS, lock, sha256, wheel_metadata
from deploy.single_container.image import (
    COMPONENTS,
    base_references,
    dockerfile,
    install_comic_aliases,
    verify_local_bases,
)


def release():
    return {
        "version": "synthetic-release",
        "images": {
            name: "sha256:" + str(index) * 64
            for index, name in enumerate(COMPONENTS, 1)
        },
        "base_references": {
            name: f"tianshu-ts348-base-{name}:synthetic" for name in COMPONENTS
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
    def test_comic_alias_conflict_never_overwrites_existing_media_or_creates_other_alias(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "ComicPublisher"
            existing.mkdir()
            (existing / "untouched").write_bytes(b"original")
            with self.assertRaisesRegex(ValueError, "comic_alias_conflict"):
                install_comic_aliases(root)
            self.assertEqual((existing / "untouched").read_bytes(), b"original")
            self.assertFalse((root / "ComicInputs").exists())
            self.assertFalse((root / "ComicInputs").is_symlink())

    def test_comic_aliases_keep_old_socket_paths_and_refuse_different_existing_links(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            try:
                install_comic_aliases(root)
            except OSError as error:
                if os.name == "nt" and error.winerror == 1314:
                    self.skipTest(
                        "Creating symlinks requires an unavailable Windows privilege"
                    )
                raise
            self.assertEqual(
                (root / "ComicInputs").readlink().as_posix(),
                "/run/tianshu/comics/inputs",
            )
            self.assertEqual(
                (root / "ComicPublisher").readlink().as_posix(),
                "/run/tianshu/comics/publisher",
            )
            install_comic_aliases(root)
            (root / "ComicPublisher").unlink()
            (root / "ComicPublisher").symlink_to(
                "/somewhere-else", target_is_directory=True
            )
            with self.assertRaisesRegex(ValueError, "comic_alias_conflict"):
                install_comic_aliases(root)
            self.assertEqual(
                (root / "ComicPublisher").readlink().as_posix(), "/somewhere-else"
            )

    def test_prepare_keeps_runtime_constraints_after_temporary_build_backend_exits(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_constraints = root / "runtime.constraints"
            runtime_constraints.write_text("synthetic-runtime==1\n", encoding="utf-8")
            value = release()
            source = {"repo": "synthetic-unused", "commit": "a" * 40}
            value.update(
                sources={name: dict(source) for name in PRODUCTS}, application=source
            )
            output = root / "output"
            storage_bytes = wheel(root, "tianshu-storage", version="0.1.3").read_bytes()

            def export(source, destination):
                destination.mkdir()
                (destination / "vendor").mkdir()
                (destination / "vendor" / dependencies.STORAGE_WHEEL).write_bytes(
                    storage_bytes
                )
                return {"commit": source["commit"], "archive_sha256": "b" * 64}

            def build_wheel(argv, *, cwd):
                backend = argv[argv.index("--build-constraints") + 1]
                self.assertTrue(backend.is_file())
                self.assertNotEqual(backend, runtime_constraints)
                name = argv[-1].name
                wheel(
                    output / "wheels",
                    PRODUCTS[name],
                    extra="nonebot" if name == "companion" else None,
                )

            def resolve(wheels, target, *, uv, python, constraints):
                self.assertEqual(constraints, runtime_constraints)
                self.assertTrue(constraints.is_file())
                (target / "dependencies.json").write_text("{}")
                (target / "runtime.lock").write_text("synthetic")
                return {
                    "lock_sha256": "c" * 64,
                    "constraints_sha256": sha256(constraints),
                }

            with (
                patch.object(build, "archive", export),
                patch.object(build, "run", build_wheel),
                patch.object(build, "application_files", return_value={}),
                patch.object(build, "lock", resolve),
                patch.object(
                    build.subprocess, "check_output", return_value="uv synthetic"
                ),
            ):
                result = build.prepare(value, output, constraints=runtime_constraints)
            self.assertEqual(result["constraints_sha256"], sha256(runtime_constraints))
            self.assertEqual(result["base_references"], value["base_references"])

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
                "deploy/observability/clear_retention.py": "clear",
                "deploy/observability/logql_filter.py": "filter",
                "deploy/observability/source_capacity.py": "capacity",
                "deploy/observability/source_segments.py": "segments",
                "deploy/observability/vector_checkpoints.py": "checkpoints",
                "deploy/observability/vector_source_retention.py": "retention",
                "deploy/observability/monitor.py": "retired",
                "deploy/observability/ledger_schema.py": "retired",
                "deploy/observability/source_retention.py": "retired",
                "deploy/observability/retained_query.py": "retired",
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
            for name in ("monitor.py", "ledger_schema.py", "source_retention.py", "retained_query.py"):
                self.assertFalse((output / "deploy/observability" / name).exists())
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
        self.assertIn(
            "COPY runtime.lock dependencies.json /opt/tianshu/distribution/", definition
        )
        self.assertLess(
            definition.index("-m pip check"), definition.index("COPY release.json")
        )
        self.assertLess(
            definition.index("COPY deploy/"), definition.index("COPY release.json")
        )
        metadata_changed = release()
        metadata_changed["version"] = "synthetic-next-metadata"
        self.assertEqual(
            definition.split("COPY deploy/")[0],
            dockerfile(metadata_changed).split("COPY deploy/")[0],
        )
        self.assertIn('"deploy.single_container.runtime"', definition)
        self.assertNotIn("/opt/tianshu/modules", definition)
        self.assertNotIn("site-packages", definition)
        self.assertNotIn("# syntax=", definition)
        self.assertNotIn("FROM --platform=linux/amd64 sha256:", definition)
        self.assertIn("tianshu-ts348-base-platform:synthetic AS runtime", definition)
        self.assertIn("/usr/share/grafana/ /usr/share/grafana/", definition)
        self.assertIn(
            'PATH="/opt/tianshu-venv/bin:/usr/share/grafana/bin:$PATH"', definition
        )
        self.assertLess(
            definition.index("ENV PYTHONDONTWRITEBYTECODE"),
            definition.index("RUN /usr/local/bin/vector --version"),
        )
        self.assertIn("&& grafana --version", definition)
        self.assertIn("install_comic_aliases; install_comic_aliases()", definition)
        self.assertLess(
            definition.index("COPY deploy/"),
            definition.index("RUN /usr/local/bin/python -c"),
        )
        changed = release()
        changed["images"]["onebot"] = "sha256:" + "a" * 64
        with self.assertRaisesRegex(ValueError, "image_set"):
            dockerfile(changed)
        changed = release()
        changed["images"]["platform"] = "platform:latest"
        with self.assertRaisesRegex(ValueError, "immutable_image"):
            dockerfile(changed)

    def test_config_ids_need_named_references_and_registry_pins_remain_unchanged(self):
        value = release()
        del value["base_references"]
        with self.assertRaisesRegex(ValueError, "named_reference_required"):
            dockerfile(value)
        for reference in (
            "sha256:" + "a" * 64,
            "missing-tag",
            "tag:bad value",
            "tag:bad\nvalue",
            "${UNREVIEWED}:tag",
        ):
            value = release()
            value["base_references"]["platform"] = reference
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                base_references(value)
        value = release()
        value.pop("base_references")
        value["images"] = {
            name: f"registry.example/tianshu/{name}@sha256:" + "a" * 64
            for name in COMPONENTS
        }
        self.assertEqual(base_references(value), value["images"])
        self.assertIn(value["images"]["platform"], dockerfile(value))

    def test_local_base_verification_reads_only_identity_and_detects_changed_tags(self):
        value = release()
        calls = []

        def inspect(argv, *, text):
            calls.append(argv)
            name = next(
                name
                for name, reference in value["base_references"].items()
                if reference == argv[-1]
            )
            return json.dumps(
                {"id": value["images"][name], "os": "linux", "architecture": "amd64"}
            )

        with patch(
            "deploy.single_container.image.subprocess.check_output", side_effect=inspect
        ):
            result = verify_local_bases(value, docker="explicit-docker")
        self.assertEqual(set(result["bases"]), set(COMPONENTS))
        self.assertEqual(len(calls), 5)
        self.assertTrue(
            all(
                argv[:4] == ["explicit-docker", "image", "inspect", "--format"]
                for argv in calls
            )
        )
        self.assertTrue(
            all(".Env" not in argv[4] and ".Config" not in argv[4] for argv in calls)
        )
        for metadata, expected_error in (
            (
                {"id": "sha256:" + "f" * 64, "os": "linux", "architecture": "amd64"},
                "identity_mismatch",
            ),
            (
                {
                    "id": value["images"]["platform"],
                    "os": "linux",
                    "architecture": "arm64",
                },
                "platform_mismatch",
            ),
        ):
            with (
                self.subTest(metadata=metadata),
                patch(
                    "deploy.single_container.image.subprocess.check_output",
                    return_value=json.dumps(metadata),
                ),
                self.assertRaisesRegex(ValueError, expected_error),
            ):
                verify_local_bases(value)

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
