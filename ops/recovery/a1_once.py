"""One-attempt A1 NAS coordinator. No action is taken on import or without --execute.

The source and preliminary clone inputs must already be prepared. This module owns
the short-lived seal -> stop/restore -> admission -> permit -> plan -> execute chain.
Every real action uses the public recovery CLI in a separate child process.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .safety import RecoveryError, canonical, child, file_hash, files, read_json, require

HEX = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[a-z][a-z0-9-]{2,63}\Z")
STAGES = ("seal", "linux-rehearse", "host-preflight", "permit", "drill-plan", "drill-execute")
TIMEOUTS = {"seal": 30, "linux-rehearse": 330, "host-preflight": 60,
            "permit": 30, "drill-plan": 30, "drill-execute": 540}
STREAM_LIMIT = 1024 * 1024
CONFIG_KEYS = {"schema_version", "scope_root", "scope_id", "code_root", "code_tree_sha256",
               "code_lock_sha256",
               "python", "docker", "source_name", "restored_name", "clone_name",
               "inputs_name", "backup_name", "permit_name", "receipt_name",
               "registration_sha256", "source_manifest_sha256", "initial_inputs_sha256",
               "source_runtime_sha256", "source_deployment_sha256", "model_template_sha256",
               "projects", "networks", "ports"}
ASSERTIONS = ["data_readback", "forgotten", "source_revoked", "model_revoked",
              "unknown_no_resend", "gateway_usage_readback"]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_once(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_raw_once(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _inside(root, name, parent):
    require(type(name) is str and NAME.fullmatch(name) is not None, "a1_name_invalid")
    return root / parent / name


def _code_files(root):
    """Pin every packaged ops/deploy file, including this coordinator."""
    rows = []
    for base in ("ops", "deploy"):
        directory = root / base
        require(directory.is_dir(), "a1_code_root_incomplete")
        for path in sorted(directory.rglob("*")):
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            require(not path.is_symlink(), "a1_code_symlink")
            if path.is_file():
                rows.append((path.relative_to(root).as_posix(), file_hash(path)))
    require(any(name == "ops/recovery/a1_once.py" for name, _ in rows),
            "a1_driver_not_pinned")
    return rows


def _code_tree(root):
    return _sha(canonical(_code_files(root)))


def _network_plan(value):
    require(set(value) == {"source", "clone"} and
            len(value["source"]) == 5 and len(value["clone"]) == 7,
            "a1_network_count_invalid")
    all_nets = []
    for group in ("source", "clone"):
        for entry in value[group]:
            require(type(entry) is str, "a1_network_invalid")
            network = ipaddress.ip_network(entry, strict=True)
            require(network.version == 4 and network.prefixlen == 28 and
                    network.subnet_of(ipaddress.ip_network("10.205.48.0/24")) and
                    not network.overlaps(ipaddress.ip_network("10.205.48.192/26")),
                    "a1_network_out_of_pool")
            require(not any(network.overlaps(other) for other in all_nets),
                    "a1_network_overlap")
            all_nets.append(network)
    return all_nets


def _ports(value):
    require(set(value) == {"source", "clone"} and
            len(value["source"]) == len(value["clone"]) == 6,
            "a1_port_count_invalid")
    ports = value["source"] + value["clone"]
    require(all(type(port) is int and 1024 <= port <= 65535 for port in ports) and
            len(set(ports)) == 12, "a1_port_invalid")
    return ports


def load_config(path, *, phase=None):
    path = Path(path)
    require(path.is_absolute() and path.is_file() and not path.is_symlink(),
            "a1_config_path_invalid")
    value = read_json(path)
    require(type(value) is dict and set(value) == CONFIG_KEYS and
            value["schema_version"] == "a1-once/1", "a1_config_schema_invalid")
    root = Path(value["scope_root"])
    code = Path(value["code_root"])
    require(root.is_absolute() and root.is_dir() and not root.is_symlink() and
            code.is_absolute() and code.is_dir() and not code.is_symlink() and
            root.resolve(strict=True) == root and code.resolve(strict=True) == code and
            path.parent == root / "inputs" and
            str(uuid.UUID(value["scope_id"])) == value["scope_id"],
            "a1_scope_binding_invalid")
    require(root.name.startswith("scope-a1-") and "-r2" not in root.name and
            read_json(root / ".recovery-scope.json")["scope_id"] == value["scope_id"],
            "a1_old_scope_forbidden")
    require(type(value["code_tree_sha256"]) is str and
            HEX.fullmatch(value["code_tree_sha256"]) and
            _code_tree(code) == value["code_tree_sha256"], "a1_code_digest_mismatch")
    lock = root / "inputs/a1-code-files.json"
    require(file_hash(lock) == value["code_lock_sha256"] and
            read_json(lock) == {"schema_version": "a1-code-lock/1",
                                "tree_sha256": value["code_tree_sha256"],
                                "files": dict(_code_files(code))},
            "a1_code_file_lock_mismatch")
    for key in ("python", "docker"):
        executable = Path(value[key])
        require(executable.is_absolute() and executable.is_file() and
                not executable.is_symlink(), "a1_executable_invalid")
    for key in ("source_name", "restored_name", "clone_name"):
        _inside(root, value[key], "deployments")
    for key in ("inputs_name",):
        _inside(root, value[key], "drill-inputs")
    for key in ("backup_name", "permit_name", "receipt_name"):
        _inside(root, value[key], "inputs")
    require(len({value[k] for k in ("source_name", "restored_name", "clone_name")}) == 3,
            "a1_deployments_overlap")
    require(set(value["projects"]) == {"core", "observability"} and
            all(type(p) is str and NAME.fullmatch(p) for p in value["projects"].values()) and
            value["projects"]["observability"] == value["projects"]["core"] + "-obs" and
            value["projects"]["core"].startswith("tianshu-qa-a1-"),
            "a1_project_invalid")
    _network_plan(value["networks"])
    _ports(value["ports"])
    for key in ("registration_sha256", "source_manifest_sha256", "initial_inputs_sha256",
                "source_runtime_sha256", "source_deployment_sha256", "model_template_sha256"):
        require(type(value[key]) is str and HEX.fullmatch(value[key]), "a1_digest_invalid")
    source = root / "deployments" / value["source_name"]
    inputs = root / "drill-inputs" / value["inputs_name"]
    require(source.is_dir() and inputs.is_dir() and
            not source.is_symlink() and not inputs.is_symlink() and
            not (root / "deployments" / value["clone_name"]).exists(),
            "a1_target_not_empty")
    if phase in (None, "seal"):
        require(not (root / "deployments" / value["restored_name"]).exists() and
                not (root / "inputs" / value["permit_name"]).exists() and
                (phase is not None or
                 not (root / "inputs" / value["receipt_name"]).exists()),
                "a1_target_not_empty")
    require(file_hash(source / "release-manifest.json") == value["source_manifest_sha256"] and
            file_hash(source / ".recovery-registration.json") == value["registration_sha256"] and
            read_json(source / ".recovery-registration.json")["scope_id"] == value["scope_id"] and
            file_hash(source / "reports/runtime-identity.json") == value["source_runtime_sha256"] and
            file_hash(source / "deployment.json") == value["source_deployment_sha256"] and
            file_hash(root / "inputs/model-publication-template.json") ==
            value["model_template_sha256"],
            "a1_initial_binding_mismatch")
    index = read_json(inputs / "inputs.json")
    require([row["id"] for row in index["assertions"]] == ASSERTIONS and
            len(index["files"]) >= 100, "a1_initial_inputs_invalid")
    if phase in (None, "seal"):
        require(file_hash(inputs / "inputs.json") == value["initial_inputs_sha256"] and
                "a1_origin_admission" not in index, "a1_initial_binding_mismatch")
        require(set(files(inputs)) == set(index["files"]) | {"inputs.json"},
                "a1_initial_file_set_mismatch")
        for name, checksum in index["files"].items():
            require(file_hash(child(inputs, name)) == checksum,
                    "a1_initial_file_digest_mismatch")
    else:
        summary = read_json(root / "inputs/clone-inputs-finalized.json")
        require(file_hash(inputs / "inputs.json") == summary["inputs_sha256"] and
                index.get("a1_origin_admission") == {"minimum_remaining_seconds": 180},
                "a1_sealed_binding_mismatch")
    require((root / "inputs" / "model-publication-template.json").is_file(),
            "a1_model_template_missing")
    _validate_allocations(value)
    return value


def _validate_allocations(c):
    """The declared address/port reservation must match both sealed stacks."""
    p = _paths(c)
    runtime = read_json(p["source"] / "reports/runtime-identity.json")
    source_docs = [row["compose_json"] for row in runtime["projects"].values()]
    clone_docs = [read_json(p["inputs"] / name) for name in
                  ("compose.json", "observability/compose.yaml")]
    for label, docs in (("source", source_docs), ("clone", clone_docs)):
        subnets, ports = set(), set()
        for doc in docs:
            for network in doc["networks"].values():
                for block in network.get("ipam", {}).get("config", []):
                    if block.get("subnet"):
                        subnets.add(block["subnet"])
            for service in doc["services"].values():
                for entry in service.get("ports", []):
                    if type(entry) is str:
                        require(entry.startswith("127.0.0.1:") and
                                len(entry.split(":")) == 3, "a1_port_not_loopback")
                        ports.add(int(entry.split(":")[1]))
                    else:
                        require(type(entry) is dict and
                                entry.get("host_ip") == "127.0.0.1" and
                                entry.get("protocol") == "tcp", "a1_port_not_loopback")
                        ports.add(int(entry["published"]))
        require(subnets == set(c["networks"][label]) and
                ports == set(c["ports"][label]), "a1_allocation_mismatch")
    require(clone_docs[0]["name"] == c["projects"]["core"] and
            clone_docs[1]["name"] == c["projects"]["observability"],
            "a1_project_mismatch")
    source_project = read_json(p["source"] / "deployment.json")["project_name"]
    require(source_project.startswith("tianshu-qa-a1-") and
            source_project not in c["projects"].values(), "a1_source_project_mismatch")


def _paths(c):
    root = Path(c["scope_root"])
    return {"root": root, "source": root / "deployments" / c["source_name"],
            "restored": root / "deployments" / c["restored_name"],
            "clone": root / "deployments" / c["clone_name"],
            "inputs": root / "drill-inputs" / c["inputs_name"],
            "permit": root / "inputs" / c["permit_name"]}


def _stamp(moment):
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def seal(c, *, publisher=None):
    """Parameter generation for the final Platform publication and two origins."""
    from .a1_acceptance import diagnose_platform_publication, run_platform_cli
    publisher = publisher or (
        lambda source, request: diagnose_platform_publication(
            source, request, docker_executable=c["docker"]),
        lambda source, action, request: run_platform_cli(
            source, action, request, docker_executable=c["docker"]),
    )
    p = _paths(c)
    private = p["root"] / "inputs"
    index_path = p["inputs"] / "inputs.json"
    index = read_json(index_path)
    require([row["id"] for row in index["assertions"]] == ASSERTIONS and
            "a1_origin_admission" not in index, "a1_seal_input_invalid")
    env = p["inputs"] / "private/gateway.env"
    original = env.read_text()
    placeholder = "TS_GATEWAY_ORIGIN=origin:clone-config-pending\n"
    require(original.count(placeholder) == 1, "a1_origin_placeholder_missing")
    for name in ("clone-publish-request.json", "clone-publish-receipt.json",
                 "clone-inputs-finalized.json"):
        require(not (private / name).exists(), "a1_seal_already_attempted")
    template = read_json(private / "model-publication-template.json")
    require(template.get("config_version") == 1 and
            template["providers"][0].get("provider_id") == "provider-synthetic" and
            template["providers"][0].get("base_url") ==
            "https://gateway.internal:9443/v1", "a1_model_template_invalid")
    now = datetime.now(timezone.utc)
    template["config_version"] = 5
    template["published_at"] = _stamp(now - timedelta(seconds=3))
    template["usable_until"] = _stamp(now + timedelta(minutes=14, seconds=30))
    request = private / "clone-publish-request.json"
    _write_once(request, template)
    require(publisher[0](p["source"], request).get("valid") is True,
            "a1_publication_invalid")
    published = publisher[1](p["source"], "publish", request)
    _write_once(private / "clone-publish-receipt.json", published)
    refs, expiries = {}, {}
    for name, entry in (("config", "config-entry"), ("actor", "web-source-actor")):
        request_file = p["inputs"] / f"private/a1-{name}-origin-request.json"
        issue_file = p["inputs"] / f"private/a1-{name}-origin-issue.json"
        _write_once(request_file, {"entry_id": entry})
        result = publisher[1](p["source"], "issue", request_file)
        receipt = result.get("receipt", {})
        require(type(receipt.get("assertion_ref")) is str and
                receipt["assertion_ref"].startswith("origin:") and
                type(receipt.get("expires_at")) is str, "a1_origin_issue_invalid")
        _write_once(issue_file, result)
        refs[name], expiries[name] = receipt["assertion_ref"], receipt["expires_at"]
    env.write_text(original.replace(placeholder, "TS_GATEWAY_ORIGIN=" + refs["config"] + "\n"))
    env.chmod(0o600)
    deadline = _stamp(datetime.now(timezone.utc) + timedelta(minutes=4, seconds=45))
    for assertion in index["assertions"]:
        aid = assertion["id"]
        if aid in {"forgotten", "source_revoked", "unknown_no_resend"}:
            assertion["request_json"]["query"]["origin"]["assertion_ref"] = refs["actor"]
        elif aid == "model_revoked":
            assertion["request_json"]["query"]["origin"]["assertion_ref"] = refs["config"]
        if aid == "unknown_no_resend":
            assertion["request_json"]["deadline_at"] = deadline
    for name in ("config", "actor"):
        for suffix in ("request", "issue"):
            file = f"private/a1-{name}-origin-{suffix}.json"
            index["files"][file] = file_hash(p["inputs"] / file)
    index["files"]["private/gateway.env"] = file_hash(env)
    index["a1_origin_admission"] = {"minimum_remaining_seconds": 180}
    index_path.write_bytes(json.dumps(index, ensure_ascii=False, sort_keys=True,
                                      indent=2).encode() + b"\n")
    index_path.chmod(0o600)
    summary = {"status": "sealed", "published_version": 5,
               "inputs_sha256": file_hash(index_path),
               "unknown_request_deadline_at": deadline,
               "origin_expiries": expiries}
    _write_once(private / "clone-inputs-finalized.json", summary)
    return summary


def make_permit(c, verification):
    from .drill_inputs import isolated_inputs, restoration_facts
    from .drill_origin_admission import check_origin_admission
    from .engine import Recovery
    from .nas_resources import profile_for
    p = _paths(c)
    require(HEX.fullmatch(verification) is not None and not p["permit"].exists() and
            not p["clone"].exists(), "a1_permit_already_attempted_or_invalid")
    registration = read_json(p["source"] / ".recovery-registration.json")
    authority = registration["authority_id"]
    recovery = Recovery(p["root"], c["scope_id"])
    facts = restoration_facts(recovery, p["source"], p["restored"], authority)
    require(_sha(canonical(facts)) == verification, "a1_restore_verification_mismatch")
    marker = read_json(p["restored"] / "RESTORE.json")
    require(marker["state"] == "restored_disabled" and
            marker["authority_deployment_id"] == authority, "a1_restored_target_invalid")
    index = read_json(p["inputs"] / "inputs.json")
    summary = read_json(p["root"] / "inputs/clone-inputs-finalized.json")
    index_sha = file_hash(p["inputs"] / "inputs.json")
    require(index_sha == summary["inputs_sha256"], "a1_sealed_inputs_changed")
    manifest = read_json(p["source"] / "release-manifest.json")
    runtime = read_json(p["source"] / "reports/runtime-identity.json")
    profile = profile_for(p["source"], runtime)
    files = index["files"]
    config = {k: v for k, v in files.items() if k.startswith((
        "config/", "private/", "observability/config/", "observability/private/",
        "observability-input/"))}
    now = int(time.time())
    value = {"schema_version": "dep-j-drill/1", "permit_id": str(uuid.uuid4()),
             "scope_id": c["scope_id"], "authority_id": authority,
             "source_directory": str(p["source"]), "restored_directory": str(p["restored"]),
             "drill_directory": str(p["clone"]), "inputs_directory": str(p["inputs"]),
             "snapshot_sha256": marker["snapshot_sha256"],
             "verification_sha256": verification,
             "runtime_identity_sha256": registration["runtime_identity"]["sha256"],
             "registration_sha256": c["registration_sha256"],
             "inputs_sha256": index_sha, "projects": c["projects"],
             "compose_sha256": {"core": files["compose.json"],
                                "observability": files["observability/compose.yaml"]},
             "config_sha256": config,
             "versions": {name: product["source"]["commit"]
                          for name, product in manifest["products"].items()},
             "image_ids": {name: service["image_id"]
                           for name, service in runtime["services"].items()},
             "issued_at": now, "expires_at": now + 600, "max_runtime_seconds": 540}
    verified, _ = isolated_inputs(value, manifest, resource_profile=profile)
    require(verified == index, "a1_inputs_changed")
    admission = check_origin_admission(p["source"], p["inputs"], index,
                                       minimum_remaining=180)
    raw = canonical(value)
    fd = os.open(p["permit"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {"status": "permit_issued", "permit_id": value["permit_id"],
            "permit_sha256": _sha(raw), "inputs_sha256": index_sha,
            "snapshot_sha256": marker["snapshot_sha256"],
            "verification_sha256": verification, "admission": admission}


def preflight(c, *, docker_run=None, route_reader=None, bind=None, memory_reader=None):
    """Read-only global checks. The recovery executor retains its own checks."""
    docker_run = docker_run or (lambda *args: subprocess.run(
        [c["docker"], *args], capture_output=True, text=True,
        timeout=20, check=True).stdout)
    route_reader = route_reader or (lambda: Path("/proc/net/route").read_text())
    bind = bind or (lambda port: _bind(port))
    memory_reader = memory_reader or (lambda: Path("/proc/meminfo").read_text())
    nets = _network_plan(c["networks"])
    _ports(c["ports"])
    clone_nets = nets[5:]
    routes = route_reader()
    require(type(routes) is str and "Iface" in routes, "a1_routes_unavailable")
    # /proc/net/route encodes IPv4 in little-endian. Include all host routes.
    for row in routes.splitlines()[1:]:
        fields = row.split()
        if len(fields) < 8:
            continue
        destination = int.from_bytes(bytes.fromhex(fields[1]), "little")
        mask = int.from_bytes(bytes.fromhex(fields[7]), "little")
        network = ipaddress.IPv4Network((destination, mask.bit_count()), strict=False)
        if network.prefixlen and any(network.overlaps(n) for n in clone_nets):
            raise ValueError("a1_route_overlap")
    observed = []
    # Explicit list then inspect avoids trusting a name filter or truncated ID.
    network_ids = docker_run("network", "ls", "-q", "--no-trunc").splitlines()
    if network_ids:
        observed = json.loads(docker_run("network", "inspect", *network_ids))
    for item in observed:
        for block in item.get("IPAM", {}).get("Config", []) or []:
            subnet = block.get("Subnet")
            if subnet:
                other = ipaddress.ip_network(subnet, strict=False)
                require(not any(other.overlaps(n) for n in clone_nets),
                        "a1_docker_network_overlap")
    for project in c["projects"].values():
        require(not docker_run("ps", "-a", "-q", "--no-trunc", "--filter",
                               f"label=com.docker.compose.project={project}").strip(),
                "a1_clone_project_exists")
    for port in c["ports"]["clone"]:
        bind(port)
    match = re.search(r"^MemAvailable:\s+(\d+) kB$", memory_reader(), re.MULTILINE)
    require(match and int(match.group(1)) >= 11.5 * 1024 * 1024,
            "a1_host_memory_insufficient")
    return {"status": "host_preflight_passed", "networks_checked": len(nets),
            "clone_ports_checked": 6}


def _bind(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def _json_child(argv, timeout, *, runner=None, cwd=None):
    """Cooperative timeout: TERM once, wait for the child's normal cleanup."""
    if runner is not None:
        return runner(argv, timeout)
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, cwd=cwd)
    except OSError:
        return 2, {"status": "child_start_failed"}
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        # Only the recovery CLI receives cancellation. Its own Docker children
        # must remain available for its bounded normal shutdown path.
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            pass
        try:
            out, err = proc.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            return 124, _with_streams({"status": "stop_unconfirmed"},
                expired.stdout or b"", expired.stderr or b"")
        except OSError:
            if _child_exited(proc):
                return 124, _with_streams({"status": "child_terminated_after_timeout"},
                    expired.stdout or b"", expired.stderr or b"")
            return 124, _with_streams({"status": "stop_unconfirmed"},
                expired.stdout or b"", expired.stderr or b"")
        return 124, _with_streams({"status": "child_terminated_after_timeout"}, out, err)
    except OSError:
        if _child_exited(proc):
            return 125, {"status": "child_terminated_after_io_error"}
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            pass
        return 125, {"status": "child_terminated_after_io_error"
                     if _child_exited(proc, wait=True) else "stop_unconfirmed"}
    if max(len(_raw_bytes(out)), len(_raw_bytes(err))) > STREAM_LIMIT:
        return proc.returncode, _with_streams({"status": "child_output_too_large"}, out, err)
    try:
        parsed = json.loads(_raw_bytes(out).strip().splitlines()[-1])
        require(type(parsed) is dict, "a1_child_receipt_invalid")
        return proc.returncode, _with_streams(parsed, out, err)
    except (RecoveryError, ValueError, IndexError, TypeError):
        return proc.returncode, _with_streams({"status": "invalid_child_receipt"}, out, err)


