"""Explicit local Linux image/liveness smoke, never a release or NAS acceptance claim.

Default is plan. --execute requires a fresh tianshu-qa-* bundle with test TLS, no provider,
no dialogue, empty state/log mounts, and a local Linux Docker daemon. Logs are not collected:
product build/runtime diagnostics may contain operator input. Only exit codes are reported.
"""

import argparse
import contextlib
import subprocess
import sys
from pathlib import Path

from bundle import TOOLS, verify_integrity
from manifest import (
    PRODUCTS,
    HERE,
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
    for name in TOOLS:
        require(
            (bundle / "tools" / name).read_bytes() == (HERE / name).read_bytes(),
            "executor_bundle_version_mismatch",
        )
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--contexts", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--scenario", choices=("liveness", "synthetic-dialogue"), default="liveness"
    )
    parser.add_argument("--report-relative", default="reports/linux-smoke-report.json")
    args = parser.parse_args(argv)
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
            "claims": {
                "real_models": False,
                "nas_acceptance": False,
                "release_acceptance": False,
            },
            "scenario": args.scenario,
            "source_inventory_sha256": digest(
                (contexts / "source-inventory.json").read_bytes()
            ),
            "dimensions": {
                name: "not_run"
                for name in (
                    "linux_images",
                    "linux_permissions",
                    "installed_dependencies",
                    "runtime_uid_gid",
                    "authenticated_readiness",
                    "synthetic_dialogue",
                    "core_stop_observed",
                    "log_chain",
                    "normal_restore",
                    "nas_acceptance",
                    "real_models",
                    "browser_rendering",
                )
            },
            "unexecuted_dimensions": [
                "nas_acceptance",
                "log_chain",
                "normal_restore",
                "real_models",
                "browser_rendering",
            ],
        }
        if args.execute:
            from linux_runtime import execute

            report_path.parent.mkdir(exist_ok=True)
            try:
                execute(
                    bundle,
                    contexts,
                    steps,
                    report,
                    report_path,
                    dialogue=args.scenario == "synthetic-dialogue",
                )
            except (
                Refused,
                OSError,
                ValueError,
                KeyError,
                subprocess.SubprocessError,
            ) as error:
                report["error"] = (
                    str(error) if isinstance(error, Refused) else "linux_runtime_failed"
                )
                report["result"] = "failed"
                report["unexecuted_dimensions"] = [
                    k for k, v in report["dimensions"].items() if v == "not_run"
                ]
                if not report_path.exists() and str(error) != "runtime_owner_busy":
                    write_json(report_path, report)
                print("failed")
                return 1
        else:
            require(
                not (bundle / "reports/execution-attempt.json").exists(),
                "execution_already_attempted",
            )
            planned = ["compose_config", *(s["id"] for s in steps)]
            planned += [
                "inspect_images_and_dependencies",
                "platform_issue_private_origin",
                "authenticated_ready",
                "memory_migrate-profiles",
                "memory_migrate-sources",
                "start_liveness",
                "platform_preflight",
                "sigterm_owned_containers_preserve_evidence",
            ]
            if args.scenario == "synthetic-dialogue":
                planned += [
                    "platform_publish_synthetic",
                    "synthetic_model_and_dialogue",
                ]
            report["steps"] = [{"id": name, "status": "not_run"} for name in planned]
            from runtime_identity import lifecycle_lease, save

            with (
                lifecycle_lease(bundle)
                if sys.platform == "linux"
                else contextlib.nullcontext()
            ):
                require(
                    not (bundle / "reports/execution-attempt.json").exists(),
                    "execution_already_attempted",
                )
                save(bundle)
                report["runtime_identity_sha256"] = digest(
                    (bundle / "reports/runtime-identity.json").read_bytes()
                )
                report["result"] = "not_run"
                report["unexecuted_dimensions"] = list(report["dimensions"])
                write_json(report_path, report)
            print("not_run")
            return 0
        report["result"] = (
            "passed"
            if report["results"]
            and all(r["status"] == "passed" for r in report["results"])
            else "not_run"
            if not args.execute
            else "failed"
        )
        report["unexecuted_dimensions"] = [
            k for k, v in report["dimensions"].items() if v == "not_run"
        ]
        report_path.parent.mkdir(exist_ok=True)
        if not args.execute:
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
