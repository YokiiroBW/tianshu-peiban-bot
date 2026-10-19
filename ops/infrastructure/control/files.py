"""Compose authority, input fingerprints, secret-preserving edits and versions."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..compose import FILENAMES, MAX_FILE, _YAML
from ..protocol import Failure
from ..sampling import stable_id

TOKEN = re.compile(r"\{\{tianshu\.secret:([a-f0-9]{32})\}\}")
REFERENCE = re.compile(r"^\$(?:\{[^{}]+\}|[A-Za-z_][A-Za-z0-9_]*)$")
URL_CREDENTIAL = re.compile(r"[a-z][a-z0-9+.-]*://[^/@\s]+:[^/@\s]+@", re.I)
UNSPECIFIED = object()
# These fields are meaningful for a deployment preview. Every other scalar is
# preserved through an opaque marker, including command/healthcheck/labels.
VISIBLE = {"image", "pull_policy", "name", "container_name", "ports", "volumes", "networks", "restart", "hostname", "working_dir", "user", "read_only", "privileged", "network_mode", "depends_on", "expose", "tmpfs", "target", "source", "type", "read_only", "protocol", "published", "mode", "external", "driver", "file", "env_file", "condition", "required", "init", "scale", "replicas", "cpus", "mem_limit", "mem_reservation", "pids_limit"}


def document(raw):
    if len(raw) > MAX_FILE:
        raise Failure("compose_file_too_large", "invalid")
    try:
        bounded = _YAML(raw)
        value = bounded.yaml.safe_load(raw)
        if not isinstance(value, dict) or not isinstance(value.get("services"), dict) or not value["services"]:
            raise ValueError("services required")
        return value
    except Failure:
        raise
    except (ValueError, TypeError, RecursionError):
        raise Failure("compose_invalid", "invalid") from None


def dump(value):
    import yaml
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode("utf-8")


def redact(raw, scope):
    value = document(raw)
    secrets = {}

    def visit(item, path, visible=False):
        if isinstance(item, dict):
            return {key: visit(child, path + (str(key),), str(key) in VISIBLE and not any(p in ("environment", "labels", "annotations", "driver_opts", "options", "args", "command", "entrypoint", "healthcheck") for p in path)) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child, path + (str(index),), visible) for index, child in enumerate(item)]
        sensitive = isinstance(item, str) and URL_CREDENTIAL.search(item)
        if not sensitive and (item is None or type(item) is bool or visible or isinstance(item, str) and REFERENCE.fullmatch(item)):
            return item
        marker = hashlib.sha256(json.dumps([scope, path, item], ensure_ascii=True, sort_keys=True).encode()).hexdigest()[:32]
        secrets[marker] = item
        return "{{tianshu.secret:" + marker + "}}"

    return dump(visit(value, ())).decode(), secrets


def unmask(content, secrets):
    value = document(content.encode())

    def visit(item):
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, str) and "{{tianshu.secret:" in item:
            match = TOKEN.fullmatch(item)
            if not match or match[1] not in secrets:
                raise Failure("secret_reference_invalid", "conflict")
            return secrets[match[1]]
        return item

    return dump(visit(value))


def normalize_binds(raw, directory):
    """Resolve relative bind paths to daemon-host paths, never Dockge /opt paths."""
    value = document(raw)
    for service in value["services"].values():
        if not isinstance(service, dict):
            raise Failure("compose_invalid", "invalid")
        volumes = service.get("volumes", [])
        if not isinstance(volumes, list):
            raise Failure("compose_invalid", "invalid")
        for index, volume in enumerate(volumes):
            if isinstance(volume, str):
                parts = volume.split(":")
                if len(parts) > 1 and parts[0].startswith(("./", "../")):
                    parts[0] = str((directory / parts[0]).resolve())
                    volumes[index] = ":".join(parts)
            elif isinstance(volume, dict) and volume.get("type") == "bind":
                source = volume.get("source")
                if isinstance(source, str) and source.startswith(("./", "../")):
                    volume["source"] = str((directory / source).resolve())
    return dump(value)


def no_symlinks(path, *, allow_missing=False):
    absolute = path.absolute()
    if absolute != path or path.resolve(strict=False) != path:
        raise Failure("stack_path_invalid", "conflict")
    for part in (path, *path.parents):
        try:
            if stat.S_ISLNK(part.lstat().st_mode):
                raise Failure("stack_path_invalid", "conflict")
        except FileNotFoundError:
            if not allow_missing:
                raise Failure("stack_path_unavailable") from None


def stat_fields(info):
    return {"device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode), "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def read_file(path, *, optional=False):
    no_symlinks(path, allow_missing=optional)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise Failure("compose_input_not_regular")
            raw = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
        if len(raw) > MAX_FILE:
            raise Failure("compose_input_too_large")
        if stat_fields(before) != stat_fields(after) or path.stat().st_ino != before.st_ino:
            raise Failure("compose_input_changed", "conflict")
        return {**stat_fields(before), "sha256": hashlib.sha256(raw).hexdigest(), "content": base64.b64encode(raw).decode()}
    except FileNotFoundError:
        if optional:
            return None
        raise Failure("compose_input_unavailable") from None
    except OSError:
        raise Failure("compose_input_unavailable") from None


def content(record):
    return base64.b64decode(record["content"])


def relative_file(directory, name):
    if not isinstance(name, str) or not name or "\x00" in name or "${" in name or "$" in name:
        raise Failure("compose_reference_unresolved", "unsupported")
    path = directory / name
    if not path.is_absolute() or not path.is_relative_to(directory) or ".." in Path(name).parts or Path(name).is_absolute():
        raise Failure("compose_reference_outside_stack", "unsupported")
    no_symlinks(path)
    return path


def references(value):
    result = {".env"}
    if any(key in value for key in ("include", "extends")):
        raise Failure("compose_reference_unsupported", "unsupported")
    for service in value["services"].values():
        if not isinstance(service, dict):
            raise Failure("compose_invalid", "invalid")
        if any(key in service for key in ("build", "extends")):
            raise Failure("compose_build_or_extends_unsupported", "unsupported")
        files = service.get("env_file", [])
        files = [files] if isinstance(files, (str, dict)) else files
        if not isinstance(files, list):
            raise Failure("compose_reference_unsupported", "unsupported")
        for entry in files:
            name = entry.get("path") if isinstance(entry, dict) else entry
            if not isinstance(name, str):
                raise Failure("compose_reference_unsupported", "unsupported")
            result.add(name)
    for kind in ("secrets", "configs"):
        entries = value.get(kind, {})
        if not isinstance(entries, dict):
            raise Failure("compose_reference_unsupported", "unsupported")
        for resource in entries.values():
            if isinstance(resource, dict) and "file" in resource:
                if not isinstance(resource["file"], str):
                    raise Failure("compose_reference_unsupported", "unsupported")
                result.add(resource["file"])
    if len(result) > 128:
        raise Failure("compose_reference_limit", "unsupported")
    return result


def fingerprint(snapshot):
    selected = {"directory": snapshot["directory"], "files": {name: None if item is None else {k:v for k,v in item.items() if k != "content"} for name,item in snapshot["files"].items()}}
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class StackFiles:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def discover(self):
        root = self.config.stacks_root
        no_symlinks(root)
        if not root.is_dir():
            raise Failure("stacks_root_unavailable")
        try:
            names = sorted(entry.name for entry in root.iterdir() if entry.name != ".tianshu-control-execution" and (entry.is_dir() or entry.is_symlink()))
        except OSError:
            raise Failure("stack_discovery_unavailable") from None
        if len(names) > 10000:
            raise Failure("control_inventory_limit_exceeded")
        return {stable_id("stack", self.config.node_id, name): name for name in names if any((root / name / filename).exists() or (root / name / filename).is_symlink() for filename in FILENAMES)}

    def directory(self, stack_id):
        name = self.discover().get(stack_id)
        if name is None:
            raise Failure("stack_not_found", "conflict")
        path = self.config.stacks_root / name
        no_symlinks(path)
        return path

    def context(self, directory):
        return (self.config.compose_path_root or self.config.stacks_root) / directory.name

    def owner(self, directory):
        owned = self.store.read("ownership", str(directory))
        return "platform" if directory.name in self.config.platform_stacks or owned and owned.get("directory_identity") == self.identity(directory) else "external"

    @staticmethod
    def identity(directory):
        no_symlinks(directory)
        info = directory.stat()
        return {key: value for key, value in stat_fields(info).items() if key not in ("size", "mtime_ns")}

    def snapshot(self, directory, *, proposed=None):
        no_symlinks(directory)
        filenames = [name for name in FILENAMES if (directory / name).exists() or (directory / name).is_symlink()]
        if len(filenames) != 1:
            raise Failure("compose_multiple_files" if filenames else "compose_file_unavailable", "unsupported")
        filename = filenames[0]
        primary = read_file(directory / filename)
        value = document(proposed if proposed is not None else content(primary))
        paths = references(value)
        files = {filename: primary}
        for name in sorted(paths):
            if name == ".env" and not (directory / name).exists() and not (directory / name).is_symlink():
                files[name] = None
            else:
                files[name] = read_file(relative_file(directory, name))
        return {"directory": self.identity(directory), "primary": filename, "files": files}

    def assert_revision(self, directory, expected):
        current = self.snapshot(directory)
        if fingerprint(current) != expected:
            raise Failure("stack_revision_changed", "conflict")
        return current

    def claim(self, directory):
        self.store.write("ownership", str(directory), {"directory_identity": self.identity(directory)})

    def reference_state(self, directory, filename):
        if not isinstance(filename, str) or Path(filename).is_absolute() or ".." in Path(filename).parts:
            raise Failure("version_reference_invalid")
        return read_file(directory / filename, optional=True)

    def replace(self, directory, filename, raw, metadata, *, expected=UNSPECIFIED):
        path = relative_file(directory, filename) if (directory / filename).exists() else directory / filename
        no_symlinks(path, allow_missing=True)
        if expected is not UNSPECIFIED and self.reference_state(directory, filename) != expected:
            raise Failure("compose_input_changed", "conflict")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tianshu-control-", delete=False) as stream:
                temporary = stream.name
                if os.name != "nt":
                    os.fchown(stream.fileno(), metadata["uid"], metadata["gid"])
                    os.fchmod(stream.fileno(), metadata["mode"])
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            if os.name != "nt":
                fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError:
            raise Failure("compose_write_failed") from None
        finally:
            if temporary:
                os.unlink(temporary)

    @contextmanager
    def validation_inputs(self, directory, snapshot):
        """Only saved reference files are staged; live Compose/.env never change."""
        if snapshot is None:
            yield None
            return
        no_symlinks(directory)
        temporary = Path(tempfile.mkdtemp(prefix=".tianshu-control-preview-", dir=directory))
        files, directories = [], {temporary}
        try:
            for name, record in snapshot["files"].items():
                if record is None or name == snapshot["primary"]:
                    continue
                path = temporary / name
                if not path.is_relative_to(temporary) or ".." in Path(name).parts:
                    raise Failure("version_reference_invalid")
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                directories.update(parent for parent in path.parents if parent.is_relative_to(temporary))
                with path.open("xb") as stream:
                    if os.name != "nt":
                        os.fchmod(stream.fileno(), 0o600)
                    stream.write(content(record))
                files.append(path)
            yield self.context(directory) / temporary.name
        finally:
            for path in files:
                path.unlink()
            for path in sorted(directories, key=lambda entry: len(entry.parts), reverse=True):
                path.rmdir()
