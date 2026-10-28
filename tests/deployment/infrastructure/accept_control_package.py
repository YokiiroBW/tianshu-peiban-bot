"""Fixed control ZIP -> outside-directory Python -> loopback SSH -> synthetic Docker."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from deploy.infrastructure import bundle, control_bundle
from accept_package import check, environment


FIXTURE_FILES = ("tests/infrastructure/helpers.py", "tests/infrastructure/control_fixture.py", "tests/infrastructure/control_ssh.py")


def accept(*, repo, commit, runtime_python, fixture_python, package, output):
    output = Path(output).resolve()
    check(not output.exists() and output.parent.is_dir(), "new output required")
    check(shutil.which("ssh"), "OpenSSH loopback client required")
    pinned = control_bundle.verify(package)
    check(pinned["source_commit"] == commit, "fixture and package source differ")
    with tempfile.TemporaryDirectory(prefix="ts208-control-") as temporary:
        directory = Path(temporary).resolve()
        repeated = directory / "repeated.zip"
        repeated_info = control_bundle.build(repo, commit, repeated)
        check(pinned == repeated_info and Path(package).read_bytes() == repeated.read_bytes(), "package not reproducible")
        code = directory / "package"
        with zipfile.ZipFile(package) as archive:
            archive.extractall(code)
        fixture = directory / "fixture-source"
        fixture.mkdir()
        for name, raw in bundle.git_files(repo, commit, FIXTURE_FILES).items():
            path = fixture / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        outside = directory / "outside"
        outside.mkdir()
        bootstrap = fixture / "start.py"
        script = fixture / "tests/infrastructure/control_ssh.py"
        bootstrap.write_text("import runpy,sys\nsys.path[:0]=" + repr([str(code), str(script.parent)])
                             + "\nrunpy.run_path(" + repr(str(script)) + ",run_name='__main__')\n", encoding="utf-8")
        ready = directory / "ready.json"
        state = directory / "synthetic-private"
        env = environment()
        env["PYTHONPATH"] = str(code)
        with (directory / "fixture-errors.log").open("wb") as errors:
            server = subprocess.Popen([str(Path(fixture_python).resolve()), "-I", "-B", str(bootstrap), "--directory", str(state), "--ready-file", str(ready)],
                                      stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, cwd=outside, env=env)
            try:
                deadline = time.monotonic() + 20
                while not ready.exists() and time.monotonic() < deadline:
                    check(server.poll() is None, "loopback fixture exited")
                    time.sleep(.05)
                check(ready.exists(), "loopback fixture not ready")
                registration = json.loads(ready.read_bytes())
                observation = json.loads(Path(registration["observation_config_path"]).read_bytes())
                check(all(node["host"] == "127.0.0.1" for node in observation["nodes"].values()), "non-loopback fixture")
                config = Path(registration["control_config_path"])
                value = json.loads(config.read_bytes())
                value["nodes"]["fixture-nas"]["command"] = [str(Path(fixture_python).resolve()), "-I", "-B",
                    str(code / "run_remote.py"), "--python", str(Path(runtime_python).resolve()), "--config", str(state / "remote-config.json")]
                config.write_bytes(bundle.json_bytes(value))
                calls = []

                def invoke(operation, params, node="fixture-nas"):
                    request = {"version": control_bundle.PROTOCOL, "request_id": "package:" + str(len(calls) + 1),
                               "node_id": node, "operation": operation, "params": params, "timeout_ms": 20000}
                    process = subprocess.run([str(Path(runtime_python).resolve()), "-I", "-B", str(code / "run_control.py"), "--config", str(config)],
                                             input=bundle.json_bytes(request), capture_output=True, cwd=outside, env=environment(), timeout=40)
                    check(process.returncode == 0 and not process.stderr, "packaged process failed")
                    response = json.loads(process.stdout)
                    check(all(response[key] == request[key] for key in ("version", "request_id", "node_id", "operation")), "response identity changed")
                    check(response["status"] in {"ok", "partial"} and response["data"] is not None, "control result: " + str(response["reason_codes"]))
                    calls.append(operation)
                    for secret in ("synthetic-fixture-password", "synthetic-fixture-dotenv"):
                        check(secret not in process.stdout.decode(), "private value escaped package")
                    return response["data"]

                def submit(params):
                    invoke("operation.submit", params)
                    expires = time.monotonic() + 20
                    while True:
                        operation = invoke("operation.get", {"operation_id": params["operation_id"]})["operation"]
                        if operation["execution_active"] is False:
                            break
                        check(time.monotonic() < expires, "original synthetic operation still active")
                        time.sleep(.05)
                    check(operation["state"] == "succeeded", "synthetic operation did not succeed")
                    before = json.loads((state / "synthetic-daemon.json").read_bytes())["calls"]
                    replay = invoke("operation.submit", params)
                    check(replay["replayed"] is True, "same operation not replayed")
                    check(json.loads((state / "synthetic-daemon.json").read_bytes())["calls"] == before, "replay sent another mutation")
                    return operation

                nodes = invoke("control.catalog", {}, node=None)["nodes"]
                check([node["id"] for node in nodes] == ["fixture-nas"], "wrong catalog")
                listed = invoke("stack.list", {})["stacks"]
                check(any(stack["id"] == registration["stack_id"] for stack in listed), "registered stack absent")
                inspected = invoke("stack.inspect", {"stack_id": registration["stack_id"]})["stack"]
                check("tianshu.secret" in inspected["compose_content"], "configuration masking missing")
                marker = state / "stacks/fixture-web/data.txt"
                marker.write_bytes(b"persistent synthetic marker")
                submit({"operation_id": "package:restart", "action": "container.restart", "target": {"container_id": registration["container_id"]}})
                plan = invoke("stack.plan", {"action": "update", "stack_id": registration["stack_id"], "expected_revision": inspected["revision"]})["plan"]
                check(plan["state"] == "ready", "update blocked")
                submit({"operation_id": "package:update", "action": "stack.apply", "target": {"plan_id": plan["plan_id"]}})
                versions = invoke("stack.versions", {"stack_id": registration["stack_id"]})["versions"]
                before = next(version["version_id"] for version in versions if version["version_id"].endswith(":before"))
                stack = invoke("stack.inspect", {"stack_id": registration["stack_id"]})["stack"]
                restore = invoke("stack.plan", {"action": "restore", "stack_id": registration["stack_id"], "expected_revision": stack["revision"], "restore_version_id": before})["plan"]
                check(restore["state"] == "ready", "restore blocked")
                submit({"operation_id": "package:restore", "action": "stack.apply", "target": {"plan_id": restore["plan_id"]}})
                check(marker.read_bytes() == b"persistent synthetic marker", "marker changed")
                final_calls = json.loads((state / "synthetic-daemon.json").read_bytes())["calls"]
                result = {"status": "accepted", "scope": "loopback_ssh_synthetic_docker", "package": pinned,
                          "isolated_python": True, "outside_working_directory": True, "source_reproduced": True,
                          "existing_python_bootstrap_to_separate_runtime": True,
                          "contract_masking_passed": True, "restart_update_restore_and_replay_passed": True,
                          "persistent_marker_preserved": True, "cli_calls": len(calls), "synthetic_mutations": final_calls,
                          "real_docker_or_nas": False}
            finally:
                server.communicate(input=b"stop", timeout=15)
                check(server.returncode == 0, "loopback fixture did not stop cleanly")
    output.write_bytes(bundle.json_bytes(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "commit", "runtime-python", "fixture-python", "package", "output"):
        parser.add_argument("--" + name, required=True)
    print(json.dumps(accept(**vars(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
