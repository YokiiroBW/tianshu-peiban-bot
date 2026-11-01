# Registered infrastructure helpers

These fixed JSON subprocesses implement node observation and the Docker/Dockge
control relay. They use the native application's existing infrastructure APIs;
they do not start another application, own application SQL, or accept SSH targets
from model or web arguments. The container includes OpenSSH and PyYAML 6.0.3.

Set `capabilities.infrastructure` in `/config/settings.json`:

```json
{
  "directory": "/data/capabilities/infrastructure",
  "contract_directory": "/opt/tianshu/deploy/core/infrastructure/contracts/node",
  "executor": {
    "command": ["/opt/tianshu/.venv/bin/python", "-I", "-B", "/opt/tianshu/deploy/core/infrastructure/run_node.py", "--config", "/config/infrastructure/executor.json"],
    "directory": "/opt/tianshu/deploy/core/infrastructure"
  },
  "poll_interval_seconds": 30,
  "stale_after_seconds": 120,
  "timeout_ms": 30000,
  "control": {
    "contract_directory": "/opt/tianshu/deploy/core/infrastructure/contracts/control",
    "executor": {
      "command": ["/opt/tianshu/.venv/bin/python", "-I", "-B", "/opt/tianshu/deploy/core/infrastructure/run_control.py", "--config", "/config/infrastructure/control-relay.json"],
      "directory": "/opt/tianshu/deploy/core/infrastructure"
    }
  }
}
```

`executor.json` retains the registered `infrastructure-node.v1` configuration:
`state_dir`, `nodes`, `ssh_command`, `max_concurrency`, `stale_after_seconds`.
Each node has `label/kind/host/port/user/identity_file/known_hosts_file`, optional
`docker={enabled,command,socket_path}` and `stacks_root`. Key and known-hosts
files are explicit read-only deployment mounts; never substitute an operator's
administrative key. Missing materials produce an unavailable observation.

`control-relay.json` has `version="infrastructure-control.config.v1"`,
`observation_config="/config/infrastructure/executor.json"`, its own persistent
`state_dir`, and `nodes={node_id:{command:[fixed remote argv]}}`. Sampling and
relay state directories must differ. Create the container relay directory on
persistent `/data`, owned by UID/GID 10001 with mode 0700. Do not store relay or
remote journals on tmpfs or discard unresolved operation IDs.

On the NAS, install this directory's three launchers and
`tianshu_infrastructure/` under `/volume2/Dockers/tianshu-core-infrastructure`.
The remote helper uses Python >=3.12 and PyYAML 6.0.3; install those dependencies
before use. A fixed relay command can select its environment explicitly:

```text
sudo -n /absolute/shared/python -I -B /volume2/Dockers/tianshu-core-infrastructure/run_remote.py --python /volume2/Dockers/tianshu-core-infrastructure/venv/bin/python --config /volume2/Dockers/tianshu-core-infrastructure/config/remote.json
```

The remote configuration remains `infrastructure-control.remote.v1`: registered
Docker/Compose commands, socket, stacks root, persistent state directory and
project/container/bind protections. It contains no application database. Keep
existing target registrations and protection rules when relocating; a new
environment path does not grant new sudo/SSH access. Detached workers launch
this same installed `run_remote.py`, using the same original operation ID.

After configuration, use the same-origin global administrator context:
`POST /api/v1/infrastructure/catalog/refresh` with `client_request_id`, then
read `/api/v1/infrastructure/view` until the real catalog contains the node.
Register its label/enabled state with `/api/v1/infrastructure/save`, passing
`node_id`, `label`, `enabled`, the new row's actual `expected_revision` and
`client_request_id`. SQL revisions start in the new native owner; do not import
old observation caches or connection revision counters. Acceptance can read
catalogs, node snapshots and inventories without submitting control actions.
