"""Prepare explicit control installation inputs; installation belongs to resident cold release."""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from .bundle import digest, json_bytes, require
from .control_bundle import CONTRACT, verify


VERSION = "infrastructure-control.install.v1"
CONFIG_FIELDS = {"version", "remote_code_root", "remote_python", "remote_runtime_directory", "remote_config_path", "relay_config_path",
                 "remote_config", "relay_config", "platform_control"}


def absolute(value):
    return (isinstance(value, str) and value.startswith("/") and "\x00" not in value
            and ".." not in PurePosixPath(value).parts and str(PurePosixPath(value)) == value)


def prepare(*, package, configuration, wheel, output):
    info = verify(package)
    config = json.loads(Path(configuration).read_bytes())
    require(isinstance(config, dict) and set(config) == CONFIG_FIELDS and config["version"] == VERSION, "control_install_config_invalid")
    for name in ("remote_code_root", "remote_python", "remote_runtime_directory", "remote_config_path", "relay_config_path"):
        require(absolute(config[name]), "control_install_path_invalid")
    wheel = Path(wheel)
    wheel_raw = wheel.read_bytes()
    with zipfile.ZipFile(package) as archive:
        hashes = re.findall(rb"--hash=sha256:([0-9a-f]{64})", archive.read("requirements.lock"))
    require(wheel.name.lower().startswith("pyyaml-6.0.3-") and wheel.suffix == ".whl"
            and digest(wheel_raw).encode() in hashes, "control_wheel_lock_mismatch")
    value = {"version": VERSION, "package": info, "configuration": config,
             "package_base64": base64.b64encode(Path(package).read_bytes()).decode(),
             "wheel": {"filename": wheel.name, "sha256": digest(wheel_raw), "base64": base64.b64encode(wheel_raw).decode()}}
    output = Path(output)
    require(output.is_absolute() and output.parent.is_dir() and not output.exists(), "new_output_required")
    with output.open("xb") as stream:
        stream.write(json_bytes(value))
    return {"status": "prepared_locally", "source_commit": info["source_commit"], "package_sha256": info["package_sha256"],
            "configuration_sha256": digest(json_bytes(config)), "output": str(output), "nas_changed": False}


def mapped_host(mounts, path, *, writable=False):
    """Resolve an explicit path through the fresh Platform bind map, retaining all mounts."""
    require(absolute(path), "control_install_path_invalid")
    target = PurePosixPath(path)
    options = [mount for mount in mounts if mount.get("Type") == "bind"
               and target.is_relative_to(PurePosixPath(mount["Destination"]))]
    require(bool(options), "control_existing_bind_required")
    mount = max(options, key=lambda item: len(PurePosixPath(item["Destination"]).parts))
    require(not writable or mount.get("RW") is True, "control_state_bind_readonly")
    return str(PurePosixPath(mount["Source"]) / target.relative_to(mount["Destination"]))


