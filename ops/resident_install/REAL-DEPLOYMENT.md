# Resident real-input handoff (2026-09-26)

The executable local input is kept outside Git at
`C:\Users\Administrator\.codex\worktrees\nas-resident-inputs-20260926\.runtime\nas-resident\prepared-real-v1`.
It contains a candidate manifest, four product settings, a private CA public
certificate and leaf certificates, OBS settings/TLS, and independent protected
runtime credentials. The CA **signing key** stays only in `private-seed` and
must not be copied to NAS. No provider is configured; model dialogue is
unavailable. The manifest is a candidate and no command claims release ready.

## Fixed facts and hashes

The operator allocation is `docs/development/nas-resident-allocation-2026-09-26.json`
in coordination main. Bundle root: `/volume2/tianshu-v2-resident`.
Platform: `192.168.31.210:18446`; Grafana and Guard: loopback `19490` and
`19491`. Core, egress, frontend, observe, storage and access use
`10.205.200.0/24` through `10.205.205.0/24`. The four product addresses are
`10.205.200.10` through `.13`. The private CA leaf SANs include each internal
DNS name and static IP; Platform also includes `192.168.31.210`.

| Input | SHA-256 |
| --- | --- |
| `plan.real.json` | `1f2406873734c0deb7fa3108af3cd6df1efd0eea306401549810aa878b565412` |
| `prepared-real-v1/release-manifest.json` | `8762c8d6ce660868cdd79374960139c64013586d63eb271484d51b541e8a3ab0` |
| `prepared-real-v1/site/deployment-input.json` | `a9cf63b1f5eea2377cf9ffe7408c423d833564b4459c9b5373e9975e406b1586` |
| `prepared-real-v1/obs-input/settings.json` | `c561e85d4ce0c506b76a9e31cc8a42d9d38a7d8638acd07c245c1d83aabda6f8` |
| `prepared-real-v1/site/tls/ca.pem` | `2d133981f715bd7308f9b7d2155e11f17a2e571779aca3564af0e8f6013ca7c1` |
| `capacity-prepared-v2/resident-capacity.json` | `c0cf7314a5bd987182728b2679ba21cde9d79d0dfedf5d2f88ae0ab235c16b26` |
| `capacity-prepared-v2/tianshu-resident-capacity.service` | `77620ca2c757484a09288bf52a427a94f316757219987bb3a4e8c914181d1f63` |

All nine image pins come from A3's NAS `RepoDigests`; the four products were
built from fixed complete Git SHAs, pushed to `127.0.0.1:19550`, then pulled
back with the same image IDs. The registry is stopped, but the pulled
`repo@sha256` references remain inspectable. The run uses `--pull never`.
A3's build receipt is
`/volume2/tianshu-v2-resident-build/build-receipt.json` (SHA-256
`3991cf241afa1b2fb2dd11c408c6210198563df2cfed7840ecae06a63692f00e`).

## Tooling layout and command

Before copying anything, the coordinator checks that
`/volume2/tianshu-v2-resident-tooling` is absent and rechecks ports, routes,
Docker projects, memory and disk. The local runner repeats those host checks
immediately before creating the bundle. The proposed new tooling layout is:

```text
/volume2/tianshu-v2-resident-tooling/
  repo/                    fixed root Git objects and this runner
  inputs/                  contents of prepared-real-v1, mode-restricted
  plan.real.json
  contracts/               original bytes from C:\YOKI\Codex\tianshu-peiban-bot\contracts
  exports/                 existing empty parent; first/ and final/ absent
  evidence/                existing private parent; first-install/ absent
  resident-capacity.json   A2's reviewed configuration
  tianshu-resident-capacity.service  reviewed rendered unit for byte comparison
  capacity-state/          A2 private state, not inside bundle
```

Use the already verified NAS Python 3.12.14 environment at
`/volume2/Dockers/tianshu-v2-validation/wave1-20260923a/tooling/venv/bin/python`.
The four fixed product Git repositories are under that validation wave's
`repos/` directory. The coordinator checks their exact objects before run.
The installed tooling `repo/` must be the final integrated main containing
this runner, A2, A3 and OBS. The root Git objects must be delivered separately;
a working-tree copy that lacks the fixed A3/OBS commits cannot satisfy the
exporter.

`capacity_inputs.py` generated the two private capacity files from the real
offline A3 export and A2 unit template at commit
`1edf794e8ee7936f41e97fd01c6e3190c2d15bdd`. Its config lists the nine
real `repo@sha256` images, all bind mounts, `/volume2/@docker` as the Docker
data root, the resolved nonsymlink Docker binary
`/volume2/@appstore/ContainerManager/usr/bin/docker`, 20 GiB free/tree limits,
and a 120-second TERM limit. The unit has
`TimeoutStopSec=600s`. The coordinator installs the exact rendered bytes at
`/etc/systemd/system/tianshu-resident-capacity.service`, reloads systemd and
leaves it inactive until the runner arms A2.

