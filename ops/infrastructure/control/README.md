# Persistent Docker and Dockge control

This is the versioned control addition to the existing node observation package.
It shares `SSHTransport`, `run_bounded`, Docker metadata parsing, inventory
paging and the bounded Compose parser. It does not modify the old observation
schemas, configuration or request dispatch. See the [control contract](../../../contracts/infrastructure-control/candidate-v1/README.md).

## Package and launchers

Export **all** of `ops/infrastructure/` from the fixed accepted commit, including `control/compose-wrapper.js` and
`control/compose-probe.js`. Do not export a mutable worktree or only the Python
files. The existing bundle builder supplies its empty `ops/__init__.py`
namespace marker; that file is not a tracked source and must not be requested
from `git archive`. Runtime requirements remain `ops/infrastructure/requirements.txt`.
The relay needs Python 3.11+, its pinned parser and OpenSSH. The remote runner
needs Python 3.11+, that parser and the deployment's Docker/Compose commands.
Dockge execution also uses its existing Node binary. No new service is installed.

Absolute launchers (independent of cwd/PYTHONPATH):

```text
REGISTERED_PYTHON ABS_PACKAGE/ops/infrastructure/control/relay_entry.py --config ABS_RELAY_CONFIG
REGISTERED_REMOTE_PYTHON ABS_PACKAGE/ops/infrastructure/control/remote_entry.py --config ABS_REMOTE_CONFIG
```

Equivalent module entry points are `python -B -m ops.infrastructure.control` and
`python -B -m ops.infrastructure.control.remote`. Both require `--config`.
The remote entry's internal `--worker` is used only to continue an already
journaled operation; it never accepts new operation semantics.

The relay config references the existing observation registration for SSH
identities/host keys and node IDs. Do not copy key material into a control file.
Example (all paths deliberately require deployment substitution):

```json
{
  "version": "infrastructure-control.config.v1",
  "observation_config": "/ABS_EXISTING/observation-config.json",
  "state_dir": "/ABS_BACKED_UP/platform/infrastructure-control-relay",
  "nodes": {
    "registered-nas": {
      "command": ["sudo", "-n", "/ABS_REGISTERED/python", "-B", "/ABS_PACKAGE/ops/infrastructure/control/remote_entry.py", "--config", "/ABS_PRIVATE/control-remote.json"]
    }
  }
}
```

Remote registration for a Dockge context:

```json
{
  "version": "infrastructure-control.remote.v1",
  "node_id": "registered-nas",
  "state_dir": "/ABS_BACKED_UP/platform/infrastructure-control-node",
  "stacks_root": "/ABS_HOST_DOCKGE_STACKS",
  "compose_path_root": "/opt/stacks",
  "docker_command": ["/ABS_DOCKER/docker"],
  "docker_socket": "/var/run/docker.sock",
  "compose_command": ["/ABS_DOCKER/docker", "--host", "unix:///var/run/docker.sock", "exec", "-i", "dockge", "docker", "--host", "unix:///var/run/docker.sock", "compose"],
  "platform_stacks": [],
  "protected_projects": {
    "tianshu-v2-resident": {"owner": "resident-update", "reason": "capacity_guard_ownership", "maintenance_ref": "resident-update"},
    "tianshu-v2-resident-obs": {"owner": "resident-update", "reason": "capacity_guard_ownership", "maintenance_ref": "resident-update"}
  },
  "protected_container_names": {
    "dockge": {"owner": "dockge", "reason": "control_execution_dependency", "maintenance_ref": null}
  },
  "protected_bind_roots": [
    {"path": "/ABS_RESIDENT_ROOT", "owner": "resident-update", "reason": "capacity_guard_mount_ownership"}
  ],
  "operation_timeout_seconds": 1800
}
```

The `exec -i` form is required: proposed Compose and runtime overrides arrive
on stdin. The registered stacks root must be the actual host directory; the
context root must match its existing Dockge mount. Do not rebuild Dockge, add
bind mounts, or relocate existing data to fit this template. Mixed host/Dockge
working-directory labels are recognized only within these two registered roots.
An unknown project directory/config-file identity is a conflict.
The inner Docker socket must address the same registered daemon as the host
collector; include its explicit `--host` as shown. Every execution lease replaces
the registered Dockge name with its freshly inspected full64 ID before sending,
so a replacement with the same name cannot receive the old invocation.

