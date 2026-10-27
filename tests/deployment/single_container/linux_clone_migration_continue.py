"""Explicit continuation of the single TS353 marker-representation refusal.

No action on import, no prepare/restore/original-PG-fingerprint rerun. The caller
supplies original successful clone receipt, the exact old tool and failure log.
No missing original before/after snapshot is reconstructed or fabricated.
"""

import argparse
import hashlib
import json
from pathlib import Path

import linux_clone as clone


PRIOR_TOOLS = {
    "4b09df8c96147e9a560f8d200afd0322f6447df9e48974dfda31ee2346d6d863",
    "d25776c79059062efc547dc80aaab7e665e518b5cb663d61b811c0af3c361c77",
}


def prior_evidence(prior_tool, prior_failure):
    # Both exact prior sources compare(before,after), append success, then make
    # the byte assertion. This evidence proves that control flow, not the lost
    # original before/after values or an independently persisted first proof.
    normalized = Path(prior_tool).read_bytes().replace(b"\r\n", b"\n")
    source_hash = hashlib.sha256(normalized).hexdigest()
    clone.require(source_hash in PRIOR_TOOLS, "exact_prior_migration_tool_required")
    failure_raw = Path(prior_failure).read_bytes()
    clone.require(len(failure_raw) < 65536, "bounded_original_failure_required")
    failure = json.loads(failure_raw.decode().strip().splitlines()[-1])
    clone.require(
        failure.get("status") == "refused"
        and failure.get("reason") == "migration_changed_platform_marker"
        and failure.get("exception_type") == "CloneError",
        "specific_marker_failure_required",
    )
    return {
        "prior_tool_normalized_sha256": source_hash,
        "prior_failure_sha256": hashlib.sha256(failure_raw).hexdigest(),
        "prior_first_comparison_reached_success": True,
        "prior_first_comparison_evidence": "pinned_control_flow_and_observed_refusal",
        "prior_original_snapshots_available": False,
        "prior_original_snapshots_reconstructed": False,
        "scope": "current_readonly_baseline_then_two_original_idempotent_passes",
    }


def continue_migration(
    stage,
    binding,
    state,
    *,
    snapshot,
    recovery_bundle,
    settings,
    readonly_probe,
    observation_sha256,
    prior_tool,
    prior_failure,
    seconds=1800,
):
    evidence = prior_evidence(prior_tool, prior_failure)
    ctx = clone.context(stage, binding, state, snapshot, recovery_bundle, seconds)
    receipt = clone.verified_clone_receipt(ctx)
    platform, expected = clone.expected_platform_marker(ctx, receipt)
    marker = clone.relative(ctx.root, platform["binding"])
    clone.marker_proof(marker, expected, marker.read_bytes())
    restored_bytes = ctx.backend.canonical(expected) + b"\n"
    evidence["current_marker_differs_from_restore_representation"] = (
        marker.read_bytes() != restored_bytes
    )
    evidence["expected_restore_representation_sha256"] = hashlib.sha256(
        restored_bytes
    ).hexdigest()
    evidence["completed_original_clone_receipt_sha256"] = clone.sha(
        ctx.stage / "clone/receipt.json"
    )
    # Intentionally no verify_clone/verify_pg: original DDL has already changed
    # legitimately. The prior restore receipt remains the pre-DDL PG proof.
    result = clone.run_platform_migration(
        ctx,
        settings=settings,
        readonly_probe=readonly_probe,
        observation_sha256=observation_sha256,
        output_name="platform-migration-continuation",
        continuation_evidence=evidence,
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "stage",
        "binding",
        "state",
        "snapshot",
        "recovery-bundle",
        "settings",
        "readonly-probe",
        "prior-tool",
        "prior-failure",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--observation-sha256", required=True)
    parser.add_argument("--seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        result = continue_migration(
            args.stage,
            args.binding,
            args.state,
            snapshot=args.snapshot,
            recovery_bundle=args.recovery_bundle,
            settings=args.settings,
            readonly_probe=args.readonly_probe,
            observation_sha256=args.observation_sha256,
            prior_tool=args.prior_tool,
            prior_failure=args.prior_failure,
            seconds=args.seconds,
        )
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, clone.CloneError)
            else "clone_continuation_not_verified"
        )
        print(json.dumps({"status": "refused", "reason": reason}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
