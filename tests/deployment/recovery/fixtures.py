"""Explicitly synthetic data. This is not a product initialization entry point."""

import argparse
import json
import sqlite3
import sys
import uuid
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ops.recovery.engine import create_sandbox
from ops.recovery.safety import canonical, digest
from ops.recovery.snapshot import fingerprint

PRODUCTS = ("platform", "companion", "memory", "gateway")
BASELINES = {
    "platform": "745ee9dd0b97ff1822a8fd4362e0626b7e3bb316",
    "companion": "ab7c58250961807a2016c5f9128ce34b0065e182",
    "memory": "9df2e9e2eb6c2c36778f215306e6b20c377e18c4",
    "gateway": "51121e6c02ed60605be14f31b19b484bc117a746",
}


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def fixture(root):
    scope_id, deployment_id = str(uuid.uuid4()), str(uuid.uuid4())
    create_sandbox(root, scope_id, execute=True)
    source = root / "deployments/source"
    source.mkdir()
    manifest = {
        "schema_version": "1.0.0",
        "release_id": "synthetic-v1",
        "status": "candidate",
        "products": {},
        "volumes": [],
        "contracts": [],
        "services": [],
        "features": [
            {
                "id": "fixture_only",
                "enabled": True,
                "verification": "unverified",
                "reason": "Synthetic application; no product readiness claim",
            }
        ],
        "blockers": ["synthetic_only"],
        "evidence": [],
    }
    resources = []
    for product in PRODUCTS:
        manifest["products"][product] = {
            "source": {"repo": "tianshu-" + product, "commit": BASELINES[product]},
            "image": {
                "reference": "synthetic/" + product + ":fixture",
                "digest": None,
                "verification": "unverified",
            },
            "service_role": product,
        }
        manifest["services"].append(
            {
                "id": product,
                "product": product,
                "role": product,
                "port": 8000 + len(resources),
                "replicas": 1,
                "hostname": product + ".invalid",
                "tls_server_names": [product + ".invalid"],
                "config_path": "config/" + product + ".json",
            }
        )
        for category, folder in (("state", "data"), ("logs", "logs")):
            path = folder + "/" + product
            (source / path).mkdir(parents=True)
            manifest["volumes"].append(
                {
                    "id": product + "-" + category,
                    "product": product,
                    "category": category,
                    "host_path": path,
                    "container_path": "/" + folder,
                    "owner_service": product,
                    "backup_group": product
                    if category == "state"
                    else product + "-logs",
                    "mount": True,
                    "kind": "directory",
                }
            )
        db_path = "data/" + product + "/main.db"
        resources.append(
            {
                "id": product + "-db",
                "volume_id": product + "-state",
                "path": db_path,
                "kind": "sqlite",
            }
        )
        db = sqlite3.connect(source / db_path)
        db.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA user_version=1; CREATE TABLE facts (id TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE receipts (id TEXT PRIMARY KEY, status TEXT NOT NULL);"
        )
        db.executemany(
            "INSERT INTO facts VALUES(?,?)",
            [
                ("visible", "synthetic-answer"),
                ("deleted", "forgotten"),
                ("model", "revoked"),
                ("sequence", "3"),
            ],
        )
        db.execute("INSERT INTO receipts VALUES('unknown-job','unknown')")
        if product == "memory":
            db.execute(
                "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.executemany(
                "INSERT INTO metadata VALUES(?,?)",
                [
                    ("schema", "3"),
                    ("source_instance", deployment_id),
                    ("source_revision", "7"),
                    ("source_recovery", "ready"),
                ],
            )
        db.commit()
        db.close()
    sidecar = "data/platform/main.db.web-inputs.sqlite"
    db = sqlite3.connect(source / sidecar)
    db.execute("CREATE TABLE receipts (id TEXT PRIMARY KEY,status TEXT NOT NULL)")
    db.execute("INSERT INTO receipts VALUES('input-1','accepted')")
    db.commit()
    db.close()
    manifest["volumes"].append(
        {
            "id": "platform-sidecar",
            "product": "platform",
            "category": "sidecar",
            "host_path": sidecar,
            "container_path": "/data/main.db.web-inputs.sqlite",
            "owner_service": "platform",
            "backup_group": "platform",
            "mount": False,
            "kind": "file",
        }
    )
    resources.append(
        {
            "id": "platform-sidecar",
            "volume_id": "platform-sidecar",
            "path": sidecar,
            "kind": "sqlite",
        }
    )
    guard = "data/memory/main.db.source-guard.json"
    put(
        source / guard,
        {"schema": 3, "instance": deployment_id, "revision": 7, "recovery": "ready"},
    )
    manifest["volumes"].append(
        {
            "id": "memory-guard",
            "product": "memory",
            "category": "guard",
            "host_path": guard,
            "container_path": "/data/main.db.source-guard.json",
            "owner_service": "memory",
            "backup_group": "memory",
            "mount": False,
            "kind": "file",
        }
    )
    resources.append(
        {
            "id": "memory-guard",
            "volume_id": "memory-guard",
            "path": guard,
            "kind": "guard",
        }
    )
    owner = "data/companion/main.db.owner"
    (source / owner).write_bytes(b"0")
    manifest["volumes"].append(
        {
            "id": "companion-owner",
            "product": "companion",
            "category": "sidecar",
            "host_path": owner,
            "container_path": "/data/main.db.owner",
            "owner_service": "companion",
            "backup_group": "companion",
            "mount": False,
            "kind": "file",
        }
    )
    resources.append(
        {
            "id": "companion-owner",
            "volume_id": "companion-owner",
            "path": owner,
            "kind": "owner_lock",
        }
    )
    package = source / "contracts/synthetic/v1"
    put(package / "manifest.json", {"fixture": True})
    (package / "README.md").write_bytes(b"Synthetic contract original CRLF bytes.\r\n")
    contract_files = [
        {"path": path.name, "sha256": digest(path.read_bytes())}
        for path in sorted(package.iterdir())
    ]
    manifest["contracts"].append(
        {
            "id": "synthetic/v1",
            "path": "contracts/synthetic/v1",
            "manifest_sha256": digest((package / "manifest.json").read_bytes()),
            "files": contract_files,
        }
    )
    put(source / "release-manifest.json", manifest)
    inventory = {
        "schema_version": "1.0.0",
        "release_manifest_sha256": digest(
            (source / "release-manifest.json").read_bytes()
        ),
        "resources": resources,
        "guard_checks": [
            {
                "kind": "memory-source-v3",
                "database": "memory-db",
                "guard": "memory-guard",
            }
        ],
        "config_references": [
            {
                "id": p + "-settings",
                "reference": "fixture:" + p,
                "sha256": digest(("synthetic-config-" + p).encode()),
            }
            for p in PRODUCTS
        ],
    }
    put(source / "recovery-inventory.json", inventory)
    put(
        source / ".deployment.json",
        {
            "scope_id": scope_id,
            "deployment_id": deployment_id,
            "environment": "synthetic",
            "role": "authority",
            "status": "offline",
        },
    )
    return scope_id, deployment_id


