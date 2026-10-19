# Infrastructure control, candidate v1

TS-205 produces `infrastructure-control.v1`; TS-206 consumes a fixed commit.
The existing `infrastructure-node.v1` request, response, configuration and
read-only entry point are unchanged. Candidate status is not a deployment claim.

Local CLI: `python -B -m ops.infrastructure.control --config ABS_CONTROL_CONFIG`.
One UTF-8 JSON request on stdin, close stdin; one JSON response and newline on
stdout. The process exits 0 for a protocol/domain result and 2 for CLI/output
failure. Request fields are `version`, `request_id`, `node_id`, `operation`,
`params`, `timeout_ms` (500–30000). Only `control.catalog` uses null `node_id`.
The response retains those identities and the existing envelope conventions,
with this new version and an additional `conflict` status. Errors do not include
raw subprocess output, credentials or exception strings.

| Operation | Params | Response data |
| --- | --- | --- |
| `control.catalog` | `{}` | `nodes`: registered ID, label, capabilities |
| `stack.list` | optional `limit` (1–200), `cursor` | `stacks` |
| `stack.inspect` | `stack_id` | `stack` with redacted editable `compose_content` |
| `stack.plan` | action-specific fields below | `plan` |
| `operation.submit` | `operation_id`, `action`, `target` | `operation`, `replayed` |
| `operation.get` | `operation_id`, optional `reconcile` (default true) | `operation`, `replayed` |
| `operation.list` | optional `limit`, `cursor` | `operations` |
| `stack.versions` | `stack_id`, optional `limit`, `cursor` | `versions` |

Plan actions: `create` requires `project_name` and `compose_content`; `deploy`
requires `stack_id`, `expected_revision`, `compose_content`; `update` requires
`stack_id`, `expected_revision`; `restore` requires `stack_id`,
`expected_revision`, `restore_version_id`. `create` derives its directory from
the registered Dockge root and project name. Existing stack IDs use the same
`stable_id("stack", node_id, directory_name)` as read-only discovery. A create
cannot adopt an existing directory/project. Only platform-owned stacks can
change configuration or restore versions; external registered stacks support
lifecycle operations and image-update plans. Ownership is deployment-registered
or recorded after a platform create, never accepted from a request.

Direct submit actions: `container.start/stop/restart` target exact full64
`container_id`; `stack.start/stop/restart` target `stack_id` and current
`expected_revision`; `stack.apply` targets a previously persisted `plan_id`.
Image updates use a preview plan, then `stack.apply`. No delete, prune, volume
removal, arbitrary command, host, socket, filename or SSH input is accepted.

Stack projections include ownership, current input `revision`, safe actions,
and `protection {protected, owner, reason, maintenance_ref}`. Protected projects
and control-execution dependencies cannot use generic mutation actions. The
registered maintenance reference identifies their existing owning workflow;
it is not an instruction to disable the guard. No maintenance endpoint means
the projection explicitly explains why the action is unavailable.

Plans contain a base input fingerprint, proposed revision, creation and expiry
time, sanitized `config_diff`, `ports`, `mounts`, `images`, `warnings`,
`blockers`, and an optional restore version. A `blocked` plan cannot execute.
Compose files are authoritative; `.env` and referenced input identity, content,
owner and mode contribute to the revision. External edits or replacements,
including an identical-content inode replacement, invalidate the old plan.
Execution repeats version, identity, ownership, conflict and protection checks
under a same-project lock. Only the selected registered directory is written.

`compose_content` returned by inspect contains opaque `{{tianshu.secret:...}}`
scalar markers. Keep markers intact to preserve existing values; markers are
bound to that stack and current revision. Interpolation such as `${API_TOKEN}`
is retained as a reference. Raw `.env`/secret contents never leave the node.
Plan diffs use the same masks and show configuration changes, ports and mounts.
Saved versions are private node files and preserve file modes/roles and image
IDs. Restore concerns configuration and runtime images; `restores_data:false`
explicitly excludes changes already written to databases/bind mounts/volumes.

`operation_id` is the platform's durable mutation key, independent from the
per-call `request_id`. The node journals acceptance before launching a detached
worker and persists `accepted/running/succeeded/failed/unknown/rejected`, phase,
command result and observation separately. A repeated ID with the same semantic
request returns the original receipt; a different request returns
`operation_id_conflict`. Once a send may have occurred, transport timeout or
disconnect is `unknown`; use `operation.get` to retrieve/reconcile the original.
Neither get nor replay launches another mutation. On worker interruption,
readback reports observed state without inventing a successful command.

`command.state` is `not_sent/sent/succeeded/failed/unknown`, with nullable exit
code. Runtime state is `running/stopped/partial/absent/unknown`. Health is
`healthy/unhealthy/starting/not_configured/mixed/unknown`; a running container
without a healthcheck is **not** healthy. Observation does not convert an
unknown command into a proven successful execution. Output is bounded and raw
command stdout/stderr is not part of this protocol.

Local config uses `infrastructure-control.config.v1`: `observation_config`
references the existing node registration (the sole SSH credential source),
`state_dir` is a separate persistent relay journal, `nodes.<id>.command` is the
fixed remote runner argv. Remote runner:
`python -B -m ops.infrastructure.control.remote --config ABS_REMOTE_CONFIG`.
Its deployment-owned `infrastructure-control.remote.v1` file registers
`node_id`, persistent `state_dir`, `stacks_root`, Docker argv/socket, complete
Compose argv (including any explicit Docker host), `platform_stacks`, protected
projects/container names and operation deadline. The remote package requires
Python 3.11+ and the existing pinned YAML parser. Paths/argv have no production
defaults. Both journal directories must be durable local filesystems, outside
the sampling tmpfs and shared by invocations; the remote one belongs in the
existing backup scope. Remote worker lifecycle is independent of SSH stdin.

Run `python contracts/infrastructure-control/candidate-v1/validate.py` for the
schemas and synthetic valid/invalid examples. No example contacts a device.
