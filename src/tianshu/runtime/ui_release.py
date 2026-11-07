"""UI online release contract, verification, atomic pointer switching, and runtime snapshot manager."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from typing import Any
from uuid import uuid4

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

UI_PACKAGE_SCHEMA_VERSION = "tianshu.ui.release.v1"
UI_CURRENT_SCHEMA_VERSION = "tianshu.ui.current.v1"
UI_STATUS_SCHEMA_VERSION = "tianshu.ui.status.v1"

UI_BUILD_ID_RE = re.compile(r"^ui-[a-z0-9][a-z0-9._-]{3,60}$")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

BACKEND_UI_CONTRACT: dict[str, Any] = {
    "contract_id": "tianshu.web_api.v1",
    "api_prefix": "/api/v1",
    "contract_version": 1,
    "supported_package_schemas": (UI_PACKAGE_SCHEMA_VERSION,),
    "provided_capabilities": frozenset(
        {
            "session.v1",
            "command_recovery.v1",
            "data_generation_header.v1",
            "ui_version_status.v1",
        }
    ),
}


def validate_build_id(build_id: str) -> str:
    if (
        not isinstance(build_id, str)
        or ".." in build_id
        or "/" in build_id
        or "\\" in build_id
        or not UI_BUILD_ID_RE.fullmatch(build_id)
    ):
        raise ValueError("invalid_ui_build_id")
    return build_id


def validate_source_sha(source_sha: str) -> str:
    if not isinstance(source_sha, str) or not SOURCE_SHA_RE.fullmatch(source_sha):
        raise ValueError("invalid_ui_source_sha")
    return source_sha


def release_base_url(build_id: str) -> str:
    validate_build_id(build_id)
    return f"/_ui/releases/{build_id}/"


def validate_relative_asset_path(rel_path: str) -> str:
    if (
        not isinstance(rel_path, str)
        or not rel_path
        or rel_path.startswith("/")
        or "\\" in rel_path
        or any(ord(c) < 32 for c in rel_path)
    ):
        raise ValueError("unsafe_ui_asset_path")
    posix = PurePosixPath(rel_path)
    parts = posix.parts
    if (
        not parts
        or posix.is_absolute()
        or "." in parts
        or ".." in parts
        or any(":" in part for part in parts)
        or any(part.startswith(".") for part in parts)
        or rel_path in {"release.json", "current.json"}
    ):
        raise ValueError("unsafe_ui_asset_path")
    normalized = posix.as_posix()
    if normalized != rel_path:
        raise ValueError("unsafe_ui_asset_path")
    return normalized


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def compute_package_sha256(files_manifest: Mapping[str, Mapping[str, Any]]) -> str:
    if not isinstance(files_manifest, Mapping) or not files_manifest:
        raise ValueError("invalid_files_manifest")
    digest = hashlib.sha256()
    for rel_path in sorted(files_manifest.keys()):
        validate_relative_asset_path(rel_path)
        entry = files_manifest[rel_path]
        if not isinstance(entry, Mapping):
            raise ValueError("invalid_file_entry")
        sha = entry.get("sha256")
        size = entry.get("size")
        if (
            not isinstance(sha, str)
            or not HEX64_RE.fullmatch(sha)
            or type(size) is not int
            or size < 0
        ):
            raise ValueError("invalid_file_entry")
        digest.update(f"{rel_path}\0{sha}\0{size}\n".encode("utf-8"))
    return digest.hexdigest()


def default_compatibility_requirement(
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    version = int(backend_contract.get("contract_version", 1))
    return {
        "contract_id": str(backend_contract["contract_id"]),
        "api_prefix": str(backend_contract["api_prefix"]),
        "required_capabilities": sorted(backend_contract["provided_capabilities"]),
        "min_backend_contract_version": version,
        "max_backend_contract_version": version,
    }


def validate_compatibility(
    manifest: Mapping[str, Any],
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> None:
    if not isinstance(manifest, Mapping):
        raise ValueError("compatibility_mismatch:invalid_manifest")
    supported_schemas = tuple(
        backend_contract.get("supported_package_schemas", (UI_PACKAGE_SCHEMA_VERSION,))
    )
    if manifest.get("schema_version") not in supported_schemas:
        raise ValueError("compatibility_mismatch:unsupported_schema")
    comp = manifest.get("compatibility")
    if not isinstance(comp, Mapping):
        raise ValueError("compatibility_mismatch:missing_compatibility_contract")
    required_keys = {
        "contract_id",
        "api_prefix",
        "required_capabilities",
        "min_backend_contract_version",
        "max_backend_contract_version",
    }
    if not required_keys.issubset(comp.keys()):
        raise ValueError("compatibility_mismatch:incomplete_compatibility_contract")
    if comp["contract_id"] != backend_contract["contract_id"]:
        raise ValueError("compatibility_mismatch:contract_id")
    if comp["api_prefix"] != backend_contract["api_prefix"]:
        raise ValueError("compatibility_mismatch:api_prefix")
    min_ver = comp["min_backend_contract_version"]
    max_ver = comp["max_backend_contract_version"]
    backend_ver = backend_contract["contract_version"]
    if (
        type(min_ver) is not int
        or type(max_ver) is not int
        or type(backend_ver) is not int
        or min_ver < 1
        or max_ver < min_ver
        or not (min_ver <= backend_ver <= max_ver)
    ):
        raise ValueError("compatibility_mismatch:contract_version_range")
    req_caps = comp["required_capabilities"]
    if (
        not isinstance(req_caps, (list, tuple))
        or not req_caps
        or any(not isinstance(cap, str) or not cap for cap in req_caps)
    ):
        raise ValueError("compatibility_mismatch:invalid_required_capabilities")
    provided_caps = set(backend_contract.get("provided_capabilities", ()))
    if not set(req_caps).issubset(provided_caps):
        raise ValueError("compatibility_mismatch:missing_backend_capabilities")


def validate_release_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_build_id: str | None = None,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest_invalid:not_an_object")
    if manifest.get("schema_version") != UI_PACKAGE_SCHEMA_VERSION:
        raise ValueError("manifest_invalid:schema_version")
    build_id = validate_build_id(manifest.get("build_id", ""))
    if expected_build_id is not None and build_id != expected_build_id:
        raise ValueError("manifest_invalid:build_id_mismatch")
    validate_source_sha(manifest.get("source_sha", ""))
    if manifest.get("vite_base") != release_base_url(build_id):
        raise ValueError("manifest_invalid:vite_base_mismatch")
    if manifest.get("entrypoint") != "index.html":
        raise ValueError("manifest_invalid:entrypoint_mismatch")
    validate_compatibility(manifest, backend_contract)
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files or "index.html" not in files:
        raise ValueError("manifest_invalid:files")
    expected_pkg_sha = compute_package_sha256(files)
    if manifest.get("package_sha256") != expected_pkg_sha:
        raise ValueError("manifest_invalid:package_sha256_mismatch")
    return dict(manifest)


def build_release_manifest(
    dist_dir: Path,
    *,
    build_id: str,
    source_sha: str,
    created_at: str | None = None,
    compatibility: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dist_dir = Path(dist_dir)
    validate_build_id(build_id)
    validate_source_sha(source_sha)
    if not dist_dir.exists() or dist_dir.is_symlink() or not dist_dir.is_dir():
        raise ValueError("unsafe_release_directory")
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(dist_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError("unsafe_release_symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("unsafe_release_special_file")
        if path.name == "release.json" and path.parent == dist_dir:
            continue
        rel_path = validate_relative_asset_path(path.relative_to(dist_dir).as_posix())
        content_type, _ = mimetypes.guess_type(path.name)
        stat = path.stat()
        files[rel_path] = {
            "sha256": file_sha256(path),
            "size": int(stat.st_size),
            "content_type": content_type or "application/octet-stream",
        }
    if "index.html" not in files:
        raise ValueError("index_html_missing")
    manifest: dict[str, Any] = {
        "schema_version": UI_PACKAGE_SCHEMA_VERSION,
        "build_id": build_id,
        "source_sha": source_sha,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "vite_base": release_base_url(build_id),
        "entrypoint": "index.html",
        "compatibility": dict(compatibility)
        if compatibility is not None
        else default_compatibility_requirement(),
        "files": files,
        "package_sha256": compute_package_sha256(files),
    }
    validate_release_manifest(manifest, expected_build_id=build_id)
    return manifest


@dataclass(frozen=True, slots=True)
class VerifiedReleaseSnapshot:
    build_id: str
    source_sha: str
    root: Path
    manifest: dict[str, Any]
    files: frozenset[str]
    package_sha256: str
    builtin: bool = False
    versioned_base: bool = True


def _assert_no_symlinks_in_chain(base: Path, target: Path) -> None:
    if base.is_symlink():
        raise ValueError("unsafe_release_symlink")
    rel = target.relative_to(base)
    current = base
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("unsafe_release_symlink")


def verify_release_directory(
    release_dir: Path,
    *,
    expected_build_id: str | None = None,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
    require_versioned_index: bool = True,
) -> VerifiedReleaseSnapshot:
    release_dir = Path(release_dir)
    if not release_dir.exists():
        raise ValueError("release_not_found")
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise ValueError("unsafe_release_symlink")
    release_json = release_dir / "release.json"
    if release_json.is_symlink():
        raise ValueError("unsafe_release_symlink")
    if not release_json.is_file():
        raise ValueError("manifest_invalid:release_json_missing")
    try:
        raw_manifest = json.loads(release_json.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("manifest_invalid:json_decode_failed") from exc
    manifest = validate_release_manifest(
        raw_manifest,
        expected_build_id=expected_build_id,
        backend_contract=backend_contract,
    )
    actual_files: dict[str, Path] = {}
    for item in sorted(release_dir.rglob("*")):
        if item.is_symlink():
            raise ValueError("unsafe_release_symlink")
        if item.is_dir():
            continue
        if not item.is_file():
            raise ValueError("unsafe_release_special_file")
        if item == release_json:
            continue
        rel = validate_relative_asset_path(item.relative_to(release_dir).as_posix())
        actual_files[rel] = item
    expected_files = manifest["files"]
    if set(actual_files.keys()) < set(expected_files.keys()):
        raise ValueError("file_missing")
    if set(actual_files.keys()) != set(expected_files.keys()):
        raise ValueError("release_files_mismatch")
    for rel, file_path in actual_files.items():
        entry = expected_files[rel]
        stat = file_path.stat()
        if int(stat.st_size) != int(entry["size"]):
            raise ValueError("digest_mismatch:size")
        if file_sha256(file_path) != entry["sha256"]:
            raise ValueError("digest_mismatch:sha256")
    index_text = (release_dir / "index.html").read_text(encoding="utf-8")
    build_id = manifest["build_id"]
    expected_base = release_base_url(build_id)
    has_versioned_base = expected_base in index_text
    if require_versioned_index:
        if not has_versioned_base:
            raise ValueError("entrypoint_base_mismatch")
        other_match = re.search(
            r"/_ui/releases/(?!" + re.escape(build_id) + r"/)[^/"'\s]+/",
            index_text,
        )
        if other_match:
            raise ValueError("entrypoint_base_mismatch:mixed_version_refs")
    return VerifiedReleaseSnapshot(
        build_id=build_id,
        source_sha=manifest["source_sha"],
        root=release_dir.resolve(),
        manifest=manifest,
        files=frozenset(actual_files.keys()),
        package_sha256=manifest["package_sha256"],
        builtin=False,
        versioned_base=has_versioned_base,
    )


def load_builtin_snapshot(
    builtin_dir: Path,
    *,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> VerifiedReleaseSnapshot:
    builtin_dir = Path(builtin_dir)
    if not builtin_dir.exists() or builtin_dir.is_symlink() or not builtin_dir.is_dir():
        raise ValueError("invalid_builtin_directory")
    if (builtin_dir / "release.json").is_file():
        snap = verify_release_directory(
            builtin_dir,
            backend_contract=backend_contract,
            require_versioned_index=False,
        )
        return VerifiedReleaseSnapshot(
            build_id=snap.build_id,
            source_sha=snap.source_sha,
            root=snap.root,
            manifest=snap.manifest,
            files=snap.files,
            package_sha256=snap.package_sha256,
            builtin=True,
            versioned_base=snap.versioned_base,
        )
    index_file = builtin_dir / "index.html"
    if not index_file.is_file() or index_file.is_symlink():
        raise ValueError("built_web_index_missing")
    files: dict[str, dict[str, Any]] = {}
    for item in sorted(builtin_dir.rglob("*")):
        if item.is_symlink():
            raise ValueError("unsafe_release_symlink")
        if not item.is_file():
            continue
        rel = item.relative_to(builtin_dir).as_posix()
        if rel.startswith("."):
            continue
        content_type, _ = mimetypes.guess_type(item.name)
        files[rel] = {
            "sha256": file_sha256(item),
            "size": int(item.stat().st_size),
            "content_type": content_type or "application/octet-stream",
        }
    pkg_sha = compute_package_sha256(files)
    source_sha = "0" * 40
    product_manifest = builtin_dir.parents[1] / "image-manifest" / "product.json" if len(builtin_dir.parents) >= 2 else None
    if product_manifest and product_manifest.is_file():
        try:
            prod_data = json.loads(product_manifest.read_text(encoding="utf-8"))
            if SOURCE_SHA_RE.fullmatch(str(prod_data.get("source_sha", ""))):
                source_sha = str(prod_data["source_sha"])
        except Exception:
            pass
    build_id = f"builtin-{pkg_sha[:12]}"
    return VerifiedReleaseSnapshot(
        build_id=build_id,
        source_sha=source_sha,
        root=builtin_dir.resolve(),
        manifest={"files": files, "package_sha256": pkg_sha},
        files=frozenset(files.keys()),
        package_sha256=pkg_sha,
        builtin=True,
        versioned_base=False,
    )


class ui_release_lock:
    """Cross-platform local file lock for UI release installation and pointer switches."""

    def __init__(self, ui_root: Path, *, blocking: bool = False):
        self.ui_root = Path(ui_root)
        self.lock_file = self.ui_root / ".lock"
        self.blocking = blocking
        self.fd = None

    def __enter__(self):
        if self.ui_root.is_symlink():
            raise ValueError("unsafe_ui_root_symlink")
        self.ui_root.mkdir(parents=True, exist_ok=True)
        if self.lock_file.is_symlink():
            raise ValueError("unsafe_ui_lock_symlink")
        self.fd = open(self.lock_file, "a+b")
        try:
            self.fd.seek(0)
            if self.fd.tell() == 0:
                self.fd.write(b"0")
                self.fd.flush()
            self.fd.seek(0)
            if sys.platform == "win32":
                mode = msvcrt.LK_LOCK if self.blocking else msvcrt.LK_NBLCK
                msvcrt.locking(self.fd.fileno(), mode, 1)
            else:
                flags = fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB)
                fcntl.flock(self.fd.fileno(), flags)
        except OSError as exc:
            self.fd.close()
            self.fd = None
            raise RuntimeError("ui_release_lock_busy") from exc
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd is not None:
            try:
                self.fd.seek(0)
                if sys.platform == "win32":
                    msvcrt.locking(self.fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
            finally:
                self.fd.close()
                self.fd = None


def install_release_directory(
    ui_root: Path,
    source_dir: Path,
    *,
    min_free_bytes: int = 64 * 1024 * 1024,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    ui_root = Path(ui_root)
    source_dir = Path(source_dir)
    if ui_root.exists() and ui_root.is_symlink():
        raise ValueError("unsafe_ui_root_symlink")
    snapshot = verify_release_directory(source_dir, backend_contract=backend_contract)
    ui_root.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(int(entry["size"]) for entry in snapshot.manifest["files"].values())
    free_bytes = shutil.disk_usage(ui_root).free
    if free_bytes < int(min_free_bytes) + total_bytes:
        raise ValueError("ui_release_insufficient_free_space")
    with ui_release_lock(ui_root):
        releases_dir = ui_root / "releases"
        staging_root = ui_root / ".staging"
        for directory in (releases_dir, staging_root):
            if directory.is_symlink():
                raise ValueError("unsafe_ui_root_symlink")
            directory.mkdir(parents=True, exist_ok=True)
        target_dir = releases_dir / snapshot.build_id
        if target_dir.is_symlink():
            raise ValueError("unsafe_release_symlink")
        if target_dir.exists():
            try:
                existing = verify_release_directory(
                    target_dir,
                    expected_build_id=snapshot.build_id,
                    backend_contract=backend_contract,
                )
            except Exception as exc:
                raise ValueError("build_id_content_conflict") from exc
            if (
                existing.package_sha256 == snapshot.package_sha256
                and existing.source_sha == snapshot.source_sha
            ):
                return {
                    "build_id": snapshot.build_id,
                    "source_sha": snapshot.source_sha,
                    "package_sha256": snapshot.package_sha256,
                    "release_dir": str(target_dir),
                    "package_installed": True,
                    "reused_existing": True,
                    "pointer_switched": False,
                    "http_verified": False,
                }
            raise ValueError("build_id_content_conflict")
        staging_dir = staging_root / f"install-{snapshot.build_id}-{uuid4().hex}"
        try:
            shutil.copytree(source_dir, staging_dir, symlinks=True)
            verify_release_directory(
                staging_dir,
                expected_build_id=snapshot.build_id,
                backend_contract=backend_contract,
            )
            os.replace(staging_dir, target_dir)
        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        return {
            "build_id": snapshot.build_id,
            "source_sha": snapshot.source_sha,
            "package_sha256": snapshot.package_sha256,
            "release_dir": str(target_dir),
            "package_installed": True,
            "reused_existing": False,
            "pointer_switched": False,
            "http_verified": False,
        }


def switch_current_release(
    ui_root: Path,
    target_build_id: str,
    *,
    expected_current_build_id: str | None = None,
    require_expected: bool = False,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    ui_root = Path(ui_root)
    validate_build_id(target_build_id)
    if expected_current_build_id is not None:
        validate_build_id(expected_current_build_id)
    with ui_release_lock(ui_root):
        releases_dir = ui_root / "releases"
        staging_root = ui_root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        target_dir = releases_dir / target_build_id
        snapshot = verify_release_directory(
            target_dir,
            expected_build_id=target_build_id,
            backend_contract=backend_contract,
        )
        current_file = ui_root / "current.json"
        if current_file.is_symlink():
            raise ValueError("unsafe_current_pointer_symlink")
        current_data: dict[str, Any] = {}
        if current_file.exists():
            try:
                loaded = json.loads(current_file.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("current_pointer_corrupted")
                current_data = loaded
            except Exception as exc:
                if require_expected or expected_current_build_id is not None:
                    raise ValueError("current_pointer_corrupted") from exc
                current_data = {}
        prev_build_id = current_data.get("build_id")
        if prev_build_id is not None and not isinstance(prev_build_id, str):
            prev_build_id = None
        if require_expected or expected_current_build_id is not None:
            if prev_build_id != expected_current_build_id:
                raise ValueError("expected_current_build_id_mismatch")
        prev_seq = current_data.get("activation_sequence", 0)
        if type(prev_seq) is not int or prev_seq < 0:
            prev_seq = 0
        next_seq = prev_seq + 1
        activated_at = datetime.now(UTC).isoformat()
        new_pointer = {
            "schema_version": UI_CURRENT_SCHEMA_VERSION,
            "build_id": snapshot.build_id,
            "source_sha": snapshot.source_sha,
            "package_sha256": snapshot.package_sha256,
            "activation_sequence": next_seq,
            "activated_at": activated_at,
            "previous_build_id": prev_build_id,
        }
        staging_file = staging_root / f"current-{uuid4().hex}.tmp"
        try:
            with staging_file.open("w", encoding="utf-8") as handle:
                json.dump(new_pointer, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging_file, current_file)
        except Exception:
            if staging_file.exists():
                staging_file.unlink(missing_ok=True)
            raise
        return {
            "previous_build_id": prev_build_id,
            "current_build_id": snapshot.build_id,
            "build_id": snapshot.build_id,
            "source_sha": snapshot.source_sha,
            "package_sha256": snapshot.package_sha256,
            "activation_sequence": next_seq,
            "activated_at": activated_at,
            "package_installed": True,
            "pointer_switched": True,
            "http_verified": False,
        }


def _classify_degraded_reason(exc: Exception) -> str:
    msg = str(exc)
    if msg.startswith("compatibility_mismatch"):
        return "compatibility_mismatch"
    if msg.startswith("digest_mismatch"):
        return "digest_mismatch"
    if msg in {"file_missing", "index_html_missing"}:
        return "file_missing"
    if msg == "release_not_found":
        return "release_not_found"
    if "symlink" in msg or "unsafe" in msg:
        return "unsafe_release_path"
    if msg.startswith("current_pointer"):
        return "current_pointer_invalid"
    if msg.startswith("manifest_invalid") or msg in {
        "release_files_mismatch",
        "entrypoint_base_mismatch",
        "invalid_ui_build_id",
        "invalid_ui_source_sha",
    } or msg.startswith("entrypoint_base_mismatch"):
        return "manifest_invalid"
    return "release_verification_failed"


class UiReleaseManager:
    """Runtime manager that resolves the active UI release without per-request full hashing."""

    def __init__(
        self,
        ui_root: Path | None,
        builtin_root: Path,
        *,
        backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
    ):
        self.ui_root = Path(ui_root) if ui_root is not None else None
        self.builtin_root = Path(builtin_root)
        self.backend_contract = backend_contract
        self.builtin_snapshot = load_builtin_snapshot(
            self.builtin_root, backend_contract=backend_contract
        )
        self._verified_releases: dict[str, VerifiedReleaseSnapshot] = {}
        self._last_known_good: VerifiedReleaseSnapshot | None = None
        self._cached_pointer_bytes: bytes | None = None
        self._cached_active: tuple[VerifiedReleaseSnapshot, dict[str, Any]] | None = None

    def _build_status(
        self,
        *,
        served: VerifiedReleaseSnapshot,
        served_mode: str,
        requested_build_id: str | None,
        activation_sequence: int,
        compatible: bool,
        degraded: bool,
        degraded_reason: str | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": UI_STATUS_SCHEMA_VERSION,
            "served_build_id": served.build_id,
            "served_source_sha": served.source_sha,
            "served_mode": served_mode,
            "requested_build_id": requested_build_id,
            "activation_sequence": activation_sequence,
            "compatible": compatible,
            "degraded": degraded,
            "degraded_reason": degraded_reason,
            "contract_id": str(self.backend_contract["contract_id"]),
            "contract_version": int(self.backend_contract["contract_version"]),
        }

    def _fallback(
        self,
        *,
        requested_build_id: str | None,
        activation_sequence: int,
        reason: str,
    ) -> tuple[VerifiedReleaseSnapshot, dict[str, Any]]:
        compatible = reason != "compatibility_mismatch"
        if self._last_known_good is not None and self._is_snapshot_Quick_valid(self._last_known_good):
            served = self._last_known_good
            mode = "fallback_last_known_good"
        else:
            served = self.builtin_snapshot
            mode = "builtin"
        status = self._build_status(
            served=served,
            served_mode=mode,
            requested_build_id=requested_build_id,
            activation_sequence=activation_sequence,
            compatible=compatible,
            degraded=True,
            degraded_reason=reason,
        )
        return served, status

    def _is_snapshot_Quick_valid(self, snapshot: VerifiedReleaseSnapshot) -> bool:
        try:
            if (
                not snapshot.root.exists()
                or snapshot.root.is_symlink()
                or not (snapshot.root / "index.html").is_file()
            ):
                return False
            validate_compatibility(snapshot.manifest, self.backend_contract)
            return True
        except Exception:
            return False

    def resolve_active(self) -> tuple[VerifiedReleaseSnapshot, dict[str, Any]]:
        if self.ui_root is None:
            status = self._build_status(
                served=self.builtin_snapshot,
                served_mode="builtin",
                requested_build_id=None,
                activation_sequence=0,
                compatible=True,
                degraded=False,
                degraded_reason=None,
            )
            return self.builtin_snapshot, status

        if self.ui_root.exists() and self.ui_root.is_symlink():
            return self._fallback(
                requested_build_id=None,
                activation_sequence=0,
                reason="unsafe_release_path",
            )

        current_file = self.ui_root / "current.json"
        if not current_file.exists():
            self._cached_pointer_bytes = None
            self._cached_active = None
            status = self._build_status(
                served=self.builtin_snapshot,
                served_mode="builtin",
                requested_build_id=None,
                activation_sequence=0,
                compatible=True,
                degraded=False,
                degraded_reason=None,
            )
            return self.builtin_snapshot, status

        if current_file.is_symlink():
            return self._fallback(
                requested_build_id=None,
                activation_sequence=0,
                reason="unsafe_release_path",
            )

        try:
            raw_bytes = current_file.read_bytes()
        except OSError:
            return self._fallback(
                requested_build_id=None,
                activation_sequence=0,
                reason="current_pointer_invalid",
            )

        if (
            self._cached_pointer_bytes == raw_bytes
            and self._cached_active is not None
            and not self._cached_active[1]["degraded"]
            and self._is_snapshot_Quick_valid(self._cached_active[0])
        ):
            return self._cached_active

        try:
            pointer = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            return self._fallback(
                requested_build_id=None,
                activation_sequence=0,
                reason="current_pointer_invalid",
            )

        if (
            not isinstance(pointer, Mapping)
            or pointer.get("schema_version") != UI_CURRENT_SCHEMA_VERSION
        ):
            req_id = pointer.get("build_id") if isinstance(pointer, Mapping) and isinstance(pointer.get("build_id"), str) else None
            return self._fallback(
                requested_build_id=req_id,
                activation_sequence=0,
                reason="current_pointer_invalid",
            )

        raw_build_id = pointer.get("build_id")
        seq = pointer.get("activation_sequence", 1)
        if type(seq) is not int or seq < 1:
            seq = 0
            return self._fallback(
                requested_build_id=raw_build_id if isinstance(raw_build_id, str) else None,
                activation_sequence=seq,
                reason="current_pointer_invalid",
            )

        try:
            build_id = validate_build_id(raw_build_id)
        except ValueError:
            return self._fallback(
                requested_build_id=raw_build_id if isinstance(raw_build_id, str) else None,
                activation_sequence=seq,
                reason="current_pointer_invalid",
            )

        release_dir = self.ui_root / "releases" / build_id
        try:
            snapshot = verify_release_directory(
                release_dir,
                expected_build_id=build_id,
                backend_contract=self.backend_contract,
            )
            if "package_sha256" in pointer and pointer["package_sha256"] != snapshot.package_sha256:
                raise ValueError("digest_mismatch:pointer_package_sha256")
            if "source_sha" in pointer and pointer["source_sha"] != snapshot.source_sha:
                raise ValueError("manifest_invalid:pointer_source_sha")
        except Exception as exc:
            self._verified_releases.pop(build_id, None)
            reason = _classify_degraded_reason(exc)
            return self._fallback(
                requested_build_id=build_id,
                activation_sequence=seq,
                reason=reason,
            )

        self._verified_releases[build_id] = snapshot
        self._last_known_good = snapshot
        status = self._build_status(
            served=snapshot,
            served_mode="external",
            requested_build_id=build_id,
            activation_sequence=seq,
            compatible=True,
            degraded=False,
            degraded_reason=None,
        )
        self._cached_pointer_bytes = raw_bytes
        self._cached_active = (snapshot, status)
        return snapshot, status

    def _get_current(self) -> VerifiedReleaseSnapshot:
        return self.resolve_active()[0]

    def status(self) -> dict[str, Any]:
        return self.resolve_active()[1]

    def get_release_snapshot(self, build_id: str) -> VerifiedReleaseSnapshot | None:
        if build_id == self.builtin_snapshot.build_id:
            return self.builtin_snapshot
        try:
            validate_build_id(build_id)
        except ValueError:
            return None
        cached = self._verified_releases.get(build_id)
        if cached is not None and self._is_snapshot_Quick_valid(cached):
            return cached
        if self.ui_root is None:
            return None
        release_dir = self.ui_root / "releases" / build_id
        try:
            snapshot = verify_release_directory(
                release_dir,
                expected_build_id=build_id,
                backend_contract=self.backend_contract,
            )
        except Exception:
            self._verified_releases.pop(build_id, None)
            return None
        self._verified_releases[build_id] = snapshot
        return snapshot

    def resolve_asset(self, build_id: str, rel_path: str) -> Path | None:
        try:
            clean_rel = validate_relative_asset_path(rel_path)
        except ValueError:
            return None
        snapshot = self.get_release_snapshot(build_id)
        if snapshot is None or clean_rel not in snapshot.files:
            return None
        target = snapshot.root / clean_rel
        try:
            _assert_no_symlinks_in_chain(snapshot.root, target)
            resolved = target.resolve(strict=True)
            if not resolved.is_relative_to(snapshot.root) or not resolved.is_file():
                return None
            return resolved
        except Exception:
            return None


def read_ui_release_status(
    ui_root: Path | None,
    *,
    builtin_root: Path | None = None,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    if builtin_root is not None and Path(builtin_root).exists():
        manager = UiReleaseManager(ui_root, Path(builtin_root), backend_contract=backend_contract)
        return manager.status()
    if ui_root is None:
        return {
            "schema_version": UI_STATUS_SCHEMA_VERSION,
            "served_build_id": None,
            "served_source_sha": None,
            "served_mode": "builtin",
            "requested_build_id": None,
            "activation_sequence": 0,
            "compatible": True,
            "degraded": False,
            "degraded_reason": None,
            "contract_id": str(backend_contract["contract_id"]),
            "contract_version": int(backend_contract["contract_version"]),
        }
    temp_builtin = Path(ui_root) / ".builtin-placeholder"
    current_file = Path(ui_root) / "current.json"
    if not current_file.exists():
        return {
            "schema_version": UI_STATUS_SCHEMA_VERSION,
            "served_build_id": None,
            "served_source_sha": None,
            "served_mode": "builtin",
            "requested_build_id": None,
            "activation_sequence": 0,
            "compatible": True,
            "degraded": False,
            "degraded_reason": None,
            "contract_id": str(backend_contract["contract_id"]),
            "contract_version": int(backend_contract["contract_version"]),
        }
    try:
        pointer = json.loads(current_file.read_text(encoding="utf-8"))
        build_id = validate_build_id(pointer.get("build_id", ""))
        seq = int(pointer.get("activation_sequence", 1))
        snapshot = verify_release_directory(
            Path(ui_root) / "releases" / build_id,
            expected_build_id=build_id,
            backend_contract=backend_contract,
        )
        return {
            "schema_version": UI_STATUS_SCHEMA_VERSION,
            "served_build_id": snapshot.build_id,
            "served_source_sha": snapshot.source_sha,
            "served_mode": "external",
            "requested_build_id": build_id,
            "activation_sequence": seq,
            "compatible": True,
            "degraded": False,
            "degraded_reason": None,
            "contract_id": str(backend_contract["contract_id"]),
            "contract_version": int(backend_contract["contract_version"]),
        }
    except Exception as exc:
        reason = _classify_degraded_reason(exc)
        return {
            "schema_version": UI_STATUS_SCHEMA_VERSION,
            "served_build_id": None,
            "served_source_sha": None,
            "served_mode": "builtin",
            "requested_build_id": pointer.get("build_id") if isinstance(locals().get("pointer"), Mapping) else None,
            "activation_sequence": 0,
            "compatible": reason != "compatibility_mismatch",
            "degraded": True,
            "degraded_reason": reason,
            "contract_id": str(backend_contract["contract_id"]),
            "contract_version": int(backend_contract["contract_version"]),
        }
