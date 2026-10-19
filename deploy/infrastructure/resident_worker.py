"""Infrastructure-specific body assembled after the existing resident helpers.

This file is a renderer input, not an independently executable deployment tool.
The coordinator stages and launches the generated worker using the existing
resident release launcher payloads.
"""

import base64
import importlib.util
from dataclasses import asdict


GATEWAY_REISSUED = False
TRANSACTION_STARTED = False
FAILURE_BASELINE_ROWS = RELEASE["baseline"]["owners"]
FAILURE_EXPECTED_ROWS = FAILURE_BASELINE_ROWS
FAILURE_CREDENTIAL_BINDS = RELEASE["infrastructure"]["credential_binds"] + RELEASE.get("media_binds", [])
FAILURE_EXPECT_SAMPLING = True


def current_settings_hashes():
    return {name: hashlib.sha256((R / "config" / name / "settings.json").read_bytes()).hexdigest()
            for name in CORE_NAMES}


def metadata_inventory(guard_module, owner_ids):
    return normalize_inventory([asdict(row) for row in guard_module.Docker(D).snapshot() if row.id not in owner_ids])


def load_candidate_guard():
    raw = base64.b64decode(RELEASE["guard_files"]["ops/resident_capacity/guard.py"]["base64"])
    path = S / "candidate-guard.py"
    atomic_bytes(path, raw, mode=0o600, uid=0, gid=0)
    spec = importlib.util.spec_from_file_location("resident_candidate_guard", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspect_inputs():
    rows = snapshot.inspect_owners(D)
    require(len(rows) == 10 and {row["service"] for row in rows} == snapshot.OWNERS, "owner_set_changed")
    require(signatures(rows) == signatures(RELEASE["baseline"]["owners"]), "production_baseline_changed")
    require(all(row["running"] and row["restart"] == "unless-stopped" for row in rows), "owners_not_running")
    require(hashlib.sha256((TOOLING / "ops/resident_capacity/guard.py").read_bytes()).hexdigest() == RELEASE["baseline"]["guard_sha256"], "guard_code_changed")
    require(hashlib.sha256(CAP.read_bytes()).hexdigest() == RELEASE["baseline"]["capacity_sha256"], "capacity_changed")
    cap = read(CAP)
    paths = paths_from(cap)
    require(all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == RELEASE["baseline"]["compose_hashes"][name] for name, path in paths.items()), "compose_changed")
    require(cap.get("external_media_binds", []) == RELEASE["baseline"]["external_media_binds"], "external_media_binds_changed")
    require(current_settings_hashes() == RELEASE["baseline"]["settings_sha256"], "settings_changed_since_baseline")
    require(guard_status()["status"] == "ready", "guard_not_ready")
    image = json.loads(command([D, "image", "inspect", RELEASE["images"]["platform"]["image_id"]]).stdout)[0]
    require(image["Config"]["Labels"]["org.opencontainers.image.revision"] == RELEASE["images"]["platform"]["revision"], "candidate_image_changed")
    for name, expected in RELEASE["pins"].items():
        require(hashlib.sha256((Path(RELEASE["contracts"]) / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == expected, "existing_contract_changed")
    return rows, cap, paths


def expect_owners(rows, originals, credential_binds, *, expect_sampling=True):
    old = {row["service"]: row for row in originals}
    require(len(rows) == 10 and {row["service"] for row in rows} == set(old), "owner_set_changed")
    for row in rows:
        prior = old[row["service"]]
        require(row["project"] == prior["project"], "owner_project_changed")
        if row["service"] == "platform":
            require(row["image_id"] == RELEASE["images"]["platform"]["image_id"], "platform_image_changed")
            actual_binds = sorted((m["Source"], m["Destination"], m["RW"]) for m in row["mounts"] if m["Type"] == "bind")
            expected = sorted([(m["Source"], m["Destination"], m["RW"]) for m in prior["mounts"] if m["Type"] == "bind"]
                              + [(m["source"], m["target"], not m["read_only"]) for m in credential_binds])
            require(actual_binds == expected, "platform_bind_changed")
            tmpfs = [m for m in row["mounts"] if m["Type"] == "tmpfs"]
            expected_targets = {m["Destination"] for m in prior["mounts"] if m["Type"] == "tmpfs"}
            actual_targets = {m["Destination"] for m in tmpfs}
            # Docker may report legacy --tmpfs only in HostConfig.Tmpfs.
            # Verify the requested state against HostConfig and the mounted
            # filesystem below instead of assuming it appears in .Mounts.
            require(actual_targets - {SAMPLE_STATE} == expected_targets - {SAMPLE_STATE}, "platform_tmpfs_changed")
            if not expect_sampling:
                require(SAMPLE_STATE not in actual_targets, "platform_sampling_mount_retained")
            require(all(m["Type"] in {"bind", "tmpfs"} for m in row["mounts"]), "platform_mount_kind_changed")
        else:
            require(row["image_id"] == prior["image_id"], "unrelated_owner_image_changed")
            if not (row["service"] == "gateway" and GATEWAY_REISSUED):
                require(row["id"] == prior["id"], "unrelated_owner_recreated")
            require(sorted(json.dumps(m, sort_keys=True) for m in row["mounts"]) == sorted(json.dumps(m, sort_keys=True) for m in prior["mounts"]), "unrelated_owner_mount_changed")


def check_private_inputs():
    if RELEASE.get("control"):
        check_control_install(RELEASE["control"])
    for bind in RELEASE.get("media_binds", []):
        path = Path(bind["source"])
        require(path.parent.is_dir() and not any(p.is_symlink() for p in (path, *path.parents)), "media_directory_parent_invalid")
        require(not path.exists() or path.is_dir(), "media_directory_invalid")
    if RELEASE.get("preserve_infrastructure"):
        return
    # The coordinator stages just these two dedicated files in the private
    # release directory. No SSH home or credential content enters this script.
    for bind in RELEASE["infrastructure"]["credential_binds"]:
        source = S / "credentials" / Path(bind["source"]).name
        info = source.stat()
        require(source.is_file() and not source.is_symlink() and info.st_uid == 0 and info.st_mode & 0o077 == 0, "staged_credential_mode_invalid")
        target = Path(bind["source"])
        require(not target.exists(), "dedicated_credential_already_exists")
    require(not Path(RELEASE["infrastructure"]["executor_host"]).exists(), "executor_config_already_exists")


def install_private_inputs():
    check_private_inputs()
    for bind in RELEASE.get("media_binds", []):
        path = Path(bind["source"])
        if not path.exists():
            path.mkdir(mode=0o755)
            os.chown(path, 10001, 10001)
            path.chmod(0o755)
    if RELEASE.get("preserve_infrastructure"):
        if RELEASE.get("control"):
            save(S / "control-installation.json", install_control(RELEASE["control"]))
        return
    # runc can inherit the existing mountpoint mode on later container starts.
    # This host directory remains empty; all sampling content lives in tmpfs.
    prepare_sampling_mountpoint(host_path(RELEASE["baseline"]["owners"], "platform", SAMPLE_STATE))
    for bind in RELEASE["infrastructure"]["credential_binds"]:
        source = S / "credentials" / Path(bind["source"]).name
        target = Path(bind["source"])
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(target.parent, 10001, 10001)
        atomic_bytes(target, source.read_bytes(), mode=0o600, uid=10001, gid=10001)
    target = Path(RELEASE["infrastructure"]["executor_host"])
    require(not target.exists(), "executor_config_already_exists")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(target.parent, 10001, 10001)
    atomic_bytes(target, (json.dumps(RELEASE["infrastructure"]["executor_config"], sort_keys=True, indent=2) + "\n").encode(), mode=0o600, uid=10001, gid=10001)


def start_and_verify(rows, paths, expected_rows, credential_binds, *, expect_sampling=True):
    global GATEWAY_REISSUED
    old = {row["service"]: row for row in rows}
    unchanged = [row["id"] for row in rows if row["service"] != "platform"]
    command([D, "update", "--restart=unless-stopped", *unchanged])
    command([D, "start", *[row["id"] for row in rows if row["service"].startswith("obs-")]], timeout=90)
    command(compose_args("tianshu-v2-resident", paths["platform"], R) + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "platform"], timeout=180)
    wait_platform("tianshu-v2-resident")
    GATEWAY_REISSUED = resume_gateway_if_expired(paths)
    command([D, "start", *[old[name]["id"] for name in ("knowledge", "memory", "companion")]], timeout=120)
    deadline = time.monotonic() + 150
    while True:
        current = snapshot.inspect_owners(D)
        expect_owners(current, expected_rows, credential_binds, expect_sampling=expect_sampling)
        health = [command([D, "inspect", "--format", "{{.State.Health.Status}}", row["id"]]).stdout.strip() for row in current if row["service"] in CORE_NAMES]
        if all(row["running"] for row in current) and health == ["healthy"] * 5:
            break
        require(time.monotonic() < deadline, "core_readiness_failed")
        time.sleep(3)
    command([D, "update", "--restart=unless-stopped", *[row["id"] for row in current]])
    command([PYTHON, "-B", "-m", "ops.resident_capacity.guard", "arm", "--config", str(CAP)], cwd=TOOLING)
    command(["systemctl", "start", "tianshu-resident-capacity.service"], timeout=90)
    require(guard_status()["status"] == "ready", "guard_not_ready_after_update")
    return current


def verify_infrastructure_runtime(platform_id, memory_limit):
    expected = copy.deepcopy(RELEASE["infrastructure"]["settings"])
    if RELEASE.get("control"):
        expected["control"] = RELEASE["control"]["configuration"]["platform_control"]
    program = "EXPECTED=" + repr(expected) + "\n" + r'''
import json,os,subprocess
from pathlib import Path
settings=json.loads(Path('/etc/tianshu/settings.json').read_bytes())
assert settings['infrastructure']==EXPECTED
assert settings['infrastructure']['directory']==settings['web_external']['directory']
state=Path('/var/lib/tianshu/infrastructure-sampling')
info=state.stat();assert info.st_uid==info.st_gid==10001 and info.st_mode&0o777==0o700
assert state.is_mount()
disk=os.statvfs(state);assert disk.f_frsize*disk.f_blocks==128*1024*1024
for name in ['identity','known_hosts']:
 p=Path('/run/tianshu-infrastructure')/name
 assert p.stat().st_uid==10001 and p.stat().st_mode&0o777==0o600 and os.access(p,os.R_OK)
config=json.loads(Path('/etc/tianshu/infrastructure/executor.json').read_bytes())
assert set(config['nodes'])=={'nas-main'} and config['nodes']['nas-main']['label']=='主 NAS'
request={'version':'infrastructure-node.v1','request_id':'resident:catalog','node_id':None,'operation':'node.catalog','params':{},'timeout_ms':1000}
p=subprocess.run(EXPECTED['executor']['command'],input=json.dumps(request).encode(),capture_output=True,timeout=10,cwd=EXPECTED['executor']['directory'])
assert p.returncode==0 and not p.stderr
result=json.loads(p.stdout);assert result['status']=='ok'
control_passed=False
if EXPECTED.get('control'):
 control=EXPECTED['control']
 request={'version':'infrastructure-control.v1','request_id':'resident:control-catalog','node_id':None,'operation':'control.catalog','params':{},'timeout_ms':1000}
 p=subprocess.run(control['executor']['command'],input=json.dumps(request).encode(),capture_output=True,timeout=10,cwd=control['executor']['directory'])
 assert p.returncode==0 and not p.stderr
 result=json.loads(p.stdout);assert result['status']=='ok' and [n['id'] for n in result['data']['nodes']]==['nas-main']
 relay_path=control['executor']['command'][-1]
 relay=json.loads(Path(relay_path).read_bytes())
 root=control['executor']['directory']
 program='import sys;sys.path.insert(0,'+repr(root)+');from ops.infrastructure.control.state import checked_directory;from pathlib import Path;checked_directory(Path('+repr(relay['state_dir'])+'))'
 p=subprocess.run([control['executor']['command'][0],'-I','-B','-c',program],capture_output=True,timeout=10)
 assert p.returncode==0,'control_relay_persistence_invalid'
 control_passed=True
print(json.dumps({'directory_reused':True,'node_ids':['nas-main'],'sampling_tmpfs_bytes':disk.f_frsize*disk.f_blocks,'credential_files_readable':True,'catalog_process_passed':True,'control_catalog_process_passed':control_passed}))
'''
    report = json.loads(command([D, "exec", "-i", platform_id, "python", "-I", "-B", "-"], input=program, timeout=20).stdout)
    raw = json.loads(command([D, "inspect", platform_id]).stdout)[0]
    require(raw["HostConfig"]["Memory"] == memory_limit, "platform_memory_limit_changed")
    require(SAMPLE_STATE in raw["HostConfig"].get("Tmpfs", {}), "platform_sampling_tmpfs_missing")
    save(S / "infrastructure-runtime.json", {**report, "platform_memory_limit_preserved": True})


def release():
    global GATEWAY_REISSUED, TRANSACTION_STARTED, FAILURE_BASELINE_ROWS, FAILURE_EXPECTED_ROWS
    require(not (S / "started.json").exists(), "release_already_started")
    rows, cap, paths = inspect_inputs()
    FAILURE_BASELINE_ROWS = FAILURE_EXPECTED_ROWS = rows
    check_private_inputs()
    candidate_guard = load_candidate_guard()
    non_targets = metadata_inventory(candidate_guard, {row["id"] for row in rows})
    save(S / "non-targets-before.json", non_targets)
    templates = {name: read(path) for name, path in paths.items()}
    before_networks = network_inventory(rows)
    definitions = network_definitions(before_networks)
    declared = declared_addresses(rows, templates)
    save(S / "original-networks.json", before_networks)
    save(S / "original-network-definitions.json", definitions)
    save(S / "declared-network-addresses.json", declared)
    save(S / "original-capacity.json", cap)
    save(S / "original-templates.json", templates)
    atomic_bytes(S / "original-unit", UNIT.read_bytes(), mode=0o600, uid=0, gid=0)
    settings_path = R / "config/platform/settings.json"
    settings = read(settings_path)
    save(S / "original-platform-settings.json", settings)
    preserve_infrastructure = RELEASE.get("preserve_infrastructure", False)
    control = RELEASE.get("control")
    if control:
        new_settings = merge_settings(settings, RELEASE["infrastructure"]["settings"], preserve_existing=preserve_infrastructure,
                                      control=control["configuration"]["platform_control"])
    else:
        new_settings = merge_settings(settings, RELEASE["infrastructure"]["settings"], preserve_existing=preserve_infrastructure)
    settings_hashes = current_settings_hashes()
    save(S / "settings-hashes.json", settings_hashes)
    platform = next(row for row in rows if row["service"] == "platform")
    raw_platform = json.loads(command([D, "inspect", platform["id"]]).stdout)[0]
    memory_limit = raw_platform["HostConfig"]["Memory"]
    guards = []
    for name in ("memory", "knowledge"):
        database = host_path(rows, name, read(R / "config" / name / "settings.json")["database_path"])
        paired = Path(str(database) + ".source-guard.json")
        require(paired.is_file(), "owner_guard_missing")
        guards.append(str(paired.relative_to(R)))
    controls = [Path(path) for path in paths.values()] + [CAP, UNIT, Path(cap["state_dir"]), TOOLING / "ops/resident_capacity"]
    staging = Path(RELEASE["baseline"]["media_staging_host"])
    staging_roots = [staging] if staging.exists() and staging.is_relative_to(R) else []
    save(S / "cold-expectation.json", {"containers": rows, "root": str(R), "controls": [str(path) for path in controls], "memory_guards": guards, "external_media_binds": cap["external_media_binds"], "external_data_binds": cap.get("external_data_binds", {}), "media_staging_roots": [str(path) for path in staging_roots]})
    media_before = media_state()
    save(S / "media-before.json", media_before)
    save(S / "started.json", {"state": "stop_intent", "owners": 10})
    TRANSACTION_STARTED = True
    stop_managed()
    snapshot.require_stopped(rows, snapshot.inspect_owners(D))
    backup = snapshot.snapshot(R, S / "cold-snapshot", rows, lambda: snapshot.inspect_owners(D), controls=controls, memory_guards=guards, external_media_binds=cap["external_media_binds"], external_data_binds=cap.get("external_data_binds", {}), media_staging_roots=staging_roots, docker=D)
    require(backup["status"] == "offline_restore_verified" and backup["restored_disabled"] and backup["authority_unchanged"], "offline_restore_not_verified")
    save(S / "user-state-before.json", user_state_hashes(R))
    # No production input is written until the existing stopped-owner backup
    # and independent disabled restore verification have succeeded.
    install_private_inputs()
    if new_settings != settings:
        atomic_bytes(settings_path, (json.dumps(new_settings, ensure_ascii=False, indent=2) + "\n").encode())
    added_binds = RELEASE["infrastructure"]["credential_binds"] + RELEASE.get("media_binds", [])
    templates, cap = patch_templates(templates, cap, RELEASE["images"]["platform"]["image_id"], RELEASE["infrastructure"]["credential_binds"], preserve_existing=preserve_infrastructure, media_binds=RELEASE.get("media_binds", []))
    for name in ("core", "platform"):
        atomic_bytes(paths[name], (json.dumps(templates[name], indent=2) + "\n").encode())
    for name, item in RELEASE["guard_files"].items():
        raw = base64.b64decode(item["base64"])
        require(hashlib.sha256(raw).hexdigest() == item["sha256"], "candidate_guard_source_changed")
        atomic_bytes(TOOLING / name, raw, mode=0o644, uid=0, gid=0)
    configure_guard_generation(cap, (S / "original-unit").read_text(), S)
    save(S / "start-intent.json", {"state": "candidate_start", "database_restore": False})
    current = start_and_verify(rows, paths, rows, added_binds)
    verify_networks(current, before_networks, definitions, declared)
    non_target_report = compare_inventory(non_targets, metadata_inventory(candidate_guard, {row["id"] for row in current}))
    save(S / "non-targets-after.json", non_target_report)
    require(non_target_report["preserved"], "non_target_container_changed")
    require(user_state_hashes(R) == read(S / "user-state-before.json"), "user_state_changed")
    require(read(settings_path) == new_settings, "platform_settings_changed")
    require(all(value == settings_hashes[name] for name, value in current_settings_hashes().items() if name != "platform" or new_settings == settings), "other_settings_changed")
    media_after = media_state()
    require(media_after["configuration_sha256"] == media_before["configuration_sha256"], "music_media_configuration_changed")
    save(S / "media-after.json", media_after)
    actual_platform = next(row for row in current if row["service"] == "platform")
    verify_infrastructure_runtime(actual_platform["id"], memory_limit)
    save(S / "result.json", {
        "status": "deployed", "images": RELEASE["images"], "guard_commit": RELEASE["guard_commit"],
        "guard": "ready", "node_ids": ["nas-main"], "backup": str(S / "cold-snapshot"),
        "sqlite_backups": backup["sqlite_databases_verified"], "health": health_checks(current),
        "user_state_preserved": True, "music_media_configuration_preserved": True,
        "non_target_containers_preserved": True, "platform_memory_limit_preserved": True,
        "existing_settings_preserved": preserve_infrastructure and new_settings == settings,
        "existing_settings_preserved_except_control": bool(control),
        "existing_settings_preserved_except_infrastructure_addition": not preserve_infrastructure,
        "control_source_commit": control["package"]["source_commit"] if control else None,
        "control_real_operations_accepted": False, "added_media_binds": RELEASE.get("media_binds", []),
        "gateway_origin_reissued": GATEWAY_REISSUED, "database_restore": False,
        "real_qq_test_sent": False, "real_node_sampling_accepted": False, "dockge_sync_required": True,
    })


def rollback_release(parent):
    """Fresh cold backup, then revert image/settings/binds/guard; retain all ledgers."""
    global S, RELEASE, GATEWAY_REISSUED, TRANSACTION_STARTED
    global FAILURE_BASELINE_ROWS, FAILURE_EXPECTED_ROWS, FAILURE_CREDENTIAL_BINDS, FAILURE_EXPECT_SAMPLING
    require((parent / "result.json").exists() or (parent / "failure.json").exists(), "release_receipt_required")
    require((parent / "cold-snapshot/receipt.json").exists(), "verified_original_backup_required")
    original = copy.deepcopy(RELEASE)
    previous_image = original["previous_images"]["platform"]
    image = json.loads(command([D, "image", "inspect", previous_image["image_id"]]).stdout)[0]
    require(image["Id"] == previous_image["image_id"] and image["Config"]["Labels"]["org.opencontainers.image.revision"] == previous_image["revision"], "rollback_image_unavailable_or_changed")
    rows = snapshot.inspect_owners(D)
    require(len(rows) == 10 and {row["service"] for row in rows} == snapshot.OWNERS, "rollback_owner_set_changed")
    cap = read(CAP)
    cold_external_media_binds = copy.deepcopy(cap["external_media_binds"])
    preserve_infrastructure = original.get("preserve_infrastructure", False)
    require(cap["state_dir"].startswith(str(parent) + "/capacity-state-") or hashlib.sha256(CAP.read_bytes()).hexdigest() == original["baseline"]["capacity_sha256"], "another_release_changed_capacity")
    if (parent / "gateway-origin-result.json").exists():
        gateway_record = read(parent / "gateway-origin-result.json")
        gateway = next(row for row in rows if row["service"] == "gateway")
        require(gateway["id"] == gateway_record["gateway_id_after"], "gateway_changed_after_release")
        GATEWAY_REISSUED = gateway_record["origin_reissued"]
    try:
        expect_owners(rows, original["baseline"]["owners"], original["infrastructure"]["credential_binds"] + original.get("media_binds", []))
    except Exception:
        require(signatures(rows) == signatures(original["baseline"]["owners"]), "rollback_owner_identity_changed")
    paths = paths_from(cap)
    templates = {name: read(path) for name, path in paths.items()}
    settings_path = R / "config/platform/settings.json"
    settings = read(settings_path)
    if original.get("control"):
        settings = restore_control_settings(settings, original["control"]["configuration"]["platform_control"], original["infrastructure"]["settings"])
    if "infrastructure" in settings and not preserve_infrastructure:
        require(settings["infrastructure"] == original["infrastructure"]["settings"], "infrastructure_changed_after_release")
        settings.pop("infrastructure")
    # Preserve every other setting, including legitimate later user changes.
    credential_binds = original["infrastructure"]["credential_binds"] + original.get("media_binds", [])
    sources = {(item["source"], item["target"]) for item in credential_binds}
    for name in ("core", "platform"):
        service = templates[name]["services"]["platform"]
        service["image"] = original["previous_images"]["platform"]["image_id"]
        service["volumes"] = [v for v in service["volumes"] if (v.get("source"), v.get("target")) not in sources]
        if not preserve_infrastructure:
            service["tmpfs"] = [item for item in service.get("tmpfs", []) if item.split(":", 1)[0] != SAMPLE_STATE]
    cap["images"]["platform"] = original["previous_images"]["platform"]["image_id"]
    cap["binds"]["platform"] = [item for item in cap["binds"]["platform"] if (item["source"], item["target"]) not in sources]
    cap["external_media_binds"] = [item for item in cap["external_media_binds"] if (item["source"], item["target"]) not in sources]
    expected = copy.deepcopy(rows)
    for row in expected:
        if row["service"] == "platform":
            row["mounts"] = [m for m in row["mounts"] if (m.get("Source"), m["Destination"]) not in sources and (preserve_infrastructure or m["Destination"] != SAMPLE_STATE)]
    FAILURE_BASELINE_ROWS, FAILURE_EXPECTED_ROWS = rows, expected
    FAILURE_CREDENTIAL_BINDS, FAILURE_EXPECT_SAMPLING = [], preserve_infrastructure
    before = network_inventory(rows)
    definitions = network_definitions(before)
    declared = declared_addresses(rows, templates)
    candidate_guard = load_candidate_guard()
    non_targets = metadata_inventory(candidate_guard, {row["id"] for row in rows})
    original_controls = read(parent / "cold-expectation.json")["controls"]
    guard_backup = parent / "cold-snapshot/controls-backup" / str(original_controls.index(str(TOOLING / "ops/resident_capacity")))
    require(guard_backup.is_dir(), "guard_rollback_backup_missing")
    guards = read(parent / "cold-expectation.json")["memory_guards"]
    staging_roots = [Path(path) for path in read(parent / "cold-expectation.json")["media_staging_roots"]]
    controls = [Path(path) for path in paths.values()] + [CAP, UNIT, Path(cap["state_dir"]), TOOLING / "ops/resident_capacity"]
    # Carry the old guard into the fresh backup before its successful snapshot
    # retention is allowed to remove the previous full backup's data trees.
    guard_control_index = len(controls)
    controls.append(guard_backup)
    S = parent / ("rollback-" + uuid.uuid4().hex)
    S.mkdir(mode=0o700)
    RELEASE = copy.deepcopy(original)
    RELEASE["stage"] = str(S)
    RELEASE["images"] = original["previous_images"]
    GATEWAY_REISSUED = False
    save(S / "original-templates.json", {name: read(path) for name, path in paths.items()})
    save(S / "cold-expectation.json", {"containers": rows, "root": str(R), "controls": [str(path) for path in controls], "memory_guards": guards, "media_staging_roots": [str(path) for path in staging_roots]})
    save(S / "started.json", {"state": "rollback_stop_intent", "database_restore": False})
    TRANSACTION_STARTED = True
    stop_managed()
    snapshot.require_stopped(rows, snapshot.inspect_owners(D))
    backup = snapshot.snapshot(R, S / "cold-snapshot", rows, lambda: snapshot.inspect_owners(D), controls=controls, memory_guards=guards, external_media_binds=cold_external_media_binds, external_data_binds=cap.get("external_data_binds", {}), media_staging_roots=staging_roots, docker=D)
    require(backup["status"] == "offline_restore_verified" and backup["restored_disabled"] and backup["authority_unchanged"], "offline_restore_not_verified")
    guard_backup = S / "cold-snapshot/controls-backup" / str(guard_control_index)
    require(guard_backup.is_dir(), "guard_rollback_carry_missing")
    user_state = user_state_hashes(R)
    media_before = media_state()
    if read(settings_path) != settings:
        atomic_bytes(settings_path, (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode())
    for name in ("core", "platform"):
        atomic_bytes(paths[name], (json.dumps(templates[name], indent=2) + "\n").encode())
    for name in RELEASE["guard_files"]:
        source = guard_backup / Path(name).name
        require(source.is_file() and not source.is_symlink(), "guard_rollback_file_missing")
        atomic_bytes(TOOLING / name, source.read_bytes(), mode=0o644, uid=0, gid=0)
    unit = (parent / "original-unit").read_text().replace(original["baseline"]["state_dir"], cap["state_dir"])
    configure_guard_generation(cap, unit, S)
    current = start_and_verify(rows, paths, expected, [], expect_sampling=preserve_infrastructure)
    verify_networks(current, before, definitions, declared)
    non_target_report = compare_inventory(non_targets, metadata_inventory(candidate_guard, {row["id"] for row in current}))
    save(S / "non-targets-after.json", non_target_report)
    require(non_target_report["preserved"], "non_target_container_changed")
    require(read(settings_path) == settings and user_state_hashes(R) == user_state, "rollback_settings_or_user_state_changed")
    require(media_state()["configuration_sha256"] == media_before["configuration_sha256"], "music_media_configuration_changed")
    save(S / "result.json", {"status": "rolled_back", "images": RELEASE["images"], "database_restore": False, "later_ledgers_preserved": True, "backup": str(S / "cold-snapshot"), "sqlite_backups": backup["sqlite_databases_verified"], "guard": "ready", "health": health_checks(current), "dedicated_credentials_retained_unmounted": True, "dockge_sync_required": True})


try:
    release()
except Exception as error:
    # Preserve subsequent ledgers; a rollback is an explicit fresh controlled
    # image/configuration update, never an automatic database restore.
    failure = {"status": "requires_inspection", "error_type": type(error).__name__,
               "code": str(error) if re.fullmatch("[a-z0-9_]+", str(error)) else "operation_failed",
               "automatic_database_restore": False, "cleanup": "not_started"}
    if TRANSACTION_STARTED:
        try:
            stop_managed()
            current = snapshot.inspect_owners(D)
            # The new Platform can exist before the fresh generation is armed.
            # Confirm its complete planned mounts/image before stopping it; all
            # other owner IDs must still be the original exact containers.
            try:
                expect_owners(current, FAILURE_EXPECTED_ROWS, FAILURE_CREDENTIAL_BINDS, expect_sampling=FAILURE_EXPECT_SAMPLING)
            except Exception:
                require(signatures(current) == signatures(FAILURE_BASELINE_ROWS), "failure_owner_identity_changed")
            identifiers = [row["id"] for row in current]
            command([D, "update", "--restart=no", *identifiers])
            command([D, "stop", "--time", "60", *identifiers], timeout=150)
            snapshot.require_stopped(current, snapshot.inspect_owners(D))
            failure["cleanup"] = "stopped_preserving_ledgers"
        except Exception:
            failure["cleanup"] = "stop_readback_unconfirmed"
    try:
        save(S / "failure.json", failure)
    except Exception:
        print(json.dumps({**failure, "receipt_write_failed": True}), flush=True)
    raise
