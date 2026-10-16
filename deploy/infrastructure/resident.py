"""Render the existing resident stage/launch/poll mechanism for this release.

All inputs are explicit local files. This module never imports nas_access or
connects to a host; only the coordinator runs the rendered remote payloads.
"""

from __future__ import annotations

import argparse
import ast
import base64
import copy
import json
from pathlib import Path, PurePosixPath

from .bundle import digest, git_files, json_bytes, require
from .image import NODE_COMMIT, NODE_SHA256


HERE = Path(__file__).resolve().parent
GUARD_FILES = tuple("ops/resident_capacity/" + name for name in (
    "__init__.py", "guard.py", "config.schema.json", "tianshu-resident-capacity.service.in",
))
BODY_FUNCTIONS = (
    "network_view", "network_inventory", "network_definitions", "declared_addresses",
    "verify_networks", "frozen", "paths_from", "resume_gateway_if_expired",
)
SAMPLE_STATE = "/var/lib/tianshu/infrastructure-sampling"
CONTAINER_SSH = "/run/tianshu-infrastructure"


def patch_templates(templates, capacity, image, credential_binds):
    """Change only Platform's image, two exact credential binds and bounded state."""
    templates, capacity = copy.deepcopy(templates), copy.deepcopy(capacity)
    for name in ("core", "platform"):
        service = templates[name]["services"]["platform"]
        service["image"] = image
        targets = {volume["target"] for volume in service["volumes"]}
        require(not targets.intersection(bind["target"] for bind in credential_binds), "credential_mount_already_present")
        require(SAMPLE_STATE not in targets, "sampling_mount_already_present")
        for bind in credential_binds:
            service["volumes"].append({"type": "bind", **bind, "bind": {"create_host_path": False}})
        tmpfs = service.setdefault("tmpfs", [])
        require(isinstance(tmpfs, list) and all(SAMPLE_STATE != entry.split(":", 1)[0] for entry in tmpfs),
                "sampling_tmpfs_already_present")
        tmpfs.append(SAMPLE_STATE + ":rw,noexec,nosuid,nodev,size=128m,uid=10001,gid=10001,mode=0700")
    capacity["images"]["platform"] = image
    capacity["binds"]["platform"].extend(copy.deepcopy(credential_binds))
    return templates, capacity


def merge_settings(settings, infrastructure):
    require("infrastructure" not in settings, "infrastructure_settings_already_present")
    require(settings["web_external"]["directory"] == infrastructure["directory"], "external_directory_mismatch")
    return {**settings, "infrastructure": copy.deepcopy(infrastructure)}


def helpers_source(raw):
    """Parameterize only the deployed helper's path assignments, before defaults bind."""
    replacements = {
        "R": "R=Path(RELEASE['paths']['root'])",
        "D": "D=RELEASE['paths']['docker']",
        "CAP": "CAP=Path(RELEASE['paths']['capacity'])",
        "UNIT": "UNIT=Path(RELEASE['paths']['unit'])",
        "TOOLING": "TOOLING=Path(RELEASE['paths']['tooling'])",
        "PYTHON": "PYTHON=RELEASE['paths']['python']",
    }
    lines = raw.splitlines(keepends=True)
    selected = {}
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in replacements:
                selected[name] = node
    require(set(selected) == set(replacements), "resident_helper_paths_changed")
    for name, node in sorted(selected.items(), key=lambda pair: pair[1].lineno, reverse=True):
        lines[node.lineno - 1:node.end_lineno] = [replacements[name] + "\n"]
    return "".join(lines)


def existing_functions(raw):
    nodes = {node.name: node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef)}
    require(set(BODY_FUNCTIONS) <= set(nodes), "resident_mechanism_functions_missing")
    return "\n\n".join(ast.get_source_segment(raw, nodes[name]) for name in BODY_FUNCTIONS) + "\n"


def existing_actions(raw):
    result = {}
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"STAGE_BODY", "LAUNCH_BODY", "POLL_BODY"}:
                result[name] = ast.literal_eval(node.value)
    require(set(result) == {"STAGE_BODY", "LAUNCH_BODY", "POLL_BODY"}, "resident_actions_missing")
    return result


