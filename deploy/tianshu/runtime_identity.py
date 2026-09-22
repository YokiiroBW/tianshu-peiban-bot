"""DEP-G identity and shared nonblocking Linux lifecycle lease. No database inspection."""

import contextlib
import json
import os
import re
import sys
from pathlib import Path

from manifest import PRODUCTS, digest, inside, read_json, require, write_json

OWNERS = (
    *PRODUCTS,
    "obs-vector",
    "obs-loki",
    "obs-grafana",
    "obs-prometheus",
    "obs-guard",
)


def canonical(document):
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


@contextlib.contextmanager
def lifecycle_lease(root):
    """All G/I/J executors MUST hold this same inode for their entire mutating operation.

    Never delete/replace the lock file, including after a crash. Kernel releases flock on
    process exit. Children do not inherit fd. Taking the lock never authorizes activation.
    """
    require(sys.platform == "linux", "linux_lease_required")
    import fcntl

    path = inside(root, ".runtime-owner.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            require(False, "runtime_owner_busy")
        yield
    finally:
        os.close(fd)


def project(root, name, directory, compose_file):
    path = inside(root, compose_file)
    document = read_json(path) if path.exists() else None
    return dict(
        name=name,
        directory=str(directory),
        compose_file=str(path),
        lifecycle_owner="coordinator",
        compose_sha256=digest(path.read_bytes()) if document else None,
        compose_json=document,
        compose_canonical_sha256=digest(canonical(document)) if document else None,
    )


def identity(root, *, observed=None, state="planned", linux_stat=False):
    from bundle import verify_integrity

    verify_integrity(root)
    manifest = read_json(root / "release-manifest.json")
    meta = read_json(root / "deployment.json")
    name = meta["project_name"]
    require(
        re.fullmatch(r"tianshu-qa-[a-z0-9-]+", name), "isolated_qa_project_required"
    )
    projects = dict(
        core=project(root, name, root, "compose.json"),
        observability=project(
            root, name + "-obs", root / "observability", "observability/compose.yaml"
        ),
    )
    services = {}
    for owner in OWNERS:
        group = projects["core" if owner in PRODUCTS else "observability"]
        service = (group["compose_json"] or {}).get("services", {}).get(owner, {})
        services[owner] = dict(
            project=group["name"],
            image_reference=service.get("image"),
            image_id=None,
            repo_digests=[],
            platform=None,
            uid=None,
            gid=None,
            container_id=None,
            status="not_observed",
        )
        if observed and owner in observed:
            services[owner].update(observed[owner])
    mounts = []
    for v in manifest["volumes"]:
        if not v["mount"]:
            continue
        path = inside(root, v["host_path"])
        st = path.stat() if linux_stat and path.exists() else None
        mounts.append(
            dict(
                id=v["id"],
                host_path=str(path),
                container_path=v["container_path"],
                owner_service=v["owner_service"],
                backup_group=v["backup_group"],
                uid=st.st_uid if st else None,
                gid=st.st_gid if st else None,
            )
        )
    return dict(
        schema_version="dep-g-runtime/1.0.0",
        scope=dict(synthetic_only=True, release_ready=False, nas_acceptance=False),
        host=dict(system=sys.platform if linux_stat else None),
        release_id=manifest["release_id"],
        deployment_root=str(root),
        project_name=name,
        projects=projects,
        services=services,
        mounts=mounts,
        integrity=dict(
            release_manifest_sha256=digest(
                (root / "release-manifest.json").read_bytes()
            ),
            bundle_integrity_sha256=digest(
                (root / "bundle-integrity.json").read_bytes()
            ),
            compose_sha256=projects["core"]["compose_sha256"],
            observability_compose_sha256=projects["observability"]["compose_sha256"],
            config_sha256={
                k: v
                for k, v in read_json(root / "bundle-integrity.json")["files"].items()
                if k.startswith(("config/", "contracts/", "tools/"))
            },
            sources={p: manifest["products"][p]["source"] for p in PRODUCTS},
        ),
        recovery_inventory=None,
        authority=None,
        lease=dict(
            path=str(inside(root, ".runtime-owner.lock")),
            protocol="linux-flock-exclusive-nonblocking-v1",
        ),
        state=state,
        activation_authorized=False,
    )


def validate(document):
    from jsonschema import Draft202012Validator

    schema = read_json(Path(__file__).with_name("runtime-identity.schema.json"))
    require(
        not list(Draft202012Validator(schema).iter_errors(document)),
        "runtime_identity_schema_invalid",
    )
    root = Path(document["deployment_root"])
    require(root.is_absolute(), "runtime_identity_absolute_root_required")
    require(
        document["lease"]["path"] == str(root / ".runtime-owner.lock"),
        "runtime_identity_lease_mismatch",
    )
    for group, suffix, directory, file in (
        ("core", "", root, "compose.json"),
        ("observability", "-obs", root / "observability", "compose.yaml"),
    ):
        value = document["projects"][group]
        require(
            value["name"] == document["project_name"] + suffix
            and value["directory"] == str(directory)
            and value["compose_file"] == str(directory / file),
            "runtime_identity_project_mismatch",
        )
        if value["compose_json"] is not None:
            require(
                digest(canonical(value["compose_json"]))
                == value["compose_canonical_sha256"],
                "runtime_identity_compose_hash_mismatch",
            )
        else:
            require(
                value["compose_sha256"] is None
                and value["compose_canonical_sha256"] is None,
                "runtime_identity_unobserved_compose",
            )
    for owner, value in document["services"].items():
        group = document["projects"]["core" if owner in PRODUCTS else "observability"]
        require(
            value["project"] == group["name"],
            "runtime_identity_service_project_mismatch",
        )
        if value["status"] == "observed":
            require(
                all(
                    value[k] is not None
                    for k in ("image_id", "platform", "container_id", "uid", "gid")
                ),
                "runtime_identity_observation_incomplete",
            )
        else:
            require(
                all(value[k] is None for k in ("container_id", "uid", "gid")),
                "runtime_identity_unobserved_container_claim",
            )
    return document


def save(root, **kwargs):
    value = validate(identity(root, **kwargs))
    path = root / "reports/runtime-identity.json"
    path.parent.mkdir(exist_ok=True)
    write_json(path, value)
    return value
