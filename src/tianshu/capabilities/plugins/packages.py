"""Bounded package staging, immutable content and version-local dependencies."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import venv
import zipfile
from urllib.parse import urlsplit

from ..contracts import Fault, require
from .schema import protocol, secret_names

MAX_FILES, MAX_BYTES = 2048, 64 * 1024 * 1024
IGNORED = {".git", ".venv", ".environment", "venv", "node_modules", ".runtime", "__pycache__"}


def clean_environment():
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SYSTEMDRIVE",
        }
    }


def relative_path(value):
    require(
        isinstance(value, str)
        and value
        and "\\" not in value
        and not PurePosixPath(value).is_absolute()
        and all(part not in {".", ".."} and ":" not in part for part in value.split("/")),
        "invalid_input",
        400,
    )
    return PurePosixPath(value)


class Packages:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def reclaim(self, roots):
        """Remove immutable package directories no instance or rollback still references."""
        removed = []
        for root in roots:
            path = Path(root)
            require(
                path.is_absolute() and path.is_relative_to(self.root) and path.name != "",
                "invalid_input",
                400,
            )
            if path.exists():
                shutil.rmtree(path)
                removed.append(str(path))
        return removed

    def prepare(self, source):
        require(isinstance(source, dict), "invalid_input", 400)
        staging = Path(tempfile.mkdtemp(prefix="staging-", dir=self.root)).resolve()
        content = staging / "content"
        content.mkdir()
        try:
            kind = source.get("type")
            if kind == "directory":
                source_root = Path(source["path"]).resolve(strict=True)
                require(
                    source_root.is_dir() and not staging.is_relative_to(source_root),
                    "invalid_input",
                    400,
                )
                count, total = 0, 0
                for path in source_root.rglob("*"):
                    relative = path.relative_to(source_root)
                    if any(part in IGNORED for part in relative.parts):
                        continue
                    require(not path.is_symlink() and not path.is_junction(), "invalid_input", 400)
                    if path.is_file():
                        relative_path(relative.as_posix())
                        count, total = count + 1, total + path.stat().st_size
                        require(count <= MAX_FILES and total <= MAX_BYTES, "budget_exceeded", 413)
                        target = content / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(path, target)
            elif kind in {"zip", "git"}:
                if kind == "zip":
                    encoded = source["data"]
                    require(
                        isinstance(encoded, str) and len(encoded) <= 8 * 1024 * 1024,
                        "budget_exceeded",
                        413,
                    )
                    raw = base64.b64decode(encoded, validate=True)
                else:
                    repository, commit = source["url"], source["commit"]
                    require(
                        isinstance(repository, str)
                        and repository.startswith("https://")
                        and not urlsplit(repository).username
                        and not urlsplit(repository).password
                        and re.fullmatch(r"[0-9a-f]{40}", commit),
                        "invalid_input",
                        400,
                    )
                    checkout = staging / "git"
                    subprocess.run(
                        ["git", "init", "--quiet", str(checkout)],
                        check=True,
                        capture_output=True,
                        timeout=15,
                    )
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(checkout),
                            "fetch",
                            "--quiet",
                            "--depth=1",
                            repository,
                            commit,
                        ],
                        check=True,
                        capture_output=True,
                        timeout=90,
                        env=clean_environment(),
                    )
                    raw = subprocess.run(
                        ["git", "-C", str(checkout), "archive", "--format=zip", "FETCH_HEAD"],
                        check=True,
                        capture_output=True,
                        timeout=30,
                    ).stdout
                    require(len(raw) <= MAX_BYTES, "budget_exceeded", 413)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    files = archive.infolist()
                    require(
                        len(files) <= MAX_FILES
                        and sum(item.file_size for item in files) <= MAX_BYTES,
                        "budget_exceeded",
                        413,
                    )
                    for item in files:
                        name = item.filename.rstrip("/")
                        relative = relative_path(name)
                        require(not stat.S_ISLNK(item.external_attr >> 16), "invalid_input", 400)
                        target = content / str(relative)
                        if item.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with target.open("xb") as output:
                                output.write(archive.read(item))
                # Common repository ZIPs have exactly one enclosing directory.
                if not (content / "plugin.json").exists():
                    children = list(content.iterdir())
                    require(len(children) == 1 and children[0].is_dir(), "invalid_input", 400)
                    content = children[0]
            else:
                raise Fault("invalid_input", 400)
            manifest = json.loads((content / "plugin.json").read_text(encoding="utf-8"))
            protocol("plugin", manifest)

            def version(value):
                parts = value.split("-", 1)
                return (*map(int, parts[0].split(".")), len(parts) == 1)

            require(
                version(manifest["sdk"]["min"])
                <= version("2.0.0")
                < version(manifest["sdk"]["max_exclusive"]),
                "plugin_incompatible",
                409,
            )
            schema = {"type": "object", "properties": {}, "additionalProperties": False}
            if manifest.get("config_schema"):
                schema = json.loads(
                    (content / str(relative_path(manifest["config_schema"]))).read_text(
                        encoding="utf-8"
                    )
                )
            for field in secret_names(schema):
                require(schema["properties"][field].get("type") == "string", "invalid_input", 400)
            fingerprint = hashlib.sha256()
            for path in sorted(content.rglob("*")):
                if path.is_file():
                    fingerprint.update(path.relative_to(content).as_posix().encode() + b"\0")
                    fingerprint.update(path.read_bytes())
            checksum = fingerprint.hexdigest()
            target = self.root / checksum
            if not target.exists():
                shutil.copytree(content, target)
            python = (
                target
                / ".environment"
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            environment = python.parents[1]
            ready = environment / ".complete"
            complete = (
                ready.is_file()
                and python.is_file()
                and ready.read_text(encoding="ascii") == checksum
            )
            if not complete:
                if environment.exists():
                    resolved = environment.resolve()
                    require(
                        resolved.parent == target.resolve()
                        and resolved.is_relative_to(self.root)
                        and resolved.name == ".environment"
                        and not environment.is_symlink()
                        and not environment.is_junction(),
                        "invalid_input",
                        400,
                    )
                    shutil.rmtree(resolved)
                lock = manifest.get("dependency_lock")
                venv.EnvBuilder(with_pip=bool(lock)).create(environment)
                if lock:
                    lock_path = target / str(relative_path(lock))
                    subprocess.run(
                        [
                            str(python),
                            "-m",
                            "pip",
                            "install",
                            "--disable-pip-version-check",
                            "--require-hashes",
                            "--only-binary=:all:",
                            "-r",
                            str(lock_path),
                        ],
                        check=True,
                        capture_output=True,
                        timeout=180,
                        env=clean_environment(),
                    )
                ready.write_text(checksum, encoding="ascii")
            return {
                "id": manifest["id"] + "@" + manifest["version"],
                "manifest": manifest,
                "schema": schema,
                "digest": checksum,
                "root": str(target),
                "python": str(python),
                "source": {key: value for key, value in source.items() if key != "data"},
                "installed": True,
            }
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError):
            raise Fault("plugin_install_failed", 400) from None
        finally:
            # This directory was created here, and its resolved path is checked before removal.
            require(
                staging.parent == self.root and staging.name.startswith("staging-"),
                "invalid_input",
                400,
            )
            shutil.rmtree(staging)
