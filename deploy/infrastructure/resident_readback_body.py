"""Renderer input: complete checks after the coordinator has resumed owners."""

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


def load_original(readonly=True):
    parent = Path(EXPECTED["stage"])
    raw = (parent / "resident-update.py").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED["worker_sha256"], "original_worker_changed"
    tree = ast.parse(raw)
    assert isinstance(tree.body[-1], ast.Try), "original_worker_entry_changed"
    entry = tree.body.pop()  # Never call release() or its cleanup handler here.
    exec(compile(tree, str(parent / "resident-update.py"), "exec"), globals())
    if not readonly:
        return entry
    original_save = save

    def resume_save(path, value):
        path = Path(path)
        require(path.parent == parent, "resume_output_outside_stage")
        prefix = EXPECTED["receipt_prefix"] + "-"
        target = path if path.name.startswith(prefix) else parent / (prefix + path.name)
        original_save(target, value)

    def no_configuration_write(*args, **kwargs):
        raise RuntimeError("resume_readback_configuration_write_forbidden")

    globals()["save"] = resume_save
    globals()["atomic_bytes"] = no_configuration_write
    return entry


def readback():
    global GATEWAY_REISSUED
    require(not (S / "result.json").exists() and not (S / (EXPECTED["receipt_prefix"] + "-result.json")).exists(), "release_result_already_exists")
    require((S / "manual-resume-start.json").is_file(), "manual_resume_marker_missing")
    failure_raw = (S / "failure.json").read_bytes()
    failure = json.loads(failure_raw)
    require(failure["code"] == "non_target_container_changed" and failure["cleanup"] == "stopped_preserving_ledgers"
            and failure["automatic_database_restore"] is False, "unexpected_original_failure")
    backup = read(S / "cold-snapshot/receipt.json")
    manifest_raw = (S / "cold-snapshot/manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    require(backup["status"] == "offline_restore_verified" and backup["restored_disabled"] and backup["authority_unchanged"]
            and backup["sqlite_databases_verified"] == EXPECTED["sqlite_databases"], "verified_backup_changed")
    require(hashlib.sha256(manifest_raw).hexdigest() == backup["manifest_sha256"]
            and manifest["sqlite_databases_verified"] == EXPECTED["sqlite_databases"]
            and manifest["restore_status"] == "restored_disabled" and manifest["authority_unchanged"], "backup_manifest_changed")
    require(signatures(manifest["owners"]) == signatures(RELEASE["baseline"]["owners"]), "backup_owner_identity_changed")
    require((S / "cold-snapshot/backup").is_dir() and (S / "cold-snapshot/controls-backup").is_dir(), "backup_tree_missing")
    require(_retention.verification_copies_available(S / "cold-snapshot"), "offline_verification_proof_missing")
    require(RELEASE["guard_commit"] == EXPECTED["guard_commit"]
            and RELEASE["images"]["platform"]["image_id"] == EXPECTED["image_id"], "release_pin_changed")
    origin = read(S / "gateway-origin-result.json")
    resumed_origin = read(Path(EXPECTED["resume_origin_stage"]) / "gateway-origin-result.json")
    require(resumed_origin["gateway_id_before"] == origin["gateway_id_after"]
            and resumed_origin["gateway_id_after"] == EXPECTED["gateway_id"], "gateway_resume_identity_changed")
    for record in (origin, resumed_origin):
        require(record["same_existing_entry_owner_and_route"] and record["original_origin_preserved"]
                and record["new_permissions"] == record["new_service_credentials"] == 0, "gateway_resume_scope_changed")
    GATEWAY_REISSUED = origin["origin_reissued"]
    rows = snapshot.inspect_owners(D)
    expect_owners(rows, RELEASE["baseline"]["owners"], RELEASE["infrastructure"]["credential_binds"])
    identifiers = {row["service"]: row["id"] for row in RELEASE["baseline"]["owners"]}
    identifiers.update(platform=EXPECTED["platform_id"], gateway=EXPECTED["gateway_id"])
    require({row["service"]: row["id"] for row in rows} == identifiers, "resume_owner_identity_changed")
    require(all(row["running"] and row["restart"] == "unless-stopped" and not any(row[key] for key in ("paused", "restarting", "dead", "oom", "error")) for row in rows), "resumed_owner_not_healthy")
    cap = read(CAP)
    paths = paths_from(cap)
    settings_path = R / "config/platform/settings.json"
    expected_settings = merge_settings(read(S / "original-platform-settings.json"), RELEASE["infrastructure"]["settings"])
    require(read(settings_path) == expected_settings, "platform_settings_changed")
    expected_templates, expected_cap = patch_templates(read(S / "original-templates.json"), read(S / "original-capacity.json"), EXPECTED["image_id"], RELEASE["infrastructure"]["credential_binds"])
    expected_cap["state_dir"] = EXPECTED["state_dir"]
    require(cap == expected_cap, "candidate_capacity_changed")
    if GATEWAY_REISSUED:
        origin_env = read(R / "config/gateway/settings.json")["platform_origin_env"]
        recovery_env = str(R / "config/gateway/recovery-origin.env")
        for group in ("core", "platform"):
            service = expected_templates[group]["services"].get("gateway")
            if service is not None:
                service.setdefault("environment", {}).pop(origin_env, None)
                service["env_file"] = [name for name in service.get("env_file", []) if name != recovery_env] + [recovery_env]
    require(all(read(path) == expected_templates[name] for name, path in paths.items()), "candidate_template_changed")
    old_state = read(S / "original-capacity.json")["state_dir"]
    require(UNIT.read_bytes() == (S / "original-unit").read_bytes().replace(old_state.encode(), EXPECTED["state_dir"].encode()), "candidate_guard_unit_changed")
    for name, item in RELEASE["guard_files"].items():
        require(hashlib.sha256((TOOLING / name).read_bytes()).hexdigest() == item["sha256"], "installed_guard_source_changed")
    for name, expected in RELEASE["pins"].items():
        require(hashlib.sha256((Path(RELEASE["contracts"]) / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == expected, "existing_contract_changed")
    require(read(RELEASE["infrastructure"]["executor_host"]) == RELEASE["infrastructure"]["executor_config"], "executor_config_changed")
    require(guard_status()["status"] == "ready", "guard_not_ready_after_resume")
    verify_networks(rows, read(S / "original-networks.json"), read(S / "original-network-definitions.json"), read(S / "declared-network-addresses.json"))
    guard_path = TOOLING / "ops/resident_capacity/guard.py"
    spec = importlib.util.spec_from_file_location("resume_readback_guard", guard_path)
    guard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guard
    spec.loader.exec_module(guard)
    before = read(S / "non-targets-before.json")
    require(len(before) == EXPECTED["non_targets"], "original_non_target_count_changed")
    current = [asdict(row) for row in guard.Docker(D).snapshot() if row.id not in set(identifiers.values())]
    report = compare_inventory(before, current)
    require(all(row.get("project") not in snapshot.PROJECTS for row in report["added"]), "unexpected_new_tianshu_owner")
    save(S / "non-targets-after.json", report)
    require(report["preserved"], "non_target_container_changed")
    require(user_state_hashes(R) == read(S / "user-state-before.json"), "user_state_changed")
    settings_hashes = read(S / "settings-hashes.json")
    require(all(value == settings_hashes[name] for name, value in current_settings_hashes().items() if name != "platform"), "other_settings_changed")
    media_after = media_state()
    require(media_after["configuration_sha256"] == read(S / "media-before.json")["configuration_sha256"], "music_media_configuration_changed")
    save(S / "media-after.json", media_after)
    verify_infrastructure_runtime(EXPECTED["platform_id"], EXPECTED["memory_limit"])
    health = health_checks(rows)
    require((S / "failure.json").read_bytes() == failure_raw and not (S / "result.json").exists(), "original_release_evidence_changed")
    result = {"status": "deployed_after_verified_resume", "images": RELEASE["images"], "guard_commit": RELEASE["guard_commit"],
              "guard": "ready", "node_ids": ["nas-main"], "backup": str(S / "cold-snapshot"), "sqlite_backups": backup["sqlite_databases_verified"],
              "health": health, "user_state_preserved": True, "music_media_configuration_preserved": True,
              "non_target_containers_preserved": True, "concurrent_non_target_additions": len(report["added"]),
              "platform_memory_limit_preserved": True, "existing_settings_preserved_except_infrastructure_addition": True,
              "gateway_origin_reissued": GATEWAY_REISSUED, "resume_gateway_origin_reissued": resumed_origin["origin_reissued"],
              "database_restore": False, "readback_configuration_rewritten": False,
              "original_failure_sha256": hashlib.sha256(failure_raw).hexdigest(), "original_worker_sha256": EXPECTED["worker_sha256"],
              "real_qq_test_sent": False, "real_node_sampling_accepted": False, "dockge_sync_required": True}
    save(S / "result.json", result)  # Save adapter publishes only resume-result.json.
    return result


def main():
    load_original()
    try:
        print(json.dumps(readback(), sort_keys=True))
    except Exception as error:
        if not (S / (EXPECTED["receipt_prefix"] + "-readback-failure.json")).exists():
            save(S / "readback-failure.json", {"status": "requires_inspection", "code": str(error) if re.fullmatch("[a-z0-9_]+", str(error)) else "readback_failed",
                                              "starts_owners": False, "readback_configuration_rewritten": False, "database_restore": False})
        raise


if __name__ == "__main__":
    main()
