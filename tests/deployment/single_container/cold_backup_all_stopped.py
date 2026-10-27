"""Offline preparation only: host230 root imports cold_backup(stage, old_owners).

Use the original reviewed PG17/snapshot/scratch protocol. Never stop/start owners,
restore production SQL, or invoke the historical migration backup/main entrypoint.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback
import uuid

ORIGINAL_STAGE = Path('/srv/tianshu-tooling/migration-20261023')
ORIGINAL_SCRIPT = ORIGINAL_STAGE / 'prepare_post_backup.py'
ORIGINAL_SCRIPT_SHA256 = 'b6b044f89e31b6a5beca4742cfef7a2e64c3a64e91a1d28c784fbac8a5ff342c'


def _load_original(stage: Path):
    if hashlib.sha256(ORIGINAL_SCRIPT.read_bytes()).hexdigest() != ORIGINAL_SCRIPT_SHA256:
        raise RuntimeError('original_backup_script_changed')
    spec = importlib.util.spec_from_file_location('host230_current_cold_backup', ORIGINAL_SCRIPT)
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    # load_upgrader resolves its reviewed file against the ORIGINAL S.
    upgrader = backup.load_upgrader()
    backup.S = stage
    backup.OPERATION = stage / 'cold-snapshot'
    backup.U = upgrader
    backup.load_upgrader = lambda: upgrader
    upgrader.S = stage  # atomic_bytes/save permission domain, not the old migration.
    bundle=json.loads((stage/'recovery-files.json').read_bytes())
    if hashlib.sha256((stage/'recovery-files.json').read_bytes()).hexdigest()!=RECOVERY_BUNDLE_SHA256:raise RuntimeError('reviewed_actor_recovery_changed')
    backup.MODULES={Path(k).stem:v['sha256'] for k,v in bundle['files'].items()}
    return backup, upgrader


def cold_backup(stage: Path, old_owners: dict, *, environment_overrides=None):
    """Return the verified receipt; raise on failure, leaving business stopped.

    old_owners is the coordinator's service -> full Docker inspect map (13 rows).
    All source credentials stay in remote memory/original root-only PG cleanup
    intents; they are never printed or placed in argv/public receipts.
    """
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('cold_backup_linux_root_required')
    stage = Path(stage)
    B, U = _load_original(stage)
    U.safe_path(stage)
    B.require(stage.is_dir() and stage not in {ORIGINAL_STAGE, ORIGINAL_STAGE.parent} and
              stage.is_relative_to(ORIGINAL_STAGE.parent) and
              not stage.is_relative_to(ORIGINAL_STAGE), 'independent_tooling_stage_required')
    B.require(isinstance(old_owners, dict) and len(old_owners) == 13 and
              U.OWNERS < set(old_owners), 'current_ten_owners_plus_three_aux_required')
    B.require(not B.OPERATION.exists(), 'new_cold_snapshot_required')
    record_root = stage / 'cold-backup-helper'
    B.require(not record_root.exists(), 'cold_backup_helper_already_used')
    record_root.mkdir(mode=0o700)
    run_id = uuid.uuid4().hex
    B.LOG_ROOT = stage / 'post-backup-private' / run_id
    B.LOG_ROOT.mkdir(mode=0o700, parents=True)
    tools_root = stage / 'post-backup-tools' / run_id
    tools_root.mkdir(mode=0o700, parents=True)
    document=json.loads(U.private_raw(ORIGINAL_STAGE / 'target-release.json'))
    document['recovery']['files'].update(json.loads((stage/'recovery-files.json').read_bytes())['files'])
    release_raw=(json.dumps(document,sort_keys=True)+'\n').encode()
    release_path = stage / 'target-release.json'
    if release_path.exists():
        B.require(U.private_raw(release_path) == release_raw, 'stage_frozen_release_changed')
    else:
        U.atomic_bytes(release_path, release_raw, mode=0o600, uid=0, gid=0)
    U.RELEASE = json.loads(release_raw)
    modules = B.load_recovery(U.RELEASE)
    snapshot, backend = modules['resident_snapshot'], modules['postgres_backend']
    environment_before = {}

    def set_environment(key, value):
        if key not in environment_before:
            environment_before[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def save(name, value):
        U.atomic_bytes(record_root / name,
                       (json.dumps(value, sort_keys=True, indent=2) + '\n').encode(),
                       mode=0o600, uid=0, gid=0)

    def check_baseline(rows):
        B.require(len(rows) == 10 and {row['service'] for row in rows} == U.OWNERS,
                  'current_owner_set_changed')
        for row in rows:
            original = old_owners[row['service']]
            labels = original['Config'].get('Labels') or {}
            B.require(row['id'] == original['Id'] and row['image_id'] == original['Image'] and
                      row['project'] == labels.get('com.docker.compose.project') and
                      row['service'] == labels.get('com.docker.compose.service') and
                      row['restart'] == original['HostConfig']['RestartPolicy']['Name'] == 'no' and
                      U.canonical_mounts(row['mounts']) == U.canonical_mounts(original['Mounts']),
                      'current_owner_differs_from_coordinator_baseline')

    # Aux inspect is metadata-only: never persist Docker Config.Env.
    aux_template = ('{"id":{{json .Id}},"image":{{json .Image}},"mounts":{{json .Mounts}},'
                    '"running":{{json .State.Running}},"state":{{json .State.Status}},"exit":{{json .State.ExitCode}},"paused":{{json .State.Paused}},'
                    '"restarting":{{json .State.Restarting}},"dead":{{json .State.Dead}},'
                    '"restart":{{json .HostConfig.RestartPolicy.Name}}}')

    def check_aux():
        for service in sorted(set(old_owners) - U.OWNERS):
            original = old_owners[service]
            row = json.loads(B.command([*B.DOCKER, 'inspect', '--format', aux_template,
                                        original['Id']]).stdout)
            B.require(row['id'] == original['Id'] and row['image'] == original['Image'] and
                      U.canonical_mounts(row['mounts']) == U.canonical_mounts(original['Mounts']) and
                      row['restart'] == original['HostConfig']['RestartPolicy']['Name'] == 'no' and
                      row['running'] is False and original['State']['Running'] is False and
                      row['state'] == original['State']['Status'] == 'exited' and
                      row['exit'] == original['State']['ExitCode'] == 0 and
                      not any(row[key] for key in ('paused', 'restarting', 'dead')),
                      'auxiliary_identity_or_stopped_state_changed')

    context = {'run_id': run_id, 'network': U.RELEASE['egress_network'],
               'log_root': str(B.LOG_ROOT), 'script_sha256': ORIGINAL_SCRIPT_SHA256,
               'release_sha256': B.sha(release_raw)}
    context_path = tools_root / 'context.private.json'
    U.atomic_bytes(context_path, (json.dumps(context) + '\n').encode(), mode=0o600, uid=0, gid=0)
    save('started.private.json', {'run_id': run_id, 'time': time.time(),
                                'operation': str(B.OPERATION), 'context': context,
                                'business_started': False})
    try:
        set_environment('DOCKER_HOST', 'unix:///var/run/docker.sock')
        for key in ('DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            set_environment(key, None)
        owners = snapshot.inspect_owners('/usr/bin/docker')
        check_baseline(owners)
        snapshot.require_stopped(owners, owners)
        U.ROWS = owners  # host_path/storage_markers now use current ten, never old IDs.
        check_aux()
        markers = U.storage_markers()
        B.require(sum(len(item['databases']) for item in markers.values()) == 24 and
                  len(markers['platform']['databases']) == 17 and
                  'plugins' in markers['platform']['databases'], 'twenty_four_pg_sources_required')
        actor=backend.actor_memory_domain(B.R,owners)
        if actor:
            B.require(actor['backend']=='postgresql','actor_postgresql_required')
            markers['actor-memory']=U.read(B.R/actor['binding'])
        expected_services=set(U.CORES)|({'actor-memory'} if actor else set())
        expected_sources=24+int(bool(actor));expected_guards=2+int(bool(actor))
        connections = {}
        for service in sorted(U.CORES):
            config = U.read(B.R / 'config' / service / 'settings.json')
            environment = (environment_overrides or {}).get(service,dict(item.split('=', 1) for item in old_owners[service]['Config']['Env']))
            url = environment.get('TIANSHU_POSTGRES_URL') or config.get('storage_backends', {}).get('postgresql_url')
            parsed = backend.connection_environment(url)
            B.require(parsed['PGHOST'] == B.PG_HOST and parsed['PGPORT'] == str(B.PG_PORT),
                      'nas_postgresql_endpoint_changed')
            key = 'HOST230_' + service.upper() + '_POSTGRES_BACKUP_URL'
            set_environment(key, url)
            connections[service] = {'url_env': key}
            maintenance = 'TIANSHU_' + service.upper() + '_BACKUP_POSTGRES_URL'
            if os.environ.get(maintenance):
                connections[service]['maintenance_url_env'] = maintenance
            elif os.environ.get('TIANSHU_BACKUP_POSTGRES_URL'):
                connections[service]['maintenance_url_env'] = 'TIANSHU_BACKUP_POSTGRES_URL'
        evidence = tools_root / 'source-evidence'
        evidence.mkdir(mode=0o700)
        for name, raw in ((ORIGINAL_SCRIPT.name, U.private_raw(ORIGINAL_SCRIPT)),
                          (B.UPGRADER_FILE, U.private_raw(ORIGINAL_STAGE / B.UPGRADER_FILE)),
                          ('target-release.json', release_raw),
                          ('cold_backup.py', Path(__file__).read_bytes())):
            U.atomic_bytes(evidence / name, raw, mode=0o600, uid=0, gid=0)
        paths = {}
        for tool in ('psql', 'pg_dump', 'pg_restore'):
            # Original B.__file__ remains the context's script identity. Each
            # child loads U before rebasing S and calls the ORIGINAL client CLI.
            source = ('#!/usr/bin/python3\nimport importlib.util,sys\nfrom pathlib import Path\n'
                      'spec=importlib.util.spec_from_file_location("current_cold_backup_helper",'
                      + repr(str(evidence / 'cold_backup.py')) + ')\n'
                      'helper=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(helper)\n'
                      'B,U=helper._load_original(Path(' + repr(str(stage)) + '))\n'
                      'sys.argv=[' + repr(str(ORIGINAL_SCRIPT)) + ',"client","--context",'
                      + repr(str(context_path)) + ',' + repr(tool) + ',"--",*sys.argv[1:]]\n'
                      'raise SystemExit(B.main())\n')
            wrapper = tools_root / tool
            U.atomic_bytes(wrapper, source.encode(), mode=0o700, uid=0, gid=0)
            paths[tool] = str(wrapper)
        image=json.loads(B.command([*B.DOCKER,'image','inspect',B.CLIENT_IMAGE]).stdout)[0]
        B.require(image['Id']==B.CLIENT_IMAGE,'existing_postgresql_client_image_required')
        client_proof = B.ensure_client_image(run_id)
        cap = U.read(U.GUARD_CONFIG)
        B.require(cap['deployment_root'] == str(B.R) and cap['service_profile'] == 'knowledge-ten',
                  'target_capacity_config_required')
        guards = []
        for service in ('memory', 'knowledge'):
            settings = U.read(B.R / 'config' / service / 'settings.json')
            container = ((settings.get('source_sync') or {}).get('recovery_path') or
                         (settings['database_path'] + '.source-guard.json'))
            guards.append(str(U.host_path(service, container).relative_to(B.R)))
        controls = [tools_root, U.GUARD_CONFIG,
                    Path('/etc/systemd/system/tianshu-resident-capacity.service'),
                    Path('/srv/tianshu-tooling/repo/ops/resident_capacity')]
        report = snapshot.snapshot(B.R, B.OPERATION, owners,
                    lambda: snapshot.inspect_owners('/usr/bin/docker'), seconds=1800,
                    controls=controls, memory_guards=guards,
                    external_media_binds=cap['external_media_binds'],
                    external_data_binds=cap.get('external_data_binds', {}),
                    docker='/usr/bin/docker', postgresql=connections, postgresql_tools=paths)
        B.require(report['status'] == 'offline_restore_verified' and report['restored_disabled'] and
                  report['authority_unchanged'] and report['postgresql_services_verified'] == len(expected_services) and
                  report['postgresql_databases_verified'] == expected_sources and report['memory_guards_verified'] == expected_guards,
                  'native_cold_restore_not_verified')
        B.require(report.get('retention', {}).get('status') == 'outside_resident_release_namespace',
                  'unexpected_snapshot_retention_namespace')
        current_markers=U.storage_markers()
        if actor:current_markers['actor-memory']=U.read(B.R/actor['binding'])
        B.require(current_markers == markers, 'backend_markers_changed_during_backup')
        manifest = U.read(B.OPERATION / 'manifest.json')
        B.require(B.sha((B.OPERATION / 'manifest.json').read_bytes()) == report['manifest_sha256'] and
                  {row['service'] for row in manifest['postgresql']} == expected_services and
                  all(row['schemas'] == markers[row['service']]['databases'] and
                      row['installation_id'] == markers[row['service']]['installation_id']
                      for row in manifest['postgresql']), 'cold_backup_binding_or_manifest_changed')
        current = snapshot.inspect_owners('/usr/bin/docker')
        check_baseline(current)
        snapshot.require_stopped(owners, current)
        check_aux()
        B.finish_scratch_requests(context, allow_drop=False)
        B.cleanup_clients(run_id)
        result = {'status': 'cold_backup_verified', 'run_id': run_id, 'operation': str(B.OPERATION),
                  'receipt': report, 'client': client_proof, 'logical_sources': expected_sources, 'guards': expected_guards,
                  'frozen_script_sha256': ORIGINAL_SCRIPT_SHA256, 'release_sha256': B.sha(release_raw),
                  'frozen_recovery_sha256': B.MODULES, 'business_started': False,
                  'production_database_restore': False, 'scratch_databases_removed': True,
                  'client_containers_removed': True, 'auxiliary_backup_included': False}
        save('result.private.json', result)
        return result
    except BaseException:
        failure = {'status': 'cold_backup_failed_business_left_stopped', 'run_id': run_id,
                   'time': time.time(), 'private_traceback': traceback.format_exc(),
                   'production_database_restore': False, 'automatic_retry': False}
        try:
            B.cleanup_clients(run_id)
        except BaseException:
            failure['initial_client_cleanup_traceback'] = traceback.format_exc()
            failure['scratch_cleanup'] = 'not_attempted_while_clients_unconfirmed'
        else:
            try:
                B.finish_scratch_requests(context, allow_drop=True)
                failure['scratch_cleanup'] = 'confirmed_absent'
            except BaseException:
                failure['scratch_cleanup'] = 'unconfirmed'
                failure['scratch_cleanup_traceback'] = traceback.format_exc()
        finally:
            try:
                B.cleanup_clients(run_id)
                failure['client_cleanup'] = 'confirmed_absent'
            except BaseException:
                failure['client_cleanup'] = 'unconfirmed'
                failure['client_cleanup_traceback'] = traceback.format_exc()
        save('failure.private.json', failure)
        raise
    finally:
        for key, value in environment_before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

RECOVERY_BUNDLE_SHA256='06bdc127ee205b91f662de392961ea8ac0e55d20067018f714b92cfb1c766ffc'
