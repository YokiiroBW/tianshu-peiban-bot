"""Freeze the created new container's exact image, command, mounts and release identity."""

import argparse
import json
import os
from pathlib import Path

from capacity import CapacityGuard, docker


def freeze(root, container):
    root = Path(root).resolve(strict=True)
    install = json.loads((root / "installation.json").read_text())
    if install["deployment_root"] != str(root):
        raise ValueError("installation_root_mismatch")
    row = json.loads(docker("docker", "inspect", container))[0]
    image = json.loads(docker("docker", "image", "inspect", install["image"]))[0]
    if (
        row["Image"] != image["Id"]
        or (image["Config"].get("Labels") or {}).get("org.opencontainers.image.revision")
        != install["source_sha"]
    ):
        raise ValueError("installation_image_mismatch")
    files = [str((root / name).resolve()) for name in install.get("config_files", ["compose.yaml"])]
    if any(not Path(name).is_relative_to(root) for name in files):
        raise ValueError("foreign_compose_file")
    binds = [
        {"source": str(root / "config"), "target": "/config", "read_only": True},
        {"source": str(root / "data"), "target": "/data", "read_only": False},
    ]
    external = install["external_binds"]
    binds.extend({key: item[key] for key in ("source", "target", "read_only")} for item in external)
    owned = [item["source"] for item in external if item.get("owned") is True]
    config = {
        "schema_version": "tianshu.capacity.v1",
        "deployment_root": str(root),
        "owned_external_paths": owned,
        "targets": [str(root), *owned],
        "state_directory": str(root / "guard/state"),
        "free_paths": ["/var/lib/docker"],
        "min_free_bytes": 21474836480,
        "max_deployment_bytes": 21474836480,
        "poll_seconds": 5,
        "term_timeout_seconds": 120,
        "docker_binary": "/usr/bin/docker",
        "container": {
            "id": row["Id"],
            "image_id": row["Image"],
            "image_bytes": image["Size"],
            "project": "tianshu-core",
            "service": "tianshu",
            "release_id": install["source_sha"],
            "config_files": ",".join(files),
            "entrypoint": ["python", "-m", "tianshu"],
            "command": ["serve", "--config", "/config/settings.json"],
            "user": "0:10001",
            "binds": binds,
        },
    }
    guard = CapacityGuard(config)
    guard.owned_container()
    path = root / "guard/capacity.json"
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(config, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    return {
        "guard_config": str(path),
        "container_id": row["Id"],
        "image_id": row["Image"],
        "binds": len(binds),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--container", required=True)
    print(json.dumps(freeze(**vars(parser.parse_args()))))


if __name__ == "__main__":
    main()