def _raw_bytes(value):
    return value.encode("utf-8") if type(value) is str else value


def _child_exited(proc, *, wait=False):
    try:
        if proc.poll() is not None:
            return True
        if wait:
            proc.wait(timeout=90)
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def _with_streams(result, stdout, stderr):
    stdout, stderr = _raw_bytes(stdout), _raw_bytes(stderr)
    return result | {"_stream_raw": {"stdout": stdout[:STREAM_LIMIT],
                                     "stderr": stderr[:STREAM_LIMIT]},
                     "_stream_truncated": {"stdout": len(stdout) > STREAM_LIMIT,
                                           "stderr": len(stderr) > STREAM_LIMIT},
                     "_stream_sha256": {"stdout": _sha(stdout), "stderr": _sha(stderr)}}


def command(c, stage, *, verification=None, permit_sha=None):
    p = _paths(c)
    base = [c["python"], "-B", "-m"]
    if stage in {"seal", "host-preflight", "permit"}:
        result = base + ["ops.recovery.a1_once", "--config",
                         str(p["root"] / "inputs/a1-once.json"), "--phase", stage, "--execute"]
        if stage == "permit":
            require(type(verification) is str and HEX.fullmatch(verification),
                    "a1_verification_missing")
            result += ["--verification-sha256", verification]
        return result
    result = base + ["ops.recovery", "--root", str(p["root"]),
                     "--scope-id", c["scope_id"]]
    if stage == "linux-rehearse":
        return result + ["--execute", "linux-rehearse", "--deployment-directory",
                         str(p["source"]), "--registration-sha256",
                         c["registration_sha256"], "--backup", c["backup_name"],
                         "--target", c["restored_name"], "--timeout", "300",
                         "--docker-executable", c["docker"]]
    require(type(permit_sha) is str and HEX.fullmatch(permit_sha), "a1_permit_missing")
    return result + (["--execute"] if stage == "drill-execute" else []) + [
        "drill-clone", "--permit", str(p["permit"]), "--permit-sha256", permit_sha,
        "--docker-executable", c["docker"]]


