"""Linux resident candidate sequence using only public product/deployment CLIs.

The normal entry requires a new root and complete inputs from real_inputs.
Explicit one-shot resumes are confined to the recorded prepare and schema-2 failures.
It never retries a failed stage or removes a partially created deployment.
The optional remaining-service phase is explicit and stays in this process so
the original 300-second Platform origin is checked at each step.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_ROOT = Path("/volume2/tianshu-v2-resident")
PREPARE_RESUME_EVIDENCE = Path("/volume2/tianshu-v2-resident-tooling/evidence/first-install")
PREPARE_RESUME_OUTPUT = Path("/volume2/tianshu-v2-resident-tooling/evidence/resume-after-prepare-20260926")
SCHEMA2_RESUME_OUTPUT = Path("/volume2/tianshu-v2-resident-tooling/evidence/resume-after-schema2-20260926")
PREPARED_BUNDLE_INTEGRITY_SHA256 = "0b8f2068b6f62512f526dcd225e4e38f9b8c315aa65bd71d27cc9a73d4011b55"
CAPACITY_UNIT_SHA256 = "77620ca2c757484a09288bf52a427a94f316757219987bb3a4e8c914181d1f63"
FIXED_INPUT_HASHES = {
    "plan": "1f2406873734c0deb7fa3108af3cd6df1efd0eea306401549810aa878b565412",
    "release-manifest.json": "8762c8d6ce660868cdd79374960139c64013586d63eb271484d51b541e8a3ab0",
    "site/deployment-input.json": "a9cf63b1f5eea2377cf9ffe7408c423d833564b4459c9b5373e9975e406b1586",
    "obs-input/settings.json": "c561e85d4ce0c506b76a9e31cc8a42d9d38a7d8638acd07c245c1d83aabda6f8",
}
sys.path.insert(0, str(ROOT))

from ops.resident_install.install import (  # noqa: E402
    CORE_PROJECT, OBS_PROJECT, SCHEMA2_RESUME_HASHES, _docker_occupants,
)
from ops.resident_install.real_inputs import _pins, _plan  # noqa: E402
from bundle import verify_integrity  # noqa: E402
from manifest import PRODUCTS, Refused, check_contracts, load_manifest, no_links  # noqa: E402


class Stopped(Exception):
    def __init__(self, stage, code, *, effects_unconfirmed=False):
        super().__init__(code)
        self.stage = stage
        self.code = code
        self.effects_unconfirmed = effects_unconfirmed


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        path.chmod(0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _local_docker_environment():
    """Pin all descendant Docker calls, including those inside public A1 CLIs."""
    for name in tuple(os.environ):
        if name.startswith(("DOCKER_", "COMPOSE_")):
            del os.environ[name]
    os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _command(stage, command, evidence, *, cwd=ROOT, timeout=180):
    _write(evidence / (stage + "-attempt.json"), {
        "stage": stage, "state": "started", "automatic_retry": False,
    })
    try:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except OSError:
        raise Stopped(stage, "command_unavailable") from None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        # Only the direct CLI process is signalled. Its descendants and Docker
        # daemon effects may persist, even if it exits after TERM.
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.communicate(timeout=30)
            parent_state = "exited_after_term"
        except (subprocess.TimeoutExpired, OSError):
            parent_state = "unconfirmed_after_term"
        try:
            _write(evidence / (stage + "-effects-unconfirmed.json"), {
                "stage": stage, "state": "effects_unconfirmed",
                "cli_pid": process.pid, "cli_parent_state": parent_state,
                "automatic_cleanup": False,
            })
        except OSError:
            pass
        raise Stopped(stage, "effects_unconfirmed_after_timeout_or_interrupt",
                      effects_unconfirmed=True) from None
    except OSError:
        raise Stopped(stage, "effects_unconfirmed_after_cli_io_failure",
                      effects_unconfirmed=True) from None
    # The command receives secrets by protected file path. Keep diagnostics
    # private because an unexpected upstream error could include input values.
    for suffix, raw in (("stdout", stdout), ("stderr", stderr)):
        with (evidence / (stage + "." + suffix)).open("xb") as stream:
            os.chmod(stream.name, 0o600)
            stream.write(raw)
    if process.returncode != 0:
        try:
            failure = json.loads(stdout)
        except (UnicodeError, ValueError, TypeError):
            failure = None
        if isinstance(failure, dict) and failure.get("code") == "product_effects_unconfirmed_timeout":
            raise Stopped(stage, "child_effects_unconfirmed_timeout",
                          effects_unconfirmed=True)
        raise Stopped(stage, "command_failed")
    return stdout


def _receipt(stage, command, evidence, expected, *, timeout=180):
    result = _json_command(stage, command, evidence, timeout=timeout)
    if result.get("status") != expected or result.get("release_ready") is not False:
        raise Stopped(stage, "receipt_status_mismatch")
    return result


def _json_command(stage, command, evidence, *, cwd=ROOT, timeout=180):
    raw = _command(stage, command, evidence, cwd=cwd, timeout=timeout)
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
    except (UnicodeError, ValueError, TypeError):
        raise Stopped(stage, "receipt_invalid") from None
    return result


def _check_paths(args, plan):
    plan = _plan(plan)
    root = Path(plan["deployment_root"])
    if _sha(args.plan) != FIXED_INPUT_HASHES["plan"]:
        raise Stopped("preflight", "reviewed_nas_plan_hash_required")
    if (os.name != "posix" or sys.version_info < (3, 12) or os.geteuid() != 0
            or root != DEPLOYMENT_ROOT or not root.parent.is_dir()):
        raise Stopped("preflight", "python312_fresh_linux_root_and_privileged_operator_required")
    if args.resume_after_prepare and args.resume_after_schema2:
        raise Stopped("preflight", "one_resume_boundary_required")
    if args.resume_after_schema2:
        if (not root.is_dir() or root.is_symlink()
                or args.prior_evidence != PREPARE_RESUME_OUTPUT
                or not args.prior_evidence.is_dir()
                or args.prior_evidence.is_symlink()
                or args.evidence != SCHEMA2_RESUME_OUTPUT
                or not args.start_remaining):
            raise Stopped("preflight", "schema2_resume_paths_required")
    elif args.resume_after_prepare:
        if (not root.is_dir() or root.is_symlink()
                or args.prior_evidence is None
                or not args.prior_evidence.is_absolute()
                or not args.prior_evidence.is_dir()
                or args.prior_evidence.is_symlink()
                or args.prior_evidence != PREPARE_RESUME_EVIDENCE
                or args.evidence != PREPARE_RESUME_OUTPUT
                or args.prior_evidence == args.evidence):
            raise Stopped("preflight", "prepared_root_and_prior_evidence_required")
    elif root.exists() or root.is_symlink() or args.prior_evidence is not None:
        raise Stopped("preflight", "fresh_deployment_root_required")
    outputs = ((args.final_export, args.evidence) if args.resume_after_schema2
               else (args.first_export, args.final_export, args.evidence))
    for output in outputs:
        if (not output.is_absolute() or output.exists() or not output.parent.is_dir()
                or output.is_relative_to(root) or root.is_relative_to(output)):
            raise Stopped("preflight", "fresh_external_output_required")
    if len({args.first_export, args.final_export, args.evidence}) != 3:
        raise Stopped("preflight", "outputs_must_be_distinct")
    if args.resume_after_schema2 and (
            not args.first_export.is_absolute() or not args.first_export.is_dir()
            or not (args.first_export / "resident-export.lock.json").is_file()
            or args.first_export.is_relative_to(root)
            or root.is_relative_to(args.first_export)):
        raise Stopped("preflight", "fixed_first_export_required")
    if args.start_remaining:
        if args.capacity_config is None or not args.capacity_config.is_file():
            raise Stopped("preflight", "capacity_guard_config_required")
        if args.capacity_unit_file is None or not args.capacity_unit_file.is_file():
            raise Stopped("preflight", "capacity_guard_unit_file_required")
        capacity = _read(args.capacity_config)
        if (capacity.get("deployment_root") != str(root)
                or capacity.get("min_free_bytes") != 20 * 1024**3
                or capacity.get("max_deployment_bytes") != 20 * 1024**3
                or not isinstance(capacity.get("state_dir"), str)
                or not Path(capacity["state_dir"]).is_absolute()):
            raise Stopped("preflight", "capacity_guard_config_mismatch")
    if not all(path.is_absolute() and path.is_dir() for path in (
        args.inputs, args.contracts, args.projects, args.root_repository,
    )):
        raise Stopped("preflight", "absolute_existing_inputs_required")
    if not all((args.inputs / "runtime-secrets" / name).is_file()
               and stat.S_IMODE((args.inputs / "runtime-secrets" / name).stat().st_mode)
               == 0o600
               for name in ("credentials.json", "admin-password.txt")):
        raise Stopped("preflight", "runtime_credentials_required")
    ready = _read(args.inputs / "ready.json")
    if (ready.get("status") != "real_candidate_inputs_ready"
            or ready.get("deployment_root") != str(root)
            or ready.get("provider") != "not_configured"
            or not (args.inputs / "release-manifest.json").is_file()
            or not (args.inputs / "obs-input/settings.json").is_file()):
        raise Stopped("preflight", "complete_real_inputs_required")
    if any(_sha(args.inputs / name) != digest for name, digest in
           FIXED_INPUT_HASHES.items() if name != "plan"):
        raise Stopped("preflight", "reviewed_real_input_hash_mismatch")
    manifest = load_manifest(args.inputs / "release-manifest.json")
    check_contracts(manifest, args.contracts)
    site = _read(args.inputs / "site/deployment-input.json")
    versions = _read(ROOT / "deploy/observability/versions.json")
    source_manifest = _read(ROOT / "deploy/tianshu/release-manifest.example.json")
    if _pins(plan, source_manifest, versions):
        raise Stopped("preflight", "nine_real_repo_digests_required")
    if (manifest.get("status") != "candidate"
            or site.get("bind_address") != plan["bind_address"]
            or site.get("web_port") != plan["web_port"]
            or site.get("subnet") != plan["subnets"]["core"]
            or site.get("auxiliary_subnets") != {
                name: plan["subnets"][name] for name in ("egress", "frontend")}
            or site.get("service_ips") != plan["service_ips"]
            or site.get("project_name") != CORE_PROJECT):
        raise Stopped("preflight", "candidate_inputs_mismatch")
    for product in ("platform", "companion", "memory", "gateway"):
        image = manifest["products"][product]["image"]
        pin = plan["image_pins"][product]
        if (pin is None or image.get("reference") != pin.get("reference")
                or image.get("digest") != pin.get("digest")):
            raise Stopped("preflight", "real_product_repo_digest_required")
    obs_settings = _read(args.inputs / "obs-input/settings.json")
    if (obs_settings.get("ports") != plan["obs_ports"]
            or obs_settings.get("network_subnets") != {
                name: plan["subnets"][name] for name in ("observe", "storage", "access")}
            or any(obs_settings["image_digests"].get(name)
                   != plan["image_pins"][name]["digest"]
                   for name in ("vector", "loki", "grafana", "prometheus", "guard"))):
        raise Stopped("preflight", "real_observability_repo_digest_required")
    return root


def _resume_prepared(root, args):
    """Read-only gate for this exact post-prepare/pre-OBS failure boundary."""
    prior = no_links(args.prior_evidence)
    root = no_links(root)
    expected = {
        "run-attempt.json", "prepare-attempt.json", "prepare.stdout",
        "prepare.stderr", "run-stopped.json",
    }
    if not all((prior / name).is_file() and not (prior / name).is_symlink()
               for name in expected):
        raise Stopped("resume", "prior_prepare_evidence_incomplete")
    stage_files = {
        path.name for path in prior.iterdir()
        if path.name.endswith(("-attempt.json", ".stdout", ".stderr"))
    }
    if stage_files != expected - {"run-stopped.json"}:
        raise Stopped("resume", "later_stage_evidence_present")
    attempt = _read(prior / "run-attempt.json")
    stopped = _read(prior / "run-stopped.json")
    prepared = json.loads((prior / "prepare.stdout").read_bytes())
    if (not all(isinstance(value, dict) for value in (attempt, stopped, prepared))
            or attempt.get("state") != "started"
            or attempt.get("automatic_retry") is not False
            or attempt.get("deployment_root") != str(root)
            or attempt.get("plan_sha256") != FIXED_INPUT_HASHES["plan"]
            or attempt.get("start_remaining_requested") is not args.start_remaining
            or _read(prior / "prepare-attempt.json") != {
                "stage": "prepare", "state": "started", "automatic_retry": False,
            }
            or (prior / "prepare.stderr").stat().st_size != 0
            or prepared.get("status") != "resident_candidate_prepared"
            or prepared.get("bundle_root") != str(root)
            or prepared.get("release_ready") is not False
            or prepared.get("provider") != "not_configured"
            or prepared.get("next_stage") != "export_fixed_sources_and_resident_compose"
            or stopped.get("state") != "needs_diagnosis"
            or stopped.get("stage") != "runtime"
            or stopped.get("code") != "unexpected_or_interrupted_stage_failure"
            or stopped.get("automatic_retry") is not False
            or stopped.get("manual_stop_required") is not False
            or stopped.get("cleanup") != {"exact_stop": "no_activation_attempt"}
            or stopped.get("release_ready") is not False):
        raise Stopped("resume", "exact_post_prepare_failure_evidence_required")
    if (any(path.exists() or path.is_symlink() for path in (
            root / "reports", root / "INCOMPLETE", root / "observability",
            root / "observability-input",
        )) or _sha(root / "bundle-integrity.json")
            != PREPARED_BUNDLE_INTEGRITY_SHA256
            or _sha(root / "release-manifest.json")
            != FIXED_INPUT_HASHES["release-manifest.json"]):
        raise Stopped("resume", "prepared_bundle_stage_changed")
    verify_integrity(root)
    for category in ("data", "logs"):
        base = no_links(root / category)
        if (not base.is_dir()
                or {member.name for member in base.iterdir()} != set(PRODUCTS)):
            raise Stopped("resume", "prepared_mutable_layout_changed")
        for product in PRODUCTS:
            directory = no_links(base / product)
            if not directory.is_dir() or any(directory.iterdir()):
                raise Stopped("resume", "prepared_mutable_layout_changed")
    check_contracts(load_manifest(root / "release-manifest.json"), root / "contracts")
    return {"prior_run_stopped_sha256": _sha(prior / "run-stopped.json"),
            "prepare_receipt_sha256": _sha(prior / "prepare.stdout"),
            "bundle_integrity_sha256": _sha(root / "bundle-integrity.json")}


def _resume_schema2_history(root, args):
    """Read-only confirmation of the one failed, pre-migration activation."""
    root = no_links(root)
    prior = no_links(args.prior_evidence)
    work = no_links(root / "reports/resident-install")
    if not all(no_links(prior / name).is_file() for name in (
        "run-attempt.json", "run-stopped.json", "first_export.stdout",
        "activate-attempt.json", "activate.stdout", "activate.stderr",
    )):
        raise Stopped("resume", "schema2_prior_runner_evidence_incomplete")
    attempt = _read(prior / "run-attempt.json")
    stopped = _read(prior / "run-stopped.json")
    exported = json.loads((prior / "first_export.stdout").read_bytes())
    activate_attempt = _read(prior / "activate-attempt.json")
    activate_result = _read(prior / "activate.stdout")
    if (not all(isinstance(item, dict) for item in
                (attempt, stopped, exported, activate_attempt, activate_result))
            or attempt.get("deployment_root") != str(root)
            or attempt.get("plan_sha256") != FIXED_INPUT_HASHES["plan"]
            or attempt.get("start_remaining_requested") is not True
            or attempt.get("resume_after_prepare") is not True
            or attempt.get("prior_evidence") != str(PREPARE_RESUME_EVIDENCE)
            or stopped.get("state") != "needs_diagnosis"
            or stopped.get("stage") != "activate"
            or stopped.get("code") != "command_failed"
            or stopped.get("automatic_retry") is not False
            or stopped.get("cleanup") != {"exact_stop": "exact_containers_stopped"}
            or stopped.get("manual_stop_required") is not False
            or stopped.get("release_ready") is not False
            or exported.get("status") != "resident_candidate"
            or exported.get("release_ready") is not False
            or activate_attempt != {
                "stage": "activate", "state": "started", "automatic_retry": False,
            }
            or activate_result != {
                "status": "refused", "code": "product_command_failed",
            }
            or (prior / "activate.stderr").stat().st_size != 0
            or not work.is_dir()
            or {path.name for path in work.iterdir()} != {
                "attempt.json", "compose_config-attempt.json",
                "memory_schema_2-attempt.json", "failure.json",
            }
            or _sha(no_links(root / "bundle-integrity.json"))
            != SCHEMA2_RESUME_HASHES["bundle-integrity.json"]
            or _sha(no_links(args.first_export / "resident-export.lock.json"))
            != SCHEMA2_RESUME_HASHES["first-lock"]
            or _sha(no_links(work / "attempt.json")) != SCHEMA2_RESUME_HASHES["attempt.json"]
            or _sha(no_links(work / "failure.json")) != SCHEMA2_RESUME_HASHES["failure.json"]):
        raise Stopped("resume", "fixed_pre_migration_failure_required")
    verify_integrity(root)
    return {"prior_run_stopped_sha256": _sha(prior / "run-stopped.json"),
            "first_export_lock_sha256": _sha(args.first_export / "resident-export.lock.json"),
            "activation_failure_sha256": _sha(work / "failure.json")}


def _read_only(command, *, seconds=30, cwd=None):
    try:
        process = subprocess.run(
            command, cwd=cwd, capture_output=True, timeout=seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Stopped("live_preflight", "inspection_unavailable") from None
    if process.returncode != 0:
        raise Stopped("live_preflight", "inspection_failed")
    return process.stdout


def _capacity_unit_preflight(args):
    if not re.fullmatch(r"[a-z0-9-]+\.service", args.capacity_unit):
        raise Stopped("live_preflight", "capacity_unit_name_invalid")
    installed = Path("/etc/systemd/system") / args.capacity_unit
    if (not installed.is_file() or installed.is_symlink()
            or _sha(installed) != CAPACITY_UNIT_SHA256
            or _sha(args.capacity_unit_file) != CAPACITY_UNIT_SHA256):
        raise Stopped("live_preflight", "capacity_unit_install_mismatch")
    raw = _read_only([
        "/usr/bin/systemctl", "show", args.capacity_unit,
        "--property=FragmentPath", "--property=LoadState", "--property=ActiveState",
        "--property=DropInPaths", "--property=NeedDaemonReload",
    ]).decode("utf-8", "strict")
    state = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    if (state.get("FragmentPath") != str(installed)
            or state.get("LoadState") != "loaded"
            or state.get("ActiveState") != "inactive"
            or state.get("DropInPaths") != ""
            or state.get("NeedDaemonReload") != "no"):
        raise Stopped("live_preflight", "capacity_unit_not_loaded_inactive")


def _live_preflight(root, plan):
    """Repeat path, resource, port, network and image reads just before write."""
    if _docker_occupants(root):
        raise Stopped("live_preflight", "deployment_root_or_project_occupied")
    if not {6, 7} <= os.sched_getaffinity(0):
        raise Stopped("live_preflight", "resident_cpus_unavailable")
    memory = Path("/proc/meminfo").read_text(encoding="ascii")
    available = next((int(line.split()[1]) * 1024 for line in memory.splitlines()
                      if line.startswith("MemAvailable:")), 0)
    if available < 11.5 * 1024**3:
        raise Stopped("live_preflight", "host_memory_below_resident_minimum")
    if shutil.disk_usage(root.parent).free < 20 * 1024**3:
        raise Stopped("live_preflight", "host_disk_below_resident_minimum")
    for address, port in (
        (plan["bind_address"], plan["web_port"]),
        ("127.0.0.1", plan["obs_ports"]["grafana"]),
        ("127.0.0.1", plan["obs_ports"]["query"]),
    ):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind((address, port))
        except OSError:
            raise Stopped("live_preflight", "resident_port_unavailable") from None
    proposed = [ipaddress.ip_network(value) for value in plan["subnets"].values()]
    network_ids = _read_only(["/usr/bin/docker", "network", "ls", "-q"]).decode().splitlines()
    if network_ids:
        try:
            networks = json.loads(_read_only(
                ["/usr/bin/docker", "network", "inspect", *network_ids], seconds=60,
            ))
            used = [ipaddress.ip_network(entry["Subnet"], strict=False)
                    for network in networks
                    for entry in (network.get("IPAM", {}).get("Config") or [])
                    if entry.get("Subnet")]
        except (UnicodeError, ValueError, KeyError, TypeError):
            raise Stopped("live_preflight", "docker_network_inventory_invalid") from None
    else:
        used = []
    ip_binary = shutil.which("ip")
    if ip_binary is None:
        raise Stopped("live_preflight", "ip_route_tool_unavailable")
    try:
        routes = _read_only([ip_binary, "-4", "route", "show"]).decode().splitlines()
    except UnicodeError:
        raise Stopped("live_preflight", "route_inventory_invalid") from None
    for line in routes:
        destination = line.split()[0] if line.split() else ""
        if destination != "default":
            try:
                used.append(ipaddress.ip_network(destination, strict=False))
            except ValueError:
                raise Stopped("live_preflight", "route_inventory_invalid") from None
    if any(a.overlaps(b) for a in proposed for b in used):
        raise Stopped("live_preflight", "resident_subnet_occupied")
    for name, pin in plan["image_pins"].items():
        raw = _read_only([
            "/usr/bin/docker", "image", "inspect", "--format", "{{json .RepoDigests}}",
            pin["repo_digest"],
        ])
        try:
            digests = json.loads(raw)
        except (UnicodeError, ValueError):
            raise Stopped("live_preflight", "image_inventory_invalid") from None
        if not isinstance(digests, list) or pin["repo_digest"] not in digests:
            raise Stopped("live_preflight", "real_repo_digest_missing:" + name)


def _capacity_config_matches(root, first, final, capacity_config):
    """Bind the preinstalled A2 configuration to the actual A3 exports."""
    try:
        from ops.resident_capacity.guard import load_config
        parsed, _ = load_config(capacity_config)
        value = _read(capacity_config)
        first_core = first / CORE_PROJECT / "compose.yaml"
        final_core = final / CORE_PROJECT / "compose.yaml"
        final_obs = final / OBS_PROJECT / "compose.yaml"
        expected_origins = {
            CORE_PROJECT: {"workdir": str(root), "file": str(final_core)},
            OBS_PROJECT: {"workdir": str(root), "file": str(final_obs)},
        }
        if (value["compose"] != expected_origins
                or value["platform_first_compose"] != {
                    "workdir": str(root), "file": str(first_core),
                } or value["deployment_root"] != str(root)
                or value["state_dir"] != "/volume2/tianshu-v2-resident-tooling/capacity-state"
                or value["docker_binary"] != "/volume2/@appstore/ContainerManager/usr/bin/docker"
                or "/volume2/@docker" not in value["free_paths"]
                or value["min_free_bytes"] != 20 * 1024**3
                or value["max_deployment_bytes"] != 20 * 1024**3
                or parsed.root != root):
            raise ValueError("capacity_origin_or_budget_mismatch")
        images = _read(final / "resident-export.lock.json")["images"]
        if value["images"] != images:
            raise ValueError("capacity_image_mismatch")
        binds = {}
        first_services = _read(first_core)["services"]
        final_services = _read(final_core)["services"]
        obs_services = _read(final_obs)["services"]
        for name, spec in {**final_services, **obs_services,
                           "platform": first_services["platform"]}.items():
            binds[name] = sorted((
                {"source": mount["source"], "target": mount["target"],
                 "read_only": mount.get("read_only") is True}
                for mount in spec["volumes"] if mount.get("type") == "bind"
            ), key=lambda item: (item["source"], item["target"]))
        configured_binds = {
            name: sorted(entries, key=lambda item: (item["source"], item["target"]))
            for name, entries in value["binds"].items()
        }
        if configured_binds != binds:
            raise ValueError("capacity_binds_mismatch")
    except (ImportError, OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        raise Stopped("start_remaining", "capacity_guard_config_or_export_mismatch") from error


def _set_runtime_permissions(root):
    """Only the newly created bundle's owned mount trees are adjusted."""
    for category in ("data", "logs", "config"):
        for product in ("platform", "companion", "memory", "gateway"):
            base = root / category / product
            if not base.is_dir() or base.is_symlink():
                raise Stopped("permissions", "runtime_mount_layout_invalid")
            for path in (base, *base.rglob("*")):
                if path.is_symlink() or not (path.is_dir() or path.is_file()):
                    raise Stopped("permissions", "runtime_mount_member_invalid")
                os.chown(path, 10001, 10001, follow_symlinks=False)
                path.chmod(0o750 if path.is_dir() else 0o640)


