# Fixed Dockge control delivery

TS-208 extends the existing source ZIP, image controller and resident cold
release. It does not create another deployment service. The coordinator alone
runs the generated NAS payloads and explicit acceptance commands.

The observation ZIP remains the original accepted source
`47564d07257a43ad99dde63d6ea96cf8c169be5a`, SHA256
`9aac3dc090a0317804b5787dd5136b4916ee686b718a11b18b74e976806cebf5`.
Control is a separate ZIP from the complete fixed TS-205 source. It includes
every declared `ops/infrastructure` file, both fixed Node scripts, the old
observation schemas, the matching control schemas, synthetic examples, the
same hashed dependency lock, and package launchers. The builder adds the empty
`ops` namespace marker; it is not a source file in TS-205's Git tree. No test
SSH server, keys, real settings, state, runtime venv or wheel is in either ZIP.

## Build and prepare offline inputs

Use the full accepted TS-205 SHA and new absolute artifact filenames:

```text
python -B -m deploy.infrastructure.control_bundle build --repo ABS_ROOT_GIT_REPOSITORY --commit FULL_TS205_SHA --output ABS_NEW_CONTROL_ZIP
python -B -m deploy.infrastructure.control_bundle verify ABS_CONTROL_ZIP
```

The control builder shares `bundle.git_files`, `write_archive` and
`read_archive` with the observation builder. Git blobs are unaffected by dirty
worktrees or export attributes. Manifest hashes cover exactly the fixed file
list, source SHA, protocol, dependency metadata and each control schema.
Repeated fixed inputs produce the same ZIP bytes. Runtime requirements remain
Python 3.11+ and PyYAML 6.0.3. `run_control.py --version` works before parser
installation; normal calls explicitly check the pinned parser version.

The NAS's already authorized Python need not have PyYAML. Download a wheel for
the **observed** interpreter/CPU/libc, verified against the existing lock. For
the observed CPython 3.12 x86_64 Linux target:

```text
python -m pip download --only-binary=:all: --no-deps --require-hashes --platform manylinux_2_17_x86_64 --python-version 312 --implementation cp --abi cp312 -r deploy/infrastructure/requirements.lock --dest ABS_PRIVATE_WHEEL_DIRECTORY
```

Copy `templates/control-install.example.json` to a separate private input.
Every `ABS_`, `UNIQUE`, node ID and package SHA placeholder needs an explicit
registration. The example does not contain a runnable production connection.
Its observation reference reuses the installed SSH registry; no key contents
belong in control settings. The remote command calls the existing authorized
Python through the existing `sudo -n` rule, then the fixed `run_remote.py`
bootstrap execs the dedicated pinned venv. No new sudo rule is installed.

The immutable remote code directory is `remote_code_root/PACKAGE_SHA256` and
the separate venv is `remote_code_root/PACKAGE_SHA256.venv`. Both exact paths
are part of the supplied command/registration. Versions are never overwritten
while a detached worker might still import their source. Use distinct config
filenames for a later code/command registration and retain old versions.

```text
python -B -m deploy.infrastructure.control_install --package ABS_CONTROL_ZIP --configuration ABS_EXPLICIT_PRIVATE_CONTROL_JSON --wheel ABS_MATCHING_LOCKED_PYYAML_WHEEL --output ABS_NEW_CONTROL_INSTALL_JSON
```

This writes local reviewed input only. The generated input contains the fixed
ZIP and verified wheel bytes plus explicit non-secret configuration references;
keep it out of Git. Its small receipt reports hashes, never config contents or
credential material. `resident.prepare` verifies it again and checks that the
candidate image contains the exact same control package.

## Existing image and resident release

Add `--control-package ABS_CONTROL_ZIP` to the existing `deploy.infrastructure.image`
command documented in `DEPLOYMENT.md`. Keep the original accepted node ZIP.
The historical `--music-commit` / `--music-image` arguments mean the **fresh
running Platform source and full image ID**, including all intervening books,
audio, comics and dependency changes. The candidate must descend from that
source. Supply `--dependency-changes` only for the reviewed exact before/after
lock changes, as in the existing image workflow.

