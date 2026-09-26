# NAS-A3-R1 resident candidate handoff

## Goal and branch

From coordination `main` baseline `a47b70c28e3d71dca80023a98e8b5360694b27e5`, branch `codex/nas-a3-r1-resident` in its own worktree. OBS resident compatibility is the fixed source commit `a194fa7b527ac2da0f13b8c3e95e76a1b836b4b3`; the remaining resource, exporter, tests and usage changes are commit `bb9757a378df61fe21886e5f731f10936434806b`. The OBS commit retains the baseline three-network template and adds only resident profile binding. Do not infer its NAS behavior from the older `65b88a6` two-network acceptance.

## Delivered

- Added exact `nas-cpuset-resident-v1` scope: project `tianshu-v2-resident`, CPUs 6,7, memory cap, no CPU quota or PID limit, explicit private LAN IP and HTTP/IP public web, operator supplied internal TLS, explicit core auxiliary subnets. QA and LAN QA scopes retain their isolated-test rules.
- Resident core Compose uses `restart: unless-stopped`. `preflight --release` refuses the profile; normal preflight reports `resident_candidate`, `pending_live_acceptance`, `release_ready=false`.
- Added offline `deploy/tianshu/resident_export.py`. It requires a complete actual OBS configured binding from fixed `a194fa7`, exact source bytes from Git, pinned nine image digest references, six explicit disjoint subnets, four source log budgets, Vector buffer and Guard reserve. It writes two uniquely named Dockge Compose files with absolute bind sources under one declared deployment root and a mode-0600 source/image/config/mount/compose lock. Existing output and external bind or secret paths fail closed. It starts no stack and writes no NAS path.
- A1 agreed to prepare the original bundle layout, use the exact OBS source in the manifest, and validate the export lock plus locally installed image RepoDigests before activation. A1 owns first administrator, migrations and product startup; this branch does not edit those paths.

## Actual local verification

All commands used the existing local `nas-a3-venv` Python. The real-contract integration test used `TIANSHU_CONTRACTS_ROOT=C:\YOKI\Codex\tianshu-peiban-bot\contracts` and `TIANSHU_PROJECTS_ROOT=C:\YOKI\Codex\tianshu-peiban-bot\projects`; contract bytes were checked against the manifest and product bytes were read from fixed Git objects.

- `tests/deployment/packaging/test_resident_export.py -v`: 3 passed. It runs actual initialize → fixed Git OBS export → public configure with TLS handshakes → real binding → preflight → Dockge export, then checks project names, digest and mount locks, absolute paths, release refusal, external secrets, missing digests and old two-network refusal.
- `tests/deployment/packaging/test_resource_profile.py`: 22 passed, including QA release refusal and lifecycle resource checks.
- `tests/deployment/packaging/test_packaging.py`: 27 passed.
- `python -m unittest discover -s tests/deployment/observability -p test_nas_resources.py -v`: 6 passed, including QA and resident binding boundaries. The synthetic OBS TLS helper gained AKI/SKI so it works under the local OpenSSL version.
- `git diff --cached --check`: clean before implementation commit. Full staged diff reviewed (10 files, 645 insertions, 23 deletions) in addition to the one-file OBS source commit.

The local integration used short-lived synthetic certificates and synthetic image digest strings. On Windows the Linux UID/GID 10001 chown operation alone was replaced in the joint test; fixed-source configure, TLS, binding and exporter were exercised. No Docker/NAS/container/live resource, throughput, disk-full or 30-day test was run.

## Candidate inputs and remaining acceptance

For a future coordinated NAS run, supply a new empty absolute deployment root, a candidate manifest with fixed product source commits and **real** nine image digests, an A1 site and separate protected credential/admin input, four operator supplied internal TLS chains, explicit core and OBS six-subnet plan, OBS settings with real loopback Guard/Grafana ports, and an empty Dockge stack target for both fixed project names. A1 must finish prepare/first administrator and inspect the installed image digests before side effects. The exported lock must match the copied bundle and all NAS mount paths. Reject target occupancy, name/port/subnet conflict, missing ownership or secret protection before start.

Live candidate acceptance still needs the new three-network OBS topology, effective cpuset/memory/no quota/no PID on all nine containers, startup and maintenance-stop/readback behavior, authenticated readiness, four-source security events through Vector/Guard/Loki with zero unexplained loss, and an observed host capacity policy with responsible operator. The lock records `host_capacity_protection=pending_live_acceptance`; Guard's OBS reserve and alerts do not establish a host-wide high-water write refusal. Release remains false; QA/verified release gates remain closed. A2's independent logging review owns the broader capacity and reclamation judgment.

See `docs/development/nas-a3-r1-resident-export.md` for the input and Dockge contract. Coordination, A1 and A2 were informed of the fixed OBS source and boundaries; no root `main` integration, push, publish, NAS write or stack start was performed.

## 2026-09-26 authorized NAS image-publication addendum

After the earlier handoff, the coordinator separately authorized a dedicated loopback registry and new fixed-source builds. The later execution is recorded in [`docs/development/nas-a3-r1-image-publication-2026-09-26.md`](../development/nas-a3-r1-image-publication-2026-09-26.md). Its NAS `build-receipt.json` SHA-256 is `3991cf241afa1b2fb2dd11c408c6210198563df2cfed7840ecae06a63692f00e`; the same new build directory holds the source inventory and complete build/push/pull logs. Four new product RepoDigests were delivered to A1 and the coordinator. The registry was stopped normally with `restart=no`, preserving its container and data. No application stack or product container was started. A1 and the coordinator own the subsequent resident first install and acceptance.
