"""Read-only inventory of an explicitly synthetic, initialized product deployment.

No schemas are created and no SQL writes populate authorization or recovery facts.
The complete state tree is registered, including first-install migration backups.
"""

from .manifest import release
from .safety import child, digest, file_hash, files, read_json, require


def inventory(directory, *, max_files=10000):
    manifest_path = child(directory, "release-manifest.json")
    manifest = release(read_json(manifest_path))
    require(manifest["schema_version"] == "1.1.0", "five_log_owners_required")
    volumes = [
        v for v in manifest["volumes"] if v["category"] == "state" and v["mount"]
    ]
    require(len(volumes) == 4, "four_state_roots_required")
    resources, by_path = [], {}
    guards = {
        v["host_path"]: v for v in manifest["volumes"] if v["category"] == "guard"
    }
    for volume in volumes:
        base = child(directory, volume["host_path"])
        names = files(base, max_files=max_files)
        sqlite_paths = set()
        for name in names:
            path = child(base, name)
            with path.open("rb") as stream:
                if stream.read(16) == b"SQLite format 3\x00":
                    sqlite_paths.add(name)
        for name in names:
            if any(
                name == database + suffix
                for database in sqlite_paths
                for suffix in ("-wal", "-shm")
            ):
                continue
            relative = volume["host_path"] + "/" + name
            kind = "sqlite" if name in sqlite_paths else "file"
            selected_volume = volume
            if relative in guards:
                kind, selected_volume = "guard", guards[relative]
            elif name == ".deployment-owner.lock" or (
                volume["product"] == "companion"
                and name.endswith(".owner")
                and name.removesuffix(".owner") in sqlite_paths
            ):
                kind = "owner_lock"
            item = {
                "id": "r-" + digest(relative.encode())[:32],
                "volume_id": selected_volume["id"],
                "path": relative,
                "kind": kind,
            }
            resources.append(item)
            by_path[relative] = item
            require(len(resources) <= max_files, "file_count_limit")
    checks = []
    for guard in guards:
        require(guard.endswith(".source-guard.json"), "unsupported_guard_path")
        database = guard.removesuffix(".source-guard.json")
        require(
            guard in by_path
            and database in by_path
            and by_path[database]["kind"] == "sqlite",
            "memory_guard_required",
        )
        checks.append(
            {
                "kind": "memory-source-v3",
                "database": by_path[database]["id"],
                "guard": by_path[guard]["id"],
            }
        )
    # Pin every settings document; values, tokens and paths in settings never leave IO.
    refs = [
        {
            "id": service["id"],
            "reference": "deployment:" + service["config_path"],
            "sha256": file_hash(child(directory, service["config_path"])),
        }
        for service in manifest["services"]
    ]
    return {
        "schema_version": "1.0.0",
        "release_manifest_sha256": file_hash(manifest_path),
        "resources": sorted(resources, key=lambda item: item["path"]),
        "guard_checks": checks,
        "config_references": refs,
    }
