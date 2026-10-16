# TS-181 Linux and registered-node acceptance

These are operator-run validation inputs for the existing deployment flow. They do not install or change remote nodes, restart services or select a default address. The coordinator owns real connections, registration values, candidate containers and deployment. No real SSH configuration/key or business log has been read by the implementer.

## Candidate Linux image

The runtime needs Python 3.11+, OpenSSH and the unchanged pinned `PyYAML==6.0.3`. The remote side needs POSIX shell and cat/uname/df/find/head, `ubus` for OpenWrt, and the actual registered Docker CLI/socket for Docker. No remote Python is required. The acceptance driver additionally uses the existing `contracts/requirements-validation.txt` (`jsonschema==4.25.1` and the existing format validators). Paramiko is only for the old synthetic SSH tests; the real Linux/NAS paths below do not use it.

Export this fixed task revision into a read-only validation directory in the candidate Linux environment and run:

```text
python -B tests/infrastructure/linux_runtime.py
```

It executes the collector's actual local shell commands against that container's Linux files, tests POSIX process-group cleanup for a living/early-exiting parent and bounded stdout/stderr, and uses an isolated temporary Compose directory to exercise real `find`/`head`, projection and symlink refusal. It contacts no SSH endpoint or Docker daemon. Output labels `scope=candidate_linux_container_only`; it must never be called NAS/OpenWrt or real Docker evidence. Running it on Windows returns `not_run/linux_runtime_required` and exit 2.

## Minimum real-node read sequence

Supply an executor-owned config with the coordinator-confirmed Docker command, Unix socket and physical stacks root. Do not substitute remembered or guessed paths. The existing operations perform the following fixed reads:

| Phase | Existing reads | Receipt evidence |
| --- | --- | --- |
| Host | hostname/boot/uptime/load, `/proc/stat`, `/proc/meminfo`, `/proc/net/dev`, ifindex, `df -P -k` | Two timestamps, host_total basis, actual memory/capacity, first/ok/reset/stale states and interface rates |
| Docker inventory | Explicit CLI/socket, `ps -a --no-trunc`, selected `inspect` fields | All pages to cursor exhaustion, unique full-ID accounting, actual total and whether it exceeds 64, runtime/health counts |
| Docker metrics | Selected actual running IDs, negotiated Engine GETs through CLI `system dial-stdio`; at most eight concurrent connection groups, sequential GETs per group, last request closes its connection, stdin retained until response EOF | Two batches of real samples, one_core basis, raw/cache-adjusted memory coverage, attribution, cumulative IO/network increases/resets/unknowns; empty HTTP bodies remain unavailable |
| Compose | Registered root's standard primary files via `find`/`head`, actual selected Compose labels | Stack/service counts, source/comparison reasons and drift findings; unreadable child stacks remain unavailable, readable discoveries remain partial; no `compose config`, `.env`, include/build/resource expansion |
| Permission | Same read operation against an explicitly registered negative case | Expected denial reason, separated from empty/healthy data; no permission changes |
| Optional log | One explicitly supplied full ID; 200 lines, 64 KiB, one hour | Identity reference, bytes/lines/truncation/body digest; no log text stored or printed |

The snapshot/inventory calls themselves establish the actual command permissions and source availability. A separate operator check may use `command -v sh cat uname df find head timeout` and the exact registered Docker command's selected version fields; neither this driver nor its request accepts arbitrary shell commands or paths. Wrong SSH identity/host key and OpenWrt authentication failure stay explicit failures, not synthetic evidence of access.

## Driver invocation

Run from the exported validation source with the existing package's actual interpreter and launcher selected explicitly:

```text
python -B -m ops.infrastructure.acceptance --config /absolute/private/executor.json --node-id REGISTERED_NODE_ID --contract-dir /absolute/node-package/contracts/infrastructure-node/candidate-v1 --python /absolute/node-venv/bin/python --launcher /absolute/node-package/run_node.py --output /absolute/private/nas-before.json
```

