#!/usr/bin/env python3
"""One-shot A3 synthetic-delivery renewal, expiry and recovery probe."""

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from math import ceil
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(
    "/volume2/tianshu-v2-validation-wave1/accept-20260925-a3/"
    "cycle-20260925-dialogue5/deployments/source"
)
CYCLE = ROOT.parent.parent
OPS = CYCLE / "operations"
REPORTS = CYCLE / "reports"
START_MARKER = OPS / "complete-cycle.started.json"
PROGRESS_PATH = REPORTS / "complete-cycle-progress.json"
FINAL_PATH = REPORTS / "synthetic-renewal-recovery-cycle.json"
DOCKER = Path("/volume2/@appstore/ContainerManager/usr/bin/docker")
TOOLING = OPS / "tooling"
CONTEXTS = Path("/volume2/tianshu-v2-validation-wave1/accept-20260925-a3/contexts")
VENV = Path("/volume2/Dockers/tianshu-v2-validation/wave1-20260923a/tooling/venv/bin/python")
PRODUCTS = ("platform", "gateway", "memory", "companion")
PORTS = {"platform": "8443", "gateway": "8443", "memory": "8130", "companion": "8765"}
STOP_ORDER = ("companion", "gateway", "memory", "platform")
TTL_SECONDS = 300
GRACE_SECONDS = 45
PERIOD_SECONDS = TTL_SECONDS + GRACE_SECONDS
MAX_HOOK_SECONDS = 600
MIN_PRESTART_AVAILABLE = 8 * 1024**3
MIN_RUNNING_AVAILABLE = 4 * 1024**3


