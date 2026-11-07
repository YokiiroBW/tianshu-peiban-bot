
import sys
import re
import json
import hashlib
import mimetypes
import shutil
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from uuid import uuid4
from collections.abc import Mapping as AbcMapping

if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl

UI_PACKAGE_SCHEMA_VERSION = "tianshu.ui.release.v1"
UI_CURRENT_SCHEMA_VERSION = "tianshu.ui.current.v1"
UI_STATUS_SCHEMA_VERSION = "tianshu.ui.status.v1"
UI_BUILD_ID_RE = re.compile(r"^ui-[a-z0-9][a-z0-9._-]{3,60}$")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

BACKEND_UI_CONTRACT = { 
    "contract_id": "tianshu.web_api.v1", 
    "api_prefix": "/api/v1", 
    "contract_version": 1, 
    "supported_package_schemas": ("tianshu.ui.release.v1",), 
    "provided_capabilities": frozenset({"session.v1", "command_recovery.v1", "data_generation_header.v1", "ui_version_status.v1"}) 
}

def validate_build_id(build_id: str) -> str:
    if not isinstance(build_id, str) or ".." in build_id or "/" in build_id or "\\" in build_id or not UI_BUILD_ID_RE.match(build_id):
        raise ValueError("invalid_ui_build_id")
    return build_id

def validate_source_sha(source_sha: str) -> str:
    if not SOURCE_SHA_RE.match(source_sha):
        raise ValueError("invalid_ui_source_sha")
    return source_sha

def release_base_url(build_id: str) -> str:
    validate_build_id(build_id)
    return f"/_ui/releases/{build_id}/"

def validate_relative_asset_path(rel_path: str) -> str:
    from pathlib import PurePosixPath
    if not rel_path or rel_path.startswith('/') or '\\' in rel_path or any(ord(c) < 32 for c in rel_path):
        raise ValueError("unsafe_ui_asset_path")
    parts = PurePosixPath(rel_path).parts
    if "." in parts or ".." in parts or rel_path == "release.json" or rel_path.startswith(".staging") or rel_path.startswith(".lock"):
        raise ValueError("unsafe_ui_asset_path")
    return rel_path

def file_sha256(path: Path) -> str:
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def compute_package_sha256(files_manifest: Mapping[str, Mapping[str, Any]]) -> str:
    sha256_hash = hashlib.sha256()
    for rel_path in sorted(files_manifest.keys()):
        entry = files_manifest[rel_path]
        data = f"{rel_path}\0{entry['sha256']}\0{int(entry['size'])}\n".encode("utf-8")
        sha256_hash.update(data)
    return sha256_hash.hexdigest()

