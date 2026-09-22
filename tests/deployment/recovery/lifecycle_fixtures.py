"""New private, synthetic fixtures. Read DEP-E only at its immutable Git commit."""

import json
import subprocess
from pathlib import Path

from fixtures import put

from ops.recovery.safety import digest

DEP_E_COMMIT = "cb371ab453621841e4a498598529a4ddcc41d3b2"
WORKSPACE = Path(__file__).resolve().parents[3]


def interface(name="release-manifest.example.json"):
    return subprocess.check_output(
        ["git", "show", DEP_E_COMMIT + ":deploy/tianshu/" + name], cwd=WORKSPACE
    )


def compose_fixture(source, *, extended=True):
    manifest = json.loads((source / "release-manifest.json").read_bytes())
    if extended:
        published = json.loads(interface())
        manifest["schema_version"] = "1.1.0"
        manifest["observability"] = published["observability"]
        manifest["volumes"].extend(
            v for v in published["volumes"] if v["product"] == "observability"
        )
        manifest["services"].extend(
            s for s in published["services"] if s["product"] == "observability"
        )
        for volume in manifest["volumes"]:
            if volume["product"] == "observability":
                folder = source / volume["host_path"]
                folder.mkdir(parents=True)
                (folder / "empty-subdirectory").mkdir()
                (folder / "checkpoint.bin").write_bytes(b"synthetic checkpoint\x00\r\n")
        put(source / "release-manifest.json", manifest)
        inventory = json.loads((source / "recovery-inventory.json").read_bytes())
        inventory["release_manifest_sha256"] = digest(
            (source / "release-manifest.json").read_bytes()
        )
        put(source / "recovery-inventory.json", inventory)
    documents = {"compose.json": {"services": {}}}
    if extended:
        documents["observability/compose.yaml"] = {
            "name": "generator-default-overridden-by-explicit-project",
            "services": {},
        }
    for service in manifest["services"]:
        key = service["id"]
        obs = service["product"] == "observability"
        mounts = [
            {
                "type": "bind",
                "source": str(source / v["host_path"]),
                "target": v["container_path"],
                "read_only": False,
            }
            for v in manifest["volumes"]
            if v["owner_service"] == key and v["mount"]
        ]
        documents["observability/compose.yaml" if obs else "compose.json"]["services"][
            key
        ] = {
            "image": "synthetic/" + key + ":fixture",
            "restart": "unless-stopped" if obs else "no",
            "volumes": mounts,
        }
    for name, document in documents.items():
        put(source / name, document)
    return list(documents)
