"""Explicit installed-platform public CLI probe; not a Docker/Linux acceptance test.

Run in a private venv with platform installed from the manifest's fixed Git archive.
Requires the coordinator's original-byte contracts, never Git's historical copy.
"""

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import test_packaging as packaging
from bundle import initialize
from linux_bootstrap import first_install_container, prepare
from manifest import (
    check_contracts,
    digest,
    load_manifest,
    read_json,
    require,
    write_json,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--platform-source", required=True, type=Path)
    args = parser.parse_args()
    args.contracts = args.contracts.absolute()
    require(not args.output.exists(), "fresh_report_required")
    manifest = load_manifest(packaging.PACKAGE / "release-manifest.example.json")
    check_contracts(manifest, args.contracts)
    source = args.platform_source.absolute()
    inventory = read_json(source.parent / "source-inventory.json")["products"][
        "platform"
    ]
    require(
        inventory["source"] == manifest["products"]["platform"]["source"],
        "installed_source_binding_changed",
    )
    installed = Path(importlib.util.find_spec("services.platform").origin).parent
    installed_hashes = {}
    for name, expected in inventory["files"].items():
        if name.startswith("services/platform/") and name.endswith(".py"):
            require(
                digest((source / name).read_bytes()) == expected,
                "source_snapshot_changed",
            )
            require(
                digest(
                    (installed / name.removeprefix("services/platform/")).read_bytes()
                )
                == expected,
                "installed_source_mismatch",
            )
            installed_hashes[name] = expected
    require(bool(installed_hashes), "installed_source_missing")
    results = []
    for dialogue in (False, True):
        fixture = packaging.PackagingTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            write_json(fixture.manifestpath, manifest)
            initialize(
                fixture.manifestpath,
                fixture.sitepath,
                args.contracts,
                fixture.output,
                fixture.env,
            )
            root = fixture.output
            (root / "reports").mkdir()
            publication, _ = prepare(root, dialogue)
            config = read_json(root / "config/platform/settings.json")
            config["database_path"] = str(root / "data/platform/platform.sqlite")
            config["contract_directory"] = str(args.contracts / "text-dialogue/v1")
            config["diagnostics"]["contract_directory"] = str(
                args.contracts / "diagnostics/v1"
            )
            config["diagnostics"]["log_directory"] = str(root / "logs/platform")
            config["web"]["static_directory"] = str(root / "local-web")
            (root / "local-web/assets").mkdir(parents=True)
            (root / "local-web/index.html").write_text(
                '<!doctype html><script src="/assets/test.js"></script>'
            )
            (root / "local-web/assets/test.js").write_text("/* synthetic */")
            config["core"]["ca_file"] = str(root / "config/platform/tls/ca.pem")
            config["tls"]["certificate_file"] = str(
                root / "config/platform/tls/server.pem"
            )
            config["tls"]["private_key_file"] = str(
                root / "config/platform/tls/server.key"
            )
            settings = root / "local-settings.json"
            write_json(settings, config)
            environment = dict(os.environ)
            for line in (root / "private/platform.env").read_text().splitlines():
                key, value = line.split("=", 1)
                environment[key] = value[1:-1]
            calls = []

            def runner(name, argv, seconds, **kwargs):
                action = argv[-1]
                path = root / (action + ".json")
                path.write_bytes(kwargs["input"])
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-m",
                        "services.platform",
                        "--settings",
                        str(settings),
                        "local",
                        "--credential-env",
                        "TS_ADMIN_TOKEN",
                        action,
                        "--input",
                        str(path),
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    timeout=seconds,
                )
                require(
                    result.returncode == 0, "installed_platform_cli_failed_" + action
                )
                calls.append(action)
                return result.stdout

            receipt = first_install_container(root, [], runner, publication)
            require(
                receipt["assertion_ref"] in (root / "private/gateway.env").read_text(),
                "private_injection_missing",
            )
            require(
                Path(config["database_path"]).is_file(),
                "public_cli_database_not_initialized",
            )
            results.append(
                dict(
                    scenario="synthetic-dialogue" if dialogue else "liveness",
                    public_cli_actions=calls,
                    status="passed",
                    private_ref_injection=True,
                )
            )
        finally:
            fixture.tearDown()
    write_json(
        args.output,
        dict(
            kind="dep-g-installed-platform-bootstrap",
            result="passed",
            source=manifest["products"]["platform"]["source"],
            installed_source_sha256=installed_hashes,
            contract_manifests={
                c["id"]: c["manifest_sha256"] for c in manifest["contracts"]
            },
            results=results,
            linux=False,
            docker=False,
            nas_acceptance=False,
            release_ready=False,
        ),
    )
    print(
        "passed: installed platform CLI liveness + synthetic publication/issue; not Docker"
    )


if __name__ == "__main__":
    main()
