"""Export declared node code from Git objects into a deterministic source ZIP.

This follows the existing deployment source-export pattern: explicit immutable
Git inputs, a new output, and file hashes. It does not install or start anything.
The four-product release manifest is deliberately not an input to this package.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import stat
import subprocess
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROTOCOL = "infrastructure-node.v1"
CONTRACT = "contracts/infrastructure-node/candidate-v1"
MANIFEST = "package-manifest.json"
SOURCE_FILES = tuple("ops/infrastructure/" + name for name in (
    "__init__.py", "__main__.py", "compose.py", "config.py", "docker.py",
    "executor.py", "linux.py", "paging.py", "protocol.py", "sampling.py",
    "transport.py", "requirements.txt", "README.md",
))
CONTRACT_FILES = tuple(CONTRACT + "/" + name for name in (
    "README.md", "request.schema.json", "response.schema.json", "config.schema.json",
    "examples/config.example.json",
))
ASSETS = {
    "run_node.py": "launcher.py",
    "requirements.lock": "requirements.lock",
    "RUNNING.md": "README.md",
    "deployment/Dockerfile.example": "templates/Dockerfile.example",
    "deployment/compose.fragment.json": "templates/compose.fragment.json",
    "deployment/platform-settings.fragment.json": "templates/platform-settings.fragment.json",
    "examples/executor-empty.json": "templates/executor-empty.json",
}
EXTRA_FILES = frozenset({"ops/__init__.py", *ASSETS})
RUNTIME = {
    "python_minimum": [3, 11],
    "entrypoint": "run_node.py",
    "dependencies": [{"name": "PyYAML", "version": "6.0.3"}],
    "requirements": "requirements.lock",
    "ssh": "deployment_supplied_openssh",
}
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024


class Refused(ValueError):
    """A packaging error code, never raw subprocess output or local secrets."""


def require(condition, code):
    if not condition:
        raise Refused(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()


def _git(repository, *args, payload=None):
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository), *args], input=payload,
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("git_unavailable") from None
    require(result.returncode == 0, "git_snapshot_unavailable")
    return result.stdout


def git_files(repository, commit, names):
    """Read literal committed blobs, unaffected by index, worktree or attributes."""
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit), "full_commit_required")
    actual = _git(repository, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
    require(actual == commit, "source_commit_mismatch")
    raw = _git(repository, "ls-tree", "-rz", commit, "--", *names)
    entries = {}
    for line in raw.split(b"\0"):
        if not line:
            continue
        header, path = line.split(b"\t", 1)
        mode, kind, object_id = header.decode("ascii").split()
        name = path.decode("utf-8")
        require(mode in {"100644", "100755"} and kind == "blob", "source_regular_files_required")
        entries[name] = object_id
    require(set(entries) == set(names), "snapshot_files_missing")
    ordered = sorted(entries)
    batch = "".join(entries[name] + "\n" for name in ordered).encode()
    stream = io.BytesIO(_git(repository, "cat-file", "--batch", payload=batch))
    result = {}
    for name in ordered:
        header = stream.readline().decode("ascii").strip().split()
        require(len(header) == 3 and header[:2] == [entries[name], "blob"], "git_blob_invalid")
        size = int(header[2])
        require(0 <= size <= MAX_FILE_BYTES, "source_file_too_large")
        result[name] = stream.read(size)
        require(len(result[name]) == size and stream.read(1) == b"\n", "git_blob_invalid")
    require(stream.read() == b"", "git_blob_invalid")
    return result


def _versions(files):
    try:
        protocol = ast.parse(files["ops/infrastructure/protocol.py"].decode("utf-8"))
        versions = [
            item.value.value for item in protocol.body
            if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
            and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in item.targets)
        ]
        require(versions == [PROTOCOL], "protocol_version_mismatch")
        for name in ("request.schema.json", "response.schema.json", "config.schema.json"):
            schema = json.loads(files[CONTRACT + "/" + name])
            require(schema["properties"]["version"]["const"] == PROTOCOL, "contract_version_mismatch")
        requirements = [
            line.strip() for line in files["ops/infrastructure/requirements.txt"].decode().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        # The matching executor currently declares exactly one real parser.
        # A dependency change belongs to a reviewed new package, not a silent
        # installation of unpinned additions from a mutable source directory.
        require(requirements == ["PyYAML==6.0.3"], "runtime_dependency_lock_mismatch")
    except Refused:
        raise
    except (ValueError, SyntaxError, KeyError, TypeError, UnicodeError):
        raise Refused("snapshot_metadata_invalid") from None


def build(repository, commit, output):
    output = Path(output)
    require(output.is_absolute() and output.parent.is_dir(), "absolute_output_parent_required")
    require(not output.exists(), "new_output_required")
    files = git_files(repository, commit, (*SOURCE_FILES, *CONTRACT_FILES))
    _versions(files)
    origins = {name: "source_git" for name in files}
    files["ops/__init__.py"] = b'"""Packaged TianShu operations namespace."""\n'
    origins["ops/__init__.py"] = "package"
    for name, asset in ASSETS.items():
        # Text assets are canonical even in a Windows CRLF checkout.
        files[name] = (HERE / asset).read_bytes().replace(b"\r\n", b"\n")
        origins[name] = "package"
    manifest = {
        "format_version": 1,
        "package": "tianshu-infrastructure-node",
        "source_commit": commit,
        "protocol_version": PROTOCOL,
        "contract": {
            "path": CONTRACT,
            "source_commit": commit,
            "files_sha256": digest(json_bytes({name: digest(files[name]) for name in sorted(CONTRACT_FILES)})),
        },
        "runtime": RUNTIME,
        "files": {
            name: {"sha256": digest(raw), "bytes": len(raw), "mode": "0644", "origin": origins[name]}
            for name, raw in sorted(files.items())
        },
    }
    files[MANIFEST] = json_bytes(manifest)
    write_archive(output, files)
    return verify(output)


def write_archive(output, files):
    """Shared deterministic ZIP writer for observation and control source packages."""
    output = Path(output)
    require(output.is_absolute() and output.parent.is_dir(), "absolute_output_parent_required")
    require(not output.exists(), "new_output_required")
    require(sum(len(raw) for raw in files.values()) <= MAX_PACKAGE_BYTES, "package_too_large")
    try:
        with output.open("xb") as stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, raw in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, raw)
    except OSError:
        raise Refused("package_write_failed") from None


def read_archive(path, expected):
    """Validate exact declared members and manifest hashes before returning bytes."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        require(set(names) == expected and len(names) == len(expected), "package_file_list_mismatch")
        require(sum(item.file_size for item in entries) <= MAX_PACKAGE_BYTES, "package_too_large")
        for item in entries:
            require(
                item.file_size <= MAX_FILE_BYTES and item.compress_type == zipfile.ZIP_STORED
                and item.create_system == 3 and stat.S_ISREG(item.external_attr >> 16),
                "package_member_invalid",
            )
        manifest = json.loads(archive.read(MANIFEST))
        require(set(manifest["files"]) == expected - {MANIFEST}, "manifest_file_list_mismatch")
        files = {name: archive.read(name) for name in names}
        for name, entry in manifest["files"].items():
            raw = files[name]
            require(len(raw) == entry["bytes"] and digest(raw) == entry["sha256"], "package_hash_mismatch")
    return manifest, files


def verify(path):
    try:
        expected = {*SOURCE_FILES, *CONTRACT_FILES, *EXTRA_FILES, MANIFEST}
        manifest, files = read_archive(path, expected)
        require(
            manifest["format_version"] == 1 and manifest["protocol_version"] == PROTOCOL
            and manifest["package"] == "tianshu-infrastructure-node" and manifest["runtime"] == RUNTIME,
            "package_version_mismatch",
        )
        require(re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"]), "full_commit_required")
        _versions(files)
        contract_hash = digest(json_bytes({name: digest(files[name]) for name in sorted(CONTRACT_FILES)}))
        require(
            manifest["contract"] == {"path": CONTRACT, "source_commit": manifest["source_commit"], "files_sha256": contract_hash},
            "contract_hash_mismatch",
        )
    except Refused:
        raise
    except (OSError, zipfile.BadZipFile, ValueError, KeyError, TypeError, UnicodeError):
        raise Refused("package_invalid") from None
    return {
        "status": "package_verified",
        "source_commit": manifest["source_commit"],
        "protocol_version": PROTOCOL,
        "package_sha256": digest(Path(path).read_bytes()),
        "file_count": len(files),
        "deployed": False,
    }
