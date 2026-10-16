"""Actual fixed executor ZIP -> isolated Python -> loopback-only SSH acceptance.

The two producer test files are exported separately from the same commit.
Nothing here is part of the runtime source package or contacts a real node.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from deploy.infrastructure import bundle


FIXTURE_FILES = ("tests/infrastructure/helpers.py", "tests/infrastructure/synthetic_ssh.py")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def environment():
    return {
        key: value for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "PROGRAMDATA", "TEMP", "TMP", "LANG", "LC_ALL"}
    }


class PackagedExecutor:
    def __init__(self, python, code, directory):
        self.python = Path(python).resolve()
        self.code = code
        self.directory = directory
        self.calls = 0

    def invoke(self, config, node_id, operation, params=None):
        self.calls += 1
        request = {
            "version": bundle.PROTOCOL, "request_id": f"package:{self.calls}",
            "node_id": node_id, "operation": operation, "params": params or {}, "timeout_ms": 20000,
        }
        result = subprocess.run(
            [str(self.python), "-I", "-B", str(self.code / "run_node.py"), "--config", str(config)],
            input=bundle.json_bytes(request), cwd=self.directory, env=environment(),
            capture_output=True, timeout=40, check=False,
        )
        check(result.returncode == 0, f"{operation} process failed: {result.stderr[:2048]!r}")
        check(not result.stderr, f"{operation} unexpected process stderr")
        response = json.loads(result.stdout)
        for field in ("version", "request_id", "node_id", "operation"):
            check(response[field] == request[field], f"response identity mismatch: {field}")
        check(response["observed_at"].endswith("Z"), "missing UTC observation time")
        check(response["status"] in {"ok", "partial"}, f"{operation}: {response['reason_codes']}")
        check(response["data"] is not None, "missing operation data")
        return response


@contextmanager
def synthetic_ssh(repository, commit, fixture_python, package, directory, containers):
    root = directory / f"fixture-{containers}"
    root.mkdir()
    files = bundle.git_files(repository, commit, FIXTURE_FILES)
    for name, raw in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    fixture = root / "tests/infrastructure/synthetic_ssh.py"
    bootstrap = root / "start_fixture.py"
    bootstrap.write_text(
        "import runpy, sys\n"
        + f"sys.path[:0] = [{str(package)!r}, {str(fixture.parent)!r}]\n"
        + f"runpy.run_path({str(fixture)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    private = root / "synthetic-connection"
    ready = root / "ready.json"
    with (root / "server.stderr").open("wb") as errors:
        process = subprocess.Popen(
            [str(Path(fixture_python).resolve()), "-I", "-B", str(bootstrap),
             "--directory", str(private), "--ready-file", str(ready), "--containers", str(containers)],
            cwd=root, env=environment(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors,
        )
        try:
            deadline = time.monotonic() + 20
            registration = None
            while time.monotonic() < deadline:
                check(process.poll() is None, "synthetic SSH fixture exited before readiness")
                if ready.exists():
                    try:
                        registration = json.loads(ready.read_bytes())
                    except ValueError:
                        pass
                    if registration:
                        break
                time.sleep(0.05)
            check(registration and registration["synthetic"] and registration["host"] == "127.0.0.1", "loopback fixture unavailable")
            config = Path(registration["config_path"])
            check(config.is_absolute() and config.resolve().is_relative_to(private.resolve()), "fixture config escaped test directory")
            value = json.loads(config.read_bytes())
            for node in value["nodes"].values():
                check(node["host"] == "127.0.0.1", "fixture attempted a non-loopback node")
                for name in ("identity_file", "known_hosts_file"):
                    check(Path(node[name]).resolve().is_relative_to(private.resolve()), "fixture credential reference escaped")
            yield config, registration
        finally:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            check(process.returncode == 0, "synthetic SSH fixture did not shut down cleanly")


def accept(repository, commit, runtime_python, fixture_python, output, package_file=None):
    check(shutil.which("ssh") is not None, "OpenSSH client required for actual loopback test")
    with tempfile.TemporaryDirectory(prefix="ts178-package-") as raw:
        directory = Path(raw).resolve()
        archive = Path(package_file).resolve() if package_file else directory / "node.zip"
        built = bundle.verify(archive) if package_file else bundle.build(repository, commit, archive)
        check(built["source_commit"] == commit, "package source commit does not match requested fixture commit")
        repeated = directory / "repeated.zip"
        again = bundle.build(repository, commit, repeated)
        check(archive.read_bytes() == repeated.read_bytes() and built == again, "fixed input did not reproduce")
        package = directory / "package"
        with zipfile.ZipFile(archive) as source:
            source.extractall(package)
        outside = directory / "empty-working-directory"
        outside.mkdir()
        executor = PackagedExecutor(runtime_python, package, outside)
        version = subprocess.run(
            [str(executor.python), "-I", "-B", str(package / "run_node.py"), "--version"],
            cwd=outside, env=environment(), capture_output=True, timeout=10, check=True,
        )
        check(json.loads(version.stdout)["source_commit"] == commit, "wrong runtime source identity")
        empty = json.loads((package / "examples/executor-empty.json").read_bytes())
        empty["state_dir"] = str(directory / "empty-state")
        empty["ssh_command"] = [shutil.which("ssh")]
        config = directory / "empty-config.json"
        config.write_bytes(bundle.json_bytes(empty))
        response = executor.invoke(config, None, "node.catalog")
        check(response["status"] == "ok" and response["data"] == {"nodes": []}, "empty catalog did not run")
        evidence = {"empty_catalog": True, "outside_working_directory": True, "isolated_python": True}

        with synthetic_ssh(repository, commit, fixture_python, package, directory, 150) as (config, registration):
            response = executor.invoke(config, None, "node.catalog")
            check({node["id"] for node in response["data"]["nodes"]} == {"fixture-nas", "fixture-router"}, "registered catalog mismatch")
            check(all(set(node) == {"id", "label", "kind", "capabilities"} for node in response["data"]["nodes"]), "catalog exposed connection fields")
            first = executor.invoke(config, "fixture-nas", "node.snapshot")["data"]
            second = executor.invoke(config, "fixture-nas", "node.snapshot")["data"]
            check(first["identity"]["kind"] == "synology" and first["identity"]["node_id"] == "fixture-nas", "wrong host identity")
            check(first["cpu"]["state"] == "first_sample" and first["cpu"]["usage_ratio"] is None, "first host rate was invented")
            check(second["cpu"]["state"] == "ok" and 0 <= second["cpu"]["usage_ratio"] <= 1, "second host CPU rate missing")
            router = executor.invoke(config, "fixture-router", "node.snapshot")["data"]
            check(router["identity"]["kind"] == "openwrt" and router["openwrt"] is not None, "OpenWrt observation missing")
            check(router["memory"]["total_bytes"] > 0 and router["interfaces"], "router memory/interface observations missing")
            ids, cursor, pages = [], None, 0
            while True:
                response = executor.invoke(config, "fixture-nas", "docker.inventory", {"limit": 100, **({"cursor": cursor} if cursor else {})})
                check(response["status"] == "ok" and response["completeness"]["total"] == 150, "inventory completeness mismatch")
                containers = response["data"]["containers"]
                check(all(item["availability"] == "ok" and item["health"] == "none" for item in containers), "unknown/no-health inventory state wrong")
                ids.extend(item["id"] for item in containers)
                pages += 1
                cursor = response["completeness"]["next_cursor"]
                if not cursor:
                    break
                check(pages < 4, "pagination failed to terminate")
            check(len(ids) == 150 and set(ids) == set(registration["container_ids"]), "inventory omitted or duplicated identities")
            identity = ids[0]
            params = {"container_ids": [identity]}
            first_stats = executor.invoke(config, "fixture-nas", "docker.stats", params)["data"]["containers"][0]
            next_stats = executor.invoke(config, "fixture-nas", "docker.stats", params)["data"]["containers"][0]
            check(first_stats["cpu"]["usage_ratio"] is None and next_stats["cpu"]["usage_ratio"] is not None, "container counter baseline did not persist")
            check(next_stats["memory"]["raw_usage_bytes"] == 10000 and next_stats["memory"]["cache_adjusted_usage_bytes"] == 9000, "memory basis incorrect")
            logs = executor.invoke(config, "fixture-nas", "docker.logs", {"container_id": identity, "tail": 10, "since_seconds": 60, "max_bytes": 1024})["data"]
            check(logs["container_id"] == identity and logs["returned_bytes"] <= 1024, "log bound or identity wrong")
            check("synthetic log" in logs["stdout"], "actual SSH log preview missing")
            stack = executor.invoke(config, "fixture-nas", "compose.inventory", {"limit": 100})["data"]["stacks"][0]
            check(stack["drift_status"] == "drifted" and "service_count_mismatch" in stack["services"][0]["findings"], "150 instances were falsely declared in sync")
            evidence.update({
                "catalog_connection_fields_excluded": True, "host_counter_samples": 2,
                "openwrt_observed": True, "container_inventory_count": len(ids), "inventory_pages": pages,
                "container_counter_samples": 2, "bounded_logs": True, "compose_many_instances": "drifted",
            })

        with synthetic_ssh(repository, commit, fixture_python, package, directory, 1) as (config, _):
            stack = executor.invoke(config, "fixture-nas", "compose.inventory", {"limit": 100})["data"]["stacks"][0]
            check(stack["drift_status"] == "in_sync", "single matching Compose instance did not reconcile")
            evidence["compose_one_instance"] = "in_sync"

        result = {
            **built, "status": "synthetic_package_acceptance_passed", "process_requests": executor.calls,
            "checks": evidence, "real_ssh_protocol": True, "real_docker_daemon": False,
            "real_nas_openwrt": False,
            "fixture_source_sha256": {name: bundle.digest(raw) for name, raw in bundle.git_files(repository, commit, FIXTURE_FILES).items()},
        }
        output.write_bytes(bundle.json_bytes(result))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--runtime-python", type=Path, required=True)
    parser.add_argument("--fixture-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", type=Path, help="Verify and exercise this already-built ZIP")
    args = parser.parse_args()
    result = accept(args.repo, args.commit, args.runtime_python, args.fixture_python, args.output, args.package)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