def _remaining(expiry):
    try:
        until = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if until.tzinfo is None:
            raise ValueError
    except (AttributeError, ValueError):
        raise Stopped("start_remaining", "origin_expiry_invalid") from None
    return (until - datetime.now(timezone.utc)).total_seconds()


def _platform_id(root, expected, first_compose, evidence):
    raw = _command(
        "platform_identity_" + str(len(list(evidence.glob("platform_identity_*-attempt.json"))) + 1),
        ["docker", "inspect", "--format",
         '{"Id":{{json .Id}},"Running":{{json .State.Running}},'
         '"Labels":{{json .Config.Labels}}}', expected],
        evidence, cwd=root, timeout=20,
    )
    try:
        container = json.loads(raw)
        labels = container["Labels"]
        valid = (
            container["Id"] == expected
            and container["Running"] is True
            and labels["com.docker.compose.project"] == CORE_PROJECT
            and labels["com.docker.compose.service"] == "platform"
            and labels["com.docker.compose.project.working_dir"] == str(root)
            and labels["com.docker.compose.project.config_files"] == str(first_compose)
        )
    except (ValueError, IndexError, KeyError, TypeError):
        valid = False
    if not valid:
        raise Stopped("platform_identity", "platform_container_changed")


def _start_remaining(root, first, final, result, evidence, capacity_config, capacity_unit):
    core = final / CORE_PROJECT / "compose.yaml"
    obs = final / OBS_PROJECT / "compose.yaml"
    lock = _read(final / "resident-export.lock.json")
    work = root / "reports/resident-install"
    attempt = _read(work / "attempt.json")
    initial = _read(work / "result.json")
    renewed = (work / "reauthorization-result.json").is_file()
    source = (_read(work / "reauthorization-result.json") if renewed else initial)
    finalized = _read(work / (
        "final-export-after-renewal.json" if renewed else "final-export.json"
    ))
    platform_id = initial.get("platform_container_id")
    source_expiry = source.get("expires_at")
    receipt_valid = (
        result.get("status") == (
            "manual_reauthorized_export_verified" if renewed
            else "resident_first_install_export_verified"
        )
        and result.get("expires_at") == source_expiry
        and result.get("final_export") == str(final)
        and (result.get("platform_container_id") in (None, platform_id)
             if renewed else result.get("platform_container_id") == platform_id)
    )
    if (finalized.get("state") != "final_export_verified"
            or finalized.get("manual_reauthorization") is not renewed
            or initial.get("state") != "authority_initialized_pending_final_export"
            or (renewed and source.get("state") != "manual_reauthorized")
            or not isinstance(source_expiry, str)
            or not isinstance(source.get("gateway_ref_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", source["gateway_ref_sha256"]) is None
            or finalized.get("source_expires_at") != source_expiry
            or finalized.get("gateway_ref_sha256") != source.get("gateway_ref_sha256")
            or lock.get("compose_sha256") != attempt.get("export_compose_sha256")
            or any(_sha(path) != lock["compose_sha256"][project]
                   for project, path in ((CORE_PROJECT, core), (OBS_PROJECT, obs)))
            or _sha(final / "resident-export.lock.json") != finalized.get("export_lock_sha256")
            or platform_id is None
            or not receipt_valid):
        raise Stopped("start_remaining", "final_export_or_platform_identity_invalid")
    first_core = first / CORE_PROJECT / "compose.yaml"
    if _sha(first_core) != _sha(core):
        raise Stopped("start_remaining", "first_and_final_core_differ")
    _capacity_config_matches(root, first, final, capacity_config)
    _platform_id(root, platform_id, first_core, evidence)
    if _remaining(source_expiry) < 120:
        raise Stopped("start_remaining", "origin_budget_below_120_seconds")
    _write(evidence / "remaining-services-attempt.json", {
        "state": "started", "automatic_retry": False,
        "platform_container_id": platform_id,
        "source_expires_at": source_expiry,
        "capacity_config_sha256": _sha(capacity_config),
    })
    core_base = ["/usr/bin/docker", "compose", "--project-directory", str(root),
                 "-p", CORE_PROJECT, "-f", str(core)]
    obs_base = ["/usr/bin/docker", "compose", "--project-directory", str(root),
                "-p", OBS_PROJECT, "-f", str(obs)]
    _command("final_core_config", [*core_base, "config", "--quiet"], evidence, cwd=root, timeout=30)
    _command("final_obs_config", [*obs_base, "config", "--quiet"], evidence, cwd=root, timeout=30)
    for name in ("memory", "gateway", "companion"):
        _platform_id(root, platform_id, first_core, evidence)
        if _remaining(source_expiry) < 45:
            raise Stopped("start_" + name, "origin_budget_below_45_seconds")
        _command(
            "start_" + name,
            [*core_base, "up", "--pull", "never", "-d", "--no-deps",
             "--wait", "--wait-timeout", "90", name],
            evidence, cwd=root, timeout=120,
        )
        _platform_id(root, platform_id, first_core, evidence)
    if _remaining(source_expiry) <= 0:
        raise Stopped("start_observability", "origin_expired_before_observability")
    _command(
        "start_observability",
        [*obs_base, "up", "--pull", "never", "-d", "--wait", "--wait-timeout", "120"],
        evidence, cwd=root, timeout=150,
    )
    _platform_id(root, platform_id, first_core, evidence)
    if _remaining(source_expiry) <= 0:
        raise Stopped("capacity_arm", "origin_expired_before_capacity_arm")
    arm = _json_command(
        "capacity_arm",
        [sys.executable, "-B", "-m", "ops.resident_capacity.guard",
         "arm", "--config", str(capacity_config)],
        evidence, cwd=ROOT, timeout=40,
    )
    if arm.get("status") != "armed" or arm.get("services") != 9:
        raise Stopped("capacity_arm", "nine_container_guard_arm_required")
    _command(
        "capacity_unit_start", ["/usr/bin/systemctl", "start", capacity_unit],
        evidence, timeout=90,
    )
    active = _command(
        "capacity_unit_active", ["/usr/bin/systemctl", "is-active", capacity_unit],
        evidence, timeout=15,
    )
    if active.strip() != b"active":
        raise Stopped("capacity_unit_active", "systemd_capacity_guard_inactive")
    guarded = _json_command(
        "capacity_status",
        [sys.executable, "-B", "-m", "ops.resident_capacity.guard",
         "status", "--config", str(capacity_config)],
        evidence, cwd=ROOT, timeout=30,
    )
    if guarded.get("status") != "ready":
        raise Stopped("capacity_status", "capacity_guard_not_ready")
    _platform_id(root, platform_id, first_core, evidence)
    if _remaining(source_expiry) <= 0:
        raise Stopped("capacity_status", "origin_expired_before_ready")
    return {
        "status": "resident_services_started_pending_live_acceptance",
        "platform_container_id": platform_id,
        "source_expires_at": source_expiry,
        "capacity_guard": "ready",
        "release_ready": False,
    }


def _exact_stop(root, first, final, evidence):
    """Disable restart and stop only inspected containers owned by this root."""
    first_core = first / CORE_PROJECT / "compose.yaml"
    final_core = final / CORE_PROJECT / "compose.yaml"
    final_obs = final / OBS_PROJECT / "compose.yaml"
    first_obs = first / OBS_PROJECT / "compose.yaml"
    first_lock = first / "resident-export.lock.json"
    if not first_lock.is_file():
        raise Stopped("exact_stop", "first_export_lock_missing")
    images = _read(first_lock)["images"]
    if (final / "resident-export.lock.json").is_file():
        if _read(final / "resident-export.lock.json")["images"] != images:
            raise Stopped("exact_stop", "export_images_changed")
    specs = {
        first_core: _read(first_core)["services"],
        first_obs: _read(first_obs)["services"],
    }
    if final_core.is_file():
        specs[final_core] = _read(final_core)["services"]
    if final_obs.is_file():
        specs[final_obs] = _read(final_obs)["services"]
    inspected = []
    unknown = []
    for project in (CORE_PROJECT, OBS_PROJECT):
        raw = _read_only([
            "/usr/bin/docker", "ps", "-a", "--no-trunc", "--filter",
            "label=com.docker.compose.project=" + project,
            "--format", "{{.ID}}",
        ])
        for container_id in raw.decode("ascii").splitlines():
            if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
                raise Stopped("exact_stop", "container_inventory_invalid")
            item = json.loads(_read_only([
                "/usr/bin/docker", "inspect", "--format",
                '{"Id":{{json .Id}},"Image":{{json .Config.Image}},'
                '"Labels":{{json .Config.Labels}},"Running":{{json .State.Running}},'
                '"Mounts":{{json .Mounts}}}', container_id,
            ]))
            labels = item.get("Labels") or {}
            service = labels.get("com.docker.compose.service")
            allowed_files = (
                {first_core} if service == "platform" and project == CORE_PROJECT
                else ({first_core, final_core} if project == CORE_PROJECT
                      else {first_obs, final_obs})
            )
            config_file = labels.get("com.docker.compose.project.config_files")
            selected = next((path for path in allowed_files if str(path) == config_file
                             and path in specs), None)
            mounts = item.get("Mounts") or []
            service_spec = specs.get(selected, {}).get(service) if selected else None
            expected_mounts = {
                (volume["source"], volume["target"], volume.get("read_only") is not True)
                for volume in service_spec.get("volumes", [])
                if volume.get("type") == "bind"
            } if service_spec else set()
            actual_mounts = {
                (mount.get("Source"), mount.get("Destination"), mount.get("RW"))
                for mount in mounts if mount.get("Type") == "bind"
            }
            if not (
                item.get("Id") == container_id
                and labels.get("com.docker.compose.project") == project
                and labels.get("com.docker.compose.project.working_dir") == str(root)
                and selected is not None
                and service in images and item.get("Image") == images[service]
                and bool(expected_mounts) and actual_mounts == expected_mounts
                and all(isinstance(source, str) and Path(source).is_relative_to(root)
                        for source, _, _ in actual_mounts)
                and all(mount.get("Type") in {"bind", "tmpfs"} for mount in mounts)
            ):
                unknown.append(container_id)
                continue
            inspected.append((service, container_id, item.get("Running") is True))
    stop_errors = []
    for service, container_id, running in inspected:
        try:
            _command(
                "cleanup_restart_" + service + "_" + container_id[:12],
                ["/usr/bin/docker", "update", "--restart=no", container_id],
                evidence, cwd=root, timeout=30,
            )
            policy = _read_only([
                "/usr/bin/docker", "inspect", "--format",
                "{{.HostConfig.RestartPolicy.Name}}", container_id,
            ]).strip()
            if policy != b"no":
                raise Stopped("exact_stop", "restart_policy_disable_unconfirmed")
            if running:
                _command(
                    "cleanup_term_" + service + "_" + container_id[:12],
                    ["/usr/bin/docker", "kill", "--signal=TERM", container_id],
                    evidence, cwd=root, timeout=30,
                )
            deadline = time.monotonic() + 120
            while True:
                observed = _read_only([
                    "/usr/bin/docker", "inspect", "--format", "{{.State.Running}}",
                    container_id,
                ], seconds=10).strip()
                if observed == b"false":
                    break
                if observed != b"true" or time.monotonic() >= deadline:
                    raise Stopped("exact_stop", "container_term_unconfirmed")
                time.sleep(1)
        except (Stopped, OSError, ValueError, TypeError) as error:
            if isinstance(error, Stopped) and error.effects_unconfirmed:
                raise
            stop_errors.append({"container_id": container_id,
                                "code": error.code if isinstance(error, Stopped)
                                else "stop_or_readback_failed"})
    report = {"status": "exact_containers_stopped" if not unknown and not stop_errors
              else "unconfirmed_containers",
              "container_ids": {
        service: container_id for service, container_id, _ in inspected},
        "unknown_ids": unknown,
        "stop_errors": stop_errors,
        "removed": False,
    }
    _write(evidence / "exact-stop.json", report)
    if unknown or stop_errors:
        raise Stopped("exact_stop", "container_stop_unconfirmed")
    return report


def _fail_closed(root, first, final, evidence, capacity_config, capacity_unit):
    """Finish the unit's ExecStopPost writer before invoking a fallback writer."""
    state_dir = _read(capacity_config).get("state_dir")
    if not isinstance(state_dir, str) or not Path(state_dir).is_absolute():
        return {"guard_fail_close": "unconfirmed_invalid_state_dir",
                "capacity_unit_stop": "not_attempted", "exact_stop": "not_attempted",
                "manual_stop_required": True}
    try:
        _command(
            "capacity_unit_stop", ["/usr/bin/systemctl", "stop", capacity_unit],
            evidence, timeout=720,
        )
        raw = _read_only([
            "/usr/bin/systemctl", "show", capacity_unit,
            "--property=ActiveState", "--property=Job",
        ], seconds=15).decode("utf-8", "strict")
        fields = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        if fields.get("ActiveState") != "inactive" or fields.get("Job") not in {"", "0"}:
            raise Stopped("capacity_unit_stop", "unit_stop_readback_unconfirmed")
    except (Stopped, UnicodeError) as error:
        return {"guard_fail_close": "not_attempted",
                "capacity_unit_stop": "unconfirmed:" + (
                    error.code if isinstance(error, Stopped) else "unit_state_invalid"),
                "exact_stop": "not_attempted", "manual_stop_required": True}
    try:
        guarded = _json_command(
            "capacity_fail_close",
            [sys.executable, "-B", "-m", "ops.resident_capacity.guard",
             "fail-close", "--state-dir", state_dir,
             "--reason", "startup_failure"],
            evidence, timeout=660,
        )
    except Stopped as error:
        return {"guard_fail_close": "unconfirmed:" + error.code,
                "capacity_unit_stop": "stopped", "exact_stop": "not_attempted",
                "manual_stop_required": True}
    guard_result = guarded.get("status")
    if guard_result not in {"stopped", "unarmed"}:
        return {"guard_fail_close": "unconfirmed_status",
                "capacity_unit_stop": "stopped", "exact_stop": "not_attempted",
                "manual_stop_required": True}
    try:
        stop = _exact_stop(root, first, final, evidence)
        stop_result = stop["status"]
    except (Stopped, OSError, ValueError, KeyError, TypeError) as error:
        stop_result = "unconfirmed:" + (
            error.code if isinstance(error, Stopped) else "inspection_or_stop_failed"
        )
    return {"guard_fail_close": guard_result, "exact_stop": stop_result,
            "capacity_unit": capacity_unit, "capacity_unit_stop": "stopped",
            "manual_stop_required": stop_result != "exact_containers_stopped"}


def run(args):
    _local_docker_environment()
    plan = _read(args.plan)
    root = _check_paths(args, plan)
    resume = _resume_prepared(root, args) if args.resume_after_prepare else None
    if args.resume_after_schema2:
        resume = _resume_schema2_history(root, args)
    if args.start_remaining:
        _capacity_unit_preflight(args)
    _live_preflight(root, plan)
    if args.resume_after_schema2:
        checked = json.loads(_read_only([
            sys.executable, "-B", "-m", "ops.resident_install.install",
            "check-schema2-resume", "--bundle-root", str(root),
            "--export-lock", str(args.first_export / "resident-export.lock.json"),
            "--export-repository", str(args.root_repository),
            "--final-export-output", str(args.final_export),
        ], seconds=180, cwd=ROOT))
        if (checked.get("status") != "schema2_resume_ready"
                or checked.get("release_ready") is not False):
            raise Stopped("resume", "schema2_read_only_gate_required")
    evidence = args.evidence
    evidence.mkdir(mode=0o700)
    os.chmod(evidence, 0o700)
    _write(evidence / "run-attempt.json", {
        "state": "started", "automatic_retry": False,
        "deployment_root": str(root), "plan_sha256": _sha(args.plan),
        "start_remaining_requested": args.start_remaining,
        "resume_after_prepare": args.resume_after_prepare,
        "resume_after_schema2": args.resume_after_schema2,
        "prior_evidence": str(args.prior_evidence) if resume else None,
    })
    if resume:
        _write(evidence / "resume-readback.json", resume)
    python = sys.executable
    activated = None
    activation_attempted = False
    try:
        if not args.resume_after_prepare and not args.resume_after_schema2:
            _receipt("prepare", [
                python, "-B", "-m", "ops.resident_install.install", "prepare",
                "--manifest", str(args.inputs / "release-manifest.json"),
                "--site", str(args.inputs / "site/deployment-input.json"),
                "--contracts", str(args.contracts),
                "--credentials", str(args.inputs / "runtime-secrets/credentials.json"),
                "--admin-password-file", str(args.inputs / "runtime-secrets/admin-password.txt"),
                "--admin-username", "admin", "--bundle-root", str(root),
            ], evidence, "resident_candidate_prepared", timeout=180)
        if not args.resume_after_schema2:
            _set_runtime_permissions(root)
            _receipt("configure_observability", [
                python, "-B", "deploy/tianshu/release.py", "configure-observability",
                "--bundle", str(root), "--settings", str(args.inputs / "obs-input/settings.json"),
                "--root-repository", str(args.root_repository),
                "--projects", str(args.projects),
            ], evidence, "observability_configured", timeout=180)
            _receipt("preflight", [
                python, "-B", "deploy/tianshu/release.py", "preflight",
                "--bundle", str(root), "--check-permissions",
            ], evidence, "resident_candidate", timeout=90)
            _receipt("first_export", [
                python, "-B", "deploy/tianshu/resident_export.py",
                "--bundle", str(root), "--repository", str(args.root_repository),
                "--deployment-root", str(root), "--output", str(args.first_export),
            ], evidence, "resident_candidate", timeout=180)
        activation_attempted = True
        activated = _receipt("activate", [
            python, "-B", "-m", "ops.resident_install.install",
            "resume-schema2" if args.resume_after_schema2 else "activate",
            "--bundle-root", str(root),
            "--export-lock", str(args.first_export / "resident-export.lock.json"),
            "--export-repository", str(args.root_repository),
            "--final-export-output", str(args.final_export),
        ], evidence, "resident_first_install_export_verified", timeout=300)
        if args.start_remaining:
            return _start_remaining(
                root, args.first_export, args.final_export, activated,
                evidence, args.capacity_config, args.capacity_unit,
            )
        return activated
    except (Exception, KeyboardInterrupt) as failure:
        error = (
            failure if isinstance(failure, Stopped)
            else Stopped("runtime", "unexpected_or_interrupted_stage_failure")
        )
        if error.effects_unconfirmed:
            cleanup = {"exact_stop": "not_attempted_after_uncertain_cli_effects",
                       "capacity_unit_stop": "not_attempted",
                       "manual_stop_required": True}
        elif args.start_remaining and activated is not None:
            cleanup = _fail_closed(root, args.first_export, args.final_export,
                                   evidence, args.capacity_config, args.capacity_unit)
        elif activation_attempted:
            try:
                cleanup = {"exact_stop": _exact_stop(
                    root, args.first_export, args.final_export, evidence,
                )["status"]}
            except (Stopped, OSError, ValueError, KeyError, TypeError) as stop_error:
                cleanup = {"exact_stop": "unconfirmed", "manual_stop_required": True,
                           "code": (
                    stop_error.code if isinstance(stop_error, Stopped)
                    else "inspection_or_stop_failed")}
        else:
            cleanup = {"exact_stop": "no_activation_attempt"}
        _write(evidence / "run-stopped.json", {
            "state": "needs_diagnosis", "stage": error.stage,
            "code": error.code, "automatic_retry": False,
            "cleanup": cleanup,
            "manual_stop_required": cleanup.get("manual_stop_required", False),
            "release_ready": False,
        })
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "inputs", "contracts", "projects",
                 "root-repository", "first-export", "final-export", "evidence"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--start-remaining", action="store_true")
    parser.add_argument("--resume-after-prepare", action="store_true")
    parser.add_argument("--resume-after-schema2", action="store_true")
    parser.add_argument("--prior-evidence", type=Path)
    parser.add_argument("--capacity-config", type=Path)
    parser.add_argument("--capacity-unit-file", type=Path)
    parser.add_argument("--capacity-unit", default="tianshu-resident-capacity.service")
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except Stopped as error:
        print(json.dumps({"status": "stopped", "stage": error.stage,
                          "code": error.code, "release_ready": False}))
        return 2
    except (OSError, ValueError, KeyError, TypeError, Refused):
        print(json.dumps({"status": "stopped", "stage": "input_or_runtime",
                          "code": "invalid_or_unavailable_input", "release_ready": False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