def _phase_result(stage, result):
    if stage == "seal":
        require(result.get("status") == "sealed" and
                HEX.fullmatch(result.get("inputs_sha256", "")), "a1_seal_result_invalid")
    elif stage == "linux-rehearse":
        require(result.get("status") == "disabled_restore_complete" and
                result.get("runtime_owners_stopped") == 9 and
                HEX.fullmatch(result.get("verification_sha256", "")),
                "a1_restore_result_invalid")
    elif stage == "host-preflight":
        require(result.get("status") == "host_preflight_passed" and
                result.get("clone_ports_checked") == 6, "a1_preflight_result_invalid")
    elif stage == "permit":
        require(result.get("status") == "permit_issued" and
                HEX.fullmatch(result.get("permit_sha256", "")) and
                type(result.get("admission")) is dict and
                result.get("admission", {}).get("remaining_at_check_seconds", 0) >= 180,
                "a1_permit_result_invalid")
    elif stage == "drill-plan":
        require(result.get("status") == "planned" and
                result.get("actual_owners_and_facts") == "not_checked" and
                result.get("mode") == "plan", "a1_plan_result_invalid")
    else:
        require(result.get("status") == "drill_passed", "a1_execute_result_invalid")


def drive(c, *, runner=None, clock=None):
    """At most one execute. Every phase receipt is private and durable."""
    clock = clock or (lambda: (datetime.now(timezone.utc).isoformat(), time.monotonic_ns()))
    p = _paths(c)
    receipt_dir = p["root"] / "inputs" / c["receipt_name"]
    receipt_dir.mkdir(mode=0o700)
    verification = permit_sha = None
    completed = []
    for stage in STAGES:
        start_utc, start_mono = clock()
        argv = command(c, stage, verification=verification, permit_sha=permit_sha)
        code, result = _json_child(argv, TIMEOUTS[stage], runner=runner,
                                   cwd=c["code_root"])
        end_utc, end_mono = clock()
        child_status = result.get("status") if type(result) is dict else None
        streams = result.pop("_stream_sha256", None) if type(result) is dict else None
        raw = result.pop("_stream_raw", None) if type(result) is dict else None
        truncated = result.pop("_stream_truncated", None) if type(result) is dict else None
        receipt_storage_failed = False
        if raw is not None:
            for kind in ("stdout", "stderr"):
                try:
                    _write_raw_once(receipt_dir / f"{len(completed) + 1:02d}-{stage}.{kind}",
                                    raw[kind])
                except OSError:
                    receipt_storage_failed = True
        validated = False
        try:
            require(type(result) is dict, "a1_child_receipt_invalid")
            if code == 0:
                _phase_result(stage, result)
                if stage == "seal":
                    require(file_hash(p["inputs"] / "inputs.json") ==
                            result["inputs_sha256"], "a1_seal_digest_mismatch")
                elif stage == "permit":
                    require(file_hash(p["permit"]) == result["permit_sha256"],
                            "a1_permit_digest_mismatch")
                validated = True
        except (RecoveryError, OSError, ValueError, TypeError, KeyError):
            result = {"status": "invalid_child_receipt"}
        if receipt_storage_failed:
            validated = False
            result = {"status": "receipt_storage_failed"}
        receipt = {"stage": stage, "utc_started": start_utc, "utc_finished": end_utc,
                   "monotonic_started_ns": start_mono, "monotonic_finished_ns": end_mono,
                   "monotonic_elapsed_ns": end_mono - start_mono,
                   "command_sha256": _sha(canonical(argv)), "exit_code": code,
                   "result_sha256": _sha(canonical(result)), "status": result.get("status"),
                   "stream_sha256": streams, "stream_truncated": truncated,
                   "validated": validated}
        try:
            _write_once(receipt_dir / f"{len(completed) + 1:02d}-{stage}.json", receipt)
        except OSError:
            validated = False
            result = {"status": "receipt_storage_failed"}
        if code != 0 or not validated:
            source_stop_status = None
            if stage == "seal" and child_status not in {
                "stop_unconfirmed", "child_start_failed"}:
                cleanup_argv = command(c, "linux-rehearse")
                cleanup_code, cleanup_result = _json_child(cleanup_argv,
                    TIMEOUTS["linux-rehearse"], runner=runner, cwd=c["code_root"])
                cleanup_streams = cleanup_result.pop("_stream_sha256", None)
                cleanup_raw = cleanup_result.pop("_stream_raw", None)
                cleanup_truncated = cleanup_result.pop("_stream_truncated", None)
                if cleanup_raw is not None:
                    for kind in ("stdout", "stderr"):
                        try:
                            _write_raw_once(receipt_dir / f"seal-failure-source-stop.{kind}",
                                            cleanup_raw[kind])
                        except OSError:
                            pass
                source_stop_status = ("disabled_restore_complete"
                    if cleanup_code == 0 and cleanup_result.get("status") ==
                       "disabled_restore_complete" and
                       cleanup_result.get("runtime_owners_stopped") == 9
                    else "stop_unconfirmed")
                try:
                    _write_once(receipt_dir / "seal-failure-source-stop.json",
                                {"status": source_stop_status,
                                 "exit_code": cleanup_code,
                                 "stream_sha256": cleanup_streams,
                                 "stream_truncated": cleanup_truncated,
                                 "command_sha256": _sha(canonical(cleanup_argv))})
                except OSError:
                    pass
            return {"status": "stopped", "failed_stage": stage,
                    "child_status": child_status,
                    "phase_status": result.get("status"),
                    "source_stop_status": source_stop_status or
                                          ("source_active_child_not_started"
                                           if stage == "seal" and
                                              child_status == "child_start_failed"
                                           else "stop_unconfirmed" if stage == "seal"
                                           else None),
                    "completed": completed}
        completed.append(stage)
        if stage == "linux-rehearse":
            verification = result["verification_sha256"]
        elif stage == "permit":
            permit_sha = result["permit_sha256"]
    return {"status": "completed", "completed": completed, "execute_calls": 1}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--emit-code-lock", metavar="ABSOLUTE_CODE_ROOT")
    parser.add_argument("--phase", choices=STAGES[:1] + STAGES[2:4])
    parser.add_argument("--verification-sha256")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.emit_code_lock:
        root = Path(args.emit_code_lock)
        require(root.is_absolute(), "a1_code_root_invalid")
        print(json.dumps({"schema_version": "a1-code-lock/1",
                          "tree_sha256": _code_tree(root),
                          "files": dict(_code_files(root))}, sort_keys=True))
        return 0
    require(args.config is not None, "a1_config_required")
    require(args.execute, "a1_execute_flag_required")
    c = load_config(args.config, phase=args.phase)
    require(Path(args.config).name == "a1-once.json", "a1_config_name_invalid")
    if args.phase == "seal":
        output = seal(c)
    elif args.phase == "host-preflight":
        output = preflight(c)
    elif args.phase == "permit":
        output = make_permit(c, args.verification_sha256)
    else:
        output = drive(c)
    print(json.dumps(output, sort_keys=True))
    return 0 if output["status"] == "completed" or args.phase else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryError as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}))
        raise SystemExit(2)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "rejected", "reason": "invalid_or_unavailable_input"}))
        raise SystemExit(2)