def default_compatibility_requirement(backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> dict[str, Any]:
    return {
        "contract_id": backend_contract["contract_id"],
        "api_prefix": backend_contract["api_prefix"],
        "required_capabilities": sorted(list(backend_contract["provided_capabilities"])),
        "min_backend_contract_version": 1,
        "max_backend_contract_version": 1
    }

def validate_compatibility(manifest: Mapping[str, Any], backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> None:
    comp = manifest.get("compatibility")
    if not isinstance(comp, dict):
        raise ValueError("compatibility_mismatch: not a dict")
    
    required = ["contract_id", "api_prefix", "required_capabilities", "min_backend_contract_version", "max_backend_contract_version"]
    if not all(k in comp for k in required):
        raise ValueError("compatibility_mismatch: missing fields")
        
    if (comp["contract_id"] != backend_contract["contract_id"] or 
        comp["api_prefix"] != backend_contract["api_prefix"] or
        not isinstance(comp["min_backend_contract_version"], int) or
        not isinstance(comp["max_backend_contract_version"], int) or
        not (comp["min_backend_contract_version"] <= backend_contract["contract_version"] <= comp["max_backend_contract_version"])):
        raise ValueError("compatibility_mismatch: contract mismatch")
        
    req_caps = comp["required_capabilities"]
    if not isinstance(req_caps, list) or not req_caps or not set(req_caps).issubset(backend_contract["provided_capabilities"]):
        raise ValueError("compatibility_mismatch: capability mismatch")

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

class ui_release_lock:
    def __init__(self, ui_root: Path, *, blocking: bool = False):
        self.lock_file = ui_root / '.lock'
        self.blocking = blocking
        self.fd = None

    def __enter__(self):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.lock_file, 'wb')
        try:
            if sys.platform == 'win32':
                msvcrt.locking(self.fd.fileno(), msvcrt.LK_NBLCK if not self.blocking else msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX | (fcntl.LOCK_NB if not self.blocking else 0))
        except (OSError, IOError):
            self.fd.close()
            raise RuntimeError('ui_release_lock_busy')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd:
            if sys.platform == 'win32':
                msvcrt.locking(self.fd.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
            self.fd.close()

def build_release_manifest(dist_dir: Path, *, build_id: str, source_sha: str, created_at: str | None = None, compatibility: Mapping[str, Any] | None = None) -> dict[str, Any]:
    validate_build_id(build_id)
    validate_source_sha(source_sha)
    manifest = {
        "schema_version": UI_PACKAGE_SCHEMA_VERSION,
        "build_id": build_id,
        "source_sha": source_sha,
        "vite_base": release_base_url(build_id),
        "entrypoint": "index.html",
        "compatibility": compatibility or default_compatibility_requirement(),
        "files": {}
    }
    
    files = {}
    for path in dist_dir.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.name == "release.json":
            continue
        rel_path = validate_relative_asset_path(str(path.relative_to(dist_dir)).replace("\\", "/"))
        content_type, _ = mimetypes.guess_type(path)
        files[rel_path] = {
            "sha256": file_sha256(path),
            "size": path.stat().st_size,
            "content_type": content_type or "application/octet-stream"
        }
        
    if "index.html" not in files:
        raise ValueError("index.html_missing")
        
    manifest["files"] = files
    manifest["package_sha256"] = compute_package_sha256(files)
    return manifest

def validate_release_manifest(manifest: Mapping[str, Any], *, expected_build_id: str | None = None, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> dict[str, Any]:
    if manifest.get("schema_version") != UI_PACKAGE_SCHEMA_VERSION:
        raise ValueError("schema_version_mismatch")
    build_id = manifest.get("build_id")
    if not isinstance(build_id, str):
        raise ValueError("invalid_build_id")
    if expected_build_id and build_id != expected_build_id:
        raise ValueError("build_id_mismatch")
    
    validate_source_sha(manifest.get("source_sha", ""))
    
    if manifest.get("vite_base") != release_base_url(build_id):
        raise ValueError("vite_base_mismatch")
    if manifest.get("entrypoint") != "index.html":
        raise ValueError("entrypoint_mismatch")
        
    validate_compatibility(manifest, backend_contract)
    
    files = manifest.get("files")
    if not isinstance(files, dict) or not files or "index.html" not in files:
        raise ValueError("invalid_files_manifest")
        
    for rel_path, entry in files.items():
        validate_relative_asset_path(rel_path)
        if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64 or not isinstance(entry.get("size"), int) or entry["size"] < 0:
            raise ValueError("invalid_file_entry")
            
    if manifest.get("package_sha256") != compute_package_sha256(files):
        raise ValueError("package_sha256_mismatch")
        
    return manifest

def verify_release_directory(release_dir: Path, *, expected_build_id: str | None = None, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT, require_versioned_index: bool = True) -> VerifiedReleaseSnapshot:
    if release_dir.is_symlink() or not release_dir.exists():
        raise ValueError('unsafe_release_symlink')
        
    release_json = release_dir / 'release.json'
    with open(release_json, 'r') as f:
        manifest = validate_release_manifest(json.load(f), expected_build_id=expected_build_id, backend_contract=backend_contract)
        
    files = {}
    for path in release_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError('unsafe_release_symlink')
        if path.is_file() and path.name != 'release.json':
            rel_path = str(path.relative_to(release_dir)).replace("\\", "/")
            files[rel_path] = path
            
    if set(files.keys()) != set(manifest['files'].keys()):
        raise ValueError('release_files_mismatch')
        
    for rel_path, path in files.items():
        entry = manifest['files'][rel_path]
        if path.stat().st_size != entry['size'] or file_sha256(path) != entry['sha256']:
            raise ValueError('release_file_digest_mismatch')
            
    if require_versioned_index:
        index_content = (release_dir / 'index.html').read_text(encoding='utf-8')
        build_id = manifest['build_id']
        if f'/_ui/releases/{build_id}/' not in index_content or re.search(r'/_ui/releases/(?!' + re.escape(build_id) + r')[^/]+/', index_content):
            raise ValueError('entrypoint_base_mismatch')
            
    return VerifiedReleaseSnapshot(manifest['build_id'], manifest['source_sha'], release_dir, manifest, frozenset(files.keys()), manifest['package_sha256'])

def load_builtin_snapshot(builtin_dir: Path, *, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> VerifiedReleaseSnapshot:
    if (builtin_dir / 'release.json').is_file():
        snapshot = verify_release_directory(builtin_dir, backend_contract=backend_contract, require_versioned_index=False)
        return VerifiedReleaseSnapshot(snapshot.build_id, snapshot.source_sha, snapshot.root, snapshot.manifest, snapshot.files, snapshot.package_sha256, True, f'/_ui/releases/' in (builtin_dir / 'index.html').read_text(encoding='utf-8'))
    
    if not builtin_dir.exists() or builtin_dir.is_symlink() or not (builtin_dir / 'index.html').is_file():
        raise ValueError('invalid_builtin_dir')
        
    files = {}
    for path in builtin_dir.rglob("*"):
        if path.is_symlink() or not path.is_file(): continue
        files[str(path.relative_to(builtin_dir)).replace("\\", "/")] = {"sha256": file_sha256(path), "size": path.stat().st_size}
        
    package_sha = compute_package_sha256(files)
    return VerifiedReleaseSnapshot(f'builtin-{package_sha[:12]}', '0'*40, builtin_dir, {}, frozenset(files.keys()), package_sha, True, False)

def install_release_directory(ui_root: Path, source_dir: Path, *, min_free_bytes: int = 64 * 1024 * 1024, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> dict[str, Any]:
    snapshot = verify_release_directory(source_dir, backend_contract=backend_contract)
    
    total_bytes = sum(entry['size'] for entry in snapshot.manifest['files'].values())
    if shutil.disk_usage(ui_root).free < min_free_bytes + total_bytes:
        raise ValueError('ui_release_insufficient_free_space')
        
    with ui_release_lock(ui_root):
        target_dir = ui_root / 'releases' / snapshot.build_id
        if target_dir.exists():
            existing = verify_release_directory(target_dir, expected_build_id=snapshot.build_id, backend_contract=backend_contract)
            if existing.package_sha256 == snapshot.package_sha256 and existing.source_sha == snapshot.source_sha:
                return {'build_id': snapshot.build_id, 'source_sha': snapshot.source_sha, 'package_sha256': snapshot.package_sha256, 'package_installed': True, 'reused_existing': True, 'pointer_switched': False, 'http_verified': False}
            raise ValueError('build_id_content_conflict')
        
        staging_dir = ui_root / '.staging' / f'install-{uuid4().hex}'
        staging_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, staging_dir)
        verify_release_directory(staging_dir, expected_build_id=snapshot.build_id, backend_contract=backend_contract)
        staging_dir.rename(target_dir)
        
        return {'build_id': snapshot.build_id, 'source_sha': snapshot.source_sha, 'package_sha256': snapshot.package_sha256, 'package_installed': True, 'reused_existing': False, 'pointer_switched': False, 'http_verified': False}

def switch_current_release(ui_root: Path, target_build_id: str, *, expected_current_build_id: str | None = None, require_expected: bool = False, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT) -> dict[str, Any]:
    with ui_release_lock(ui_root):
        target_dir = ui_root / 'releases' / target_build_id
        snapshot = verify_release_directory(target_dir, expected_build_id=target_build_id, backend_contract=backend_contract)
        
        current_file = ui_root / 'current.json'
        current_data = {}
        if current_file.exists():
            with open(current_file, 'r') as f:
                current_data = json.load(f)
                
        prev_build_id = current_data.get('build_id')
        if (require_expected or expected_current_build_id is not None) and prev_build_id != expected_current_build_id:
            raise ValueError('expected_current_build_id_mismatch')
            
        new_data = {
            'schema_version': UI_CURRENT_SCHEMA_VERSION,
            'build_id': target_build_id,
            'activation_sequence': current_data.get('activation_sequence', 0) + 1
        }
        
        staging_file = ui_root / '.staging' / f'current-{uuid4().hex}.tmp'
        staging_file.parent.mkdir(parents=True, exist_ok=True)
        with open(staging_file, 'w') as f:
            json.dump(new_data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(staging_file, current_file)
        
        return {
            'previous_build_id': prev_build_id,
            'current_build_id': target_build_id,
            'build_id': target_build_id,
            'source_sha': snapshot.source_sha,
            'package_sha256': snapshot.package_sha256,
            'activation_sequence': new_data['activation_sequence'],
            'package_installed': True,
            'pointer_switched': True,
            'http_verified': False
        }

class UiReleaseManager:
    def __init__(self, ui_root: Path, builtin_root: Path, backend_contract: Mapping[str, Any] = BACKEND_UI_CONTRACT):
        self.ui_root = ui_root
        self.builtin_snapshot = load_builtin_snapshot(builtin_root, backend_contract=backend_contract)
        self.backend_contract = backend_contract
        self._verified_releases = {}
        self._current_snapshot = None
        self._current_mtime = 0
        self._last_known_good = self.builtin_snapshot

    def _get_current(self) -> VerifiedReleaseSnapshot:
        current_file = self.ui_root / 'current.json'
        if not current_file.exists(): return self._last_known_good
        
        mtime = current_file.stat().st_mtime
        if mtime == self._current_mtime and self._current_snapshot: return self._current_snapshot
        
        try:
            with open(current_file, 'r') as f:
                data = json.load(f)
            build_id = data['build_id']
            if build_id in self._verified_releases:
                snapshot = self._verified_releases[build_id]
            else:
                snapshot = verify_release_directory(self.ui_root / 'releases' / build_id, backend_contract=self.backend_contract)
                self._verified_releases[build_id] = snapshot
            self._current_snapshot = snapshot
            self._current_mtime = mtime
            return snapshot
        except Exception:
            return self._last_known_good

    def status(self) -> dict[str, Any]:
        snapshot = self._get_current()
        return {
            "schema_version": UI_STATUS_SCHEMA_VERSION,
            "served_build_id": snapshot.build_id,
            "served_source_sha": snapshot.source_sha,
            "served_mode": "external" if not snapshot.builtin else "builtin",
            "requested_build_id": snapshot.build_id,
            "activation_sequence": snapshot.manifest.get("activation_sequence", 1),
            "compatible": True,
            "degraded": snapshot == self._last_known_good and self._last_known_good != self.builtin_snapshot,
            "degraded_reason": "manifest_mismatch" if snapshot == self._last_known_good and self._last_known_good != self.builtin_snapshot else None,
            "contract_id": self.backend_contract["contract_id"],
            "contract_version": self.backend_contract["contract_version"]
        }

    def resolve_asset(self, build_id: str, rel_path: str) -> Optional[Path]:
        if build_id in self._verified_releases:
            snapshot = self._verified_releases[build_id]
        else:
            try:
                snapshot = verify_release_directory(self.ui_root / 'releases' / build_id, backend_contract=self.backend_contract)
                self._verified_releases[build_id] = snapshot
            except Exception: return None
            
        if rel_path in snapshot.files:
            return snapshot.root / rel_path
        return None
