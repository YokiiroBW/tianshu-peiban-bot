"""Read-only consumer of DEP-G 880c2c33 identity, with independent live checks."""

from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from acceptance import COMPONENTS, require, sha, volume_layout
from configure import confined
from policy import Policy, canonical

INTERFACE_COMMIT = "880c2c33ffc4c12b9267d38267c2dacde074890d"
PRODUCTS = ("platform", "companion", "memory", "gateway")


def load_identity(path, expected):
    from jsonschema import Draft202012Validator

    raw = Path(path).read_bytes()
    require(sha(raw) == expected, "identity_hash_mismatch")
    document = json.loads(raw)
    schema_raw = Path(__file__).with_name("runtime-identity.schema.json").read_bytes()
    require(
        sha(schema_raw)
        == "4869bb1df94e5ac3457b244f812026948d22fc9091c477384c57032f07d4a8c2",
        "fixed_interface_schema_changed",
    )
    schema = json.loads(schema_raw)
    require(
        not list(Draft202012Validator(schema).iter_errors(document)),
        "identity_schema_invalid",
    )
    root = PurePosixPath(document["deployment_root"])
    require(root.is_absolute() and ".." not in root.parts, "identity_root_invalid")
    require(
        document["lease"]
        == {
            "path": str(root / ".runtime-owner.lock"),
            "protocol": "linux-flock-exclusive-nonblocking-v1",
        },
        "lease_mismatch",
    )
    for group, suffix, directory, filename in (
        ("core", "", root, "compose.json"),
        ("observability", "-obs", root / "observability", "compose.yaml"),
    ):
        entry = document["projects"][group]
        require(
            entry["name"] == document["project_name"] + suffix
            and entry["directory"] == str(directory)
            and entry["compose_file"] == str(directory / filename),
            "project_binding_mismatch",
        )
        if entry["compose_json"] is not None:
            require(
                sha(canonical(entry["compose_json"]))
                == entry["compose_canonical_sha256"],
                "compose_canonical_hash_mismatch",
            )
    return document


@contextmanager
def lifecycle_lease(root):
    require(sys.platform == "linux", "linux_lease_required")
    import fcntl

    path = confined(root, ".runtime-owner.lock", exists=False)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "lease_not_regular_file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("runtime_owner_busy") from None
        yield
    finally:
        os.close(fd)


