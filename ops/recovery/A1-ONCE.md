# A1 single-attempt coordinator

`python -B -m ops.recovery.a1_once` coordinates one prepared NAS A1 scope.
Importing the module and omitting `--execute` cause no mutation. The
`--emit-code-lock ABSOLUTE_CODE_ROOT` command is read-only. The run command
requires `--config ABSOLUTE_SCOPE_ROOT/inputs/a1-once.json --execute` and
refuses an existing receipt directory, restored target, clone target, or permit.

## Preparation boundary

A separate preparation step must create a **new** registered A1 source with
synthetic semantic receipts and a placeholder clone input package before
short-lived origins are issued. This coordinator does not create that source,
initialize credentials/TLS, or infer expected assertions from a previous scope.
The old `a1_r2h_*` scripts have hardcoded r2h paths and must not be reused.
The operator must independently verify four healthy core owners, five running
observability owners, two unknown turns, revocations, forgetting, and four
Gateway usage attempts. These prerequisites are not established by the
driver's offline fixture.

The `a1-once/1` JSON config contains exactly these fields:

- `scope_root`, `scope_id`, `code_root`, `python`, `docker`: absolute paths and
  the fresh scope UUID. Both executables must be fixed absolute files.
- `source_name`, `restored_name`, `clone_name`, `inputs_name`, `backup_name`,
  `permit_name`, `receipt_name`: simple names. The config path is fixed as
  `scope_root/inputs/a1-once.json`.
- `projects`: exact `core` and `observability` project names, with the latter
  equal to the core name plus `-obs`.
- `networks` and `ports`: `source` and `clone` arrays, respectively 5+7
  disjoint `/28` subnets and 6+6 distinct loopback ports. They must match the
  source runtime identity and clone Compose. No value is defaulted; the last
  `/26` of `10.205.48.0/24` is excluded.
- `registration_sha256`, `source_manifest_sha256`, `source_runtime_sha256`,
  `source_deployment_sha256`, `model_template_sha256`,
  `initial_inputs_sha256`: exact file hashes. The initial input index must
  enumerate and hash every input file.
- `code_tree_sha256` and `code_lock_sha256`: digest of every file below
  `code_root/ops` and `code_root/deploy`, and SHA of
  `scope_root/inputs/a1-code-files.json`. The latter records an exact path to
  hash map and the tree digest. Generate its content with
  `--emit-code-lock ABSOLUTE_CODE_ROOT` and copy its `tree_sha256` into the
  config. These private
  inputs are outside Git.

The preliminary clone package must have six fixed functional assertions in
order. The seal child publishes synthetic model version 5, issues two Platform
origins, updates bound assertions and Gateway env, sets one unknown deadline,
and seals their file hashes. Every Platform call uses the configured Docker
executable. Partial sealing is one-way and never retried.

## Run and failure behavior

The parent process invokes separate public child CLIs in this order: seal;
`linux-rehearse` with nine-owner stop, backup, disabled restore, and full-fact
verification; live route/network/project/loopback port/memory preflight;
one independent permit with frozen restore facts and current Platform origin
rows; `drill-clone` plan; exactly one `drill-clone --execute`. The execute
retains its own identity, owner, restore, resource, 180-second origin, and
one-use claim checks. A plan result is a static check and explicitly does not
certify owner state or recovery facts.

Each child has a finite timeout. The outer timer signals only the child CLI
process with SIGTERM, then waits up to 90 seconds for its normal cleanup. It
never signals the Docker subprocess group or hard-kills a container. A child
still running after that wait is `stop_unconfirmed`; no other writer starts.
If seal fails after its child exits, the driver attempts one exact registered
owner `linux-rehearse` stop/backup/restore and reports that outcome separately.
A failed or incomplete drill execute is never replayed.

The private receipt directory is created mode 0700 once. Each phase records
UTC start/end, monotonic timestamps, command SHA, result SHA, exit code,
validation state, and raw stdout/stderr SHA. Up to 1 MiB per raw stream is
kept mode 0600; oversized output is marked truncated and fails the phase.
Public output contains only stage/status summaries.

The offline fixture exercises the real coordinator, static binding, seal and
permit parameter generation, preflight logic, command construction, and phase
state machine with injected product/Docker adapters. It cannot demonstrate
live nine-owner shutdown, NAS routing/resource state, or six HTTPS assertions.
Those remain admission requirements of the real public CLIs. No fresh scope,
permit, or container is created by the fixture.
