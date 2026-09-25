# A1 single-attempt coordinator

`python -B -m ops.recovery.a1_once` coordinates one prepared NAS A1 scope.
Importing the module and omitting `--execute` cause no mutation. The
`--emit-code-lock ABSOLUTE_CODE_ROOT` command is read-only. The run command
requires `--config ABSOLUTE_SCOPE_ROOT/inputs/a1-once.json --execute` and
refuses an existing receipt directory, restored target, clone target, or permit.

## Preparation boundary

From the exact `scope_parent/tooling` code directory,
`ABS_PYTHON -B -m ops.recovery.a1_prepare --config ABSOLUTE_PREPARATION_JSON`
checks a private `a1-preparation/1` plan without writing. `--execute` creates
only the specified fresh scope, deriving its candidate manifest by changing
the pinned original's `web_text_dialogue.enabled` from true to false. It
initializes the source bundle, fresh TLS and credentials, observability, and
nine explicitly fictional product input files. Its config binds a per-file
`ops`/`deploy` code lock, original manifest, resource profile, fixed
observability Git commit, four fixed product Git commits under `projects_root`,
Gateway import trees, source/clone project names, twelve distinct `/28`
networks in the assigned pool, and twelve loopback ports. The preparation
config explicitly supplies the allocation file's absolute path and expected
SHA256. Both preparation and driver verify the original file bytes, its
scope name, execution UUID, purpose-ordered networks and ports. The source
attempt records the observed preparation-config SHA; clone preparation
requires that same SHA and carries it into the driver config and attempt
receipt. The coordinator separately fixes the expected config SHA before
upload and checks plan and execution receipts against it. Source Companion,
Memory and Gateway API ports are fixed by the candidate at 19512/19513/19514.
The config
lives at `scope_parent/preparations/scope_name.json`; its lock is the adjacent
`scope_name.code-lock.json`. Values must be explicitly supplied. There is no
default allocation. A partial failure leaves the scope for inspection and
cannot be retried into that scope.

From the same locked code directory,
`ABS_PYTHON -B -m ops.recovery.a1_source_flow --config ABSOLUTE_PREPARATION_JSON`
plans the source run. `--execute` checks live allocations, then makes one
source attempt through the public product CLIs and HTTPS APIs: Memory
migrations, nine owner startup, fictional v3 turns and Memory facts,
forget/retract/model revocation, v4 offline controls, Gateway usage readback,
runtime identity, and `linux-prepare` authority registration. It uses the
configured Docker executable throughout. A failed attempt retains its
private receipts and cannot be repeated in that scope. The offline fixture
checks action order; an actual Linux/NAS run is required to establish product
and owner facts.

Platform fanout acceptance starts asynchronous Companion work. The source
entry polls its authorized web snapshot until both expected turns have one
unknown reply and `closed_unknown` state, with no active turn or collector.
Each turn's message ID and revision must match its registered input and the
fanout admission. A second read must preserve turn and reply identity before
the entry binds the observed Memory scope and rebuilds Memory. A failed turn,
changed final snapshot, or bounded wait expiry stops the source attempt. The
later Companion source-facts read and trusted Memory commit remain separate
checks; an automatic outbox entry marked unknown is not a Memory commit.

From that same code directory,
`ABS_PYTHON -B -m ops.recovery.a1_clone_prepare --config ABSOLUTE_PREPARATION_JSON`
checks a read-only clone plan after source registration. `--execute` verifies
the source semantic receipts, two Companion route IDs, Gateway usage CLI
report, model template, and registered source. It creates a separate clone
input package with fresh TLS and mapped credentials, six fixed assertions,
private Gateway probe, the code lock, and `inputs/a1-once.json`. It does not
create or run the clone. Partial output is one-way and requires a fresh scope
to try again. The old `a1_r2h_*` helpers contain fixed r2h paths and must not
be reused.

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
  pinned allocation, source runtime identity and clone Compose. The last
  `/26` of the assigned `/24` remains unallocated.
- `allocation_file`, `allocation_sha256`, `execution_id`: the explicitly
  supplied absolute allocation path, its SHA256, and its execution UUID.
  The file must reside beside the preparation config under
  `scope_parent/preparations`. The coordinator supplies and records the
  config and allocation SHA values; the code never chooses a default file.
- `preparation_config_sha256`: the SHA of the preparation JSON used by the
  source attempt. The driver verifies the same bytes and records this digest
  with its own config and allocation digests in the one-attempt receipt.
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
  inputs are outside Git. The actual loaded entry and recovery modules must
  resolve inside the locked `scope_parent/tooling` tree.

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

Each child has a finite timeout; seal allows 240 seconds for four bounded
Platform calls and local input sealing. The outer timer signals only the child CLI
process with SIGTERM, then waits up to 90 seconds for its normal cleanup. It
never signals the Docker subprocess group or hard-kills a container. A child
still running after that wait is `stop_unconfirmed`; no other writer starts.
Seal writes a private, durable completion marker only after the Platform
publication and both issue calls return synchronously and their three receipts
are stored. The marker binds this scope, the driver's one-use attempt UUID,
and all three receipt hashes. Normal seal success requires that marker too.
If seal fails after its child exits and this marker verifies, the driver
attempts one exact registered owner `linux-rehearse` stop/backup/restore and
reports that outcome separately. A missing or invalid marker never allows a
second writer, even if the seal CLI exited.
A failed or incomplete drill execute is never replayed.

The private receipt directory is created mode 0700 once. Each phase records
UTC start/end, monotonic timestamps, command SHA, result SHA, exit code,
validation state, and raw stdout/stderr SHA. Up to 1 MiB per raw stream is
kept mode 0600; oversized output is marked truncated and fails the phase.
Public output contains only stage/status summaries.

The offline fixture exercises static source creation, real bundle
initialization with local fixed contract bytes when available, synthetic input
generation, clone placeholder creation, static binding, and the real driver
control flow with injected product/Docker adapters. It cannot demonstrate
live source semantics, nine-owner shutdown, NAS routing/resource state, or six
HTTPS assertions. Those remain admission requirements of the real public CLIs.
The fixture creates no NAS scope, permit, or container.
