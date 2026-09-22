"""Explicit local Linux image/liveness smoke, never a release or NAS acceptance claim.

Default is plan. --execute requires a fresh tianshu-qa-* bundle with test TLS, no provider,
no dialogue, empty state/log mounts, and a local Linux Docker daemon. Logs are not collected:
product build/runtime diagnostics may contain operator input. Only exit codes are reported.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from bundle import preflight, verify_integrity
from manifest import (
    PRODUCTS,
    Refused,
    digest,
    inside,
    load_manifest,
    read_json,
    require,
    write_json,
)


def plan(bundle, contexts):
    verify_integrity(bundle)
    manifest = load_manifest(bundle / "release-manifest.json")
    inventory = read_json(contexts / "source-inventory.json")
    require(
        inventory["release_id"] == manifest["release_id"],
        "source_inventory_release_mismatch",
    )
    steps = []
    for p in PRODUCTS:
        source = inventory["products"][p]
        require(
            source["source"] == manifest["products"][p]["source"],
            "source_commit_mismatch",
        )
        found = {
            path.relative_to(contexts / p).as_posix()
            for path in (contexts / p).rglob("*")
            if path.is_file()
        }
        require(found == set(source["files"]), "source_inventory_changed")
        for name, expected in source["files"].items():
            require(
                digest(inside(contexts / p, name).read_bytes()) == expected,
                "source_bytes_changed",
            )
        # Rebuild argv locally; never execute a command embedded in an input report.
        dockerfile = "infra/container/Dockerfile" if p == "memory" else "Dockerfile"
        steps.append(
            {
                "id": "build_" + p,
                "argv": [
                    "docker",
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--file",
                    str(inside(contexts / p, dockerfile)),
                    "--tag",
                    manifest["products"][p]["image"]["reference"],
                    str(contexts / p),
                ],
            }
        )
    return manifest, steps


def execute(bundle, contexts, steps):
    require(sys.platform == "linux", "linux_host_required")
    require(shutil.which("docker") is not None, "docker_executable_missing")
    require(
        not os.environ.get("DOCKER_HOST") and not os.environ.get("DOCKER_CONTEXT"),
        "custom_docker_endpoint_refused",
    )
    inspect = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        timeout=15,
        check=True,
    )
    require(
        inspect.stdout.strip().startswith(b"unix:///"), "local_docker_endpoint_required"
    )
    preflight(bundle, runtime=True)
    meta = read_json(bundle / "deployment.json")
    require(
        meta["project_name"].startswith("tianshu-qa-"), "isolated_qa_project_required"
    )
    require(
        all(v == "isolated_test" for v in meta["tls_provenance"].values()),
        "synthetic_tls_required",
    )
    platform = read_json(bundle / "config/platform/settings.json")
    gateway = read_json(bundle / "config/gateway/settings.json")
    require(
        not platform["web"]["dialogue_enabled"] and not platform["providers"],
        "synthetic_smoke_must_not_call_models",
    )
    require(
        len(gateway["targets"]) == 1
        and gateway["targets"][0]["base_url"] == gateway["platform_base_url"],
        "external_model_target_refused",
    )
    for p in PRODUCTS:
        for category in ("data", "logs"):
            require(
                not any(inside(bundle, category + "/" + p).iterdir()),
                "fresh_qa_storage_required",
            )
    compose = [
        "docker",
        "compose",
        "--project-directory",
        str(bundle),
        "-f",
        str(bundle / "compose.json"),
    ]
    existing = subprocess.run(
        [*compose, "ps", "--all", "--quiet"],
        capture_output=True,
        timeout=15,
        check=True,
    )
    require(not existing.stdout.strip(), "existing_project_refused")
    results = []

    def run(name, argv, seconds):
        start = time.monotonic()
        try:
            code = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=seconds,
            ).returncode
            status = "passed" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            code, status = None, "timeout"
        results.append(
            {
                "step": name,
                "status": status,
                "exit_code": code,
                "duration_seconds": round(time.monotonic() - start, 3),
            }
        )
        return status == "passed"

    if not run("compose_config", [*compose, "config", "--quiet"], 30):
        return results
    for step in steps:
        if not run(step["id"], step["argv"], 1800):
            return results
    # Single project checked absent above; storage was verified empty before any write.
    # Product-owned first-install commands only. These are never run by package init.
    try:
        for action, backup in (
            ("migrate-profiles", "first-install.pre-profiles.sqlite"),
            ("migrate-sources", "first-install.pre-sources.sqlite"),
        ):
            if not run(
                "memory_" + action,
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "memory",
                    "--config",
                    "/etc/tianshu/settings.json",
                    action,
                    "--backup",
                    "/srv/tianshu/" + backup,
                ],
                60,
            ):
                return results
        if run(
            "start_liveness",
            [
                *compose,
                "up",
                "--detach",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "120",
            ],
            180,
        ):
            # Actual non-root ownership/lock paths and entrypoints were exercised by up.
            # Readiness is intentionally a separate DEP-D concern; liveness says nothing
            # about dialogue/consumers, and this is never an existing-state upgrade rehearsal.
            run(
                "platform_preflight",
                [
                    *compose,
                    "exec",
                    "-T",
                    "platform",
                    "python",
                    "-m",
                    "services.platform",
                    "--settings",
                    "/etc/tianshu/settings.json",
                    "preflight",
                ],
                30,
            )
    finally:
        run("stop_qa_stack", [*compose, "down", "--timeout", "30"], 60)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--contexts", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--report-relative", default="reports/linux-smoke-report.json")
    args = parser.parse_args()
    try:
        bundle, contexts = args.bundle.absolute(), args.contexts.absolute()
        report_path = inside(bundle, args.report_relative)
        require(
            report_path.parent == bundle / "reports" and not report_path.exists(),
            "new_report_under_reports_required",
        )
        manifest, steps = plan(bundle, contexts)
        report = {
            "kind": "linux_packaging_smoke",
            "release_id": manifest["release_id"],
            "release_ready": False,
            "mode": "execute" if args.execute else "plan",
            "products": manifest["products"],
            "results": [],
            "claims": {"real_models": False, "nas": False, "release_acceptance": False},
        }
        if args.execute:
            report["results"] = execute(bundle, contexts, steps)
        else:
            planned = ["compose_config", *(s["id"] for s in steps)]
            planned += [
                "memory_migrate-profiles",
                "memory_migrate-sources",
                "start_liveness",
                "platform_preflight",
                "stop_qa_stack",
            ]
            report["steps"] = [{"id": name, "status": "not_run"} for name in planned]
        report["result"] = (
            "passed"
            if report["results"]
            and all(r["status"] == "passed" for r in report["results"])
            else "not_run"
            if not args.execute
            else "failed"
        )
        report_path.parent.mkdir(exist_ok=True)
        write_json(report_path, report)
        print(report["result"])
        return 0 if report["result"] in {"passed", "not_run"} else 1
    except Refused as error:
        print(str(error))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("linux_validation_input_or_runtime_failed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
