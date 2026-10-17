# Resident infrastructure release inputs

The coordinator alone executes remote build, staging, launch, rollback and
readback. These local tools emit reviewable inputs for the existing resident
release mechanism; neither preparer imports the NAS connection helper or calls
Docker/SSH. Keep outputs, actual executor config and all receipts in a private
`.runtime` directory. No credential contents belong in Git or terminal output.

The accepted node source input is `47564d07257a43ad99dde63d6ea96cf8c169be5a`,
ZIP SHA256 `9aac3dc090a0317804b5787dd5136b4916ee686b718a11b18b74e976806cebf5`,
with the deployment settings fragment set to timeout 30000 ms. `image.py` owns
that one pin pair. This source replaces the preceding `172d20d` after actual NAS
probes exposed Docker24 health, dial-stdio EOF and partial-find compatibility
issues. The new ZIP passed the separate 12-request outside-package synthetic SSH
chain; the coordinator independently checks the package and actual NAS launcher.

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
The private stage root remains 0700 under umask 077. Extraction explicitly sets
every internal build-source directory to 0755 and preserves each committed
file's 0644/0755 mode. This applies to all COPY trees, including contracts, tools,
services, frontend, build scripts and the node package, so host staging privacy
does not make image contents unreadable to the product's UID 10001.

The controller never mounts production data or starts a production service. It
checks installed source/contracts/web/node hashes, original image configuration,
both Python environments, exact PyYAML, resolved OpenSSH version, and unchanged
music binary/source-archive/ffmpeg identities. Music bridge/decode and ffmpeg use
exact executable SHA256, plus the ffmpeg version line. The source archive compares
member paths, types, file bytes, link targets and whether regular files/directories
have any execution/traversal bit. Compression headers, mtime, ownership and other
permission bits are ignored; private staging can legitimately change 0755 to 0700.
Linux validation and the TS-181/182
installed-package checks must finish before the resident update is prepared.
Copy the resulting `build-result.json` into the local private evidence directory.

If an already built immutable candidate needs the corrected acceptance, copy the
reviewed `image_controller.py` under a **new filename** in its original build
stage, preserving the original `build-inputs.json`, controller, logs and failed
receipt. Do not rerun the original build launcher or change the candidate tag.
The coordinator invokes the new standalone file with both explicit arguments:

```text
ABS_CONFIRMED_NAS_TOOL_PYTHON -B ABS_ORIGINAL_BUILD_STAGE/image-reverify.py --verify-image sha256:FULL_BUILT_IMAGE_ID --receipt-name build-reverified.json
```

This mode performs the same input/base checks and complete candidate acceptance
as the normal build path, including installed source/web/contracts/node hashes,
image labels/configuration, dependencies and music. It never builds or tags an
image, and it always runs container probes against the inspected full image ID.
It requires a new stage-local JSON name and publishes without replacing existing
evidence. A failure gets its own corresponding `*-controller-error.log`.
A successful result retains `status=built` for the existing resident preparer,
with `verification_only=true`, `image_rebuilt=false`, the new controller/input
hashes and original build evidence hashes. Use this new JSON as `--build-result`;
retain the earlier failed receipt and logs unchanged.
An actual source-directory permission failure requires a new stage/tag and a
normal build with corrected staging, retaining the failed image. Re-verifying
the same image cannot repair its directory modes; keep the full expected file
set and the original product UID. Normal builds retain Docker cache reuse.

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
- Preparing the empty host mountpoint beneath the existing Platform data bind
  as 10001:10001/0700 after verified cold backup, before installing private inputs.
  Existing nonempty paths or symlinks are refused; no recursive chmod or data
  deletion is used. This preserves 0700 across the observed runc restart behavior.
- Promoting the accepted complete guard files and a fresh capacity/state/unit
  generation, preserving WatchdogSec=30s, immutable image IDs and exact registered
  external-media source/target/read-only matching.

## 3. Coordinator execution and rollback

For an already installed infrastructure deployment, use the same resident
preparer with `--preserve-infrastructure`. Metadata must set
`infrastructure_configured=true` and include the observed
`infrastructure_settings`; all existing settings bytes, dedicated SSH files,
sampling tmpfs and other mounts are preserved. No credentials need staging in
this mode. The exact observed executor configuration is still validated, and
the fixed candidate must match the fresh live base image/revision.

Optional `--additional-media-binds ABS_JSON` contains explicit `{source, target,
read_only}` objects for new external media directories. Targets must be under
`/media/`; sources must be outside the resident data root. Existing mount targets
cannot be overwritten. After the verified cold snapshot, missing leaf
directories are created under existing parents as UID/GID 10001; existing
directories are not chmod/chown'ed or cleared. Both Compose views and the guard's
registered external-media binds are updated together. Rollback removes only the
new bindings, keeps existing infrastructure and all media files, and snapshots
the current external-media scope before switching back. The same latest verified
backup retention policy remains active.

