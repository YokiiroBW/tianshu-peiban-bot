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
