"""DEP-I evidence and local Docker boundary. No production or source reclamation."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from configure import confined

COMPONENTS = ("vector", "loki", "grafana", "prometheus", "guard")
DIMENSIONS = (
    "identity_permissions",
    "numbered_collection",
    "storage_outage_disk_buffer",
    "reconnect_replay",
    "collector_recreate_rotation",
    "sensitive_canary",
    "query_permissions",
    "grafana_datasources_dashboard",
    "grafana_viewer_permissions",
    "prometheus_targets",
    "local_alert_delivery",
    "disk_watermark_rejection",
    "physical_enospc",
    "vector_buffer_full",
    "production_30_day_retention",
    "accelerated_ttl",
    "application_capacity_gate",
    "bounded_tmpfs_capacity_probe",
)


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def contract_snapshot(source, destination, expected_manifest):
    """Read the coordinator's actual bytes, verify manifest first, then copy."""
    raw = (source / "manifest.json").read_bytes()
    require(sha(raw) == expected_manifest, "contract_manifest_changed")
    manifest = json.loads(raw)
    material = {"manifest.json": raw}
    for name, digest in manifest["files"].items():
        path = confined(source, name)
        require(path.is_file(), "contract_file_required")
        material[name] = path.read_bytes()
        require(sha(material[name]) == digest, "contract_file_changed")
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in material.items():
        path = confined(destination, name, exists=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return {name: sha(content) for name, content in material.items()}


def volume_layout(root, manifest, stack):
    require(manifest["schema_version"] == "1.1.0", "manifest_1_1_required")
    expected = set()
    volumes = [v for v in manifest["volumes"] if v["product"] == "observability"]
    require(len(volumes) == 5, "five_observability_volumes_required")
    for name in COMPONENTS:
        value = {
            "id": "obs-" + name + "-state",
            "product": "observability",
            "category": "observability_state",
            "host_path": "observability/data/" + name,
            "container_path": "/var/lib/" + name,
            "owner_service": "obs-" + name,
            "backup_group": "obs-" + name,
            "mount": True,
            "kind": "directory",
        }
        require(value in volumes, "five_volume_owner_mismatch")
        expected.add(
            (
                value["owner_service"],
                str(confined(root, value["host_path"])),
                value["container_path"],
            )
        )
    require(
        set(stack["services"]) == {"obs-" + n for n in COMPONENTS}, "unexpected_service"
    )
    actual = set()
    for service, spec in stack["services"].items():
        require(spec.get("user") == "10001:10001", "runtime_uid_mismatch")
        require(
            spec.get("read_only") is True and spec.get("cap_drop") == ["ALL"],
            "runtime_privilege_mismatch",
        )
        for mount in spec["volumes"]:
            require(mount["type"] == "bind", "unexpected_mount_type")
            source = Path(mount["source"])
            require(
                source.is_absolute() and source.is_relative_to(root),
                "mount_outside_scope",
            )
            confined(root, source.relative_to(root).as_posix())
            if mount.get("read_only") is not True:
                actual.add((service, str(source), mount["target"]))
        require(
            not spec.get("privileged")
            and not spec.get("devices")
            and not spec.get("network_mode"),
            "unsafe_container_option",
        )
    require(actual == expected, "write_mounts_not_owned")
    return expected


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.report = {
            "schema_version": "dep-i/1",
            "status": "not_run",
            "release_ready": False,
            "nas_verified": False,
            "application_reclamation_authorized": False,
            "dimensions": {
                name: {"status": "not_run", "reason": "not_executed"}
                for name in DIMENSIONS
            },
            "artifacts": {},
        }

    def artifact(self, name, value):
        require(re.fullmatch(r"[a-z0-9_-]+\.json", name), "invalid_evidence_name")
        path = self.directory / name
        write_new(path, value)
        self.report["artifacts"][name] = sha(path.read_bytes())
        return name

    def record(self, name, status, reason=None, artifacts=()):
        require(
            name in DIMENSIONS and status in {"passed", "failed", "not_run", "blocked"},
            "invalid_dimension",
        )
        require(status != "passed" or bool(artifacts), "pass_requires_evidence")
        require(
            all(a in self.report["artifacts"] for a in artifacts), "unknown_artifact"
        )
        self.report["dimensions"][name] = {
            "status": status,
            "reason": reason,
            "artifacts": list(artifacts),
        }

    def finish(self):
        states = [v["status"] for v in self.report["dimensions"].values()]
        self.report["status"] = (
            "failed"
            if "failed" in states or self.report.get("error_code")
            else "partial"
            if "passed" in states
            else "not_run"
        )
        write_new(self.directory / "report.json", self.report)


def verify_evidence(directory):
    directory = Path(directory)
    report = json.loads((directory / "report.json").read_bytes())
    require(
        report["release_ready"] is False
        and report["application_reclamation_authorized"] is False,
        "unsupported_release_claim",
    )
    require(
        set(report["dimensions"]) == set(DIMENSIONS), "dimension_inventory_incomplete"
    )
    for name, digest in report["artifacts"].items():
        require(re.fullmatch(r"[a-z0-9_-]+\.json", name), "invalid_evidence_name")
        require(
            sha(confined(directory, name).read_bytes()) == digest,
            "evidence_artifact_changed",
        )
    for value in report["dimensions"].values():
        require(
            value["status"] in {"passed", "failed", "not_run", "blocked"},
            "invalid_evidence_status",
        )
        if value["status"] == "passed":
            require(
                value.get("artifacts")
                and all(a in report["artifacts"] for a in value["artifacts"]),
                "pass_without_bound_artifacts",
            )
    # This verifier proves report/artifact consistency, never creates execution evidence.
    return {
        "status": "evidence_hashes_verified",
        "run_status": report["status"],
        "release_ready": False,
    }


class LocalDocker:
    """Only the explicitly pinned local UNIX daemon, never shell commands."""

    def __init__(self):
        require(os.name == "posix", "linux_host_required")
        require(
            not any(
                os.environ.get(k)
                for k in (
                    "DOCKER_HOST",
                    "DOCKER_CONTEXT",
                    "DOCKER_TLS_VERIFY",
                    "DOCKER_CERT_PATH",
                )
            ),
            "docker_override_refused",
        )
        context = self.raw(["context", "inspect"])
        entry = json.loads(context)[0]
        endpoint = entry["Endpoints"]["docker"]["Host"]
        require(endpoint.startswith("unix:///"), "local_unix_daemon_required")
        self.endpoint = endpoint
        require(
            self.call(["info", "--format", "{{.OSType}}/{{.Architecture}}"])
            .decode()
            .strip()
            in {"linux/x86_64", "linux/amd64"},
            "linux_amd64_daemon_required",
        )

    @staticmethod
    def raw(args, timeout=180):
        proc = subprocess.run(
            ["docker", *args], capture_output=True, timeout=timeout, check=False
        )
        require(proc.returncode == 0, "docker_command_failed")
        return proc.stdout

    def call(self, args, timeout=180):
        return self.raw(["--host", self.endpoint, *args], timeout=timeout)

    def compose(self, root, project, *args):
        require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", project), "invalid_project")
        require(
            args and args[0] in {"up", "ps", "config", "exec"},
            "compose_action_refused",
        )
        if args[0] == "up":
            require(
                "--no-recreate" in args
                and not any(
                    flag in args
                    for flag in (
                        "--force-recreate",
                        "--always-recreate-deps",
                        "--renew-anon-volumes",
                        "--remove-orphans",
                        "-V",
                    )
                ),
                "implicit_recreate_refused",
            )
        return self.call(
            [
                "compose",
                "-p",
                project,
                "--project-directory",
                str(root),
                "-f",
                str(root / "compose.yaml"),
                *args,
            ]
        )


def capacity_gate(
    measured_daily_bytes, source_budget, central_budget, buffer_budget, outage_hours
):
    values = (
        measured_daily_bytes,
        source_budget,
        central_budget,
        buffer_budget,
        outage_hours,
    )
    require(
        all(type(v) is int and v > 0 for v in values),
        "positive_capacity_measurements_required",
    )
    return {
        "status": "blocked",
        "daily_source_bytes": measured_daily_bytes,
        "source_days_until_rejection": source_budget / measured_daily_bytes,
        "central_30_days_raw_floor_met": central_budget >= measured_daily_bytes * 30,
        "outage_buffer_raw_floor_met": buffer_budget * 24
        >= measured_daily_bytes * outage_hours,
        "blockers": [
            "application_reclamation_contract_missing",
            "measured_loki_expansion_and_safety_required",
        ],
        "operator_action": "Stop admitting new work before source budget; preserve all source segments and expand isolated storage or resolve the reclamation contract.",
        "application_reclamation_authorized": False,
    }