The existing repository Dockerfile still builds the full application, frontend,
music tools and runtime. Control adds image-owned read-only code at
`/opt/tianshu-infrastructure-control`, sharing the existing isolated node venv.
The controller verifies all installed control hashes, schema pins and file
ownership, compiles every packaged Python source, starts both absolute
launchers and runs an actual empty catalog in a temporary private `/tmp` tmpfs.
It uses no production data mounts or network. All previous source/web/node,
image configuration and unchanged music-runtime checks remain active.

Prepare with a newly captured ready baseline and metadata, the exact observed
read-only executor JSON, current recovery input, and existing release mechanism:

```text
python -B -m deploy.infrastructure.resident --baseline ABS_FRESH_BASELINE --metadata ABS_FRESH_METADATA --build-result ABS_VERIFIED_BUILD_RESULT --mechanism-directory ABS_EXISTING_RELEASE_MECHANISM --recovery ABS_CURRENT_RECOVERY_INPUT --executor-config ABS_EXISTING_OBSERVATION_CONFIG --guard-repo ABS_ROOT_GIT_REPOSITORY --guard-commit FULL_ACCEPTED_GUARD_SHA --stage /volume2/tianshu-v2-resident-updates/EXPLICIT_NEW_STAGE --output ABS_NEW_PRIVATE_RELEASE_DIRECTORY --preserve-infrastructure --control-install ABS_CONTROL_INSTALL_JSON
```

The baseline's complete current Compose volumes, tmpfs, memory limits,
environment, other services and all infrastructure fields are retained.
Control adds/replaces only the explicitly supplied `infrastructure.control`.
It adds no Platform Docker socket, code bind, credential bind or sampling
mount. Its relay state must map through a current persistent Platform bind;
both journals must be separate directories inside the existing resident
`data/platform` backup scope. The sampling tmpfs is refused for control state.
Registration must protect both resident projects, Dockge and the resident bind
root. The actual guard and complete mount checks are preserved.

The existing stage/launch/poll payloads run the existing stopped-owner cold
snapshot and independent disabled restore verification first. Only afterward
does the worker install the immutable remote source, create its separate venv,
install the embedded wheel with `--no-index --require-hashes`, write new private
config files, and create the two 0700 journals. Existing matching config files,
code files, venv and journal contents are reused. Conflicting existing inputs
produce an explicit error; no source or journal is cleared or replaced. Failed
private installation staging is retained for inspection. The original Python
environment and Dockge image/container/mounts are unchanged.

The small `control-installation.json` receipt reports source/package/config
hashes and preservation facts. After startup the existing runtime check also
launches the real configured relay catalog and checks relay persistence as the
actual Platform UID. Catalog success is installation evidence, not lifecycle
acceptance. `result.json` keeps `control_real_operations_accepted=false`.

Use the existing `rollback.py` for a fresh cold-backed rollback. It restores
the prior control setting if one existed, or removes only the newly added
control key on first installation. It preserves later unrelated settings,
all current ledgers, journal files, immutable code, venv and dedicated config
files. It never claims database/data rollback. The shared one-backup retention
policy remains unchanged.

## Node process proof without host Node or Dockge Python

Stage the fixed TS-208 deployment tooling/module and verified ZIP in the
coordinator's existing tooling area. This explicit command uses the freshly
inspected **full64 Dockge container ID**, its existing Node, and a new synthetic
temporary directory. No Dockge rebuild, mount or Python installation is needed:

```text
ABS_REGISTERED_NAS_PYTHON -B -m deploy.infrastructure.control_node_check --package ABS_VERIFIED_CONTROL_ZIP --docker-command-json '["ABS_EXISTING_DOCKER","--host","unix://ABS_EXISTING_SOCKET"]' --dockge-id FULL64_FRESH_DOCKGE_ID --directory /tmp/tianshu-control-proof-EXPLICIT_UNIQUE_LOWERCASE --output ABS_NEW_NODE_PROOF_JSON
```

