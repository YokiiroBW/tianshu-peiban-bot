"""Run existing isolated host/product checks; never SSH, install or cut over services.

The caller provides installed product wheels, frozen test fixtures and optional
verification-only pytest dependencies. No production configuration is consumed.
Run the candidate image with --network none and private temporary mounts.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


HOST_TESTS = (
    "test_exchange.py",
    "test_threaded_exchange.py",
    "test_business_host.py",
    "test_host_startup_failure.py",
    "test_runtime_ownership.py",
    "test_edge.py",
    "test_host_settings.py",
    "test_distribution.py",
)
SCHEDULE_TESTS = (
    "test_plugins.PluginTests.test_schedule_coalesces_missed_runs_and_stop_prevents_new_ticks",
    "test_plugins.PluginTests.test_failed_schedule_keeps_due_and_does_not_starve_later_schedule",
    "test_plugins.PluginTests.test_schedule_receipt_save_failure_reuses_accepted_intent_on_retry",
    "test_plugins.PluginTests.test_quiesce_drains_accepted_action_and_child_without_closing_worker",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "host-source",
        "platform-fixtures",
        "contracts",
        "memory-contracts",
        "diagnostics-contracts",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--memory-fixture", type=Path)
    parser.add_argument("--companion-fixtures", type=Path)
    parser.add_argument(
        "--verification-site", type=Path, help="Pure Python test-only vendor directory"
    )
    args = parser.parse_args()
    root = args.host_source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tests = root / "tests/deployment/single_container"
    sys.path.insert(0, str(root))
    from deploy.single_container.host_settings import module_environment

    environment = module_environment({"env_file": [], "env": {}}, environ=os.environ)
    environment.update(
        TS012_CONTRACT_DIR=str(args.contracts.resolve()),
        TIANSHU_CONTRACTS=str(args.contracts.resolve()),
        TIANSHU_UNIFIED_RUNTIME_REPO=str(root),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    # Runtime wheels stay in site-packages; only explicit root/testing code is added.
    paths = [str(root), str(tests), str(args.platform_fixtures.resolve())]
    if args.verification_site:
        paths.append(str(args.verification_site.resolve()))
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    native = [sys.executable, "-u", str(tests / "native_host_smoke.py")]
    for name in (
        "host-source",
        "platform-fixtures",
        "contracts",
        "memory-contracts",
        "diagnostics-contracts",
    ):
        native += ["--" + name, str(getattr(args, name.replace("-", "_")).resolve())]
    if args.memory_fixture:
        native += ["--memory-fixture", str(args.memory_fixture.resolve())]
    native += ["--http-web-access", "--output", str(output / "native")]
    commands = [
        ("native-six-module-http-tls", native, environment),
        (
            "owner-callback-cancel-drain",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *[str(tests / name) for name in HOST_TESTS],
                str(args.platform_fixtures / "test_role_runtime_concurrency.py"),
            ],
            environment,
        ),
        (
            "plugin-schedule-receipts",
            [sys.executable, "-m", "unittest", "-v", *SCHEDULE_TESTS],
            environment,
        ),
    ]
    if args.companion_fixtures:
        companion_env = dict(environment)
        companion_env["PYTHONPATH"] = os.pathsep.join(
            [str(args.companion_fixtures.resolve()), *paths]
        )
        commands.append(
            (
                "companion-source-memory-receipts",
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "-v",
                    "test_source_bridge.SourceBridgeTests",
                ],
                companion_env,
            )
        )
    receipt = {
        "platform": sys.platform,
        "python": sys.version,
        "status": "running",
        "production_operations": 0,
        "checks": [],
        "companion_source_chain": "planned"
        if args.companion_fixtures
        else "not_selected",
        "scope": "original host owner concurrency, plugin schedule receipts and optional original synthetic source chain; no real QQ/model/NAS",
    }
    result_file = output / "acceptance.json"
    for name, command, child_env in commands:
        started = time.monotonic()
        log = output / (name + ".log")
        with log.open("wb") as stream:
            result = subprocess.run(
                command,
                cwd=root,
                env=child_env,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        receipt["checks"].append(
            {
                "name": name,
                "returncode": result.returncode,
                "seconds": round(time.monotonic() - started, 3),
                "log": log.name,
            }
        )
        if name == "companion-source-memory-receipts":
            receipt["companion_source_chain"] = (
                "failed" if result.returncode else "passed"
            )
        receipt["status"] = "failed" if result.returncode else "running"
        result_file.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        if result.returncode:
            print(
                json.dumps(
                    {"status": "failed", "check": name, "receipt": str(result_file)}
                )
            )
            return result.returncode
    receipt["status"] = "isolated_checks_passed"
    result_file.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "receipt": str(result_file)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