class Binding:
    def __init__(self, document):
        self.document = document
        self.root = Path(document["deployment_root"])
        self.obs_root = self.root / "observability"
        self.obs_project = document["projects"]["observability"]["name"]
        require(self.root.resolve(strict=True) == self.root, "canonical_scope_required")
        confined(self.root, "release-manifest.json")
        integrity = document["integrity"]
        for filename, key in (
            ("release-manifest.json", "release_manifest_sha256"),
            ("bundle-integrity.json", "bundle_integrity_sha256"),
            ("compose.json", "compose_sha256"),
            ("observability/compose.yaml", "observability_compose_sha256"),
        ):
            require(
                sha(confined(self.root, filename).read_bytes()) == integrity[key],
                "bound_file_changed",
            )
        self.inventory = json.loads((self.root / "bundle-integrity.json").read_bytes())[
            "files"
        ]
        for filename, digest in self.inventory.items():
            require(
                sha(confined(self.root, filename).read_bytes()) == digest,
                "inventory_file_changed",
            )
        config = {
            k: v
            for k, v in self.inventory.items()
            if k.startswith(("config/", "contracts/", "tools/"))
        }
        require(config == integrity["config_sha256"], "config_inventory_mismatch")
        self.manifest = json.loads((self.root / "release-manifest.json").read_bytes())
        require(
            self.manifest["release_id"] == document["release_id"], "release_id_mismatch"
        )
        require(
            {p: self.manifest["products"][p]["source"] for p in PRODUCTS}
            == integrity["sources"],
            "source_commit_mismatch",
        )
        for group in ("core", "observability"):
            entry = document["projects"][group]
            raw = Path(entry["compose_file"]).read_bytes()
            require(
                sha(raw) == entry["compose_sha256"]
                and json.loads(raw) == entry["compose_json"],
                "compose_binding_changed",
            )
        self.stack = document["projects"]["observability"]["compose_json"]
        from nas_resources import bind, check_compose

        metadata = json.loads((self.root / "deployment.json").read_bytes())
        self.resource_profile = bind(
            self.root, metadata["compose_inputs"].get("resource_profile")
        )
        for group in ("core", "observability"):
            check_compose(
                document["projects"][group]["compose_json"], self.resource_profile
            )
        volume_layout(self.root, self.manifest, self.stack)
        declared = {m["id"]: m for m in document["mounts"]}
        mounted = [m for m in self.manifest["volumes"] if m["mount"]]
        require(
            len(declared) == len(document["mounts"]) == len(mounted),
            "identity_mount_inventory_mismatch",
        )
        for mount in mounted:
            observed = declared.get(mount["id"], {})
            path = confined(self.root, mount["host_path"])
            require(
                observed.get("host_path") == str(path)
                and all(
                    observed.get(k) == mount[k]
                    for k in ("container_path", "owner_service", "backup_group")
                ),
                "identity_mount_binding_mismatch",
            )
            current = path.stat()
            require(
                observed.get("uid") in (None, current.st_uid)
                and observed.get("gid") in (None, current.st_gid),
                "identity_mount_owner_changed",
            )
        self.logs = {}
        for product in PRODUCTS:
            candidates = [
                v
                for v in self.manifest["volumes"]
                if v["product"] == product and v["category"] == "logs" and v["mount"]
            ]
            require(len(candidates) == 1, "one_log_root_per_product_required")
            self.logs[product] = confined(self.root, candidates[0]["host_path"])
        require(len(set(self.logs.values())) == 4, "log_roots_overlap")
        self.snapshot = json.loads(
            (self.obs_root / "code/vocabulary.json").read_bytes()
        )
        require(
            {
                p: {
                    "repo": self.snapshot["products"][p]["repo"],
                    "commit": self.snapshot["products"][p]["commit"],
                }
                for p in PRODUCTS
            }
            == integrity["sources"],
            "snapshot_source_mismatch",
        )
        Policy(self.snapshot).verify_contract(self.root / "contracts/diagnostics/v1")
        for role in ("vector", "guard"):
            mounts = self.stack["services"]["obs-" + role]["volumes"]
            for product, path in self.logs.items():
                require(
                    any(
                        v["target"] == "/sources/" + product
                        and Path(v["source"]) == path
                        and v.get("read_only") is True
                        for v in mounts
                    ),
                    "source_mount_mismatch",
                )
        self.ca = self.mount_source("obs-guard", "/run/tls/ca.pem")
        self.query_token = self.mount_source("obs-guard", "/run/secrets/query_token")
        self.writer_token = self.mount_source("obs-guard", "/run/secrets/writer_token")
        self.grafana_password = self.mount_source(
            "obs-grafana", "/run/secrets/grafana_admin_password"
        )
        self.query_url = self.loopback_url("obs-guard", 8443)
        self.grafana_url = self.loopback_url("obs-grafana", 3000)
        self.validate_pipeline()

    def mount_source(self, owner, target):
        values = [
            m["source"]
            for m in self.stack["services"][owner]["volumes"]
            if m["target"] == target
        ]
        require(len(values) == 1, "unique_mount_required")
        return Path(values[0])

    def loopback_url(self, owner, port):
        values = self.stack["services"][owner]["ports"]
        require(
            len(values) == 1
            and re.fullmatch(r"127\.0\.0\.1:[0-9]+:" + str(port), values[0]),
            "loopback_only_port_required",
        )
        return "https://127.0.0.1:" + values[0].split(":")[1]

    def validate_pipeline(self):
        from configs import vector, loki

        for name in (
            "guard.py",
            "guard_lifecycle.py",
            "query.py",
            "transport.py",
            "monitor.py",
            "policy.py",
            "alerts.py",
        ):
            local = Path(__file__).with_name(name)
            deployed = confined(self.root, "observability/code/" + name)
            require(
                sha(local.read_bytes()) == sha(deployed.read_bytes()),
                "observability_runtime_revision_mismatch",
            )

        observed = json.loads((self.obs_root / "config/vector.json").read_bytes())
        require(
            observed == vector(self.snapshot), "full_log_pipeline_configuration_changed"
        )
        require(
            json.loads((self.obs_root / "config/loki.json").read_bytes()) == loki(),
            "production_retention_configuration_changed",
        )
        require(
            all(
                n.get("internal") is True and not n.get("external")
                for n in self.stack["networks"].values()
            ),
            "external_observability_network_refused",
        )

    def containers(self, docker, project):
        ids = (
            docker.call(
                ["ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]
            )
            .decode()
            .split()
        )
        return json.loads(docker.call(["inspect", *ids])) if ids else []

    def check_container(self, row, project, owner, spec):
        from nas_resources import container_check

        container_check(spec, row)
        labels = row["Config"].get("Labels") or {}
        require(
            labels.get("com.docker.compose.project") == project
            and labels.get("com.docker.compose.service") == owner
            and labels.get("com.docker.compose.oneoff", "false").lower() == "false",
            "container_owner_mismatch",
        )
        require(
            row["Config"].get("User") == "10001:10001"
            and not row["HostConfig"].get("Privileged")
            and row["HostConfig"].get("ReadonlyRootfs") is True,
            "container_privileges_mismatch",
        )
        directory = self.root if owner in PRODUCTS else self.obs_root
        expected = {
            (
                str((directory / m["source"]).resolve()),
                m["target"],
                m.get("read_only") is not True,
            )
            for m in spec["volumes"]
        }
        actual = {
            (m["Source"], m["Destination"], m["RW"])
            for m in row["Mounts"]
            if m["Type"] == "bind"
        }
        require(
            expected == actual
            and all(m["Type"] in {"bind", "tmpfs"} for m in row["Mounts"]),
            "container_mounts_mismatch",
        )

    def preflight(self, docker):
        facts = {
            "interface_commit": INTERFACE_COMMIT,
            "images": {},
            "core_stopped": [],
            "mounts": [],
        }
        if self.resource_profile is not None:
            from nas_resources import host_check

            facts["resource_capabilities"] = host_check(
                self.resource_profile,
                json.loads(docker.call(["info", "--format", "{{json .}}"])),
            )
        core_project = self.document["project_name"]
        cores = self.containers(docker, core_project)
        require(len(cores) == 4, "four_stopped_core_containers_required")
        seen = set()
        for row in cores:
            owner = row["Config"]["Labels"].get("com.docker.compose.service")
            require(owner in PRODUCTS and owner not in seen, "unexpected_core_owner")
            seen.add(owner)
            self.check_container(
                row,
                core_project,
                owner,
                self.document["projects"]["core"]["compose_json"]["services"][owner],
            )
            require(
                row["State"]["Status"] == "exited" and not row["State"].get("Running"),
                "core_writer_not_stopped",
            )
            require(
                row["Id"] == self.document["services"][owner]["container_id"],
                "core_container_identity_changed",
            )
            require(
                row["Image"] == self.document["services"][owner]["image_id"],
                "core_image_identity_changed",
            )
            facts["core_stopped"].append(
                {
                    "owner": owner,
                    "container_id": row["Id"],
                    "exit_code": row["State"].get("ExitCode"),
                    "normal_shutdown_certified": False,
                }
            )
        # Only this G synthetic scope; retain its core bootstrap logs as baseline.
        require(
            not self.containers(docker, self.obs_project),
            "fresh_observability_project_required",
        )
        for path in (
            *self.logs.values(),
            *(self.obs_root / "data" / n for n in COMPONENTS),
        ):
            if path not in self.logs.values():
                require(
                    not list(path.iterdir()),
                    "fresh_empty_observability_volumes_required",
                )
            else:
                require(
                    not any(p.name.startswith("depi") for p in path.iterdir()),
                    "new_scenario_log_names_required",
                )
            st = path.stat()
            require(
                (st.st_uid, st.st_gid) == (10001, 10001)
                and stat.S_IMODE(st.st_mode) & 0o007 == 0,
                "mount_owner_or_mode_mismatch",
            )
            facts["mounts"].append(
                {
                    "path": str(path.relative_to(self.root)),
                    "uid": st.st_uid,
                    "gid": st.st_gid,
                    "mode": oct(stat.S_IMODE(st.st_mode)),
                }
            )
        # Refuse ANY other container binding this scope, including stopped containers.
        ids = docker.call(["ps", "-aq"]).decode().split()
        all_rows = json.loads(docker.call(["inspect", *ids])) if ids else []
        for row in all_rows:
            if row["Id"] in {c["Id"] for c in cores}:
                continue
            for mount in row["Mounts"]:
                if mount["Type"] == "bind":
                    source = Path(mount["Source"]).resolve()
                    require(
                        not (
                            source.is_relative_to(self.root)
                            or self.root.is_relative_to(source)
                        ),
                        "scope_mounted_by_other_container",
                    )
        for owner, spec in self.stack["services"].items():
            image = json.loads(docker.call(["image", "inspect", spec["image"]]))[0]
            expected = self.document["services"][owner]
            require(
                expected["project"] == self.obs_project
                and expected["image_reference"] == spec["image"],
                "observability_image_binding_mismatch",
            )
            require(
                expected["image_id"] is not None
                and image["Id"] == expected["image_id"],
                "fixed_image_id_required",
            )
            require(
                image["Os"] == "linux" and image["Architecture"] == "amd64",
                "image_platform_mismatch",
            )
            require(
                sorted(image.get("RepoDigests") or [])
                == sorted(expected["repo_digests"]),
                "registry_digest_mismatch",
            )
            facts["images"][owner] = {
                "local_image_id": image["Id"],
                "registry_repo_digests": image.get("RepoDigests") or [],
                "platform": "linux/amd64",
            }
        return facts

    def running(self, docker):
        rows = self.containers(docker, self.obs_project)
        require(len(rows) == 5, "five_observability_containers_required")
        seen = set()
        for row in rows:
            owner = row["Config"]["Labels"].get("com.docker.compose.service")
            require(
                owner in self.stack["services"] and owner not in seen,
                "unexpected_observability_owner",
            )
            seen.add(owner)
            self.check_container(
                row, self.obs_project, owner, self.stack["services"][owner]
            )
            require(
                row["Image"] == self.document["services"][owner]["image_id"],
                "running_image_mismatch",
            )
            require(row["State"]["Running"] is True, "observability_not_running")
            from process_identity import process_status

            process = process_status(docker, row)
            for field in ("Uid", "Gid"):
                values = next(
                    (
                        line.split()[1:]
                        for line in process.splitlines()
                        if line.startswith(field + ":")
                    ),
                    [],
                )
                require(values == ["10001"] * 4, "actual_process_uid_gid_mismatch")
        return [
            {
                "owner": r["Config"]["Labels"]["com.docker.compose.service"],
                "container_id": r["Id"],
                "image_id": r["Image"],
            }
            for r in rows
        ]
