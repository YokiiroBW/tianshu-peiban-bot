"""DEP-A public CLI. All output is bounded metadata; never print exception/config values."""

import argparse
import json
import ssl
import subprocess
from pathlib import Path

from acceptance import inspect_acceptance
from bundle import export_sources, initialize, preflight, runtime_available
from manifest import Refused, load_manifest, no_links


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="TianShu four-core packaging and offline preflight"
    )
    sub = parser.add_subparsers(dest="action", required=True)
    validate = sub.add_parser("validate-manifest")
    validate.add_argument("--manifest", required=True, type=Path)
    init = sub.add_parser(
        "init",
        help="Create a NEW local bundle; never start containers or initialize DBs",
    )
    init.add_argument("--manifest", required=True, type=Path)
    init.add_argument("--inputs", required=True, type=Path)
    init.add_argument("--contracts-root", required=True, type=Path)
    init.add_argument("--target", required=True, type=Path)
    check = sub.add_parser("preflight")
    check.add_argument("--bundle", required=True, type=Path)
    check.add_argument(
        "--release", action="store_true", help="Require release-bound Linux evidence"
    )
    check.add_argument("--check-permissions", action="store_true")
    export = sub.add_parser(
        "export-sources", help="Export fixed git commits, never working-tree files"
    )
    export.add_argument("--manifest", required=True, type=Path)
    export.add_argument("--repos", required=True, type=Path)
    export.add_argument("--contracts-root", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    sub.add_parser("runtime-check")
    acceptance = sub.add_parser(
        "inspect-acceptance",
        help="Check DEP-D report against its immutable tested manifest",
    )
    acceptance.add_argument("--report", required=True, type=Path)
    acceptance.add_argument("--subject-manifest", required=True, type=Path)
    observation = sub.add_parser(
        "configure-observability", help="Configure pinned log package; start nothing"
    )
    observation.add_argument("--bundle", required=True, type=Path)
    observation.add_argument("--settings", required=True, type=Path)
    observation.add_argument("--root-repository", required=True, type=Path)
    observation.add_argument(
        "--projects", type=Path, help="Fixed Git objects for updated vocabulary"
    )
    reconcile = sub.add_parser(
        "reconcile-logs", help="Explicit synthetic loopback log reconciliation"
    )
    for name in ("bundle", "generated", "ca", "token-file", "report"):
        reconcile.add_argument("--" + name, required=True, type=Path)
    reconcile.add_argument("--url", required=True)
    reconcile.add_argument("--start-ns", required=True, type=int)
    reconcile.add_argument("--end-ns", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.action == "validate-manifest":
            manifest = load_manifest(args.manifest)
            report = {
                "status": "manifest_valid",
                "release_id": manifest["release_id"],
                "release_ready": False,
                "blockers": manifest["blockers"],
            }
        elif args.action == "init":
            report = initialize(
                args.manifest,
                no_links(args.inputs.absolute()),
                args.contracts_root,
                args.target,
            )
        elif args.action == "preflight":
            report = preflight(args.bundle, args.release, args.check_permissions)
        elif args.action == "export-sources":
            report = export_sources(
                args.manifest, args.repos, args.contracts_root, args.output
            )
        elif args.action == "inspect-acceptance":
            report = inspect_acceptance(args.report, args.subject_manifest)
        elif args.action == "configure-observability":
            from observability_release import configure

            report = configure(
                args.bundle, args.settings, args.root_repository, args.projects
            )
        elif args.action == "reconcile-logs":
            from observability_release import reconcile

            report = reconcile(
                args.bundle,
                args.generated,
                args.url,
                args.ca,
                args.token_file,
                args.start_ns,
                args.end_ns,
                args.report,
            )
        else:
            report = runtime_available()
        print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
        if report["status"] == "log_reconciliation_failed":
            return 1
        return 0 if report["status"] != "not_available" else 3
    except Refused as error:
        report = {"status": "refused", "code": str(error), "release_ready": False}
    except ssl.SSLError:
        report = {
            "status": "refused",
            "code": "tls_verification_failed",
            "release_ready": False,
        }
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        report = {
            "status": "refused",
            "code": "invalid_or_unavailable_input",
            "release_ready": False,
        }
    print(json.dumps(report, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
