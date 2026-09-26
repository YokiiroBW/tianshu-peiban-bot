# NAS resident real inputs and one-shot runner

## Goal and scope

Prepare real candidate inputs and a single Linux orchestration command for
the fixed `/volume2/tianshu-v2-resident` allocation. This branch does not
write to the NAS or start services. Base: `1fcd3fa9c51366b2ff8c50aaca2a90ac4dbc410b`;
branch: `codex/nas-resident-inputs-20260926`.

## Changes

- `real_inputs.py` creates distinct protected credentials and a private CA,
  then renders the reviewed allocation with the nine real NAS RepoDigests.
  The CA signing key is excluded from transferable inputs.
- `capacity_inputs.py` renders the A2 config and systemd unit from a real A3
  export, with complete nine-service bind identities and 20 GiB limits.
- `run_candidate.py` checks the live host, reviewed input hashes, installed
  inactive A2 unit, actual first/final export and capacity config, then calls
  the public A1/OBS/A3 interfaces. Its optional full-start phase checks the
  original Platform ID and origin expiry between stages and arms A2 only after
  all nine services are running. Ordinary failures after activation trigger
  serial, exact-ID fail-closed cleanup. An uncertain CLI or stop state requires
  manual inspection; no automatic retry, SIGKILL or deletion occurs.
- `REAL-DEPLOYMENT.md` gives paths, hashes, commands and failure boundary.

## Actual local verification

- Created `.runtime/nas-resident/prepared-real-v1` from independent random
  credentials, a private CA and the nine NAS-observed image RepoDigests. No
  `ca.key` was copied into transferable output.
- Product TLS checks verified all four DNS and static-IP SANs. OBS `verify_tls`
  verified five server handshakes, including Loki client authentication.
- `load_manifest` and `check_contracts` passed against original-byte root
  contracts. With the local POSIX and ownership gates bypassed only for the
  Windows harness, the actual public A1 prepare, OBS configure and A3 export
  completed with the real inputs. This is offline export evidence, not a live
  NAS activation.
- Generated `.runtime/nas-resident/capacity-prepared-v2` from that export and
  A2 fixed commit `1edf794e8ee7936f41e97fd01c6e3190c2d15bdd`;
  JSON Schema validation passed. Config SHA-256:
  `c0cf7314a5bd987182728b2679ba21cde9d79d0dfedf5d2f88ae0ab235c16b26`.
  Rendered unit SHA-256:
  `77620ca2c757484a09288bf52a427a94f316757219987bb3a4e8c914181d1f63`.
- Python 3.12 `compileall` and the three CLIs' `--help` completed; staged
  diff whitespace check completed.

## Remaining live work and risks

Coordinator integrates A2 and this branch, verifies the final tooling Git
objects, copies only `.runtime/nas-resident/prepared-real-v1`, `plan.real.json`,
`capacity-prepared-v2` and original contracts into a fresh NAS tooling root,
and installs the byte-identical systemd unit inactive. Before running, repeat
the live host preflight. A4 reviews the actual final lock, both Compose files,
bundle, live administrator login and A2 ready status before acceptance.

The original Platform assertion lasts 300 seconds. The runner keeps its
identity and expiry checks within one continuous process; a forced process
kill before A2 arm cannot execute Python cleanup and needs the coordinator's
immediate exact-ID inspection/stop. No provider is configured, so model
dialogue remains unavailable and `release_ready` remains false.

See `ops/resident_install/REAL-DEPLOYMENT.md` for the copy layout and command.

## Post-prepare NAS correction

The first NAS run completed A1 `prepare` but the runner failed while parsing
the captured receipt because `_command` returned the stdout stream object.
The follow-up returns stdout bytes, parses complete JSON, and provides an
explicit `--resume-after-prepare` path for the exact recorded evidence and
bundle SHA. It uses a new evidence directory, checks the original receipt and
bundle read-only, then skips `prepare`. Default fresh-root checks remain in
force. The isolated real child JSON receipt regression test passes. NAS
continuation and live acceptance remain with the coordinator.

The initial resume gate used A1's later activation check, which expects OBS
data directories that are only created during OBS configuration. The narrow
correction checks the actual post-prepare layout: exactly four empty product
directories under each of `data/` and `logs/`, with no OBS or reports tree.

## Pre-migration schema-2 NAS correction

The next NAS attempt completed OBS configuration, preflight and first A3
export. It failed at `memory_schema_2` before any migration because Compose
v2.20.1 rejects `docker compose run --pull never`. The coordinator confirmed
exact container stop, no project networks, 13 empty mutable directories, no
issued Platform origin and no final export. The four original A1 report files
and prior runner evidence remain fixed by SHA-256.

The narrow correction removes `--pull never` from the three Compose `run`
sites while retaining it for `up`. Each product CLI `run` checks the pinned
local RepoDigest first. A failed command now leaves bounded private stdout,
stderr, return code and hashes. A product-command timeout propagates
unconfirmed effects so the runner does not automatically stop uncertain
containers. The exact `--resume-after-schema2` path checks fixed input hashes,
prior child failure and cleanup receipts, empty mutable state, original A3
export, local images and Docker occupants before a single resumed activation.
The precheck uses automatically removed temporary export directories without
changing deployment state. New result/failure markers preserve the original
failure files. Command and precise paths are in `ops/resident_install/REAL-DEPLOYMENT.md`.

Local verification: scoped resident-install and runner tests, Python 3.12
compile, CLI help, full changed diff and whitespace check. These are local
checks; NAS continuation, remaining service startup, A2 arm and A4 live
acceptance are still coordinator-owned and not claimed here. No NAS write,
push or merge was performed in this worktree.

## Platform static-IP preflight correction

The supervised schema-2 attempt completed both Memory migrations and started
a healthy Platform, then `platform_preflight` failed because Compose `run`
created a second Platform container on its occupied static IP. The coordinator
exact-stopped the original container; this branch did not operate the NAS.
Neither migration nor the schema-2 resume may be replayed.

`install._platform_activation_tail(root, work, images, stacks, publication,
origin, repository, final_output, *, preflight_marker="platform_preflight",
expected_id=None)` now contains the shared post-start path. It verifies the
running Platform identity before `compose exec -T` preflight, rechecks the same
ID after preflight and around the public CLI, then performs the existing
receipt/origin/final-export logic. `_local` uses `exec -T` with its protected
stdin frame and emits child stderr only to the private failure capture. The
coordinator will gate the exact stopped container and completed migrations,
restart only that ID, and call this tail with a new preflight marker; no new
generic resume CLI was added here. Local targeted tests cover command shape,
identity-before-exec and exact-ID tail invocation. The scoped suite passed
17/17 with original-byte coordination contracts; Python 3.12 compile and
`git diff --check` passed. Real NAS continuation and remaining services are
pending.