def candidate(root, release_id="synthetic-v2"):
    source = root / "deployments/source"
    document = json.loads((source / "release-manifest.json").read_bytes())
    document = deepcopy(document)
    document["release_id"] = release_id
    for product in document["products"].values():
        # These are synthetic assertions used to exercise the selector, never real images.
        product["image"]["verification"] = "verified"
        product["image"]["digest"] = "sha256:" + "a" * 64
    path = root / (release_id + ".json")
    put(path, document)
    compatibility = {
        "candidate_manifest_sha256": digest(path.read_bytes()),
        "current_manifest_sha256": digest(
            (source / "release-manifest.json").read_bytes()
        ),
        "migration": "none",
        "read_write_schema_sha256": {
            path.relative_to(source).as_posix(): fingerprint(path)["schema_sha256"]
            for path in source.glob("data/*/*")
            if path.suffix in {".db", ".sqlite"}
        },
    }
    compatibility_path = root / (release_id + "-compatibility.json")
    put(compatibility_path, compatibility)
    return path, compatibility_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test fixture generator only, never a product initializer"
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    destination = Path(args.root).absolute()
    boundary = Path(__file__).resolve().parent / ".runtime"
    if not destination.is_relative_to(boundary) or destination.exists():
        raise ValueError("use a new directory inside this test's .runtime")
    if args.execute:
        boundary.mkdir(exist_ok=True)
        scope, authority = fixture(destination)
        print(
            json.dumps(
                {
                    "scope_id": scope,
                    "authority_id": authority,
                    "deployment": "source",
                    "synthetic_only": True,
                }
            )
        )
    else:
        print(json.dumps({"mode": "plan", "synthetic_only": True}))