class Blocked(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code):
    if not condition:
        raise Blocked(code)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def extend_synthetic_publication(publication):
    require(
        publication.get("schema_version") == 1
        and publication.get("request_id") == "dep-g-bootstrap"
        and publication.get("status") == "published"
        and publication.get("config_version") == 1
        and [item.get("provider_id") for item in publication.get("providers", [])] == ["provider-synthetic"],
        "synthetic_publication_scope_invalid",
    )
    try:
        published = datetime.fromisoformat(publication["published_at"].replace("Z", "+00:00"))
        original_until = datetime.fromisoformat(publication["usable_until"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        raise Blocked("synthetic_publication_window_invalid") from None
    require(published.tzinfo is not None and original_until - published == timedelta(minutes=15),
            "synthetic_publication_default_window_changed")
    extended = dict(publication)
    extended["usable_until"] = (published + timedelta(minutes=30)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    return extended


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(str(temporary), str(path))


def run(argv, *, timeout=30, input_bytes=None, check=True):
    try:
        result = subprocess.run(
            [str(item) for item in argv],
            capture_output=True,
            timeout=timeout,
            input=input_bytes,
            env=os.environ,
        )
    except (OSError, subprocess.SubprocessError):
        raise Blocked("runtime_command_unavailable") from None
    if check and result.returncode != 0:
        raise Blocked("runtime_command_failed")
    return result


def docker(*args, timeout=30, check=True):
    return run([DOCKER, *args], timeout=timeout, check=check)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise Blocked("required_scope_report_unreadable") from None


def set_phase(name, **facts):
    state["phase"] = name
    state["updated_at"] = utc_now()
    state.update(facts)
    write_json(PROGRESS_PATH, state)
    print(json.dumps({"progress": name, **facts}, sort_keys=True), flush=True)


def project_ids(project):
    output = docker(
        "ps",
        "-a",
        "--no-trunc",
        "-q",
        "--filter",
        "label=com.docker.compose.project=" + project,
    ).stdout.decode("ascii", "strict")
    return sorted(output.split())


def inspect(cid):
    values = json.loads(docker("inspect", cid).stdout)
    require(len(values) == 1 and values[0].get("Id") == cid, "container_identity_unreadable")
    return values[0]


def identity(value):
    labels = value.get("Config", {}).get("Labels") or {}
    return {
        "id": value.get("Id"),
        "owner": labels.get("com.docker.compose.service"),
        "image": value.get("Image"),
        "name": value.get("Name"),
        "nonce": labels.get("org.tianshu.execution"),
        "project": labels.get("com.docker.compose.project"),
        "directory": labels.get("com.docker.compose.project.working_dir"),
        "config_files": labels.get("com.docker.compose.project.config_files"),
    }


def inspect_project(project):
    ids = project_ids(project)
    values = json.loads(docker("inspect", *ids).stdout) if ids else []
    by_owner = {}
    for value in values:
        fact = identity(value)
        owner = fact["owner"]
        require(owner in PRODUCTS and owner not in by_owner, "project_service_set_changed")
        require(fact["project"] == project, "project_label_mismatch")
        by_owner[owner] = value
    return ids, by_owner


def report_fact(report, owner):
    matches = [fact for fact in report["owned_containers"].values() if fact.get("owner") == owner]
    require(len(matches) == 1, "linux_validation_identity_missing")
    return matches[0]


def active_identity(report, project):
    ids, by_owner = inspect_project(project)
    expected = {owner: report_fact(report, owner) for owner in PRODUCTS}
    require(set(ids) == {fact["id"] for fact in expected.values()}, "active_container_set_changed")
    require(set(by_owner) == set(PRODUCTS), "active_service_set_changed")
    for owner in PRODUCTS:
        value = by_owner[owner]
        require(identity(value) == expected[owner], owner + "_active_identity_changed")
        require(value["State"].get("Running") is True, owner + "_not_running")
        require(value["State"].get("Health", {}).get("Status") == "healthy", owner + "_not_healthy")
        require(value.get("RestartCount") == 0, owner + "_unexpected_restart")
        require(probe(owner, value["Id"]), owner + "_authenticated_readiness_lost")
    return {
        owner: {"id": expected[owner]["id"], "image": expected[owner]["image"]}
        for owner in PRODUCTS
    }


def same_process_renewal(report, project, password, standard_proof, standard_completed_at):
    hook_started = time.monotonic()
    boot = read_json(ROOT / "reports/bootstrap/result.json")
    old_expiry = datetime.fromisoformat(boot["expires_at"].replace("Z", "+00:00"))
    require(old_expiry.tzinfo is not None, "initial_issue_expiry_invalid")
    validate_delivery_proof(standard_proof, "before_initial_expiry")
    standard_observed_at = datetime.fromisoformat(
        standard_proof["observed_at"].replace("Z", "+00:00")
    )
    require(standard_observed_at.tzinfo is not None, "standard_observed_time_invalid")
    require(standard_observed_at <= standard_completed_at, "standard_observed_time_after_step")
    standard_remaining = (old_expiry - standard_completed_at).total_seconds()
    state["standard_synthetic_delivery_before_expiry_at"] = standard_completed_at.isoformat().replace("+00:00", "Z")
    state["standard_delivery_remaining_initial_seconds"] = round(standard_remaining, 1)
    require(standard_remaining > 0, "standard_delivery_missed_initial_expiry")
    state["synthetic_deliveries"] = {"before_initial_expiry": {
        "result": "passed", "platform_id": report_fact(report, "platform")["id"],
        "completed_at": standard_completed_at.isoformat().replace("+00:00", "Z"),
        **standard_proof,
    }}
    marker = ROOT / "reports/bootstrap/reauthorization-attempt.json"
    require(not marker.exists(), "reauthorization_before_initial_expiry")
    before_identity = active_identity(report, project)
    state["active_identity_before"] = before_identity
    state["synthetic_deliveries"]["before_initial_expiry"]["gateway_id"] = before_identity["gateway"]["id"]
    state["synthetic_deliveries"]["before_initial_expiry"]["gateway_image_id"] = before_identity["gateway"]["image"]
    state["initial_issue_receipt_expiry"] = boot["expires_at"]
    state["initial_receipt_used_as_boundary_only"] = True
    platform_id = before_identity["platform"]["id"]
    gateway_id = before_identity["gateway"]["id"]

    wait_seconds = max(0, ceil((old_expiry - datetime.now(timezone.utc)).total_seconds() + 15))
    require(wait_seconds <= PERIOD_SECONDS, "initial_expiry_wait_out_of_bounds")
    set_phase("active_synthetic_renewal", target_seconds=wait_seconds)
    state["resource_samples"] = idle_period(wait_seconds, platform_id, gateway_id)
    require(time.monotonic() - hook_started < MAX_HOOK_SECONDS, "active_hook_total_deadline")
    require(datetime.now(timezone.utc) > old_expiry, "initial_receipt_expiry_not_crossed")
    require(not marker.exists(), "unexpected_reauthorization_during_active_renewal")
    after_identity = active_identity(report, project)
    require(after_identity == before_identity, "four_core_identity_changed_across_initial_expiry")
    state["active_identity_after"] = after_identity

    set_phase("synthetic_delivery_after_initial_expiry")
    state["synthetic_deliveries"]["after_initial_expiry"] = synthetic_delivery(
        platform_id, gateway_id, password, "after_initial_expiry"
    )
    after_at = datetime.fromisoformat(
        state["synthetic_deliveries"]["after_initial_expiry"]["checked_at"].replace("Z", "+00:00")
    )
    require(after_at > old_expiry, "second_delivery_not_after_initial_expiry")
    require(active_identity(report, project) == before_identity, "four_core_identity_changed_after_delivery")
    require(not marker.exists(), "unexpected_reauthorization_during_active_renewal")
    require(time.monotonic() - hook_started < MAX_HOOK_SECONDS, "active_hook_total_deadline")
    state["active_renewal_probe"] = "passed_synthetic_delivery_with_same_four_containers"
    state["active_renewal_observation_seconds"] = wait_seconds
    state["active_hook_elapsed_seconds"] = round(time.monotonic() - hook_started, 1)


def make_scope_synthetic_hook(original_call, project, captured, hook_called, refused_type, parse_proof, invalid_proof_type):
    def scope_synthetic_hook(runner, name, argv, seconds, *, input=None, capture=False):
        output = original_call(
            runner, name, argv, seconds, input=input,
            capture=(capture or name == "synthetic_dialogue"),
        )
        if name != "synthetic_dialogue":
            return output
        completed_at = datetime.now(timezone.utc)
        if (
            runner.report.get("scenario") != "synthetic-dialogue"
            or runner.report.get("release_ready") is not False
            or runner.report.get("claims", {}).get("real_models") is not False
        ):
            raise refused_type("synthetic_hook_scope_invalid")
        if hook_called[0]:
            raise refused_type("synthetic_hook_called_twice")
        hook_called[0] = True
        state["standard_probe_stdout"] = {
            "byte_count": len(output) if isinstance(output, bytes) else None,
            "sha256": hashlib.sha256(output).hexdigest() if isinstance(output, bytes) else None,
            "captured_at": completed_at.isoformat().replace("+00:00", "Z"),
        }
        try:
            standard_proof = parse_proof(output)
            same_process_renewal(
                runner.report, project, captured["password"],
                standard_proof, completed_at,
            )
        except Blocked as error:
            raise refused_type(error.code) from None
        except invalid_proof_type as error:
            raise refused_type(str(error)) from None
        return output if capture else None

    return scope_synthetic_hook


def host_mem_available():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


def require_host_reserve(minimum_bytes, code):
    available = host_mem_available()
    require(available is not None and available >= minimum_bytes, code)
    return available


def stats_sample(owners, ids, elapsed=None):
    selected = [owner for owner in owners if owner in ids]
    if not selected:
        return None
    output = docker(
        "stats",
        "--no-stream",
        "--format",
        "{{json .}}",
        *[ids[owner] for owner in selected],
        timeout=20,
    ).stdout.decode("utf-8", "replace")
    rows = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        rows.append(
            {
                "name": value.get("Name"),
                "cpu_percent": value.get("CPUPerc"),
                "memory_usage": value.get("MemUsage"),
                "memory_percent": value.get("MemPerc"),
                "pids": value.get("PIDs"),
            }
        )
    return {
        "elapsed_seconds": elapsed,
        "host_mem_available_bytes": host_mem_available(),
        "containers": rows,
    }


def probe(owner, cid):
    script = (ROOT / "tools/container_probe.py").read_text()
    result = docker(
        "exec",
        cid,
        "python",
        "-c",
        script,
        "ready",
        owner,
        PORTS[owner],
        timeout=20,
        check=False,
    )
    return result.returncode == 0


def start_synthetic_model(gateway_id):
    fixture = (ROOT / "tools/synthetic_model.py").read_text()
    docker("exec", "-d", gateway_id, "python", "-c", fixture, timeout=15)
    script = (ROOT / "tools/container_probe.py").read_text()
    ready = docker(
        "exec", gateway_id, "python", "-c", script, "model-ready",
        timeout=20, check=False,
    )
    require(ready.returncode == 0, "synthetic_model_not_ready")


def synthetic_delivery(platform_id, gateway_id, password, phase):
    require_host_reserve(MIN_RUNNING_AVAILABLE, "host_memory_reserve_lost")
    gateway = inspect(gateway_id)
    require(gateway["State"].get("Running") is True, phase + "_gateway_not_running")
    script = (OPS / "delivery_probe.py").read_text()
    client_id = str(uuid.uuid4())
    result = run(
        [DOCKER, "exec", "-i", platform_id, "python", "-c", script, "delivery-dialogue"],
        timeout=90,
        input_bytes=json.dumps({
            "password": password, "client_id": client_id, "phase": phase
        }).encode(),
        check=False,
    )
    require(result.returncode == 0, phase + "_synthetic_delivery_failed")
    proof = json.loads(result.stdout)
    validate_delivery_proof(proof, phase)
    require(proof["client_id"] == client_id, phase + "_client_id_mismatch")
    previous = state.get("synthetic_deliveries", {}).values()
    require(
        all(
            proof["client_id"] != item.get("client_id")
            and proof["message_id"] != item.get("message_id")
            and proof["turn_id"] != item.get("turn_id")
            and set(proof["reply_ids"]).isdisjoint(item.get("reply_ids", []))
            for item in previous
        ),
        phase + "_request_or_message_reused",
    )
    require_host_reserve(MIN_RUNNING_AVAILABLE, "host_memory_reserve_lost")
    return {
        "result": "passed", "checked_at": utc_now(),
        "platform_id": platform_id, "gateway_id": gateway_id,
        "gateway_image_id": gateway["Image"], **proof,
    }


def validate_delivery_proof(proof, phase):
    require(proof.get("schema_version") == "synthetic-dialogue-proof/v1", phase + "_proof_schema_invalid")
    require(isinstance(proof.get("client_id"), str) and proof["client_id"], phase + "_client_id_missing")
    require(isinstance(proof.get("message_id"), str) and proof["message_id"], phase + "_message_id_missing")
    require(isinstance(proof.get("turn_id"), str) and proof["turn_id"], phase + "_turn_id_missing")
    reply_ids = proof.get("reply_ids")
    require(isinstance(reply_ids, list) and reply_ids and all(isinstance(x, str) and x for x in reply_ids), phase + "_reply_ids_missing")
    require(len(reply_ids) == len(set(reply_ids)), phase + "_reply_ids_repeated")
    require(proof.get("receipt_state") == "accepted", phase + "_acceptance_receipt_missing")
    require(proof.get("turn_phase") == "sent", phase + "_delivery_not_sent")
    require(proof.get("reply_count") == len(reply_ids), phase + "_reply_count_mismatch")
    require(proof.get("reply_state") == "sent" and proof.get("content_state") == "available", phase + "_reply_not_delivered")
    require(proof.get("reply_sha256") == hashlib.sha256(b"DEP-G synthetic recorded reply.").hexdigest(), phase + "_reply_content_mismatch")
    try:
        observed = datetime.fromisoformat(proof["observed_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        raise Blocked(phase + "_observed_time_invalid") from None
    require(observed.tzinfo is not None, phase + "_observed_time_invalid")


def wait_healthy(owner, cid, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require_host_reserve(MIN_RUNNING_AVAILABLE, "host_memory_reserve_lost")
        value = inspect(cid)
        status = value.get("State", {}).get("Status")
        if status in ("exited", "dead") or not value.get("State", {}).get("Running"):
            raise Blocked(owner + "_exited_before_readiness")
        health = value.get("State", {}).get("Health", {}).get("Status")
        if health == "healthy" and probe(owner, cid):
            return value
        time.sleep(2)
    raise Blocked(owner + "_readiness_deadline")


def wait_gateway_start_classification(cid, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require_host_reserve(MIN_RUNNING_AVAILABLE, "host_memory_reserve_lost")
        value = inspect(cid)
        state_value = value.get("State", {})
        if not state_value.get("Running"):
            require(state_value.get("Status") == "exited", "gateway_start_state_invalid")
            if state_value.get("ExitCode") != 0 and state_value.get("OOMKilled") is False:
                return "expired", value
            raise Blocked("gateway_stopped_without_expiry_failure")
        if state_value.get("Health", {}).get("Status") == "healthy" and probe("gateway", cid):
            return "ready", value
        time.sleep(2)
    raise Blocked("gateway_start_classification_deadline")


def stop_one(owner, cid):
    before = inspect(cid)
    if before.get("State", {}).get("Running"):
        docker("stop", "--time", "30", cid, timeout=40)
    value = inspect(cid)
    state_value = value.get("State", {})
    require(state_value.get("Status") == "exited", owner + "_did_not_stop")
    require(state_value.get("ExitCode") == 0, owner + "_stop_exit_not_zero")
    require(state_value.get("OOMKilled") is False, owner + "_stop_oom_killed")
    require(value.get("RestartCount") == 0, owner + "_restart_observed")
    return {"container_id": cid, "exit_code": state_value.get("ExitCode"), "oom_killed": False}


def stop_running_project(project):
    try:
        ids, by_owner = inspect_project(project)
    except Exception:
        return []
    stopped = []
    for owner in STOP_ORDER:
        value = by_owner.get(owner)
        if not value or not value.get("State", {}).get("Running"):
            continue
        try:
            stopped.append(stop_one(owner, value["Id"]))
        except Exception:
            stopped.append({"container_id": value.get("Id"), "stop": "unconfirmed"})
    return stopped


def idle_period(seconds, platform_id, gateway_id):
    started = time.monotonic()
    last_check = -30
    samples = []
    samples.append(stats_sample(("platform", "gateway"), {"platform": platform_id, "gateway": gateway_id}, 0))
    while True:
        require_host_reserve(MIN_RUNNING_AVAILABLE, "host_memory_reserve_lost")
        elapsed = int(time.monotonic() - started)
        if elapsed >= seconds:
            break
        time.sleep(min(10, seconds - elapsed))
        elapsed = int(time.monotonic() - started)
        if elapsed - last_check < 30 and elapsed < seconds:
            continue
        gateway = inspect(gateway_id)
        platform = inspect(platform_id)
        require(gateway.get("State", {}).get("Running") is True, "gateway_died_during_idle_renewal")
        require(gateway.get("State", {}).get("Health", {}).get("Status") == "healthy", "gateway_unhealthy_during_idle_renewal")
        require(platform.get("State", {}).get("Running") is True, "platform_died_during_idle_renewal")
        require(platform.get("State", {}).get("Health", {}).get("Status") == "healthy", "platform_unhealthy_during_idle_renewal")
        require(probe("gateway", gateway_id), "gateway_authenticated_readiness_lost")
        require(probe("platform", platform_id), "platform_authenticated_readiness_lost")
        samples.append(stats_sample(("platform", "gateway"), {"platform": platform_id, "gateway": gateway_id}, elapsed))
        last_check = elapsed
        set_phase("active_synthetic_renewal", elapsed_seconds=elapsed, target_seconds=seconds)
    require(probe("gateway", gateway_id), "gateway_authenticated_readiness_lost_at_idle_deadline")
    require(probe("platform", platform_id), "platform_authenticated_readiness_lost_at_idle_deadline")
    samples.append(stats_sample(("platform", "gateway"), {"platform": platform_id, "gateway": gateway_id}, seconds))
    return samples


def stopped_period(seconds, project, expected_ids):
    started = time.monotonic()
    last_check = -30
    samples = []
    while True:
        available = require_host_reserve(MIN_RUNNING_AVAILABLE, "host_memory_reserve_lost")
        elapsed = int(time.monotonic() - started)
        ids, by_owner = inspect_project(project)
        require(set(ids) == set(expected_ids.values()), "stopped_container_set_changed")
        for owner in PRODUCTS:
            value = by_owner[owner]
            require(value["Id"] == expected_ids[owner], owner + "_stopped_identity_changed")
            require(value["State"].get("Status") == "exited", owner + "_started_during_downtime")
            require(value["State"].get("ExitCode") == 0, owner + "_normal_stop_exit_changed")
            require(value["State"].get("OOMKilled") is False, owner + "_oom_during_downtime")
            require(value.get("RestartCount") == 0, owner + "_restart_during_downtime")
        if elapsed - last_check >= 30 or elapsed >= seconds:
            samples.append({
                "elapsed_seconds": elapsed,
                "host_mem_available_bytes": available,
                "all_four_stopped_exit_zero": True,
            })
            last_check = elapsed
            set_phase("all_four_stopped_waiting_for_natural_expiry", elapsed_seconds=elapsed, target_seconds=seconds)
        if elapsed >= seconds:
            break
        time.sleep(min(10, seconds - elapsed))
    return samples


def validate_initial_scope(compose, report):
    project = compose.get("name")
    require(project == "tianshu-accept-a3-dialogue5", "unexpected_project_name")
    require(report.get("result") == "passed", "linux_synthetic_report_not_passed")
    require(report.get("scenario") == "synthetic-dialogue", "wrong_linux_scenario")
    require(
        report.get("dimensions", {}).get("synthetic_dialogue") == "passed",
        "initial_synthetic_delivery_not_passed",
    )
    require(
        report.get("dimensions", {}).get("core_stop_observed") == "passed"
        and report.get("stop_confirmed") is True,
        "initial_normal_stop_not_confirmed",
    )
    require(report.get("release_ready") is False, "release_ready_must_remain_false")
    require(set(compose.get("services", {})) == set(PRODUCTS), "unexpected_compose_service_set")
    gateway_depends = set(compose["services"]["gateway"].get("depends_on", {}))
    require(gateway_depends == {"platform"}, "gateway_dependency_set_changed")

    gateway_settings = read_json(ROOT / "config/gateway/settings.json")
    platform_settings = read_json(ROOT / "config/platform/settings.json")
    entry = platform_settings.get("entries", {}).get("config-entry", {})
    require(gateway_settings.get("platform_origin_renewal") is True, "gateway_renewal_not_enabled")
    require(platform_settings.get("model_origin_renewal_http") is True, "platform_renewal_not_enabled")
    require(platform_settings.get("web", {}).get("dialogue_enabled") is True, "synthetic_dialogue_not_enabled")
    require(set(platform_settings.get("providers", {})) == {"provider-synthetic"}, "unexpected_model_provider")
    require(entry.get("ttl_seconds") == TTL_SECONDS, "config_entry_ttl_changed")
    require("expires_at" not in entry, "unexpected_absolute_entry_expiry")
    for owner in PRODUCTS:
        service = compose["services"][owner]
        require(service.get("mem_limit") == "1g", owner + "_memory_cap_changed")
        require(service.get("cpuset") == "6,7", owner + "_cpu_set_changed")
    return project, gateway_settings, platform_settings


def verify_new_gateway(compose, report, prior_ids, project, fixed_ids):
    ids, by_owner = inspect_project(project)
    require(set(ids) == set(fixed_ids.values()), "compose_create_changed_project_container_set")
    require(set(by_owner) == set(PRODUCTS), "compose_create_service_set_incomplete")
    for owner in ("platform", "companion", "memory"):
        require(by_owner[owner]["Id"] == prior_ids[owner], owner + "_container_identity_changed")
    gateway = by_owner["gateway"]
    gateway_id = gateway["Id"]
    require(gateway_id != prior_ids["gateway"], "gateway_container_was_not_recreated")
    fact = identity(gateway)
    require(fact["owner"] == "gateway" and fact["project"] == project, "new_gateway_labels_invalid")
    expected_report = report_fact(report, "gateway")
    require(fact["image"] == expected_report["image"], "new_gateway_image_changed")
    require(gateway.get("Config", {}).get("Image") == compose["services"]["gateway"]["image"], "new_gateway_image_reference_changed")
    require(fact["directory"] == str(ROOT), "new_gateway_working_directory_changed")
    require(fact["config_files"] == str(ROOT / "compose.json"), "new_gateway_compose_file_changed")
    labels = gateway.get("Config", {}).get("Labels") or {}
    require(labels.get("org.tianshu.release") == read_json(ROOT / "release-manifest.json")["release_id"], "new_gateway_release_label_changed")
    require(labels.get("org.tianshu.product") == "gateway", "new_gateway_product_label_changed")
    require(gateway.get("Config", {}).get("User") == "10001:10001", "new_gateway_user_changed")
    require(gateway.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") == "no", "new_gateway_restart_policy_changed")
    require(not (gateway.get("HostConfig", {}).get("PortBindings") or {}), "new_gateway_host_port_binding_unexpected")

    sys.path.insert(0, str(TOOLING))
    from resource_profile import container_check

    container_check(compose["services"]["gateway"], gateway)
    service = compose["services"]["gateway"]
    expected_targets = {volume["target"] for volume in service.get("volumes", [])}
    actual_targets = {
        mount.get("Destination")
        for mount in gateway.get("Mounts", [])
        if mount.get("Type") == "bind"
    }
    require(actual_targets == expected_targets, "new_gateway_bind_mount_set_changed")
    for mount in gateway.get("Mounts", []):
        if mount.get("Type") != "bind":
            continue
        expected_volume = next(v for v in service["volumes"] if v["target"] == mount["Destination"])
        expected_source = (ROOT / expected_volume["source"]).resolve()
        require(Path(mount["Source"]).resolve() == expected_source, "new_gateway_bind_mount_source_changed")
        require(mount.get("RW") is (not expected_volume.get("read_only", False)), "new_gateway_bind_mount_mode_changed")
    network_names = set((gateway.get("NetworkSettings", {}).get("Networks") or {}).keys())
    require(network_names == {project + "_core", project + "_egress"}, "new_gateway_network_set_changed")

    platform = by_owner["platform"]
    companion = by_owner["companion"]
    memory = by_owner["memory"]
    require(platform["State"].get("Running") is True, "platform_not_running_before_gateway_start")
    require(platform["State"].get("Health", {}).get("Status") == "healthy", "platform_not_healthy_before_gateway_start")
    require(companion["State"].get("Running") is False and memory["State"].get("Running") is False, "unexpected_dependency_started")
    return gateway_id, fact, gateway


def main():
    global state
    state = {"result": "running", "phase": "initializing", "started_at": utc_now()}
    os.environ["PATH"] = str(DOCKER.parent) + ":" + os.environ.get("PATH", "")
    OPS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    require(not START_MARKER.exists(), "one_shot_marker_already_exists")
    require(not FINAL_PATH.exists(), "cycle_report_already_exists")
    require(not PROGRESS_PATH.exists(), "cycle_progress_already_exists")
    write_json(START_MARKER, {"state": "started", "started_at": utc_now(), "replay_allowed": False})
    write_json(PROGRESS_PATH, state)

    project = "tianshu-accept-a3-dialogue5"
    success = False
    try:
        require(DOCKER.is_file() and VENV.is_file(), "nas_runtime_path_missing")
        require(ROOT.is_dir() and TOOLING.is_dir(), "scope_runtime_path_missing")
        require(
            hashlib.sha256((CONTEXTS / "source-inventory.json").read_bytes()).hexdigest()
            == "ffa41ae611f00bdec8eae48aa2b20809d6d9665bc61fd3822425f48058ab21fa",
            "source_inventory_changed",
        )
        require(
            hashlib.sha256((TOOLING / "linux_reauthorize.py").read_bytes()).hexdigest()
            == "3439ca0d95d9773b9b2eb87acfc0e8e518a8e835204158d31d57fdbc437bb378",
            "a3_reauthorization_fix_not_loaded",
        )
        require(
            hashlib.sha256((ROOT / "tools/container_probe.py").read_bytes()).hexdigest()
            == "1cece914b3d1d1f3d45a89a6b9c9380cd3bd35fd307c7375fb52fc9664dacfa9",
            "standard_synthetic_probe_version_changed",
        )
        require(
            hashlib.sha256((OPS / "delivery_probe.py").read_bytes()).hexdigest()
            == "b9972a41d706523c10d1824396ad81b1cf9e40d626f825303c0ede925250db32",
            "followup_synthetic_probe_version_changed",
        )
        require(
            hashlib.sha256((TOOLING / "synthetic_probe_proof.py").read_bytes()).hexdigest()
            == "d9d63d957704ef89bd849cfe320e9ada424441c3c9ba2b1450b8f494aa53414a",
            "standard_probe_parser_version_changed",
        )
        state["tooling"] = {
            "a3_fix_commit": "0ad6165924e96476d66ab0ed974ced7ad693699d",
            "synthetic_probe_commit": "501eddd",
            "synthetic_parser_commit": "20012f41ebb122b30764bcbc92aff74f2960d8c3",
            "linux_reauthorize_sha256": "3439ca0d95d9773b9b2eb87acfc0e8e518a8e835204158d31d57fdbc437bb378",
            "standard_probe_sha256": "1cece914b3d1d1f3d45a89a6b9c9380cd3bd35fd307c7375fb52fc9664dacfa9",
            "followup_probe_sha256": "b9972a41d706523c10d1824396ad81b1cf9e40d626f825303c0ede925250db32",
            "standard_parser_sha256": "d9d63d957704ef89bd849cfe320e9ada424441c3c9ba2b1450b8f494aa53414a",
            "source_inventory_sha256": "ffa41ae611f00bdec8eae48aa2b20809d6d9665bc61fd3822425f48058ab21fa",
        }
        sys.path.insert(0, str(TOOLING))
        import linux_reauthorize
        import linux_runtime
        import linux_validate
        from bundle import verify_integrity
        from manifest import Refused
        from synthetic_probe_proof import InvalidProof, parse as parse_standard_proof

        verify_integrity(ROOT)
        compose = read_json(ROOT / "compose.json")
        require(compose.get("name") == project, "unexpected_project_name")
        require(not (ROOT / "reports/execution-attempt.json").exists(), "scope_already_executed")
        require(not (ROOT / "reports/linux-executed.json").exists(), "linux_report_already_exists")
        state["host_mem_available_before_start_bytes"] = require_host_reserve(
            MIN_PRESTART_AVAILABLE, "host_memory_headroom_below_8gib"
        )
        set_phase("initial_linux_synthetic_validation")
        captured = {}
        original_prepare = linux_runtime.prepare
        original_runner_call = linux_runtime.Runner.__call__
        hook_called = [False]

        def capture_synthetic_password(bundle, dialogue, *, real_provider=None):
            publication, password = original_prepare(
                bundle, dialogue, real_provider=real_provider
            )
            require(dialogue and real_provider is None and publication is not None,
                    "expected_synthetic_publication_missing")
            publication = extend_synthetic_publication(publication)
            captured["password"] = password
            captured["publication_usable_until"] = publication["usable_until"]
            state["synthetic_publication_requested_lifetime_seconds"] = 1800
            return publication, password

        linux_runtime.prepare = capture_synthetic_password
        linux_runtime.Runner.__call__ = make_scope_synthetic_hook(
            original_runner_call, project, captured, hook_called,
            Refused, parse_standard_proof, InvalidProof,
        )
        try:
            validation_code = linux_validate.main([
                "--bundle", str(ROOT), "--contexts", str(CONTEXTS),
                "--execute", "--scenario", "synthetic-dialogue",
                "--report-relative", "reports/linux-executed.json",
            ])
        finally:
            linux_runtime.prepare = original_prepare
            linux_runtime.Runner.__call__ = original_runner_call
        require(validation_code == 0, "initial_linux_synthetic_validation_failed")
        require(hook_called[0], "synthetic_hook_not_called")
        password = captured.pop("password", None)
        require(isinstance(password, str) and password, "synthetic_test_password_missing")
        publication_expiry = datetime.fromisoformat(
            captured["publication_usable_until"].replace("Z", "+00:00")
        )
        require(publication_expiry.tzinfo is not None, "synthetic_publication_expiry_invalid")
        state["synthetic_publication_usable_until"] = captured["publication_usable_until"]
        require_host_reserve(MIN_RUNNING_AVAILABLE, "host_memory_reserve_lost")
        state["initial_linux_synthetic_validation"] = "passed"
        report = read_json(ROOT / "reports/linux-executed.json")
        project, gateway_settings, platform_settings = validate_initial_scope(compose, report)
        bootstrap = read_json(ROOT / "reports/bootstrap/result.json")
        require(bootstrap.get("state") == "authority_initialized", "bootstrap_receipt_invalid")
        require(bootstrap.get("issuer") == "product_platform_cli", "bootstrap_issuer_invalid")
        old_expiry = datetime.fromisoformat(bootstrap["expires_at"].replace("Z", "+00:00"))
        require(old_expiry.tzinfo is not None, "bootstrap_expiry_invalid")

        expected_ids = {owner: report_fact(report, owner)["id"] for owner in PRODUCTS}
        initial_ids, initial_by_owner = inspect_project(project)
        require(set(initial_ids) == set(expected_ids.values()), "initial_project_container_set_changed")
        require(set(initial_by_owner) == set(PRODUCTS), "initial_project_service_set_incomplete")
        for owner, cid in expected_ids.items():
            value = initial_by_owner[owner]
            require(value["Id"] == cid, owner + "_initial_id_changed")
            require(identity(value) == report_fact(report, owner), owner + "_initial_identity_changed")
            require(value.get("State", {}).get("Running") is False, owner + "_initially_running")
            require(value.get("RestartCount") == 0, owner + "_restart_observed_before_cycle")
        require(initial_by_owner["gateway"]["State"].get("ExitCode") == 0, "initial_gateway_stop_not_clean")
        require(initial_by_owner["gateway"]["State"].get("OOMKilled") is False, "initial_gateway_oom_killed")
        for owner in ("companion", "memory", "platform"):
            require(initial_by_owner[owner]["State"].get("ExitCode") == 0, owner + "_initial_stop_not_clean")

        state.update(
            {
                "project": project,
                "scope": CYCLE.name,
                "release_ready": False,
                "real_models": False,
                "renewal_config": {
                    "gateway_platform_origin_renewal": gateway_settings["platform_origin_renewal"],
                    "platform_model_origin_renewal_http": platform_settings["model_origin_renewal_http"],
                    "config_entry_ttl_seconds": TTL_SECONDS,
                    "absolute_config_entry_expiry": False,
                    "initial_issue_receipt_expiry_is_not_renewal_state": True,
                },
                "initial_container_ids": expected_ids,
            }
        )
        set_phase("preflight_passed")

        active_samples = state.get("resource_samples", [])
        state["initial_normal_stop"] = {
            "confirmed_by_linux_report": True,
            "all_four_exit_zero": True,
            "conservative_wait_start_at": utc_now(),
        }
        set_phase("all_four_stopped_waiting_for_natural_expiry", target_seconds=PERIOD_SECONDS)
        downtime_samples = stopped_period(PERIOD_SECONDS, project, expected_ids)
        state["gateway_downtime_observation_seconds"] = PERIOD_SECONDS
        state["downtime_resource_samples"] = downtime_samples

        set_phase("starting_platform_for_expired_gateway")
        docker("start", expected_ids["platform"], timeout=30)
        wait_healthy("platform", expected_ids["platform"])
        platform_id = expected_ids["platform"]
        set_phase("restarting_expired_gateway")
        docker("start", expected_ids["gateway"], timeout=30)
        expiry_class, expiry_value = wait_gateway_start_classification(expected_ids["gateway"])
        require(expiry_class == "expired", "gateway_origin_not_expired_after_stop_ttl")
        natural_expiry = {
            "result": "confirmed_after_stopped_ttl_and_grace",
            "container_id": expected_ids["gateway"],
            "stopped_seconds": PERIOD_SECONDS,
            "exit_code": expiry_value["State"]["ExitCode"],
            "oom_killed": False,
        }
        state["natural_expiry"] = natural_expiry
        set_phase("natural_expiry_confirmed", exit_code=natural_expiry["exit_code"])
        require(
            (publication_expiry - datetime.now(timezone.utc)).total_seconds() >= 180,
            "synthetic_publication_budget_before_reauthorization_insufficient",
        )

        # The only signing attempt and the verified Compose create are in this process.
        issue_started = time.monotonic()
        set_phase("platform_cli_reauthorization_started")
        reauth = linux_reauthorize.execute(ROOT)
        state["reauthorization"] = {
            "status": reauth.get("status"),
            "platform_container_id": reauth.get("platform_container_id"),
            "old_expires_at": reauth.get("old_expires_at"),
            "expires_at": reauth.get("expires_at"),
            "ref_in_report": reauth.get("ref_in_report"),
            "automatic_retry": reauth.get("automatic_retry"),
        }
        require(reauth.get("status") == "reauthorized", "platform_cli_reauthorization_incomplete")
        require(reauth.get("ref_in_report") is False, "reauthorization_report_contains_ref")
        require(reauth.get("platform_container_id") == platform_id, "reauthorization_platform_identity_changed")
        reauth_expiry = datetime.fromisoformat(reauth["expires_at"].replace("Z", "+00:00"))
        remaining = (reauth_expiry - datetime.now(timezone.utc)).total_seconds()
        require(remaining >= 120, "new_origin_budget_below_120_seconds")
        state["new_origin_remaining_seconds_before_create"] = int(remaining)
        require(time.monotonic() - issue_started < 45, "reauthorization_precreate_budget_exceeded")

        # Pre-create dependency checks prevent Compose create from introducing services.
        current_ids, current_by_owner = inspect_project(project)
        require(set(current_ids) == set(expected_ids.values()), "project_container_set_changed_before_create")
        require(set(current_by_owner) == set(PRODUCTS), "project_service_set_changed_before_create")
        require(current_by_owner["platform"]["Id"] == platform_id, "platform_identity_changed_before_create")
        require(current_by_owner["platform"]["State"].get("Running") is True, "platform_not_running_before_create")
        require(current_by_owner["platform"]["State"].get("Health", {}).get("Status") == "healthy", "platform_not_healthy_before_create")
        for owner in ("companion", "memory"):
            require(current_by_owner[owner]["State"].get("Running") is False, owner + "_running_before_gateway_create")
        old_gateway = current_by_owner["gateway"]
        require(old_gateway["Id"] == expected_ids["gateway"], "old_gateway_identity_changed")
        require(old_gateway["State"].get("Status") == "exited", "old_gateway_not_stopped_before_remove")
        require(old_gateway["State"].get("ExitCode") != 0, "old_gateway_expiry_exit_missing")
        require(old_gateway["State"].get("OOMKilled") is False, "old_gateway_expiry_was_oom")
        old_gateway_id = expected_ids["gateway"]
        docker("rm", old_gateway_id, timeout=30)
        state["old_gateway_removed"] = {"container_id": old_gateway_id, "forced": False, "volumes_removed": False}

        set_phase("compose_gateway_create")
        create = docker(
            "compose",
            "-p",
            project,
            "--project-directory",
            str(ROOT),
            "-f",
            str(ROOT / "compose.json"),
            "create",
            "--no-recreate",
            "--no-build",
            "--pull",
            "never",
            "gateway",
            timeout=45,
        )
        state["compose_create"] = {
            "return_code": create.returncode,
            "command_options": ["create", "--no-recreate", "--no-build", "--pull never", "gateway"],
            "no_deps_option_used": False,
        }
        new_ids, new_by_owner = inspect_project(project)
        fixed_ids = {owner: new_by_owner[owner]["Id"] for owner in PRODUCTS}
        new_gateway_id, new_gateway_identity, new_gateway_value = verify_new_gateway(
            compose, report, expected_ids, project, fixed_ids
        )
        require(set(new_ids) == set(fixed_ids.values()), "compose_create_added_unexpected_containers")
        state["new_container_ids"] = fixed_ids
        state["new_gateway_identity"] = {
            "container_id": new_gateway_id,
            "image_id": new_gateway_identity["image"],
            "name": new_gateway_identity["name"],
            "project": new_gateway_identity["project"],
            "working_directory": new_gateway_identity["directory"],
            "compose_file": new_gateway_identity["config_files"],
            "user": new_gateway_value["Config"]["User"],
            "memory_limit_bytes": new_gateway_value["HostConfig"]["Memory"],
            "cpu_set": new_gateway_value["HostConfig"]["CpusetCpus"],
            "cpu_quota": new_gateway_value["HostConfig"].get("CpuQuota"),
            "pids_limit": new_gateway_value["HostConfig"].get("PidsLimit"),
            "restart_policy": new_gateway_value["HostConfig"]["RestartPolicy"]["Name"],
            "bind_mount_targets": sorted(
                mount["Destination"] for mount in new_gateway_value.get("Mounts", []) if mount.get("Type") == "bind"
            ),
        }
        for owner in ("platform", "companion", "memory"):
            require(new_by_owner[owner]["State"].get("Running") is (owner == "platform"), owner + "_unexpected_running_state_after_create")

        require(time.monotonic() - issue_started < 90, "reauthorization_create_budget_exceeded")
        set_phase("starting_reauthorized_gateway")
        docker("start", new_gateway_id, timeout=30)
        wait_healthy("gateway", new_gateway_id, timeout=90)
        state["readiness"] = {"platform": "passed", "gateway": "passed"}
        samples_final = [stats_sample(("platform", "gateway"), {"platform": platform_id, "gateway": new_gateway_id}, 0)]

        set_phase("starting_memory")
        docker("start", fixed_ids["memory"], timeout=30)
        wait_healthy("memory", fixed_ids["memory"], timeout=90)
        state["readiness"]["memory"] = "passed"
        set_phase("starting_companion")
        docker("start", fixed_ids["companion"], timeout=30)
        wait_healthy("companion", fixed_ids["companion"], timeout=90)
        state["readiness"]["companion"] = "passed"
        state["readiness"]["all_four_authenticated"] = True
        require_host_reserve(MIN_RUNNING_AVAILABLE, "host_memory_reserve_lost")
        require(
            (reauth_expiry - datetime.now(timezone.utc)).total_seconds() >= 110,
            "new_origin_budget_before_delivery_insufficient",
        )
        require(
            (publication_expiry - datetime.now(timezone.utc)).total_seconds() >= 110,
            "synthetic_publication_budget_before_delivery_insufficient",
        )
        start_synthetic_model(new_gateway_id)
        state["synthetic_deliveries"]["after_reauthorization"] = synthetic_delivery(
            platform_id, new_gateway_id, password, "after_reauthorization"
        )
        require(new_gateway_id != expected_ids["gateway"], "recovery_dialogue_not_on_new_gateway")
        samples_final.append(stats_sample(PRODUCTS, fixed_ids, 1))
        state["resource_samples"] = active_samples + samples_final

        set_phase("normal_sigterm_stop")
        stopped = {}
        for owner in STOP_ORDER:
            stopped[owner] = stop_one(owner, fixed_ids[owner])
        state["normal_stop"] = {
            "method": "docker stop --time 30 (SIGTERM)",
            "services": stopped,
            "running_container_count_after_stop": 0,
        }
        remaining_project_ids, remaining_by_owner = inspect_project(project)
        require(set(remaining_project_ids) == set(fixed_ids.values()), "container_ids_changed_after_stop")
        require(all(value.get("State", {}).get("Running") is False for value in remaining_by_owner.values()), "container_left_running_after_stop")
        require(time.monotonic() - issue_started < 240, "reauthorization_full_cycle_budget_exceeded")
        success = True
        state["result"] = "passed"
        state["phase"] = "complete"
        state["active_renewal_observation"] = state.get("active_renewal_probe")
        state["completed_at"] = utc_now()
        state["post_issue_elapsed_seconds"] = round(time.monotonic() - issue_started, 1)
    except Blocked as error:
        state["result"] = "blocked"
        state["failure_code"] = error.code
        state["failed_at"] = utc_now()
    except Exception:
        state["result"] = "failed"
        state["failure_code"] = "unexpected_runtime_error"
        state["failed_at"] = utc_now()
    finally:
        if project and not success:
            state["failure_cleanup_stops"] = stop_running_project(project)
        if state.get("result") == "running":
            state["result"] = "failed"
            state["failure_code"] = "cycle_interrupted"
        state["updated_at"] = utc_now()
        write_json(FINAL_PATH, state)
        write_json(PROGRESS_PATH, state)
        marker = read_json(START_MARKER)
        marker["state"] = state["result"]
        marker["completed_at"] = utc_now()
        marker["replay_allowed"] = False
        marker["report"] = str(FINAL_PATH)
        write_json(START_MARKER, marker)
        print(json.dumps({"result": state["result"], "phase": state.get("phase"), "failure_code": state.get("failure_code")}, sort_keys=True), flush=True)
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())



