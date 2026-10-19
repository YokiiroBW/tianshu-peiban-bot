"""Renderer input: control installation after the resident's verified cold backup."""

import base64
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import uuid
import zipfile
from pathlib import Path


def control_require(condition, code):
    if not condition:
        raise ValueError(code)


def control_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()


def control_path(path):
    path = Path(path)
    control_require(path.is_absolute() and ".." not in path.parts
                    and not any(parent.is_symlink() for parent in (path, *path.parents)), "control_install_path_invalid")
    return path


def control_sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def control_private_directory(path, uid):
    path = control_path(path)
    if path.exists():
        info = path.stat()
        control_require(path.is_dir() and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700,
                        "control_directory_owner_or_mode_invalid")
        return path
    control_require(path.parent.is_dir(), "control_directory_parent_missing")
    path.mkdir(mode=0o700)
    os.chown(path, uid, uid)
    path.chmod(0o700)
    control_sync_directory(path.parent)
    return path


def control_configuration(path, value, uid):
    path = control_path(path)
    raw = control_bytes(value)
    if path.exists():
        info = path.stat()
        control_require(path.is_file() and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o600,
                        "control_config_owner_or_mode_invalid")
        control_require(json.loads(path.read_bytes()) == value, "control_existing_config_changed")
        return hashlib.sha256(path.read_bytes()).hexdigest()
    control_require(path.parent.is_dir(), "control_config_parent_missing")
    temporary = path.with_name(".control-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, uid)
        temporary.chmod(0o600)
        os.link(temporary, path)
        control_sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(raw).hexdigest()


def control_payload(value):
    raw = base64.b64decode(value["package_base64"], validate=True)
    control_require(hashlib.sha256(raw).hexdigest() == value["package"]["package_sha256"], "control_package_changed")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("package-manifest.json"))
        expected = set(manifest["files"]) | {"package-manifest.json"}
        names = archive.namelist()
        control_require(set(names) == expected and len(names) == len(expected), "control_package_members_changed")
        control_require(manifest["package"] == "tianshu-infrastructure-control"
                        and manifest["source_commit"] == value["package"]["source_commit"]
                        and manifest["contract"] == value["package"]["contract"], "control_package_metadata_changed")
        files = {}
        for entry in archive.infolist():
            relative = Path(entry.filename)
            control_require(not relative.is_absolute() and ".." not in relative.parts
                            and stat.S_ISREG(entry.external_attr >> 16), "control_package_member_invalid")
            content = archive.read(entry)
            if entry.filename != "package-manifest.json":
                metadata = manifest["files"][entry.filename]
                control_require(len(content) == metadata["bytes"]
                                and hashlib.sha256(content).hexdigest() == metadata["sha256"], "control_package_file_changed")
            files[entry.filename] = content
    wheel = value["wheel"]
    locked = re.findall(rb"--hash=sha256:([0-9a-f]{64})", files["requirements.lock"])
    control_require("/" not in wheel["filename"] and "\\" not in wheel["filename"]
                    and wheel["filename"].lower().startswith("pyyaml-6.0.3-") and wheel["filename"].endswith(".whl")
                    and wheel["sha256"].encode() in locked
                    and hashlib.sha256(base64.b64decode(wheel["base64"], validate=True)).hexdigest() == wheel["sha256"],
                    "control_wheel_lock_mismatch")
    return files


def check_control_install(value):
    """Read-only preflight; no state, config or code directory is created here."""
    if not value:
        return
    config = value["configuration"]
    files = control_payload(value)
    target = control_path(value["code_directory"])
    parent = control_path(config["remote_code_root"])
    control_require(parent.parent.is_dir() and (not parent.exists() or parent.is_dir()), "control_code_parent_missing")
    if target.exists():
        control_require(target.is_dir(), "control_code_directory_invalid")
        actual = {str(path.relative_to(target)).replace("\\", "/") for path in target.rglob("*") if path.is_file()}
        control_require(actual == set(files), "control_installed_file_list_changed")
        for name, content in files.items():
            path = control_path(target / name)
            info = path.stat()
            control_require(path.read_bytes() == content and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o644,
                            "control_installed_source_changed")
    for key, field, uid in (("remote_config_path", "remote_config", 0), ("relay_config_path", "relay_config", 10001)):
        path = control_path(config[key])
        control_require(path.parent.is_dir(), "control_config_parent_missing")
        if path.exists():
            info = path.stat()
            control_require(path.is_file() and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o600
                            and json.loads(path.read_bytes()) == config[field], "control_existing_config_changed")
    for path, uid in ((config["remote_config"]["state_dir"], 0), (value["relay_state_host"], 10001)):
        path = control_path(path)
        control_require(path.parent.is_dir(), "control_state_parent_missing")
        if path.exists():
            info = path.stat()
            control_require(path.is_dir() and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700,
                            "control_directory_owner_or_mode_invalid")