Intentional candidate dependency changes can use the image preparer's
`--dependency-changes ABS_JSON`: exact `base_revision`, `revision`, and `files`
mapping changed dependency-input paths to `before`/`after` SHA256. Missing,
additional or mismatched changes are refused; without this input the original
unchanged-dependency behavior remains. This is a fixed-input record, and does not
replace installed Linux, source and unchanged music-runtime verification.

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

Existing non-target container identities and every normalized metadata field
must remain unchanged. Mount array order and tuple/JSON-list representation are
canonicalized. Concurrent non-target additions are recorded in
`non-targets-after.json`; they do not require unrelated NAS work to stop.

For the explicitly inspected TS-180 verification failure, the coordinator owns
the manual recovery. `resident_readback` only prepares a standalone completion
and a new `rollback-canonical-metadata.py`; it starts no owner or remote action.
All stage, original-worker hash, image/container/gateway/state/guard/memory pins
and the same-scope resume-origin evidence directory are required arguments.
Run the generated readback only after manual recovery. It loads the pinned
original definitions without their release entry, verifies the 37-database
snapshot and the original 367 non-targets, and completes the original settings,
user, media, network, runtime and health checks. Outputs use independent
`resume-*` names, including `resume-result.json`; original failure/result files
are not replaced. The separate rollback wrapper is for an explicit future
rollback only: it retains the original controlled rollback/cleanup, injects the
same canonical comparison, and reads the latest same-scope gateway renewal
record. Staging it does not perform a rollback.
The generator requires `--receipt-prefix` (for example `resume2`) and the exact
`--sampling-underlay`. New attempts use new filenames and prefixes, retaining
earlier partial readbacks. Its separate `repair-sampling-mountpoint-PREFIX.py`
is the explicitly reviewed metadata repair for this deployment: it verifies the
fixed running Platform/image, empty host mountpoint and bounded live tmpfs, then
changes only the host directory owner/mode and live tmpfs root mode. It changes
no file content, image, configuration, memory limit or service running state.
The ordinary runtime acceptance continues to require 0700.

## Narrow final music configuration comparison

The reused `media_state()` compares only `account` and `media_target`. The music
release's older final-readback helper adds `music_account`, `music_settings`,
`music_navidrome` and `subscription`, but it still omits the actual music playlist,
Navidrome link and weekly maintenance documents. Do not run its whole key/credential
comparison for this narrow check.

Copy this release's standalone `music_readback.py` into the private remote stage
and invoke it with the existing tooling Python. Resolve both media database paths
through the known resident bind using the existing `host_path` helper; the
`--before` database is the already verified cold-snapshot backup, never an older
music-release snapshot. The coordinator owns this read-only remote invocation:

```text
ABS_NAS_TOOL_PYTHON -B ABS_STAGE/music_readback.py --before ABS_THIS_RELEASE_COLD_BACKUP_MEDIA_SQLITE --after ABS_CURRENT_MEDIA_SQLITE --expect-playlists 43 --expect-links 43
```

The one JSON result can be included in the existing final readback. It reports
counts, per-kind digests, interval/enabled summaries and changed field names;
it outputs no playlist IDs, account/connection values, keys or credentials.
Queries use SQLite `mode=ro`, one read transaction and explicit JSON scalar
extraction, returning no complete document body. It selects only:

| Kind | Stable fields |
| --- | --- |
| `music_account` | account_id, provider |
| `music_playlist` | playlist_id, provider, account_id, source_id, state, interval_seconds, auto_download, quality.level, quality.allow_fallback |
| `music_navidrome_playlist` | link_id, playlist_id, connection_id, remote_id, auto_sync, public |
| `music_maintenance` | enabled, interval_seconds, playlist_interval_seconds |
| `music_settings` | directory, layout, filename |
| `music_navidrome` | connection_id, enabled, music_folder_id, music_directory, local_directory |

Document IDs are compared in memory and only digests/counts are returned.
Missing coverage, missing remote IDs, unexpected counts or stable-field changes
exit 2. Members/order, job/lyric progress, transient link status, revisions and
next-scan/sync timestamps can advance without becoming configuration differences.
The current source has **no separate automatic-lyrics database setting**:
`Media.start()` calls `Music.start_background()`, which registers `music_lyrics`
unconditionally when Media is configured; the lyric weekly check is code constant
604800 seconds. Preserve the already verified installed music source and the
original `settings.media` plus music library directory. This SQL result does not
claim that a background task ran; reuse the prior music runtime acceptance for
that behavior and retain this release's installed-source/readiness checks.
