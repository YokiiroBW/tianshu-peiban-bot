"""Build, package, install, activate, rollback, preserve-legacy, and inspect UI releases."""
from __future__ import annotations
import argparse
from collections.abc import Mapping
from datetime import UTC, datetime
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4
try:
    from tianshu.runtime.ui_release import (
        BACKEND_UI_CONTRACT,
        UI_CURRENT_SCHEMA_VERSION,
        UI_PACKAGE_SCHEMA_VERSION,
        UI_STATUS_SCHEMA_VERSION,
        UiReleaseManager,
        build_release_manifest,
        compute_package_sha256,
        default_compatibility_requirement,
        file_sha256,
        install_release_directory,
        release_base_url,
        switch_current_release,
        ui_release_lock,
        validate_build_id,
        validate_compatibility,
        validate_release_manifest,
        validate_source_sha,
        verify_release_directory,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from tianshu.runtime.ui_release import (
        BACKEND_UI_CONTRACT,
        UI_CURRENT_SCHEMA_VERSION,
        UI_PACKAGE_SCHEMA_VERSION,
        UI_STATUS_SCHEMA_VERSION,
        UiReleaseManager,
        build_release_manifest,
        compute_package_sha256,
        default_compatibility_requirement,
        file_sha256,
        install_release_directory,
        release_base_url,
        switch_current_release,
        ui_release_lock,
        validate_build_id,
        validate_compatibility,
        validate_release_manifest,
        validate_source_sha,
        verify_release_directory,
    )

DEFAULT_BUILD_MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_INSTALL_MIN_FREE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
__all__ = [
    "BACKEND_UI_CONTRACT",
    "UI_CURRENT_SCHEMA_VERSION",
    "UI_PACKAGE_SCHEMA_VERSION",
    "UI_STATUS_SCHEMA_VERSION",
    "UiReleaseManager",
    "UiReleaseVerificationError",
    "activate_ui_release",
    "build_release_manifest",
    "build_ui_package",
    "compute_package_sha256",
    "default_build_id",
    "default_compatibility_requirement",
    "file_sha256",
    "install_release_directory",
    "install_ui_package",
    "preserve_legacy_root",
    "release_base_url",
    "rollback_ui_release",
    "status_ui_release",
    "switch_current_release",
    "ui_release_lock",
    "validate_build_id",
    "validate_compatibility",
    "validate_release_manifest",
    "validate_source_sha",
    "validate_tar_members",
    "verify_http_release",
    "verify_release_directory",
]

class UiReleaseVerificationError(RuntimeError):
    """Raised when pointer switch succeeded locally but HTTP verification failed."""
    def __init__(self, message: str, receipt: dict[str, Any]) -> None:
        super().__init__(message)
        self.verification_error = message
        self.receipt = receipt

def run(
    command: list[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    executable_command = list(command)
    try:
        result = subprocess.run(
            executable_command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=False,
        )
    except FileNotFoundError:
        resolved = shutil.which(executable_command[0]) if executable_command else None
        if not resolved:
            raise RuntimeError("ui_release_build_command_failed") from None
        executable_command[0] = resolved
        result = subprocess.run(
            executable_command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=False,
        )
    if result.returncode:
        raise RuntimeError("ui_release_build_command_failed")

def default_build_id(source_sha: str, *, now: datetime | None = None) -> str:
    validated_sha = validate_source_sha(source_sha)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dt%H%M%Sz")
    return validate_build_id(f"ui-{timestamp}-{validated_sha[:12]}")

def _link_or_junction_directory(source_dir: Path, target_dir: Path) -> None:
    try:
        os.symlink(source_dir, target_dir, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(source_dir), str(target_dir))
        else:
            raise

def _validate_archive_member_path(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name.startswith("/")
        or name.startswith("\\")
        or "\\" in name
        or any(ord(ch) < 32 for ch in name)
    ):
        raise ValueError("unsafe_ui_archive_member")
    posix_path = PurePosixPath(name)
    parts = posix_path.parts
    if not parts:
        raise ValueError("unsafe_ui_archive_member")
    for index, part in enumerate(parts):
        if part in {"", ".", ".."}:
            raise ValueError("unsafe_ui_archive_member")
        if index == 0 and ":" in part:
            raise ValueError("unsafe_ui_archive_member")
        if part == ".lock" or part == "current.json" or part.startswith(".staging"):
            raise ValueError("unsafe_ui_archive_member")
    return posix_path.as_posix()

def validate_tar_members(
    archive: tarfile.TarFile,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_total_bytes: int = MAX_ARCHIVE_TOTAL_BYTES,
) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if not members or len(members) > max_members:
        raise ValueError("unsafe_ui_archive_member")
    total_bytes = 0
    for member in members:
        if (
            member.issym()
            or member.islnk()
            or member.ischr()
            or member.isblk()
            or member.isfifo()
            or member.isdev()
            or member.linkname
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError("unsafe_ui_archive_member")
        if member.size < 0:
            raise ValueError("unsafe_ui_archive_member")
        _validate_archive_member_path(member.name)
        total_bytes += int(member.size)
        if total_bytes > max_total_bytes:
            raise ValueError("unsafe_ui_archive_member")
    return members

def _safe_extract_tar(archive: tarfile.TarFile, extract_dir: Path) -> Path:
    members = validate_tar_members(archive)
    resolved_root = extract_dir.resolve()
    for member in members:
        rel_posix = _validate_archive_member_path(member.name)
        target_path = extract_dir.joinpath(*PurePosixPath(rel_posix).parts)
        resolved_target = target_path.resolve()
        if not resolved_target.is_relative_to(resolved_root) or resolved_target == resolved_root:
            raise ValueError("unsafe_ui_archive_member")
        if member.isdir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        extracted = archive.extractfile(member)
        if extracted is None:
            raise ValueError("unsafe_ui_archive_member")
        with extracted, target_path.open("wb") as out_stream:
            shutil.copyfileobj(extracted, out_stream, length=1024 * 1024)
        target_path.chmod(0o644)
    if (extract_dir / "release.json").is_file():
        return extract_dir
    child_entries = [entry for entry in extract_dir.iterdir()]
    if (
        len(child_entries) == 1
        and child_entries[0].is_dir()
        and not child_entries[0].is_symlink()
        and (child_entries[0] / "release.json").is_file()
    ):
        return child_entries[0]
    return extract_dir

def _pack_release_directory(release_dir: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for file_path in sorted(release_dir.rglob("*"), key=lambda item: item.relative_to(release_dir).as_posix()):
            if file_path.is_symlink():
                raise ValueError("unsafe_release_symlink")
            if not file_path.is_file():
                continue
            rel_posix = file_path.relative_to(release_dir).as_posix()
            _validate_archive_member_path(rel_posix)
            info = tarfile.TarInfo(name=rel_posix)
            stat_result = file_path.stat()
            info.size = stat_result.st_size
            info.mtime = int(stat_result.st_mtime)
            info.mode = 0o644
            info.type = tarfile.REGTYPE
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with file_path.open("rb") as stream:
                archive.addfile(info, stream)

def build_ui_package(
    checkout: str | Path,
    source_sha: str,
    owned_root: str | Path,
    output: str | Path,
    build_id: str | None = None,
    *,
    node_modules: str | Path | None = None,
    min_free_bytes: int = DEFAULT_BUILD_MIN_FREE_BYTES,
    npm_binary: str = "npm",
    git_binary: str = "git",
    created_at: str | None = None,
    compatibility: Mapping[str, Any] | None = None,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    checkout_path = Path(checkout).resolve(strict=True)
    owned_root_path = Path(owned_root).resolve(strict=True)
    output_path = Path(output)
    if (
        not output_path.is_absolute()
        or output_path.exists()
        or output_path.is_symlink()
        or not output_path.resolve().is_relative_to(owned_root_path)
        or output_path.resolve() == owned_root_path
        or owned_root_path == Path(owned_root_path.anchor)
    ):
        raise ValueError("new_managed_build_directory_required")
    validated_sha = validate_source_sha(source_sha)
    resolved_build_id = (
        validate_build_id(build_id)
        if build_id is not None
        else default_build_id(validated_sha)
    )
    vite_base = release_base_url(resolved_build_id)
    if shutil.disk_usage(owned_root_path).free < min_free_bytes:
        raise ValueError("build_host_minimum_free_bytes_breached")
    node_modules_path: Path | None = None
    if node_modules is not None:
        candidate_nm = Path(node_modules)
        if not candidate_nm.exists() or not candidate_nm.is_dir():
            raise ValueError("node_modules_directory_missing")
        node_modules_path = candidate_nm.resolve(strict=True)
    output_path.mkdir(mode=0o700)
    source_tar = output_path / "web-source.tar"
    run(
        [
            git_binary,
            "archive",
            "--format=tar",
            "--output",
            str(source_tar),
            validated_sha,
            "web",
        ],
        cwd=checkout_path,
    )
    source_dir = output_path / "source"
    source_dir.mkdir(mode=0o700)
    with tarfile.open(source_tar) as archive:
        archive.extractall(source_dir, filter="data")
    web_dir = source_dir / "web"
    if not (web_dir / "package.json").is_file():
        raise ValueError("exported_web_source_missing")
    build_env = os.environ.copy()
    build_env["TIANSHU_UI_BUILD_ID"] = resolved_build_id
    build_env["TIANSHU_UI_BASE"] = vite_base
    if node_modules_path is not None:
        target_nm = web_dir / "node_modules"
        if target_nm.exists() or target_nm.is_symlink():
            if target_nm.is_symlink() or target_nm.is_file():
                target_nm.unlink()
            else:
                shutil.rmtree(target_nm)
        _link_or_junction_directory(node_modules_path, target_nm)
    else:
        run([npm_binary, "ci"], cwd=web_dir, env=build_env)
    if shutil.which(npm_binary) is not None or Path(npm_binary).is_file():
        run([npm_binary, "run", "build"], cwd=web_dir, env=build_env)
    else:
        node_bin = shutil.which("node") or "node"
        run([node_bin, "node_modules/typescript/bin/tsc", "-p", "tsconfig.json", "--noEmit"], cwd=web_dir, env=build_env)
        run([node_bin, "node_modules/vite/bin/vite.js", "build"], cwd=web_dir, env=build_env)
    dist_dir = web_dir / "dist"
    if not dist_dir.is_dir() or dist_dir.is_symlink():
        raise ValueError("built_ui_dist_missing")
    manifest = build_release_manifest(
        dist_dir,
        build_id=resolved_build_id,
        source_sha=validated_sha,
        created_at=created_at,
        compatibility=compatibility,
    )
    if created_at is not None and "created_at" not in manifest:
        manifest["created_at"] = created_at
    elif "created_at" not in manifest:
        manifest["created_at"] = datetime.now(UTC).isoformat()
    validate_release_manifest(
        manifest,
        expected_build_id=resolved_build_id,
        backend_contract=backend_contract,
    )
    release_manifest_path = dist_dir / "release.json"
    release_manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verified = verify_release_directory(
        dist_dir,
        expected_build_id=resolved_build_id,
        backend_contract=backend_contract,
    )
    archive_path = output_path / f"ui-release-{resolved_build_id}.tar.gz"
    _pack_release_directory(dist_dir, archive_path)
    with tarfile.open(archive_path, "r:gz") as packed_archive:
        validate_tar_members(packed_archive)
    output_manifest_copy = output_path / "release.json"
    output_manifest_copy.write_text(
        release_manifest_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    total_bytes = sum(int(entry["size"]) for entry in manifest["files"].values())
    return {
        "schema_version": UI_PACKAGE_SCHEMA_VERSION,
        "build_id": verified.build_id,
        "source_sha": verified.source_sha,
        "vite_base": manifest["vite_base"],
        "package_sha256": verified.package_sha256,
        "archive_path": str(archive_path),
        "archive_sha256": file_sha256(archive_path),
        "release_dir": str(dist_dir),
        "release_manifest": str(output_manifest_copy),
        "file_count": len(verified.files),
        "total_bytes": total_bytes,
        "package_installed": False,
        "pointer_switched": False,
        "http_verified": False,
    }

def install_ui_package(
    ui_root: str | Path,
    package_path: str | Path,
    *,
    min_free_bytes: int = DEFAULT_INSTALL_MIN_FREE_BYTES,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    ui_root_path = Path(ui_root)
    package = Path(package_path)
    if ui_root_path.exists() and ui_root_path.is_symlink():
        raise ValueError("unsafe_ui_root_symlink")
    if package.is_symlink() or not package.exists():
        raise ValueError("unsafe_ui_package_source")
    ui_root_path.mkdir(parents=True, exist_ok=True)
    (ui_root_path / "releases").mkdir(parents=True, exist_ok=True)
    staging_root = ui_root_path / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    if package.is_dir():
        result = dict(
            install_release_directory(
                ui_root_path,
                package,
                min_free_bytes=min_free_bytes,
                backend_contract=backend_contract,
            )
        )
        result.setdefault("release_dir", str(ui_root_path / "releases" / result["build_id"]))
        return result
    if not package.is_file():
        raise ValueError("unsafe_ui_package_source")
    extract_dir = staging_root / f"extract-{uuid4().hex}"
    extract_dir.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(package, "r:*") as archive:
            extracted_release_dir = _safe_extract_tar(archive, extract_dir)
        result = dict(
            install_release_directory(
                ui_root_path,
                extracted_release_dir,
                min_free_bytes=min_free_bytes,
                backend_contract=backend_contract,
            )
        )
        result.setdefault("release_dir", str(ui_root_path / "releases" / result["build_id"]))
        return result
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

def _persist_previous_build_id_if_missing(ui_root: Path, switch_result: Mapping[str, Any]) -> None:
    current_file = ui_root / "current.json"
    if not current_file.is_file() or current_file.is_symlink():
        return
    with ui_release_lock(ui_root):
        try:
            current_data = json.loads(current_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(current_data, dict):
            return
        if current_data.get("build_id") != switch_result.get("current_build_id"):
            return
        updated = False
        if "previous_build_id" not in current_data:
            current_data["previous_build_id"] = switch_result.get("previous_build_id")
            updated = True
        if "source_sha" not in current_data and switch_result.get("source_sha"):
            current_data["source_sha"] = switch_result["source_sha"]
            updated = True
        if "package_sha256" not in current_data and switch_result.get("package_sha256"):
            current_data["package_sha256"] = switch_result["package_sha256"]
            updated = True
        if not updated:
            return
        staging_file = ui_root / ".staging" / f"current-{uuid4().hex}.tmp"
        staging_file.parent.mkdir(parents=True, exist_ok=True)
        with staging_file.open("w", encoding="utf-8") as stream:
            json.dump(current_data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging_file, current_file)

def _normalize_verify_base_url(verify_url: str) -> str:
    cleaned = verify_url.strip()
    if not cleaned.startswith(("http://", "https://")):
        raise ValueError("invalid_verify_url")
    if cleaned.endswith("/_ui/version"):
        cleaned = cleaned[: -len("/_ui/version")]
    return cleaned.rstrip("/")

def verify_http_release(
    verify_url: str,
    expected_build_id: str | None = None,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    base_url = _normalize_verify_base_url(verify_url)
    if expected_build_id is not None:
        validate_build_id(expected_build_id)
    version_req = Request(
        f"{base_url}/_ui/version",
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
        method="GET",
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(version_req, timeout=timeout) as response:
            status_code = getattr(response, "status", response.getcode())
            raw_body = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"version_endpoint_request_failed: {exc}") from exc
    if status_code != 200:
        raise RuntimeError(f"version_endpoint_unexpected_status: {status_code}")
    try:
        version_payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("version_endpoint_invalid_json") from exc
    if not isinstance(version_payload, dict):
        raise RuntimeError("version_endpoint_invalid_payload")
    if version_payload.get("schema_version") != UI_STATUS_SCHEMA_VERSION:
        raise RuntimeError("version_endpoint_schema_mismatch")
    if version_payload.get("compatible") is not True:
        raise RuntimeError("version_endpoint_incompatible")
    if version_payload.get("degraded") is True:
        raise RuntimeError(
            f"version_endpoint_degraded: {version_payload.get('degraded_reason')}"
        )
    served_build_id = version_payload.get("served_build_id")
    if not isinstance(served_build_id, str) or not served_build_id:
        raise RuntimeError("version_endpoint_missing_served_build_id")
    if expected_build_id is not None and served_build_id != expected_build_id:
        raise RuntimeError(
            f"served_build_id_mismatch: expected {expected_build_id}, got {served_build_id}"
        )
    root_req = Request(
        f"{base_url}/",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
        method="GET",
    )
    try:
        with opener.open(root_req, timeout=timeout) as response:
            root_status = getattr(response, "status", response.getcode())
            html_body = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"root_entrypoint_request_failed: {exc}") from exc
    if root_status != 200:
        raise RuntimeError(f"root_entrypoint_unexpected_status: {root_status}")
    target_for_html = expected_build_id or served_build_id
    if not target_for_html.startswith("builtin-"):
        expected_base = release_base_url(target_for_html)
        if expected_base not in html_body:
            raise RuntimeError(f"root_entrypoint_missing_base: {expected_base}")
    return version_payload

def activate_ui_release(
    ui_root: str | Path,
    target_build_id: str,
    *,
    expected_current_build_id: str | None = None,
    require_expected: bool = False,
    verify_url: str | None = None,
    strict_verify: bool = True,
    timeout: float = 5.0,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    ui_root_path = Path(ui_root)
    validate_build_id(target_build_id)
    if expected_current_build_id is not None:
        validate_build_id(expected_current_build_id)
    receipt = dict(
        switch_current_release(
            ui_root_path,
            target_build_id,
            expected_current_build_id=expected_current_build_id,
            require_expected=require_expected,
            backend_contract=backend_contract,
        )
    )
    _persist_previous_build_id_if_missing(ui_root_path, receipt)
    receipt["package_installed"] = True
    receipt["pointer_switched"] = True
    receipt["http_verified"] = False
    receipt["verification_error"] = None
    if verify_url is not None:
        try:
            http_status = verify_http_release(
                verify_url,
                target_build_id,
                timeout=timeout,
            )
            receipt["http_verified"] = True
            receipt["http_status"] = http_status
        except Exception as exc:
            receipt["http_verified"] = False
            receipt["verification_error"] = str(exc)
            if strict_verify:
                raise UiReleaseVerificationError(str(exc), receipt) from exc
    return receipt

def rollback_ui_release(
    ui_root: str | Path,
    to_build_id: str | None = None,
    *,
    expected_current_build_id: str | None = None,
    require_expected: bool = False,
    verify_url: str | None = None,
    strict_verify: bool = True,
    timeout: float = 5.0,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
) -> dict[str, Any]:
    ui_root_path = Path(ui_root)
    target_build_id = to_build_id
    if target_build_id is None:
        current_file = ui_root_path / "current.json"
        if not current_file.is_file() or current_file.is_symlink():
            raise ValueError("rollback_previous_build_id_unavailable")
        try:
            current_data = json.loads(current_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError("rollback_previous_build_id_unavailable") from exc
        previous_id = (
            current_data.get("previous_build_id")
            if isinstance(current_data, dict)
            else None
        )
        if not isinstance(previous_id, str) or not previous_id:
            raise ValueError("rollback_previous_build_id_unavailable")
        target_build_id = previous_id
    validate_build_id(target_build_id)
    receipt = activate_ui_release(
        ui_root_path,
        target_build_id,
        expected_current_build_id=expected_current_build_id,
        require_expected=require_expected,
        verify_url=verify_url,
        strict_verify=strict_verify,
        timeout=timeout,
        backend_contract=backend_contract,
    )
    receipt["rolled_back_to"] = target_build_id
    return receipt

def _verify_no_symlinks_in_tree(directory: Path, *, error_code: str) -> tuple[int, int]:
    if directory.is_symlink() or not directory.exists() or not directory.is_dir():
        raise ValueError(error_code)
    file_count = 0
    total_bytes = 0
    for item in directory.rglob("*"):
        if item.is_symlink():
            raise ValueError(error_code)
        if item.is_file():
            file_count += 1
            total_bytes += item.stat().st_size
        elif not item.is_dir():
            raise ValueError(error_code)
    return file_count, total_bytes

def preserve_legacy_root(
    ui_root: str | Path,
    source_dir: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    ui_root_path = Path(ui_root)
    source_path = Path(source_dir)
    if ui_root_path.exists() and ui_root_path.is_symlink():
        raise ValueError("unsafe_ui_root_symlink")
    _verify_no_symlinks_in_tree(source_path, error_code="unsafe_legacy_symlink")
    ui_root_path.mkdir(parents=True, exist_ok=True)
    staging_root = ui_root_path / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    legacy_dir = ui_root_path / "legacy-root"
    with ui_release_lock(ui_root_path):
        if legacy_dir.exists() or legacy_dir.is_symlink():
            if legacy_dir.is_symlink():
                raise ValueError("unsafe_legacy_symlink")
            if not overwrite:
                existing_count, existing_bytes = _verify_no_symlinks_in_tree(
                    legacy_dir,
                    error_code="unsafe_legacy_symlink",
                )
                return {
                    "legacy_root": str(legacy_dir),
                    "preserved": True,
                    "reused_existing": True,
                    "file_count": existing_count,
                    "total_bytes": existing_bytes,
                }
        staging_legacy = staging_root / f"legacy-{uuid4().hex}"
        staging_legacy.mkdir(parents=True, exist_ok=False)
        backup_legacy: Path | None = None
        try:
            file_count = 0
            total_bytes = 0
            for item in sorted(source_path.rglob("*"), key=lambda p: p.relative_to(source_path).as_posix()):
                if item.is_symlink():
                    raise ValueError("unsafe_legacy_symlink")
                rel_path = item.relative_to(source_path)
                rel_posix = rel_path.as_posix()
                if rel_posix in {"release.json", "current.json", ".lock"} or rel_posix.startswith(".staging"):
                    continue
                target_item = staging_legacy / rel_path
                if item.is_dir():
                    target_item.mkdir(parents=True, exist_ok=True)
                elif item.is_file():
                    target_item.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target_item, follow_symlinks=False)
                    file_count += 1
                    total_bytes += target_item.stat().st_size
                else:
                    raise ValueError("unsafe_legacy_symlink")
            _verify_no_symlinks_in_tree(staging_legacy, error_code="unsafe_legacy_symlink")
            if legacy_dir.exists():
                backup_legacy = staging_root / f"legacy-backup-{uuid4().hex}"
                legacy_dir.rename(backup_legacy)
            staging_legacy.rename(legacy_dir)
            if backup_legacy is not None:
                shutil.rmtree(backup_legacy, ignore_errors=True)
            return {
                "legacy_root": str(legacy_dir),
                "preserved": True,
                "reused_existing": False,
                "file_count": file_count,
                "total_bytes": total_bytes,
            }
        except Exception:
            if backup_legacy is not None and backup_legacy.exists() and not legacy_dir.exists():
                backup_legacy.rename(legacy_dir)
            shutil.rmtree(staging_legacy, ignore_errors=True)
            raise

def _directory_disk_bytes(directory: Path) -> int:
    if not directory.exists() or directory.is_symlink():
        return 0
    total = 0
    for item in directory.rglob("*"):
        if not item.is_symlink() and item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total

def status_ui_release(
    ui_root: str | Path,
    *,
    builtin_root: str | Path | None = None,
    verify_url: str | None = None,
    backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT,
    timeout: float = 5.0,
) -> dict[str, Any]:
    ui_root_path = Path(ui_root)
    current_file = ui_root_path / "current.json"
    current_data: dict[str, Any] | None = None
    current_build_id: str | None = None
    previous_build_id: str | None = None
    activation_sequence = 0
    if current_file.is_file() and not current_file.is_symlink():
        try:
            loaded = json.loads(current_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current_data = loaded
                raw_build_id = loaded.get("build_id")
                if isinstance(raw_build_id, str):
                    current_build_id = raw_build_id
                raw_prev_id = loaded.get("previous_build_id")
                if isinstance(raw_prev_id, str):
                    previous_build_id = raw_prev_id
                raw_seq = loaded.get("activation_sequence")
                if isinstance(raw_seq, int):
                    activation_sequence = raw_seq
        except Exception:
            current_data = None
    releases: list[dict[str, Any]] = []
    releases_dir = ui_root_path / "releases"
    if releases_dir.is_dir() and not releases_dir.is_symlink():
        for release_dir in sorted(releases_dir.iterdir(), key=lambda p: p.name):
            if not release_dir.is_dir() and not release_dir.is_symlink():
                continue
            disk_bytes = _directory_disk_bytes(release_dir)
            try:
                snapshot = verify_release_directory(
                    release_dir,
                    expected_build_id=release_dir.name,
                    backend_contract=backend_contract,
                )
                releases.append(
                    {
                        "build_id": snapshot.build_id,
                        "source_sha": snapshot.source_sha,
                        "package_sha256": snapshot.package_sha256,
                        "disk_bytes": disk_bytes,
                        "compatible": True,
                        "valid": True,
                        "active": snapshot.build_id == current_build_id,
                        "error": None,
                    }
                )
            except Exception as exc:
                raw_source_sha: str | None = None
                raw_pkg_sha: str | None = None
                is_compatible = False
                manifest_file = release_dir / "release.json"
                if (
                    not release_dir.is_symlink()
                    and manifest_file.is_file()
                    and not manifest_file.is_symlink()
                ):
                    try:
                        raw_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                        if isinstance(raw_manifest, dict):
                            if isinstance(raw_manifest.get("source_sha"), str):
                                raw_source_sha = raw_manifest["source_sha"]
                            if isinstance(raw_manifest.get("package_sha256"), str):
                                raw_pkg_sha = raw_manifest["package_sha256"]
                            validate_compatibility(raw_manifest, backend_contract)
                            is_compatible = True
                    except Exception:
                        is_compatible = False
                releases.append(
                    {
                        "build_id": release_dir.name,
                        "source_sha": raw_source_sha,
                        "package_sha256": raw_pkg_sha,
                        "disk_bytes": disk_bytes,
                        "compatible": is_compatible,
                        "valid": False,
                        "active": release_dir.name == current_build_id,
                        "error": str(exc),
                    }
                )
    releases_disk_bytes = sum(item["disk_bytes"] for item in releases)
    total_disk_bytes = _directory_disk_bytes(ui_root_path)
    legacy_dir = ui_root_path / "legacy-root"
    legacy_root_present = legacy_dir.is_dir() and not legacy_dir.is_symlink()
    manager_status: dict[str, Any] | None = None
    if builtin_root is not None:
        try:
            manager = UiReleaseManager(
                ui_root_path,
                Path(builtin_root),
                backend_contract=backend_contract,
            )
            manager_status = manager.status()
        except Exception:
            manager_status = None
    http_verified = False
    http_status: dict[str, Any] | None = None
    verification_error: str | None = None
    if verify_url is not None:
        try:
            http_status = verify_http_release(
                verify_url,
                current_build_id,
                timeout=timeout,
            )
            http_verified = True
        except Exception as exc:
            http_verified = False
            verification_error = str(exc)
    return {
        "schema_version": UI_STATUS_SCHEMA_VERSION,
        "ui_root": str(ui_root_path),
        "current": current_data,
        "current_build_id": current_build_id,
        "previous_build_id": previous_build_id,
        "activation_sequence": activation_sequence,
        "releases": releases,
        "installed_releases": releases,
        "release_count": len(releases),
        "releases_disk_bytes": releases_disk_bytes,
        "total_disk_bytes": total_disk_bytes,
        "legacy_root_present": legacy_root_present,
        "manager_status": manager_status,
        "http_verified": http_verified,
        "http_status": http_status,
        "verification_error": verification_error,
    }

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ui_release")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for cmd_name in ("build", "package"):
        build_parser = subparsers.add_parser(cmd_name)
        build_parser.add_argument("--checkout", required=True)
        build_parser.add_argument("--source-sha", required=True)
        build_parser.add_argument("--owned-root", required=True)
        build_parser.add_argument("--output", required=True)
        build_parser.add_argument("--build-id", default=None)
        build_parser.add_argument("--node-modules", default=None)
        build_parser.add_argument(
            "--min-free-bytes",
            type=int,
            default=DEFAULT_BUILD_MIN_FREE_BYTES,
        )
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--ui-root", required=True)
    install_parser.add_argument("--package", required=True)
    install_parser.add_argument(
        "--min-free-bytes",
        type=int,
        default=DEFAULT_INSTALL_MIN_FREE_BYTES,
    )
    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument("--ui-root", required=True)
    activate_parser.add_argument("--build-id", required=True)
    activate_parser.add_argument("--expected-current", default=None)
    activate_parser.add_argument("--require-expected", action="store_true")
    activate_parser.add_argument("--verify-url", default=None)
    activate_parser.add_argument("--timeout", type=float, default=5.0)
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--ui-root", required=True)
    rollback_parser.add_argument("--to", dest="to_build_id", default=None)
    rollback_parser.add_argument("--expected-current", default=None)
    rollback_parser.add_argument("--require-expected", action="store_true")
    rollback_parser.add_argument("--verify-url", default=None)
    rollback_parser.add_argument("--timeout", type=float, default=5.0)
    legacy_parser = subparsers.add_parser("preserve-legacy")
    legacy_parser.add_argument("--ui-root", required=True)
    legacy_parser.add_argument("--source-dir", "--source-dist", dest="source_dir", required=True)
    legacy_parser.add_argument("--overwrite", action="store_true")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--ui-root", required=True)
    status_parser.add_argument("--builtin-root", default=None)
    status_parser.add_argument("--verify-url", default=None)
    status_parser.add_argument("--timeout", type=float, default=5.0)
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"build", "package"}:
            result = build_ui_package(
                args.checkout,
                args.source_sha,
                args.owned_root,
                args.output,
                build_id=args.build_id,
                node_modules=args.node_modules,
                min_free_bytes=args.min_free_bytes,
            )
        elif args.command == "install":
            result = install_ui_package(
                args.ui_root,
                args.package,
                min_free_bytes=args.min_free_bytes,
            )
        elif args.command == "activate":
            result = activate_ui_release(
                args.ui_root,
                args.build_id,
                expected_current_build_id=args.expected_current,
                require_expected=args.require_expected,
                verify_url=args.verify_url,
                timeout=args.timeout,
            )
        elif args.command == "rollback":
            result = rollback_ui_release(
                args.ui_root,
                to_build_id=args.to_build_id,
                expected_current_build_id=args.expected_current,
                require_expected=args.require_expected,
                verify_url=args.verify_url,
                timeout=args.timeout,
            )
        elif args.command == "preserve-legacy":
            result = preserve_legacy_root(
                args.ui_root,
                args.source_dir,
                overwrite=args.overwrite,
            )
        elif args.command == "status":
            result = status_ui_release(
                args.ui_root,
                builtin_root=args.builtin_root,
                verify_url=args.verify_url,
                timeout=args.timeout,
            )
            if args.verify_url is not None and not result["http_verified"]:
                print(json.dumps(result, ensure_ascii=False))
                return 1
        else:  # pragma: no cover
            parser.error(f"unknown_command: {args.command}")
            return 2
    except UiReleaseVerificationError as exc:
        print(json.dumps(exc.receipt, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
