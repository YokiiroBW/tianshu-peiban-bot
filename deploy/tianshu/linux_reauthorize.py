"""Reissue an expired synthetic gateway origin through the Platform public CLI."""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bundle import verify_integrity
from linux_bootstrap import (
    CLI,
    begin_reauthorization,
    request_frame,
    store_reauthorization_receipt,
)
from manifest import Refused, read_json, require, write_json


OWNERS = {"platform", "companion", "memory", "gateway"}


def _run(argv, *, timeout=20, input=None):
    try:
        result = subprocess.run(
            argv, capture_output=True, timeout=timeout, input=input
        )
    except (OSError, subprocess.SubprocessError):
        raise Refused("reauthorization_runtime_unavailable") from None
    require(result.returncode == 0, "reauthorization_runtime_failed")
    return result.stdout


def _owned_platform(root, report):
    compose = read_json(root / "compose.json")
    project = compose["name"]
    expected = report.get("owned_containers")
    require(isinstance(expected, dict) and len(expected) == 4, "owned_core_set_required")
    require(
        {fact.get("owner") for fact in expected.values()} == OWNERS,
        "owned_core_set_required",
    )
    ids = list(expected)
    listed = _run(
        [
            "docker",
            "ps",
            "--no-trunc",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ]
    ).decode("ascii").split()
    require(set(listed) == set(ids), "owned_container_set_changed")
    values = json.loads(_run(["docker", "inspect", *ids]))
    require(len(values) == 4, "owned_core_set_required")
    actual_by_owner = {}
    for value in values:
        labels = value["Config"]["Labels"]
        fact = {
            "id": value["Id"],
            "owner": labels.get("com.docker.compose.service"),
            "image": value["Image"],
            "name": value["Name"],
            "nonce": labels.get("org.tianshu.execution"),
            "project": labels.get("com.docker.compose.project"),
            "directory": labels.get("com.docker.compose.project.working_dir"),
            "config_files": labels.get("com.docker.compose.project.config_files"),
        }
        expected_fact = expected.get(value["Id"])
        require(expected_fact == fact, "owned_container_identity_changed")
        owner = fact["owner"]
        require(owner in OWNERS and owner not in actual_by_owner, "owned_core_set_required")
        actual_by_owner[owner] = value
    require(set(actual_by_owner) == OWNERS, "owned_core_set_required")
    require(
        actual_by_owner["platform"]["State"]["Running"] is True,
        "owned_platform_not_running",
    )
    for owner in ("companion", "memory"):
        require(
            actual_by_owner[owner]["State"]["Running"] is False,
            "other_core_service_running",
        )
    gateway_state = actual_by_owner["gateway"]["State"]
    require(
        gateway_state["Status"] == "exited"
        and gateway_state["Running"] is False
        and gateway_state["ExitCode"] != 0
        and gateway_state["OOMKilled"] is False,
        "expired_gateway_failure_not_observed",
    )
    return actual_by_owner["platform"]["Id"]


def execute(root):
    root = root.absolute()
    verify_integrity(root)
    previous = read_json(root / "reports/bootstrap/result.json")
    require(
        previous.get("state") == "authority_initialized"
        and previous.get("issuer") == "product_platform_cli",
        "bootstrap_result_invalid",
    )
    now = datetime.now(timezone.utc)
    old_expiry = datetime.fromisoformat(previous["expires_at"].replace("Z", "+00:00"))
    require(old_expiry.tzinfo is not None and old_expiry <= now, "bootstrap_origin_not_expired")
    run_report = read_json(root / "reports/linux-executed.json")
    require(run_report.get("result") == "passed", "linux_validation_pass_required")
    platform_id = _owned_platform(root, run_report)
    marker = begin_reauthorization(root, now=now)

    try:
        raw_receipt = _run(
            [
                "docker",
                "exec",
                "-i",
                platform_id,
                "python",
                "-c",
                CLI,
                "issue",
            ],
            timeout=35,
            input=request_frame({"entry_id": "config-entry"}),
        )
        try:
            receipt = json.loads(raw_receipt)
        except (ValueError, UnicodeError):
            raise Refused("product_issue_receipt_invalid") from None
        result = store_reauthorization_receipt(root, receipt, now=now)
    except Exception:
        # The public issue may already have succeeded. A marker prevents replay after an
        # uncertain outcome; the product output is deliberately discarded.
        state = read_json(marker)
        state["state"] = "failed_or_unknown"
        write_json(marker, state)
        raise
    return {
        "status": "reauthorized",
        "platform_container_id": platform_id,
        "old_expires_at": result["old_expires_at"],
        "expires_at": result["expires_at"],
        "ref_in_report": False,
        "automatic_retry": False,
        "gateway_recreated": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.bundle.absolute()
        verify_integrity(root)
        if not args.execute:
            previous = read_json(root / "reports/bootstrap/result.json")
            print(
                json.dumps(
                    {
                        "status": "not_run",
                        "old_expires_at": previous.get("expires_at"),
                        "planned": [
                            "verify_exact_owned_four_container_set",
                            "platform_local_issue_new_origin",
                            "replace_private_gateway_origin",
                        ],
                        "starts_or_recreates_services": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        print(json.dumps(execute(root), sort_keys=True))
        return 0
    except Refused as error:
        print(json.dumps({"status": "refused", "code": str(error)}, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(json.dumps({"status": "failed", "code": "reauthorization_failed"}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