def rollback_source(stage, expected):
    return "PARENT=" + repr(stage) + "\nEXPECTED=" + repr(expected) + "\n" + '''
import ast,hashlib
from pathlib import Path
parent=Path(PARENT)
raw=(parent/'resident-update.py').read_bytes()
assert hashlib.sha256(raw).hexdigest()==EXPECTED,'release_script_changed'
tree=ast.parse(raw)
assert isinstance(tree.body[-1],ast.Try),'release_entry_changed'
entry=tree.body.pop()
exec(compile(tree,str(parent/'resident-update.py'),'exec'))
entry.body=[ast.Expr(value=ast.Call(func=ast.Name(id='rollback_release',ctx=ast.Load()),args=[ast.Name(id='parent',ctx=ast.Load())],keywords=[]))]
module=ast.fix_missing_locations(ast.Module(body=[entry],type_ignores=[]))
exec(compile(module,str(parent/'rollback.py'),'exec'))
print(json.dumps({'status':'rolled_back','stage':str(S),'database_restore':False,'later_ledgers_preserved':True,'dockge_sync_required':True}))
'''


def prepare(*, baseline, metadata, build_result, mechanism_directory, recovery,
            executor_config, guard_repo, guard_commit, stage, output):
    output = Path(output)
    require(output.is_absolute() and output.parent.is_dir() and not output.exists(), "new_output_required")
    require(PurePosixPath(stage).parent == PurePosixPath("/volume2/tianshu-v2-resident-updates"), "resident_stage_invalid")
    baseline = json.loads(Path(baseline).read_bytes())
    metadata = json.loads(Path(metadata).read_bytes())
    build = json.loads(Path(build_result).read_bytes())
    executor = json.loads(Path(executor_config).read_bytes())
    from ops.infrastructure.config import parse_config
    parse_config(executor)
    recovery = json.loads(Path(recovery).read_bytes())
    require(all(row["running"] for row in baseline["owners"]) and baseline["guard_status"]["status"] == "ready", "final_ready_baseline_required")
    require(build["status"] == "built" and build["production_changed"] is False, "verified_candidate_required")
    require(build["node_commit"] == NODE_COMMIT and build["node_package_sha256"] == NODE_SHA256, "accepted_node_package_required")
    require(baseline["owner_runtime"]["platform"]["revision"] == build["base_revision"], "final_music_revision_mismatch")
    platform = next(row for row in baseline["owners"] if row["service"] == "platform")
    require(platform["image_id"] == build["base_image_id"], "final_music_image_mismatch")
    require(set(executor["nodes"]) == {"nas-main"} and executor["nodes"]["nas-main"]["kind"] == "synology", "nas_only_registration_required")
    require(executor["nodes"]["nas-main"]["label"] == "主 NAS", "nas_identity_mismatch")
    require(executor["state_dir"] == SAMPLE_STATE and executor.get("ssh_command") == ["/usr/bin/ssh"], "executor_local_paths_mismatch")
    node = executor["nodes"]["nas-main"]
    require(node["identity_file"] == CONTAINER_SSH + "/identity" and node["known_hosts_file"] == CONTAINER_SSH + "/known_hosts", "executor_credential_paths_mismatch")
    root = metadata["resident_root"]
    require(root == "/volume2/tianshu-v2-resident" and metadata["external_directory"] == "/var/lib/tianshu/external", "resident_authority_mismatch")
    require(not metadata["infrastructure_configured"], "infrastructure_already_configured")
    credential_binds = [
        {"source": root + "/config/infrastructure/ssh/" + name,
         "target": CONTAINER_SSH + "/" + name, "read_only": True}
        for name in ("identity", "known_hosts")
    ]
    settings = json.loads((HERE / "templates/platform-settings.fragment.json").read_bytes())["infrastructure"]
    guard_files = git_files(guard_repo, guard_commit, GUARD_FILES)
    mechanism = Path(mechanism_directory)
    source_bytes = {name: (mechanism / name).read_bytes() for name in (
        "resident_helpers.py.txt", "resident_update_body.py.txt", "resident_release_launcher.py",
    )}
    sources = {name: raw.decode("utf-8").replace("\r\n", "\n") for name, raw in source_bytes.items()}
    for name, item in recovery["files"].items():
        require(name in {"ops/__init__.py", "ops/recovery/__init__.py", "ops/recovery/resident_snapshot.py", "ops/recovery/safety.py"}, "recovery_file_invalid")
        require(digest(base64.b64decode(item["base64"])) == item["sha256"], "recovery_source_changed")
    require("ops/recovery/resident_snapshot.py" in recovery["files"], "recovery_source_missing")
    contract_source = next(mount["Source"] for mount in platform["mounts"] if mount["Destination"] == "/contracts")
    release = {
        "stage": stage, "recovery": recovery,
        "paths": {"root": root, "docker": metadata["docker_path"], "capacity": metadata["capacity_path"],
                  "unit": "/etc/systemd/system/tianshu-resident-capacity.service", "tooling": str(PurePosixPath(metadata["guard_code_path"]).parents[2]),
                  "python": metadata["tool_python"]},
        "images": {"platform": {"image_id": build["image_id"], "revision": build["revision"]}},
        "previous_images": {"platform": {"image_id": platform["image_id"], "revision": build["base_revision"]}},
        "contracts": contract_source, "contracts_revision": build["contracts_revision"],
        "pins": baseline["owner_contract_pins"]["platform"], "baseline": baseline,
        "guard_commit": guard_commit,
        "guard_files": {name: {"sha256": digest(raw), "base64": base64.b64encode(raw).decode()} for name, raw in guard_files.items()},
        "infrastructure": {"settings": settings, "executor_config": executor,
                           "executor_host": root + "/config/platform/infrastructure/executor.json",
                           "credential_binds": credential_binds},
        "mechanism_sha256": {name: digest(raw) for name, raw in source_bytes.items()},
    }
    functions = existing_functions(sources["resident_update_body.py.txt"])
    helpers = helpers_source(sources["resident_helpers.py.txt"])
    worker = (HERE / "resident_worker.py").read_text(encoding="utf-8")
    inventory_source = (HERE / "resident_metadata.py").read_text(encoding="utf-8")
    mountpoint_source = (HERE / "sampling_mountpoint.py").read_text(encoding="utf-8")
    # The runtime gets the exact same tested patch rules; no second merge algorithm.
    merge_source = "\n\n".join(ast.get_source_segment(Path(__file__).read_text(encoding="utf-8"), node)
                                 for node in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
                                 if isinstance(node, ast.FunctionDef) and node.name in {"patch_templates", "merge_settings"})
    script = ("RELEASE=" + repr(release) + "\n" + helpers + "\n" + functions
              + "\nSAMPLE_STATE=" + repr(SAMPLE_STATE) + "\n" + merge_source + "\n" + inventory_source
              + "\n" + mountpoint_source + "\n" + worker)
    compile(script, "resident-update.py", "exec")
    inputs = {"release": release, "script_sha256": digest(script.encode())}
    rollback = rollback_source(stage, inputs["script_sha256"])
    compile(rollback, "rollback.py", "exec")
    actions = existing_actions(sources["resident_release_launcher.py"])
    output.mkdir(mode=0o700)
    (output / "resident-update.py").write_text(script, encoding="utf-8", newline="\n")
    (output / "resident-release.json").write_bytes(json_bytes(inputs))
    (output / "rollback.py").write_text(rollback, encoding="utf-8", newline="\n")
    for action, body in actions.items():
        prefix = ("inputs=" + repr(inputs) + "\nREMOTE_STAGE=" + repr(stage)
                  + "\nREMOTE_PYTHON=" + repr(metadata["tool_python"]) + "\n")
        if action == "STAGE_BODY":
            prefix += "DATA=" + repr(base64.b64encode(script.encode()).decode()) + "\n"
            prefix += "ROLLBACK_DATA=" + repr(base64.b64encode(rollback.encode()).decode()) + "\n"
            # Preserve the existing stage logic, writing the reviewed rollback
            # beside the worker before its one final staged receipt.
            parts = body.rsplit("\nprint(", 1)
            require(len(parts) == 2, "resident_stage_receipt_changed")
            extra = '''
rollback=base64.b64decode(ROLLBACK_DATA)
compile(rollback,str(stage/'rollback.py'),'exec')
with (stage/'rollback.py').open('xb') as stream:
 stream.write(rollback);stream.flush();os.fsync(stream.fileno())
(stage/'rollback.py').chmod(0o600)
'''
            body = parts[0] + "\n" + extra + "\nprint(" + parts[1]
        payload = prefix + body
        compile(payload, "remote-" + action.lower(), "exec")
        (output / ("remote-" + action.removesuffix("_BODY").lower() + ".py")).write_text(payload, encoding="utf-8", newline="\n")
    receipt = {"status": "prepared_locally", "stage": stage, "script_sha256": inputs["script_sha256"],
               "guard_commit": guard_commit, "node_commit": NODE_COMMIT, "image_id": build["image_id"],
               "nas_changed": False, "node_ids": ["nas-main"], "output": str(output), "rollback_sha256": digest(rollback.encode())}
    (output / "prepared.json").write_bytes(json_bytes(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "metadata", "build-result", "mechanism-directory", "recovery",
                 "executor-config", "guard-repo", "guard-commit", "stage", "output"):
        parser.add_argument("--" + name, required=True)
    print(json.dumps(prepare(**vars(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
