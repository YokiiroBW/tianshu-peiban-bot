"""Read DEP-D dep-d/1 without upgrading its static/adapter claims into execution proof."""

import json

from manifest import PRODUCTS, digest, load_manifest, read_json, require

CASES = {
    "runtime_binding",
    "configuration_loading",
    "health_and_auth",
    "web_login_csrf",
    "dialogue_model_reply",
    "memory_candidate",
    "memory_finalized",
    "chat_archive",
    "memory_backlog_boundary",
    "unknown_no_resend",
    "restart_recovery",
    "source_revocation",
    "model_revocation",
    "timeout_cancel",
    "log_causality",
    "failure_truthfulness",
    "abnormal_readiness",
    "browser_rendering",
    "real_model_quality",
    "observation_24h",
}


def inspect_acceptance(report_path, subject_path):
    report = read_json(report_path)
    manifest = load_manifest(subject_path)
    require(
        report.get("report_version") == "dep-d/1"
        and report.get("kind") == "release_acceptance",
        "unsupported_acceptance_report",
    )
    payload = {k: v for k, v in report.items() if k != "content_sha256"}
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    require(
        digest(content) == report.get("content_sha256"),
        "acceptance_content_hash_mismatch",
    )
    binding = report["binding"]
    require(
        binding["manifest_sha256"] == digest(subject_path.read_bytes()),
        "acceptance_subject_mismatch",
    )
    products = {
        p: {
            "repo": manifest["products"][p]["source"]["repo"],
            "commit": manifest["products"][p]["source"]["commit"],
            "image": manifest["products"][p]["image"]["reference"],
            "digest": manifest["products"][p]["image"]["digest"],
        }
        for p in PRODUCTS
    }
    contracts = {
        c["id"] + "/" + f["path"]: f["sha256"]
        for c in manifest["contracts"]
        for f in c["files"]
    }
    require(
        binding["products"] == products
        and binding["contracts"] == contracts
        and binding["release_id"] == manifest["release_id"]
        and binding["release_status"] == manifest["status"]
        and binding["features"] == manifest["features"]
        and binding["release_blockers"] == manifest["blockers"],
        "acceptance_binding_mismatch",
    )
    rows = report["results"]
    require(
        isinstance(rows, list)
        and {r["case"] for r in rows} == CASES
        and len(rows) == len(CASES),
        "acceptance_cases_missing_or_duplicate",
    )
    require(
        all(
            r["status"] in {"pass", "fail", "dependency_missing", "skipped", "not_run"}
            for r in rows
        ),
        "acceptance_case_status_invalid",
    )
    counts = {
        status: sum(r["status"] == status for r in rows)
        for status in ("pass", "fail", "dependency_missing", "skipped", "not_run")
    }
    blockers = []
    if report["mode"] != "container" or report["runtime_kind"] != "product":
        blockers.append("not_product_container_execution")
    if counts["pass"] != len(CASES):
        blockers.append("acceptance_incomplete")
    if binding.get("identity_basis") == "static_input_only":
        blockers.append("independent_runtime_identity_review_required")
    if any(p["digest"] is None for p in products.values()):
        blockers.append("application_digests_unverified")
    # The report is not a signature. Even a complete future report requires independent
    # coordinator evidence for runtime identity and the other four distinct release gates.
    blockers.append("coordinator_and_other_release_evidence_required")
    return {
        "status": "acceptance_integrity_valid",
        "report_version": "dep-d/1",
        "report_file_sha256": digest(report_path.read_bytes()),
        "subject_manifest_sha256": digest(subject_path.read_bytes()),
        "counts": counts,
        "verdict": report["verdict"],
        "release_ready": False,
        "blockers": blockers,
    }
