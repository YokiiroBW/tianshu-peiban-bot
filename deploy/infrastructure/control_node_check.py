"""Run the fixed Node-only process proof in an explicitly selected Dockge full ID."""

import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path

from .bundle import digest, json_bytes, require
from .control_bundle import verify


def run(*, package, docker_command, dockge_id, directory, output):
    info = verify(package)
    require(bool(re.fullmatch(r"[0-9a-f]{64}", dockge_id)), "full_dockge_container_id_required")
    require(bool(re.fullmatch(r"/tmp/tianshu-control-proof-[a-z0-9_-]+", directory)), "explicit_new_node_proof_directory_required")
    require(isinstance(docker_command, list) and docker_command, "explicit_docker_command_required")
    output = Path(output)
    require(output.is_absolute() and output.parent.is_dir() and not output.exists(), "new_output_required")
    script = Path(__file__).with_suffix(".js").read_text(encoding="utf-8").replace("\r\n", "\n")
    with zipfile.ZipFile(package) as archive:
        wrapper = archive.read("ops/infrastructure/control/compose-wrapper.js")
        probe = archive.read("ops/infrastructure/control/compose-probe.js")
    data = {"wrapper": wrapper.decode(), "probe": probe.decode(), "wrapper_sha256": digest(wrapper), "probe_sha256": digest(probe)}
    process = subprocess.run([*docker_command, "exec", "-i", dockge_id, "node", "-e", script, directory],
                             input=json_bytes(data), capture_output=True, timeout=20)
    require(process.returncode == 0 and not process.stderr, "control_node_proof_failed")
    result = json.loads(process.stdout)
    require(result["status"] == "accepted" and result["tests"] == 2, "control_node_proof_failed")
    result.update(source_commit=info["source_commit"], package_sha256=info["package_sha256"], dockge_id=dockge_id,
                  wrapper_sha256=digest(wrapper), probe_sha256=digest(probe), harness_sha256=digest(script.encode()))
    with output.open("xb") as stream:
        stream.write(json_bytes(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package", "dockge-id", "directory", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--docker-command-json", required=True)
    args = vars(parser.parse_args(argv))
    args["docker_command"] = json.loads(args.pop("docker_command_json"))
    print(json.dumps(run(**args), sort_keys=True))


if __name__ == "__main__":
    main()
