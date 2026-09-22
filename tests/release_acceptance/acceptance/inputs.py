"""Consumer-side JSON pointer mapping, not another release-manifest schema."""

import io
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from .evidence import canonical, digest, read_json

ROLES = ("platform", "companion", "memory", "gateway")
SHA = re.compile(r"^[a-f0-9]{64}$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pointer(value, path):
    require(
        isinstance(path, str) and (path == "" or path.startswith("/")),
        "invalid_pointer",
    )
    for key in path.split("/")[1:]:
        key = key.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def inside(root, name):
    root = Path(root).resolve()
    require(
        isinstance(name, str) and name and "\\" not in name and ":" not in name,
        "unsafe_path",
    )
    path = PurePosixPath(name)
    require(
        not path.is_absolute() and all(x not in ("..", ".") for x in path.parts),
        "unsafe_path",
    )
    target = (root / name).resolve()
    require(target.is_relative_to(root) and target != root, "path_escape")
    return target


def load_binding(manifest_path, mapping_path, contracts_root):
    raw = Path(manifest_path).read_bytes()
    source, mapping = read_json(manifest_path), read_json(mapping_path)
    products = {}
    for role in ROLES:
        fields = mapping["products"][role]
        item = {
            key: pointer(source, fields[key])
            for key in ("repo", "commit", "image", "digest")
        }
        require(COMMIT.fullmatch(item["commit"]) is not None, "full_commit_required")
        require(isinstance(item["repo"], str) and item["repo"], "repo_id_required")
        require(
            item["digest"] is None
            or re.fullmatch(r"sha256:[a-f0-9]{64}", item["digest"]),
            "invalid_image_digest",
        )
        require(
            item["image"] is None or isinstance(item["image"], str),
            "invalid_image_reference",
        )
        products[role] = item
    contracts = pointer(source, mapping["contracts"])
    if isinstance(contracts, list):
        flattened = {}
        for package in contracts:
            prefix = package["path"].removeprefix("contracts/").rstrip("/")
            flattened[prefix + "/manifest.json"] = package["manifest_sha256"]
            for member in package["files"]:
                name = member["path"]
                name = name.removeprefix("contracts/")
                if not name.startswith(prefix + "/"):
                    name = prefix + "/" + name
                require(
                    name not in flattened or flattened[name] == member["sha256"],
                    "duplicate_contract_binding",
                )
                flattened[name] = member["sha256"]
        contracts = flattened
    require(isinstance(contracts, dict) and contracts, "contracts_required")
    verified = {}
    for name, expected in contracts.items():
        require(
            isinstance(expected, str) and SHA.fullmatch(expected),
            "invalid_contract_hash",
        )
        actual = digest(inside(contracts_root, name).read_bytes())
        require(actual == expected, "contract_hash_mismatch")
        verified[name] = actual
    require(
        "diagnostics/v1/event.schema.json" in verified
        and "diagnostics/v1/manifest.json" in verified,
        "diagnostics_binding_required",
    )
    # Verify every file of each bound package against its manifest, with raw bytes.
    for name in tuple(verified):
        if not name.endswith("/manifest.json"):
            continue
        package = inside(contracts_root, name).parent
        package_manifest = read_json(package / "manifest.json")
        files = package_manifest.get("files", package_manifest.get("sha256", {}))
        require(isinstance(files, dict) and files, "contract_file_hashes_required")
        for child, expected in files.items():
            require(
                isinstance(expected, str) and SHA.fullmatch(expected),
                "invalid_contract_hash",
            )
            prefix = str(PurePosixPath(name).parent) + "/"
            child = child.removeprefix(prefix)
            member_raw = inside(package, child).read_bytes()
            normalized = "CRLF" in package_manifest.get(
                "hash_basis", ""
            ) and "LF" in package_manifest.get("hash_basis", "")
            # Legacy packages explicitly define normalized member hashes. Release
            # hashes above and diagnostics hashes always use the original bytes.
            require(
                digest(member_raw.replace(b"\r\n", b"\n") if normalized else member_raw)
                == expected,
                "contract_member_hash_mismatch",
            )
            verified[str(PurePosixPath(name).parent / child)] = digest(member_raw)
    require(pointer(source, mapping["schema_version"]), "schema_version_required")
    return {
        "manifest_sha256": digest(raw),
        "mapping_sha256": digest(Path(mapping_path).read_bytes()),
        "release_id": pointer(source, mapping["release_id"]),
        "products": products,
        "release_status": source.get("status", "unverified"),
        "features": source.get("features", []),
        "release_blockers": source.get("blockers", []),
        "contracts": verified,
        "identity_basis": "static_input_only",
    }


def export_sources(binding, repositories, destination, contracts_root):
    """Only committed bytes; no working-tree copy, filters, symlinks, or git execution hooks."""
    destination = Path(destination).resolve()
    require(not destination.exists(), "snapshot_target_must_be_new")
    archives = {}
    # Validate all sources before creating any output.
    for role in ROLES:
        commit = binding["products"][role]["commit"]
        repo = str(Path(repositories[role]).resolve())
        resolved = (
            subprocess.check_output(
                ["git", "-C", repo, "rev-parse", "--verify", commit + "^{commit}"],
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            .decode()
            .strip()
        )
        require(resolved == commit, "commit_mismatch")
        archives[role] = subprocess.check_output(
            ["git", "-C", repo, "archive", "--format=tar", commit],
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    destination.mkdir(parents=True)
    hashes, file_hashes = {}, {}
    for role, raw in archives.items():
        folder = destination / role
        folder.mkdir()
        hashes[role] = digest(raw)
        file_hashes[role] = {}
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                target = inside(folder, member.name.rstrip("/"))
                require(
                    member.isdir() or member.isfile(), "archive_link_or_special_file"
                )
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as stream:
                        contents = stream.read()
                        target.write_bytes(contents)
                        file_hashes[role][member.name] = digest(contents)
    for name, expected in binding["contracts"].items():
        raw = inside(contracts_root, name).read_bytes()
        require(digest(raw) == expected, "contract_changed_during_export")
        target = inside(destination / "contracts", name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    marker = {"binding": binding, "archive_sha256": hashes, "file_sha256": file_hashes}
    (destination / "snapshot.json").write_bytes(canonical(marker) + b"\n")
    return marker


def verify_snapshot(directory):
    directory = Path(directory).resolve()
    marker = read_json(directory / "snapshot.json")
    for role in ROLES:
        expected = marker["file_sha256"][role]
        actual = {
            p.relative_to(directory / role).as_posix(): digest(p.read_bytes())
            for p in (directory / role).rglob("*")
            if p.is_file()
        }
        require(actual == expected, "snapshot_files_changed")
    for name, expected in marker["binding"]["contracts"].items():
        require(
            digest(inside(directory / "contracts", name).read_bytes()) == expected,
            "snapshot_contract_changed",
        )
    return marker