This driver may remain outside the production package; it has no need to change the packager's fixed source list. `--launcher` invokes the installed package's actual `python -I -B run_node.py` entry point. Without it, the driver invokes this source module's normal CLI in a subprocess. The source/package distinction must be recorded by the coordinator. The default total budget is 300 seconds (allowed 10–600); individual requests remain at most 30 seconds, pages at most 200 and total identities at most 10000. `--interval` defaults to one second. Nothing reads logs by default.

The 200-ID ceiling does not promise a fixed request duration. Observe actual call durations and partial reasons. First CPU/interface observations may be `first_sample` with null rates; existing polling may instead yield `ok`, `stale` or `counter_reset`. A second sample within the configured stale interval should be `ok` only when identity/counters remain usable and state is retained. A zero IO/network delta is valid for an idle source; container CPU uses one_core and may exceed one. Do not replace unsupported/reset/empty data with zero.

Docker24 on the actual DSM node takes about one second per stats GET. Running IDs are spread across at most eight groups, which share the request deadline with 0.5 seconds reserved for projection/cleanup. The aggregate captured stats output, including stderr, stays at most 3 MiB through fixed per-group shares. Raw bodies are projected within their worker and released; stopped objects never issue stats GETs. On deadline/output exhaustion, complete prefix samples remain available and all remaining requested IDs carry explicit unavailable reasons. The collector joins its bounded workers before releasing the node sampling lock. The coordinator-selected legal 30-second NAS deployment timeout covers the observed running set; a source-driver pass does not replace actual installed-launcher or platform-round verification.

After a coordinator-managed candidate/container restart, repeat with `--before /absolute/private/nas-before.json`. The driver only compares opaque boot/container/started-at identities and current sampling state; it never performs a restart. A changed boot, reset/stale rate and a newly created container are different observations. First-sample after lost state remains visible. Do not restart the NAS itself merely to force a reset test; actual reset may instead be exercised on a separately authorized isolated Docker target, with its real observations recorded separately.

Permission evidence requires an already registered negative case; the driver does not create credentials or change authorization:

```text
python -B -m ops.infrastructure.acceptance --config /absolute/private/negative-executor.json --node-id REGISTERED_NODE_ID --contract-dir /absolute/node-package/contracts/infrastructure-node/candidate-v1 --python /absolute/node-venv/bin/python --launcher /absolute/node-package/run_node.py --mode denied
```

The optional log phase is separate and requires the coordinator's registered single target:

```text
python -B -m ops.infrastructure.acceptance --config /absolute/private/executor.json --node-id REGISTERED_NODE_ID --contract-dir /absolute/node-package/contracts/infrastructure-node/candidate-v1 --python /absolute/node-venv/bin/python --launcher /absolute/node-package/run_node.py --mode logs --log-container-id FULL_64_HEX_ID
```

## Receipt meaning

`format=infrastructure-acceptance.v1`, verification time, mode and opaque node/object references identify each run. The receipt contains only whitelisted numeric/status facts: request status/reasons/time/duration, counts and identity-set digest, memory/capacity/rates, source/basis, health and drift counts, counter deltas and restart comparison. It omits addresses, users, key/known_hosts/socket/stacks paths, hostname, container/project/service names, port/address/mount details and all Compose/log bodies. Raw responses stay only in the running process. Use a private output location, then share only this redacted receipt.

`verification=passed` means the requested read/contract/accounting checks completed. It does **not** declare every observed service healthy, every metric supported or every stack in sync. The actual partial/unsupported/unknown states and drift findings remain in the receipt. Completed phase summaries are added immediately, so a later failure retains earlier host/inventory/metric facts while the receipt remains failed. Failure status, changing/incomplete inventory, duplicate IDs, mismatched response identity and exceeded log bounds cannot pass. Partial Compose discovery carries `compose_discovery_partial`/`permission_denied`, and its known-object total cannot establish that the whole root was readable. A successful `denied` phase means the requested denial was actually observed. Only the coordinator can combine these results with deployment, backup/restore, guard, music continuity and browser evidence into a release conclusion.
