"""Run with python -B -m ops.recovery. Mutations always require --execute."""

import argparse
import json
import signal
import sqlite3

from .engine import Control, Recovery, create_sandbox
from .safety import RecoveryError


def parser():
    result = argparse.ArgumentParser(
        description="Synthetic-only offline recovery; no NAS/network/service control. Default: plan."
    )
    result.add_argument("--root", required=True, help="Absolute local sandbox path")
    result.add_argument("--scope-id", required=True, help="Explicit sandbox UUID")
    result.add_argument(
        "--max-bytes",
        type=int,
        default=1024**3,
        help="Total snapshot budget, default 1 GiB, maximum 64 GiB",
    )
    result.add_argument(
        "--max-files", type=int, default=10000, help="Total file budget, maximum 100000"
    )
    result.add_argument(
        "--execute", action="store_true", help="Perform the named synthetic operation"
    )
    result.add_argument(
        "--dry-run", action="store_true", help="Explicit read-only plan (default)"
    )
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "init-sandbox", help="Create a new empty recovery sandbox, not product state"
    )
    backup = sub.add_parser("backup")
    backup.add_argument("--deployment", required=True)
    backup.add_argument("--backup", required=True)
    verify = sub.add_parser("verify-backup")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--snapshot-sha256", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument(
        "--snapshot-sha256",
        required=True,
        help="Receipt saved separately when backing up",
    )
    restore.add_argument("--target", required=True)
    restore.add_argument(
        "--authority",
        required=True,
        help="Current original offline deployment, never backup payload",
    )
    restore.add_argument("--authority-id", required=True)
    restore.add_argument("--replace-target", action="store_true")
    restore.add_argument("--expected-target-id")
    restored = sub.add_parser("verify-restored")
    restored.add_argument("--target", required=True)
    restored.add_argument("--authority", required=True)
    restored.add_argument("--authority-id", required=True)
    for name in ("prepare-update", "rollback-code"):
        action = sub.add_parser(name)
        action.add_argument("--deployment", required=True)
        action.add_argument("--candidate-manifest", required=True)
        action.add_argument("--compatibility", required=True)
        action.add_argument("--update-id", required=True)
        if name == "prepare-update":
            action.add_argument("--backup", required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    cancelled = False

    def cancel(*_):
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGINT, cancel)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, cancel)
    try:
        if args.execute and args.dry_run:
            raise RecoveryError("conflicting_execution_flags")
        if args.command == "init-sandbox":
            output = create_sandbox(args.root, args.scope_id, execute=args.execute)
        else:
            recovery = Recovery(
                args.root,
                args.scope_id,
                control=Control(cancel=lambda: cancelled),
                max_bytes=args.max_bytes,
                max_files=args.max_files,
            )
            if args.command == "backup":
                output = recovery.backup(
                    args.deployment, args.backup, execute=args.execute
                )
            elif args.command == "verify-backup":
                output = recovery.verify(args.backup, args.snapshot_sha256)
            elif args.command == "restore":
                output = recovery.restore(
                    args.backup,
                    args.snapshot_sha256,
                    args.target,
                    args.authority,
                    args.authority_id,
                    execute=args.execute,
                    replace_target=args.replace_target,
                    expected_target_id=args.expected_target_id,
                )
            elif args.command == "verify-restored":
                output = recovery.verify_restored(
                    args.target, args.authority, args.authority_id
                )
            else:
                output = recovery.select_code(
                    args.deployment,
                    args.candidate_manifest,
                    args.compatibility,
                    args.update_id,
                    getattr(args, "backup", None),
                    rollback=args.command == "rollback-code",
                    execute=args.execute,
                )
        print(json.dumps(output, sort_keys=True))
        return 0
    except RecoveryError as error:
        print(
            json.dumps(
                {"status": "rejected", "reason": str(error), "activation": "disabled"}
            )
        )
        return 130 if str(error) == "cancelled" else 2
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, OverflowError):
        # Never disclose database contents, SQL, credentials, paths or exception messages.
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "reason": "invalid_or_unavailable_input",
                    "activation": "disabled",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
