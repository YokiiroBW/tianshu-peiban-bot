"""Consume fixed DEP-G identity bytes; independently verify every immutable input."""

import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

from .manifest import PRODUCTS, release, sha256
from .safety import child, digest, file_hash, files, read_json, require, safe_path

DEP_G_COMMIT = "880c2c33ffc4c12b9267d38267c2dacde074890d"
INTERFACES = Path(__file__).with_name("interfaces")


def schema_check(document, schema):
    from jsonschema import Draft202012Validator

    require(
        not list(Draft202012Validator(schema).iter_errors(document)),
        "runtime_identity_schema_invalid",
    )


def load_identity(directory, path, expected_sha256, *, observed=False):
    directory, path = safe_path(directory), safe_path(path)
    require(path.is_relative_to(directory), "identity_outside_deployment")
    require(
        file_hash(path) == sha256(expected_sha256), "runtime_identity_digest_mismatch"
    )
    value = read_json(path)
    pin = read_json(INTERFACES / "dep-g-pin.json")
    require(pin["commit"] == DEP_G_COMMIT, "runtime_interface_version_mismatch")
    schema_path = INTERFACES / "runtime-identity.schema.json"
    require(
        file_hash(schema_path) == pin["files"][schema_path.name],
        "runtime_interface_digest_mismatch",
    )
    schema_check(value, read_json(schema_path))
    require(value["deployment_root"] == str(directory), "runtime_directory_mismatch")
    require(
        value["lease"]["path"] == str(directory / ".runtime-owner.lock"),
        "runtime_lease_mismatch",
    )
    manifest = release(read_json(child(directory, "release-manifest.json")))
    require(manifest["schema_version"] == "1.1.0", "five_log_owners_required")
    require(value["release_id"] == manifest["release_id"], "runtime_release_mismatch")
    integrity = value["integrity"]
    for filename, key in (
        ("release-manifest.json", "release_manifest_sha256"),
        ("bundle-integrity.json", "bundle_integrity_sha256"),
    ):
        require(
            file_hash(child(directory, filename)) == integrity[key],
            "runtime_input_drift",
        )
    index = read_json(child(directory, "bundle-integrity.json"))
    require(
        index.get("schema_version") == "1.0.0" and isinstance(index.get("files"), dict),
        "bundle_inventory_invalid",
    )
    require(not (directory / "INCOMPLETE").exists(), "initialization_incomplete")
    for filename, checksum in index["files"].items():
        require(
            file_hash(child(directory, filename)) == sha256(checksum),
            "bundle_bytes_changed",
        )
    for name in (
        "config",
        "private",
        "contracts",
        "tools",
        "observability-input",
        "observability/config",
        "observability/code",
    ):
        folder = child(directory, name, exists=False)
        if folder.exists():
            require(
                all(name + "/" + p in index["files"] for p in files(folder)),
                "unlisted_bundle_file",
            )
    require(
        integrity["config_sha256"]
        == {
            k: v
            for k, v in index["files"].items()
            if k.startswith(("config/", "contracts/", "tools/"))
        },
        "runtime_config_binding_mismatch",
    )
    require(
        integrity["sources"]
        == {p: manifest["products"][p]["source"] for p in PRODUCTS},
        "runtime_source_binding_mismatch",
    )
    for group, suffix, filename in (
        ("core", "", "compose.json"),
        ("observability", "-obs", "observability/compose.yaml"),
    ):
        project = value["projects"][group]
        compose = child(directory, filename)
        require(
            project["name"] == value["project_name"] + suffix
            and project["directory"] == str(compose.parent)
            and project["compose_file"] == str(compose),
            "runtime_project_mismatch",
        )
        require(project["compose_json"] is not None, "runtime_compose_missing")
        document = read_json(compose)
        normalized = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        require(
            project["compose_json"] == document
            and project["compose_sha256"] == file_hash(compose)
            and project["compose_canonical_sha256"] == digest(normalized),
            "runtime_compose_mismatch",
        )
        key = "compose_sha256" if group == "core" else "observability_compose_sha256"
        require(integrity[key] == project["compose_sha256"], "runtime_compose_mismatch")
        expected_owners = (
            PRODUCTS
            if group == "core"
            else {
                "obs-" + n for n in ("vector", "loki", "grafana", "prometheus", "guard")
            }
        )
        require(
            set(document["services"]) == expected_owners, "runtime_owner_set_mismatch"
        )
        for owner, service in document["services"].items():
            actual = value["services"][owner]
            require(
                actual["project"] == project["name"]
                and actual["image_reference"] == service["image"],
                "runtime_service_binding_mismatch",
            )
            if observed:
                require(
                    isinstance(actual["image_id"], str)
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", actual["image_id"])
                    and actual["platform"] == "linux/amd64",
                    "runtime_image_not_observed",
                )
                if actual["status"] == "observed":
                    require(
                        actual["container_id"] is not None
                        and actual["uid"] == actual["gid"] == 10001,
                        "runtime_container_not_observed",
                    )
    mounts = {m["id"]: m for m in value["mounts"]}
    expected_mounts = {v["id"]: v for v in manifest["volumes"] if v["mount"]}
    require(
        len(mounts) == len(value["mounts"]) and set(mounts) == set(expected_mounts),
        "runtime_mount_set_mismatch",
    )
    for key, volume in expected_mounts.items():
        mount = mounts[key]
        expected = {
            k: volume[k]
            for k in ("id", "container_path", "owner_service", "backup_group")
        }
        expected["host_path"] = str(child(directory, volume["host_path"]))
        require(
            {k: mount[k] for k in expected} == expected,
            "runtime_mount_binding_mismatch",
        )
        if observed:
            st = Path(expected["host_path"]).stat()
            require(
                mount["uid"] == st.st_uid == 10001
                and mount["gid"] == st.st_gid == 10001,
                "runtime_mount_owner_mismatch",
            )
    return value


@contextmanager
def runtime_lease(directory):
    """The same inode/protocol as DEP-G. Never unlink, replace or inherit it."""
    require(sys.platform == "linux", "linux_lease_required")
    import fcntl

    path = child(safe_path(directory), ".runtime-owner.lock", exists=False)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            require(False, "runtime_owner_busy")
        yield
    finally:
        os.close(fd)