def install_control(value):
    """Idempotently install immutable code; retain every existing config/journal file."""
    if not value:
        return None
    check_control_install(value)
    config = value["configuration"]
    files = control_payload(value)
    root, target = Path(config["remote_code_root"]), Path(value["code_directory"])
    if not root.exists():
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        os.chown(root, 0, 0)
    if not target.exists():
        temporary = root / (".install-" + uuid.uuid4().hex)
        temporary.mkdir(mode=0o755)
        try:
            for name, content in files.items():
                path = temporary / name
                path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                path.chmod(0o644)
                os.chown(path, 0, 0)
            for directory in [temporary, *(path for path in temporary.rglob("*") if path.is_dir())]:
                directory.chmod(0o755)
                os.chown(directory, 0, 0)
                control_sync_directory(directory)
            os.rename(temporary, target)
            control_sync_directory(root)
        except Exception:
            # Keep a failed private staging tree for inspection; never remove an
            # installed version or walk a registered state/data directory.
            raise
    runtime = control_path(config["remote_runtime_directory"])
    python = runtime / "bin/python"
    if not runtime.exists():
        wheel = value["wheel"]
        wheelhouse = root / ("wheels-" + wheel["sha256"])
        control_private_directory(wheelhouse, 0)
        wheel_path = wheelhouse / wheel["filename"]
        raw = base64.b64decode(wheel["base64"], validate=True)
        if wheel_path.exists():
            control_require(wheel_path.is_file() and not wheel_path.is_symlink() and wheel_path.read_bytes() == raw,
                            "control_wheel_changed")
        else:
            with wheel_path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            wheel_path.chmod(0o600)
        process = subprocess.run([config["remote_python"], "-I", "-B", "-m", "venv", str(runtime)], capture_output=True, timeout=90)
        control_require(process.returncode == 0, "control_runtime_creation_failed")
        process = subprocess.run([str(python), "-I", "-m", "pip", "install", "--no-cache-dir", "--no-index", "--find-links", str(wheelhouse),
                                  "--only-binary=:all:", "--no-deps", "--require-hashes", "-r", str(target / "requirements.lock")],
                                 capture_output=True, timeout=90)
        control_require(process.returncode == 0, "control_runtime_dependency_failed")
    control_require(runtime.is_dir() and runtime.stat().st_uid == 0, "control_runtime_owner_invalid")
    process = subprocess.run([str(python), "-I", "-m", "pip", "check"], capture_output=True, timeout=20)
    control_require(process.returncode == 0, "control_runtime_dependency_failed")
    remote_sha = control_configuration(config["remote_config_path"], config["remote_config"], 0)
    relay_sha = control_configuration(config["relay_config_path"], config["relay_config"], 10001)
    control_private_directory(config["remote_config"]["state_dir"], 0)
    control_private_directory(value["relay_state_host"], 10001)
    # Validate the real fixed node config and persistence checks with the exact
    # registered NAS interpreter. No Docker/Compose command is issued here.
    program = ("import sys;sys.path.insert(0," + repr(str(target)) + ");"
               "from ops.infrastructure.control.config import load_remote;"
               "from ops.infrastructure.control.state import checked_directory;"
               "c=load_remote(" + repr(config["remote_config_path"]) + ");"
               "checked_directory(c.state_dir);"
               "import importlib.metadata;assert importlib.metadata.version('PyYAML')=='6.0.3'")
    process = subprocess.run([str(python), "-I", "-B", "-c", program], capture_output=True, timeout=20)
    control_require(process.returncode == 0, "control_remote_runtime_unavailable")
    return {"status": "installed", "source_commit": value["package"]["source_commit"],
            "package_sha256": value["package"]["package_sha256"], "remote_config_sha256": remote_sha,
            "relay_config_sha256": relay_sha, "persistent_journals_preserved": True,
            "dockge_recreated": False, "new_permissions_granted": False}