With A2's capacity configuration and systemd unit installed, the **single
continuous** supervised command is:

```text
cd /volume2/tianshu-v2-resident-tooling/repo
sudo -n /volume2/Dockers/tianshu-v2-validation/wave1-20260923a/tooling/venv/bin/python -B -m ops.resident_install.run_candidate \
  --plan /volume2/tianshu-v2-resident-tooling/plan.real.json \
  --inputs /volume2/tianshu-v2-resident-tooling/inputs \
  --contracts /volume2/tianshu-v2-resident-tooling/contracts \
  --projects /volume2/Dockers/tianshu-v2-validation/wave1-20260923a/repos \
  --root-repository /volume2/tianshu-v2-resident-tooling/repo \
  --first-export /volume2/tianshu-v2-resident-tooling/exports/first \
  --final-export /volume2/tianshu-v2-resident-tooling/exports/final \
  --evidence /volume2/tianshu-v2-resident-tooling/evidence/first-install \
  --start-remaining \
  --capacity-config /volume2/tianshu-v2-resident-tooling/resident-capacity.json \
  --capacity-unit-file /volume2/tianshu-v2-resident-tooling/tianshu-resident-capacity.service
```

The runner requires fresh paths, the reviewed input hashes and an inactive
loaded A2 unit matching the fixed SHA with no drop-ins or pending daemon
reload. It clears Docker/Compose overrides, fixes the local Unix daemon and
both project names, and compares the manifest to the **original contract
bytes** before writing. It invokes public A1 `prepare`, public OBS
`configure-observability`, permission preflight, first A3 export, A1 `activate`
and final export readback. Only then does the optional phase use the final
Compose with pulls disabled to start Memory, Gateway, Companion, then OBS.
Platform is never targeted by a second `up`. It compares the capacity config
against the actual first and final A3 exports, including every bind mount. It
checks the same full Platform container ID and the live origin budget between
stages, then calls A2 `arm`, starts `tianshu-resident-capacity.service`, checks
that unit is active and asks A2 `status` for a ready readback of all nine IDs.
It checks the origin expiry again before reporting pending live acceptance.

On a normal command failure after activation begins, the runner inspects exact
project, image, Compose origin and all bind identities, then disables restart,
sends TERM and reads back matching containers for up to 120 seconds. During
optional startup, it first stops the capacity unit and waits for its
`ExecStopPost` to finish, then calls A2 `fail-close` and only then the exact-ID
fallback. A CLI timeout or uncertain stop prevents further writers and
requires immediate manual inspection. It preserves containers, logs, DBs and
evidence for diagnosis; unknown or unconfirmed IDs require
the coordinator's immediate manual stop. A process kill before A2 arm cannot
be closed by Python exception handling; the coordinator must remain online,
inspect exact IDs and stop them. No blind retry, project-wide `down`, unrelated
container cleanup, or assertion of live acceptance is part of this command.

Omitting `--start-remaining` runs only through A1's final export and leaves
Platform as the sole running candidate service for a staffed, bounded review.
The short-lived origin still expires on its original 300-second schedule.

## Exact post-prepare continuation

The first NAS invocation stopped after the public `prepare` child succeeded:
the runner had returned its stdout stream instead of the captured bytes. No
OBS stage, export, activation, resident container or Platform origin was
started. The correction returns bytes and parses the complete JSON receipt.
The normal command above still requires a fresh, absent deployment root.

For this one recorded state, use the same command and options above, changing
only `--evidence` to the new, absent
`/volume2/tianshu-v2-resident-tooling/evidence/resume-after-prepare-20260926`
and adding:

```text
  --resume-after-prepare \
  --prior-evidence /volume2/tianshu-v2-resident-tooling/evidence/first-install
```

The continuation requires the exact original prepare receipt, run-stopped
classification, fixed bundle-integrity SHA, unchanged manifest, empty mutable
state in the four product `data/` and `logs/` directories, absent OBS,
`observability-input`, `INCOMPLETE` and `reports` directories, absent first/final
exports and a fresh new
evidence directory. It rechecks the live host and requires no Docker occupants
before continuing at runtime permissions. It never calls `prepare` again or
edits the original evidence. The coordinator also verifies no old CLI or
supervisor process remains before starting this command. A mismatch stops
without creating the new evidence directory.

## Local evidence and remaining live work

The local Python 3.12 generator created distinct credentials and the private
CA; `load_inputs` and the existing `tls_check` completed real in-memory
handshakes for all four product DNS/static-IP SANs. The fixed OBS `verify_tls`
completed all five server handshakes, including Loki client authentication.
`load_manifest` and `check_contracts` passed against the original coordination
contract tree. No NAS bundle, product container, provider call or Dockge stack
was started by this preparation. A4 still must inspect the actual final lock,
both actual Compose files, the copied bundle, the real administrator login and
the A2 capacity guard before acceptance.
