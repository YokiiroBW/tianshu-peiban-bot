"""Explicit identity/CLI doubles; never represent Linux execution evidence."""

import json

from fixtures import put
from lifecycle_fixtures import compose_fixture
from test_compose_lifecycle import DockerContract

from ops.recovery.safety import canonical, digest, file_hash


def runtime_fixture(source):
    compose_fixture(source)
    manifest = json.loads((source / "release-manifest.json").read_bytes())
    for service in manifest["services"]:
        put(source / service["config_path"], {"synthetic": service["id"]})
    index = {
        "schema_version": "1.0.0",
        "files": {
            p.relative_to(source).as_posix(): file_hash(p)
            for folder in (
                source / "config",
                source / "contracts",
                source / "observability/config",
            )
            for p in folder.rglob("*")
            if p.is_file()
        },
    }
    put(source / "bundle-integrity.json", index)
    name = "tianshu-qa-recovery"
    projects, services = {}, {}
    for group, suffix, filename in (
        ("core", "", "compose.json"),
        ("observability", "-obs", "observability/compose.yaml"),
    ):
        path = source / filename
        document = json.loads(path.read_bytes())
        for service, definition in document["services"].items():
            if service in manifest["products"]:
                definition["image"] = manifest["products"][service]["image"][
                    "reference"
                ]
        put(path, document)
        projects[group] = {
            "name": name + suffix,
            "directory": str(path.parent),
            "compose_file": str(path),
            "lifecycle_owner": "coordinator",
            "compose_sha256": file_hash(path),
            "compose_json": document,
            "compose_canonical_sha256": digest(canonical(document)),
        }
        for service, definition in document["services"].items():
            number = len(services) + 1
            services[service] = {
                "project": name + suffix,
                "image_reference": definition["image"],
                "image_id": "sha256:" + f"{number:064x}",
                "repo_digests": [],
                "platform": "linux/amd64",
                "uid": None,
                "gid": None,
                "container_id": None,
                "status": "not_observed",
            }
    value = {
        "schema_version": "dep-g-runtime/1.0.0",
        "scope": {
            "synthetic_only": True,
            "release_ready": False,
            "nas_acceptance": False,
        },
        "host": {"system": None},
        "release_id": manifest["release_id"],
        "deployment_root": str(source),
        "project_name": name,
        "projects": projects,
        "services": services,
        "mounts": [
            {
                "id": v["id"],
                "host_path": str(source / v["host_path"]),
                "container_path": v["container_path"],
                "owner_service": v["owner_service"],
                "backup_group": v["backup_group"],
                "uid": None,
                "gid": None,
            }
            for v in manifest["volumes"]
            if v["mount"]
        ],
        "integrity": {
            "release_manifest_sha256": file_hash(source / "release-manifest.json"),
            "bundle_integrity_sha256": file_hash(source / "bundle-integrity.json"),
            "compose_sha256": projects["core"]["compose_sha256"],
            "observability_compose_sha256": projects["observability"]["compose_sha256"],
            "config_sha256": {
                k: v
                for k, v in index["files"].items()
                if k.startswith(("config/", "contracts/", "tools/"))
            },
            "sources": {p: v["source"] for p, v in manifest["products"].items()},
        },
        "recovery_inventory": None,
        "authority": None,
        "lease": {
            "path": str(source / ".runtime-owner.lock"),
            "protocol": "linux-flock-exclusive-nonblocking-v1",
        },
        "state": "planned",
        "activation_authorized": False,
    }
    path = source / "reports/runtime-identity.json"
    put(path, value)
    return path, value


class RuntimeDocker(DockerContract):
    def __init__(self, directory, binding):
        super().__init__(directory, binding)
        for container in self.containers:
            container["Config"]["User"] = "10001:10001"

    def run(self, *args):
        if args[:2] == ("image", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": self.images[args[2]],
                        "Os": "linux",
                        "Architecture": "amd64",
                        "RepoDigests": [],
                    }
                ]
            )
        return super().run(*args)