The driver feeds the package's hashed wrapper/probe source to fixed Node-only
synthetic child/grandchild tests. It proves timeout kills the original process
group, an unmatched/dead process probe is false, and closed exec output still
leaves the original fsynced completion receipt. It does not run Docker/Compose
mutations inside Dockge. The receipt clearly records this synthetic scope.

## Explicit isolated Docker lifecycle acceptance

`control_acceptance.py` is a standalone standard-library driver, copied from
the fixed TS-208 commit. It has no connection or production target defaults.
Its private JSON input requires exactly:

```json
{
  "version": "infrastructure-control.acceptance.v1",
  "command": ["ABS_DOCKER", "exec", "-i", "FULL64_FRESH_PLATFORM_ID", "/opt/tianshu-infrastructure-venv/bin/python", "-I", "-B", "/opt/tianshu-infrastructure-control/run_control.py", "--config", "/etc/tianshu/infrastructure/EXPLICIT_INSTALLED_RELAY_CONFIG.json"],
  "directory": "/ABS_EXISTING_COORDINATOR_STAGE",
  "docker_command": ["ABS_DOCKER", "--host", "unix://ABS_SOCKET"],
  "node_id": "EXPLICIT_REGISTERED_NODE",
  "project_name": "tianshu-control-acceptance-EXPLICIT_LOWERCASE_SUFFIX",
  "image": "EXPLICIT_AVAILABLE_BUSYBOX_TAG_OR_REPO_DIGEST",
  "expected_image_id": "sha256:FULL64_OBSERVED_LOCAL_IMAGE_ID",
  "replacement_image": "EXPLICIT_SECOND_AVAILABLE_SH_SLEEP_IMAGE_TAG_OR_REPO_DIGEST",
  "replacement_image_id": "sha256:FULL64_DISTINCT_SECOND_LOCAL_IMAGE_ID",
  "stacks_root": "/ABS_EXISTING_HOST_DOCKGE_STACKS",
  "marker_directory": "/ABS_EXISTING_TEST_PARENT/EXPLICIT_NEW_MARKER_DIRECTORY",
  "operation_timeout_seconds": 600
}
```

```text
ABS_REGISTERED_NAS_PYTHON -I -B ABS_FIXED_TS208_CONTROL_ACCEPTANCE_PY --configuration ABS_EXPLICIT_TEST_JSON --output ABS_NEW_PRIVATE_EVIDENCE_DIRECTORY
```

The project must have the literal `tianshu-control-acceptance-` prefix and
must not already exist. The marker directory must also be new under an
existing explicit parent, outside the new stack directory and protected
resident root. Both explicit test images must have their expected distinct
local IDs and provide `sh`, `printf`, `test` and `sleep` (the observed busybox/
alpine images are suitable). Creation/configuration uses `pull_policy:never`.
The explicit image-update action performs its actual pull, so choose a
resolvable second test image reference.

The driver creates one marker service, verifies container stop/start/restart
and stack stop/start/restart retain the exact container ID, deploys a second
configuration with the second actual image ID, updates that image, then restores
the prior executed config and original image. Update records the actual before/
after IDs and whether they changed; a successful same-ID pull is not called a
version upgrade. Every completed operation is replayed with the same ID and verified to
leave target identity/state/configuration unchanged. Runtime and health are
checked separately; this service has `not_configured` health. The persistent
marker hash must survive all operations. Restore must read back the original
configuration revision and image ID; it makes no data rollback claim.

Before/after full non-target IDs, state, image, mount and configuration hashes
are recorded without raw environment values. Existing non-target containers
must be preserved; unrelated additions are reported. On a send timeout the
driver reads the original operation ID, never blindly resubmits a mutation.
Unknown command results cannot pass as success. It retains operation intents,
sanitized responses, receipts, test stack and marker on both success and
failure. It never performs `down`, remove, prune, volume/data deletion or
cleanup of existing services. Actual guard/media/config preservation and the
production webpage are separately checked by the coordinator.
