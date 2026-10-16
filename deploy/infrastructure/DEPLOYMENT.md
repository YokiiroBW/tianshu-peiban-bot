# Resident infrastructure release inputs

The coordinator alone executes remote build, staging, launch, rollback and
readback. These local tools emit reviewable inputs for the existing resident
release mechanism; neither preparer imports the NAS connection helper or calls
Docker/SSH. Keep outputs, actual executor config and all receipts in a private
`.runtime` directory. No credential contents belong in Git or terminal output.

The first TS-180 tooling candidate still names the **preceding** accepted node
package `172d20d` / `6e674a3b…dd63f` in `image.py`. Actual NAS probes found node
compatibility issues; the coordinator will provide the final replacement source
and accepted package hash. Update that one pin pair before final image preparation.
Do not deploy this preceding node package as the final release.

## 1. Prepare the fixed candidate image locally

First finish and read back the independent music release. Use its final full
commit and image ID, and the fixed Platform commit containing both that history
and the accepted infrastructure code. Dependency/build input bytes must match
the music commit. The generated Dockerfile retains the original music-tools,
web and application build stages, adding OpenSSH and the node package in its own
venv. It does not change the product Dockerfile or application PATH/venv.

```text
python -B -m deploy.infrastructure.image --platform-repo ABS_PLATFORM_REPO --platform-commit FULL_MERGED_PLATFORM_SHA --music-commit FULL_DEPLOYED_MUSIC_SHA --music-image sha256:FULL_IMAGE_ID --contracts-repo ABS_COORDINATOR_REPO --contracts-commit FULL_CONTRACTS_SHA --node-package ABS_ACCEPTED_NODE_ZIP --stage /volume2/tianshu-v2-resident-updates/UNIQUE_BUILD_STAGE --docker ABS_CONFIRMED_NAS_DOCKER --remote-python ABS_CONFIRMED_NAS_TOOL_PYTHON --tag UNIQUE_CANDIDATE_TAG --output ABS_NEW_PRIVATE_DIRECTORY
```

This emits a fixed Git `build-context.tar.gz`, its digest, `Dockerfile.candidate`,
`build-inputs.json`, `build-controller.py` and three remote payloads. The
coordinator reviews them, then runs `remote-build-stage.py`,
`remote-build-launch.py`, and `remote-build-poll.py` through the existing framed
`nas_access.run` transport. The first two create a new private stage and start a
detached build worker; polling reads its existing receipt. An unknown launch
outcome requires inspection, never a blind repeat. Do not print entire payloads.

The controller never mounts production data or starts a production service. It
checks installed source/contracts/web/node hashes, original image configuration,
both Python environments, exact PyYAML, resolved OpenSSH version, and unchanged
music binary/source-archive/ffmpeg identities. Linux validation and the TS-181/182
installed-package checks must finish before the resident update is prepared.
Copy the resulting `build-result.json` into the local private evidence directory.

## 2. Prepare the controlled resident update locally

Refresh the current resident baseline after music acceptance. It must include
the ten exact owners, runtime revision, ready guard status, capacity/compose/
settings hashes, external-media binds, state directory, media staging root, and
existing owner contract pins. Running state is derived from `owners`; no separate
`all_owners_running` field is required. The initial capture made during music's
cold backup is structural evidence only and is refused as a final ready baseline.

Use the coordinator's observed metadata and a separate private executor JSON:
only `nas-main` / `主 NAS` / `synology`; explicit confirmed Docker argv,
`/var/run/docker.sock` and physical host stacks root; `/usr/bin/ssh`; sampling
state `/var/lib/tianshu/infrastructure-sampling`; credential references
`/run/tianshu-infrastructure/{identity,known_hosts}`. OpenWrt is not registered.

```text
python -B -m deploy.infrastructure.resident --baseline ABS_FINAL_BASELINE_JSON --metadata ABS_NAS_METADATA_JSON --build-result ABS_ACCEPTED_BUILD_RESULT_JSON --mechanism-directory ABS_EXISTING_RELEASE_DIRECTORY --recovery ABS_EXISTING_RECOVERY_CODE_JSON --executor-config ABS_PRIVATE_EXECUTOR_JSON --guard-repo ABS_TS180_REPO --guard-commit FULL_ACCEPTED_GUARD_SHA --stage /volume2/tianshu-v2-resident-updates/UNIQUE_RELEASE_STAGE --output ABS_NEW_PRIVATE_DIRECTORY
```

`mechanism-directory` supplies the existing `resident_helpers.py.txt`,
`resident_update_body.py.txt`, and `resident_release_launcher.py`; their raw-byte
hashes are recorded. The preparer parameterizes only helper path assignments,
reuses network/gateway-origin functions and the existing stage/launch/poll
payloads, and adds the reviewed worker and rollback script. Recovery source is
separately hashed and staged using the existing mechanism.

The production changes are confined to:

- Adding only `settings.infrastructure`, reusing the initialized
  `/var/lib/tianshu/external` directory. Timeout is 30000 ms; polling remains 30 s
  and stale observation age remains 120 s.
- Writing `config/platform/infrastructure/executor.json` under the existing
  read-only `/etc/tianshu` config mount. Two dedicated files at
  `config/infrastructure/ssh/{identity,known_hosts}` receive separate read-only
  file binds to `/run/tianshu-infrastructure/`. No whole SSH directory is mounted.
- Adding a 128 MiB local tmpfs, UID/GID 10001, mode 0700, at the sampling path.
  Existing memory limits, music mounts and every other service remain unchanged.
- Promoting the accepted complete guard files and a fresh capacity/state/unit
  generation, preserving WatchdogSec=30s, immutable image IDs and exact registered
  external-media source/target/read-only matching.

## 3. Coordinator execution and rollback

Run `remote-stage.py` through the existing transport. It stages both the fixed
worker and reviewed `rollback.py`. Place only the dedicated NAS identity and the
already-trusted `[192.168.31.210]:77` known-host line in the new remote stage's
`credentials/{identity,known_hosts}` (root-owned 0600); never stage a whole home
or unrelated node list. `remote-launch.py` then uses the existing detached
worker mechanism, and `remote-poll.py` returns only receipts.

Before any production input write, the worker stops the existing managed owners
through their guard, calls the existing `resident_snapshot.snapshot`, and
requires its `offline_restore_verified` receipt. That function copies the backup
into an independent `restored-disabled` tree, verifies SQLite integrity/foreign
keys/paired source guards, and compares restored metadata/bytes with the original.
It explicitly does not claim a functional restore activation. Only then are the
two staged credential files copied and settings/templates/guard updated.

The ordinary start order, optional existing-scope gateway origin renewal, exact
owner identities/mounts, TLS/readiness and fresh guard arm are retained. Receipts
separately record non-target container preservation, music/user/settings
preservation, unchanged memory, bounded state and catalog process success.
Actual NAS sampling, limited final-Platform logs, Dockge and real web/UI acceptance
remain coordinator/TS-181/182 checks; catalog success cannot replace those checks.

On failure inspect private receipts first. Cleanup only acts after this worker
created its own stop intent; rerunning a prior completed worker cannot stop a live
release on a preflight refusal. Only complete old/planned owner identities can be
stopped. The staged `rollback.py` is an explicit fresh controlled update: it takes
another verified cold snapshot, restores the prior image/settings/binds/guard
generation, and preserves later database ledgers and music work. It never restores
an old database automatically. Dedicated credentials remain private and unmounted.
Read back guard, Docker/Dockge and health after either update or rollback.
