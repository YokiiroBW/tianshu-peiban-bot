"""Explicit isolated-stack acceptance driver. The coordinator runs it on the node."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
import uuid
from pathlib import Path


VERSION = "infrastructure-control.v1"


def require(condition, code):
    if not condition:
        raise ValueError(code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode()


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def docker_json(command, *args):
    result = subprocess.run([*command, *args], capture_output=True, timeout=45)
    require(result.returncode == 0 and len(result.stdout) <= 32 * 1024 * 1024, "acceptance_docker_readback_failed")
    return json.loads(result.stdout)


def inventory(command):
    result = subprocess.run([*command, "ps", "--all", "--quiet", "--no-trunc"], capture_output=True, timeout=30)
    require(result.returncode == 0, "acceptance_inventory_failed")
    ids = result.stdout.decode().split()
    require(all(re.fullmatch(r"[0-9a-f]{64}", identity) for identity in ids), "acceptance_inventory_identity_invalid")
    rows = []
    for start in range(0, len(ids), 32):
        for item in docker_json(command, "inspect", *ids[start:start + 32]):
            state = item["State"]
            labels = item["Config"].get("Labels") or {}
            rows.append({"id": item["Id"], "name": item["Name"], "image_id": item["Image"],
                         "project": labels.get("com.docker.compose.project"),
                         "state": {key: state.get(key) for key in ("Status", "Running", "Paused", "Restarting", "Dead", "OOMKilled", "ExitCode")},
                         "health": (state.get("Health") or {}).get("Status"), "mounts": item["Mounts"],
                         "configuration_sha256": sha(item["Config"]), "host_configuration_sha256": sha(item["HostConfig"])})
    return sorted(rows, key=lambda item: item["id"])


def compare_inventory(before, after, project):
    old = {row["id"]: row for row in before if row["project"] != project}
    new = {row["id"]: row for row in after if row["project"] != project}
    removed = sorted(set(old) - set(new))
    changed = sorted(identity for identity in old.keys() & new.keys() if old[identity] != new[identity])
    return {"preserved": not removed and not changed, "original_count": len(old), "current_count": len(new),
            "removed": removed, "changed": changed, "added": sorted(set(new) - set(old))}


class Acceptance:
    def __init__(self, config, output):
        required = {"version", "command", "directory", "docker_command", "node_id", "project_name", "image",
                    "expected_image_id", "replacement_image", "replacement_image_id", "stacks_root", "marker_directory", "operation_timeout_seconds"}
        require(isinstance(config, dict) and set(config) == required and config["version"] == "infrastructure-control.acceptance.v1",
                "acceptance_configuration_invalid")
        require(bool(re.fullmatch(r"tianshu-control-acceptance-[a-z0-9][a-z0-9_-]{0,63}", config["project_name"])),
                "explicit_isolated_acceptance_project_required")
        for field in ("command", "docker_command"):
            require(isinstance(config[field], list) and config[field] and all(isinstance(value, str) and value for value in config[field]),
                    "acceptance_command_required")
        for field in ("directory", "stacks_root", "marker_directory"):
            path = Path(config[field])
            require(path.is_absolute() and ".." not in path.parts, "acceptance_absolute_path_required")
        require(config["image"] and re.fullmatch(r"sha256:[0-9a-f]{64}", config["expected_image_id"]), "acceptance_image_required")
        require(config["replacement_image"] and re.fullmatch(r"sha256:[0-9a-f]{64}", config["replacement_image_id"])
                and config["replacement_image_id"] != config["expected_image_id"], "acceptance_distinct_replacement_image_required")
        require(type(config["operation_timeout_seconds"]) is int and 5 <= config["operation_timeout_seconds"] <= 3600,
                "acceptance_deadline_invalid")
        self.config, self.output = config, Path(output)
        require(self.output.is_absolute() and self.output.parent.is_dir() and not self.output.exists(), "new_acceptance_output_required")
        self.run_id = uuid.uuid4().hex
        self.counter = 0
        self.receipts = []

    def save(self, name, value):
        with (self.output / name).open("xb") as stream:
            stream.write(encoded(value))

    def call(self, operation, params):
        self.counter += 1
        request = {"version": VERSION, "request_id": "acceptance:" + self.run_id + ":" + str(self.counter),
                   "node_id": self.config["node_id"], "operation": operation, "params": params, "timeout_ms": 30000}
        process = subprocess.run(self.config["command"], input=encoded(request), cwd=self.config["directory"],
                                 capture_output=True, timeout=45)
        require(process.returncode == 0 and len(process.stdout) <= 4 * 1024 * 1024, "acceptance_control_process_failed")
        response = json.loads(process.stdout)
        require(all(response.get(key) == request[key] for key in ("version", "request_id", "node_id", "operation")),
                "acceptance_response_identity_mismatch")
        self.save(f"response-{self.counter:04d}.json", response)
        require(response["status"] in {"ok", "partial"} and isinstance(response.get("data"), dict), "acceptance_control_response_failed")
        return response["data"]

    def wait(self, operation_id):
        deadline = time.monotonic() + self.config["operation_timeout_seconds"]
        while True:
            operation = self.call("operation.get", {"operation_id": operation_id, "reconcile": True})["operation"]
            if operation["execution_active"] is False:
                require(operation["state"] == "succeeded" and operation["command"]["state"] == "succeeded",
                        "acceptance_operation_not_successful")
                return operation
            require(time.monotonic() < deadline, "acceptance_original_operation_still_active")
            time.sleep(.5)

    def submit(self, action, target, label):
        operation_id = "acceptance:" + self.run_id + ":" + label
        params = {"operation_id": operation_id, "action": action, "target": target}
        self.save("intent-" + label + ".json", params)
        try:
            self.call("operation.submit", params)
        except (OSError, subprocess.TimeoutExpired):
            # A send may have happened. Read the original ID; never retry a send.
            pass
        operation = self.wait(operation_id)
        require(operation["runtime"]["state"] == ("stopped" if action.endswith(".stop") else "running")
                and operation["runtime"]["health"] == "not_configured", "acceptance_runtime_or_health_mismatch")
        target_before = self.target()
        replay = self.call("operation.submit", params)
        require(replay["replayed"] is True and replay["operation"]["operation_id"] == operation_id
                and replay["operation"]["state"] == "succeeded" and replay["operation"]["execution_active"] is False,
                "acceptance_replay_failed")
        require(self.target() == target_before, "acceptance_replay_changed_target")
        self.receipts.append({"label": label, "operation": operation, "replayed_without_runtime_change": True})
        self.save("completed-" + label + ".json", self.receipts[-1])
        return operation

    def target(self):
        result = subprocess.run([*self.config["docker_command"], "ps", "--all", "--quiet", "--no-trunc", "--filter",
                                 "label=com.docker.compose.project=" + self.config["project_name"]], capture_output=True, timeout=30)
        require(result.returncode == 0, "acceptance_target_readback_failed")
        ids = result.stdout.decode().split()
        require(len(ids) == 1 and re.fullmatch(r"[0-9a-f]{64}", ids[0]), "acceptance_exactly_one_target_required")
        value = docker_json(self.config["docker_command"], "inspect", ids[0])[0]
        require(value["Config"]["Labels"]["com.docker.compose.project"] == self.config["project_name"], "acceptance_target_project_changed")
        environment = dict(item.split("=", 1) for item in value["Config"].get("Env", []) if "=" in item)
        return {"id": value["Id"], "image_id": value["Image"], "state": {key: value["State"].get(key) for key in (
            "Status", "Running", "Paused", "StartedAt", "FinishedAt")}, "mounts": value["Mounts"],
            "configuration_sha256": sha(value["Config"]), "acceptance_revision": environment.get("ACCEPTANCE_REVISION")}

    def stack(self, stack_id):
        return self.call("stack.inspect", {"stack_id": stack_id})["stack"]

    def plan(self, params):
        plan = self.call("stack.plan", params)["plan"]
        require(plan["state"] == "ready" and not plan["blockers"], "acceptance_plan_blocked")
        return plan

    def compose(self, revision):
        project = self.config["project_name"]
        return json.dumps({"name": project, "services": {"marker": {
            "image": self.config["image"] if revision == "one" else self.config["replacement_image"],
            "pull_policy": "never", "restart": "no",
            "command": ["sh", "-c", "test -f /marker/value || printf '%s' " + self.run_id + " > /marker/value; exec sleep 86400"],
            "environment": {"ACCEPTANCE_REVISION": revision},
            "volumes": [{"type": "bind", "source": self.config["marker_directory"], "target": "/marker", "read_only": False}],
        }}}, sort_keys=True)

    def marker(self):
        path = Path(self.config["marker_directory"]) / "value"
        require(path.is_file() and not path.is_symlink() and path.read_text() == self.run_id, "acceptance_marker_data_changed")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def run(self):
        self.output.mkdir(mode=0o700)
        before = inventory(self.config["docker_command"])
        self.save("non-targets-before.json", before)
        require(not any(row["project"] == self.config["project_name"] for row in before), "acceptance_project_already_exists")
        stack_path = Path(self.config["stacks_root"]) / self.config["project_name"]
        require(not stack_path.exists(), "acceptance_stack_directory_already_exists")
        marker = Path(self.config["marker_directory"])
        require(not marker.exists() and marker.parent.is_dir()
                and not any(path.is_symlink() for path in (marker, *marker.parents))
                and not marker.is_relative_to(stack_path), "new_isolated_marker_directory_required")
        image = docker_json(self.config["docker_command"], "image", "inspect", self.config["image"])[0]
        require(image["Id"] == self.config["expected_image_id"], "acceptance_initial_image_changed")
        replacement = docker_json(self.config["docker_command"], "image", "inspect", self.config["replacement_image"])[0]
        require(replacement["Id"] == self.config["replacement_image_id"], "acceptance_replacement_image_changed")
        marker.mkdir(mode=0o700)
        try:
            plan = self.plan({"action": "create", "project_name": self.config["project_name"], "compose_content": self.compose("one")})
            stack_id = plan["stack_id"]
            self.submit("stack.apply", {"plan_id": plan["plan_id"]}, "create")
            original_id = self.target()["id"]
            marker_sha = self.marker()
            for action in ("container.stop", "container.start", "container.restart"):
                self.submit(action, {"container_id": original_id}, action.replace(".", "-"))
                require(self.target()["id"] == original_id and self.marker() == marker_sha, "acceptance_container_identity_or_data_changed")
            for action in ("stack.stop", "stack.start", "stack.restart"):
                stack = self.stack(stack_id)
                self.submit(action, {"stack_id": stack_id, "expected_revision": stack["revision"]}, action.replace(".", "-"))
                require(self.target()["id"] == original_id and self.marker() == marker_sha, "acceptance_stack_identity_or_data_changed")
            stack = self.stack(stack_id)
            deployed = self.plan({"action": "deploy", "stack_id": stack_id, "expected_revision": stack["revision"], "compose_content": self.compose("two")})
            self.submit("stack.apply", {"plan_id": deployed["plan_id"]}, "deploy")
            after_deploy = self.target()
            require(after_deploy["image_id"] == self.config["replacement_image_id"] and after_deploy["acceptance_revision"] == "two"
                    and self.marker() == marker_sha, "acceptance_replacement_runtime_mismatch")
            versions = self.call("stack.versions", {"stack_id": stack_id, "limit": 200})["versions"]
            restore_id = next(item["version_id"] for item in versions if item["version_id"].endswith(":deploy:before"))
            stack = self.stack(stack_id)
            updated = self.plan({"action": "update", "stack_id": stack_id, "expected_revision": stack["revision"]})
            self.submit("stack.apply", {"plan_id": updated["plan_id"]}, "update")
            after_update = self.target()
            update_observation = {"before_image_id": after_deploy["image_id"], "after_image_id": after_update["image_id"],
                                  "image_id_changed": after_deploy["image_id"] != after_update["image_id"]}
            self.save("image-update-observation.json", update_observation)
            stack = self.stack(stack_id)
            restored = self.plan({"action": "restore", "stack_id": stack_id, "expected_revision": stack["revision"], "restore_version_id": restore_id})
            self.submit("stack.apply", {"plan_id": restored["plan_id"]}, "restore")
            require(self.marker() == marker_sha, "acceptance_marker_data_changed")
            final = self.target()
            require(final["state"]["Running"] is True and final["acceptance_revision"] == "one"
                    and final["image_id"] == self.config["expected_image_id"], "acceptance_final_restore_mismatch")
            self.save("target-final.json", final)
            report = compare_inventory(before, inventory(self.config["docker_command"]), self.config["project_name"])
            require(report["preserved"], "acceptance_non_target_changed")
            self.save("result.json", {"status": "accepted", "scope": "explicit_isolated_docker_stack", "project": self.config["project_name"],
                                      "stack_id": stack_id, "operations": self.receipts, "marker_sha256": marker_sha,
                                      "configuration_image_switch": {"from": self.config["expected_image_id"], "deployed": after_deploy["image_id"], "restored": final["image_id"]},
                                      "image_update_observation": update_observation,
                                      "non_targets": report, "data_rollback_claimed": False,
                                      "test_stack_and_marker_retained": True, "production_services_mutated": False})
        except Exception as error:
            # Preserve the exact old operation ID, test stack, marker and evidence.
            # There is no automatic stop/down/delete or mutation retry.
            code = str(error)
            self.save("failure.json", {"status": "requires_inspection", "code": code if re.fullmatch(r"[a-z0-9_]+", code) else "acceptance_failed",
                                       "automatic_cleanup": False, "operations_completed": len(self.receipts)})
            raise
        finally:
            after = inventory(self.config["docker_command"])
            self.save("non-targets-after.json", after)
            self.save("non-target-comparison.json", compare_inventory(before, after, self.config["project_name"]))
        return json.loads((self.output / "result.json").read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = Acceptance(json.loads(args.configuration.read_bytes()), args.output).run()
    print(json.dumps({key: result[key] for key in ("status", "scope", "project", "stack_id", "marker_sha256", "non_targets")}, sort_keys=True))


if __name__ == "__main__":
    main()