The control state directories are separate from observation sampling and must
be owned by the runner, private (0700), persistent, and included in backup.
Linux tmpfs/ramfs are refused. They contain private 0600 journals, full version
inputs and secret-preserving plans. Do not expose or log those private records.
Only sanitized protocol projections leave the node.

## Execution and recovery

The node durably accepts a semantic operation key before launching a detached
worker. Project reservation and OS locks are shared between container and stack
actions. Launcher PID plus process creation identity handle delayed starts;
there is no age-based assumption that a child has died. A late worker checks
that its original reservation still belongs to it before sending any command.

Docker exec is a client, not proof that its container-side command has ended.
For this registration shape the runner reads the fixed JS files and invokes
Dockge's Node with `-e`. The wrapper starts Compose in its own process group,
enforces its deadline, kills remaining group members, and writes a small
fsynced receipt containing state/PID/creation tick/exit code only. It keeps
draining child pipes if the exec client's output closes. Receipts live in the
single private `.tianshu-control-execution` directory under the **already
mounted stacks root**, without any Compose file. Control discovery explicitly
excludes that directory; existing read-only discovery finds only standard
Compose filenames and therefore also excludes it. It is not a stack or a new
Docker bind. No stdout, `.env` value or Compose input is stored there.

An uncertain local result remains `unknown`. Readback checks the original
completion receipt or the original PID+creation tick in Dockge; unavailable
proof keeps `execution_active:null` and the project reservation. A confirmed
dead command permits a new user operation but never replays the old command.
Node `unavailable` after acceptance is also unknown, including storage failures;
only explicit invalid/unsupported/conflict rejections are known to be unaccepted.

Simple stack lifecycle uses `compose start/stop/restart`, preserving existing
containers and their mounts. First creation/configuration/image update uses a
plan and `up --detach --no-build --pull never` with pinned image IDs. Creation
and configuration respect `pull_policy:never` and existing local image IDs;
the normal missing policy reuses local images. A distinct image-update plan is
an explicit pull. Pull failure leaves the old containers running and does not
delete files, volumes or data. Restore pins saved actual image IDs. A missing
saved image fails explicitly; data written by applications is never rolled back.

Relative binds are resolved to real host paths for preview and platform-owned
configuration. External image update keeps canonical Compose untouched and uses
a reviewed stdin override; an unexpected change from the existing actual bind
source blocks the plan. Applied image-source/ID provenance is kept so refresh
and later configuration plans preserve a restored image. Plans bind the input
inode/content/owner/mode, root identity and container identity set. Long pulls
repeat those checks before applying.

Restore preview stages only its saved `.env`/referenced configuration in a
private temporary directory inside that stack's existing mapping, rewrites
validation references to those staged files and removes them afterward. Live
Compose/.env files are untouched during preview. If a saved version says an
input was absent but it was later added, restoration is explicitly blocked;
the controller does not delete a newly supplied file or validate with one
version then execute another. Versions have no inferred `current` flag: atomic
replacement changes inode fingerprints, so revision equality is not a reliable
runtime-version identity.

No generic operation disables the resident guard or restarts its owner. The
registered protected bind roots also prevent ordinary stacks from triggering
the guard's foreign-deployment-mount rule, including recognizable symlink aliases.
Unsupported includes/build/reference topology and ambiguous multiple Compose
files remain explicit; they are not replaced with empty inventory or success.

## Isolated checks and consumer joint fixture

```text
python contracts/infrastructure-control/candidate-v1/validate.py
python -m unittest discover -s tests/infrastructure -v
```

`test_control_node_wrapper.py` additionally runs real Node/process-group checks
on Linux. Windows skips those and may lack symlink creation privileges. These
checks use a synthetic Docker/Compose subprocess, never the real daemon.

For a platform joint test, export `tests/infrastructure/{helpers.py,
control_fixture.py,control_ssh.py}` together with the fixed runtime package,
install the existing test-only requirements and start:

```text
python tests/infrastructure/control_ssh.py --directory ABS_NEW_FIXTURE_DIRECTORY --ready-file ABS_READY_JSON
```

Keep stdin open. The ready JSON supplies `control_command`,
`control_config_path`, `observation_config_path`, `node_id`, exact container ID
and stack ID. It is actual OpenSSH + relay CLI + remote CLI + persistent node
worker + synthetic Docker subprocess. Close stdin after operations settle.
This is not NAS/Docker-daemon acceptance; the coordinator must record that
separately on an explicitly named isolated stack.