def validate(value, *, resident_root, mounts, node_ids):
    """Check deployment ownership and references, then let the fixed node validate its config."""
    require(isinstance(value, dict) and set(value) == {"version", "package", "configuration", "package_base64", "wheel"}
            and value["version"] == VERSION, "control_install_invalid")
    config, info = value["configuration"], value["package"]
    require(set(config) == CONFIG_FIELDS and config["version"] == VERSION, "control_install_config_invalid")
    package_raw = base64.b64decode(value["package_base64"], validate=True)
    require(digest(package_raw) == info["package_sha256"], "control_install_package_changed")
    # Verify the embedded bytes with the same ZIP verifier used by build/image.
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "control.zip"
        archive.write_bytes(package_raw)
        require(verify(archive) == info, "control_install_package_changed")
    wheel = value["wheel"]
    with zipfile.ZipFile(io.BytesIO(package_raw)) as archive:
        locked = re.findall(rb"--hash=sha256:([0-9a-f]{64})", archive.read("requirements.lock"))
    require(set(wheel) == {"filename", "sha256", "base64"} and "/" not in wheel["filename"] and "\\" not in wheel["filename"]
            and wheel["filename"].lower().startswith("pyyaml-6.0.3-") and wheel["filename"].endswith(".whl")
            and digest(base64.b64decode(wheel["base64"], validate=True)) == wheel["sha256"]
            and wheel["sha256"].encode() in locked, "control_wheel_lock_mismatch")
    for name in ("remote_code_root", "remote_python", "remote_runtime_directory", "remote_config_path", "relay_config_path"):
        require(absolute(config[name]), "control_install_path_invalid")
    root = PurePosixPath(resident_root)
    code = PurePosixPath(config["remote_code_root"]) / info["package_sha256"]
    require(not code.is_relative_to(root) and not root.is_relative_to(code), "control_code_must_be_separate")
    runtime = PurePosixPath(config["remote_runtime_directory"])
    require(runtime.parent == PurePosixPath(config["remote_code_root"]) and runtime.name == info["package_sha256"] + ".venv",
            "control_runtime_version_mismatch")
    private_root = root / "config/platform/infrastructure"
    for key in ("relay_config_path", "remote_config_path"):
        require(PurePosixPath(config[key]).parent == private_root, "control_private_config_scope_invalid")
    require(config["relay_config_path"] != config["remote_config_path"], "control_private_config_overlap")
    relay, remote, platform = config["relay_config"], config["remote_config"], config["platform_control"]
    require(set(relay) == {"version", "observation_config", "state_dir", "nodes"}
            and relay["version"] == "infrastructure-control.config.v1", "control_relay_config_invalid")
    require(relay["observation_config"] == "/etc/tianshu/infrastructure/executor.json", "control_observation_reference_changed")
    require(set(relay["nodes"]) == set(node_ids) == {remote["node_id"]}, "control_node_identity_mismatch")
    require(remote["version"] == "infrastructure-control.remote.v1", "control_remote_config_invalid")
    expected_command = ["sudo", "-n", config["remote_python"], "-B", str(code / "run_remote.py"),
                        "--python", str(runtime / "bin/python"), "--config", config["remote_config_path"]]
    require(relay["nodes"][remote["node_id"]] == {"command": expected_command}, "control_fixed_remote_command_mismatch")
    require({"tianshu-v2-resident", "tianshu-v2-resident-obs"} <= set(remote["protected_projects"])
            and "dockge" in remote["protected_container_names"], "control_protection_missing")
    require(any(item["path"] == resident_root for item in remote.get("protected_bind_roots", [])), "control_bind_protection_missing")
    require(absolute(remote["state_dir"]) and absolute(relay["state_dir"]), "control_state_path_invalid")
    relay_host = mapped_host(mounts, relay["state_dir"], writable=True)
    states = [PurePosixPath(relay_host), PurePosixPath(remote["state_dir"])]
    require(all(path.is_relative_to(root / "data/platform") and path != root / "data/platform" for path in states),
            "control_state_backup_scope_invalid")
    require(not states[0].is_relative_to(states[1]) and not states[1].is_relative_to(states[0]), "control_state_overlap")
    require(all(not relay["state_dir"].startswith(mount["Destination"] + "/")
                and relay["state_dir"] != mount["Destination"] for mount in mounts if mount.get("Type") == "tmpfs"),
            "control_state_must_be_persistent")
    require(set(platform) == {"executor", "contract_directory"} and set(platform["executor"]) == {"command", "directory"},
            "control_platform_settings_invalid")
    package_root = platform["executor"]["directory"]
    require(package_root == "/opt/tianshu-infrastructure-control" and platform["contract_directory"] == package_root + "/" + CONTRACT,
            "control_platform_contract_mismatch")
    command = platform["executor"]["command"]
    require(isinstance(command, list) and len(command) == 6 and command[0] == "/opt/tianshu-infrastructure-venv/bin/python"
            and command[1:5] == ["-I", "-B", package_root + "/run_control.py", "--config"], "control_platform_command_invalid")
    require(mapped_host(mounts, command[5]) == config["relay_config_path"], "control_relay_reference_mismatch")
    return {**value, "code_directory": str(code), "relay_state_host": relay_host}


def runtime_source():
    return Path(__file__).with_name("control_install_body.py").read_text(encoding="utf-8").replace("\r\n", "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package", "configuration", "wheel", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    print(json.dumps(prepare(**vars(parser.parse_args(argv))), sort_keys=True))


if __name__ == "__main__":
    main()
