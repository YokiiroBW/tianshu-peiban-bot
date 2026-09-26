# NAS A3-R1 deployment input: read-only snapshot

> Historical inventory only. The registry version and old-image publication proposal below were superseded by the authorized fixed-source build and digest receipt in [nas-a3-r1-image-publication-2026-09-26.md](nas-a3-r1-image-publication-2026-09-26.md). Use the latter for deployment inputs.

Observed 2026-09-26 around 09:20 CST via `agent@192.168.31.210:77` using the dedicated SSH identity, strict existing host key, batch mode and `sudo -n` for Docker. Commands were read-only (`docker info/image inspect/ps/network inspect`, `netstat`, `ip route`, `stat`, `df`, `free`, `systemctl --version`). No NAS file, Docker image, container, network or service was created or changed. This snapshot is **not** a reservation; repeat conflict checks immediately before any write.

## Fixed images and the activation blocker

The candidate manifest's fixed product source commits are platform `c1c7547680911462439ee9c9a09f4e72f44f36a3`, companion `e94b609099365f75ca933d9fed03cdfbc83ec235`, memory `9a3b2bed6aebff9f0677f2c62e979859769e0c9c`, gateway `601974194042641c5a85cc3c061cbd1880d7daf1`. The installed product tags match the first 12 SHA characters, but all four inspected image labels were `null` and all four `RepoDigests` were `[]`. A tag or local image ID alone does not prove the complete source commit or satisfy A1's digest gate; tie these to existing build receipts before release.

| Product tag | Local image ID | RepoDigests |
|---|---|---|
| `tianshu/platform:c1c754768091` | `sha256:17cf711c4a9288021384c66a5bdc6d079b37ded125b1e4c96a1c75b94a57d39c` | `[]` |
| `tianshu/companion:e94b60909936` | `sha256:6b7467e534dd39ec9663d78e8051dd5b8c8a4a69b394217e0bc33355b19a0a70` | `[]` |
| `tianshu/memory:9a3b2bed6aeb` | `sha256:6a36e18a5696f3f54d0a89c6ba237381fa585c07defbf810f3aa7df9401cc6ee` | `[]` |
| `tianshu/gateway:601974194042` | `sha256:10746fed0f95f138501609620368e94d4764888dab08363124dfeccc2273a7be` | `[]` |

The five OBS images were `linux/amd64` and had these exact local `RepoDigests`:

| Fixed tag | RepoDigest |
|---|---|
| `timberio/vector:0.58.0-debian` | `timberio/vector@sha256:1c1ea358c617ea0b23003d5af87f7a678b30f8f7096437e680380c47fc13d2d9` |
| `grafana/loki:3.7.8` | `grafana/loki@sha256:1107dd5274e0ada47e42472b7a7e71f3b2a2fe878878108f3e2f9e51528f0193` |
| `grafana/grafana:13.2.2` | `grafana/grafana@sha256:ac461fb352abc50da10a51c7d02462e9c05488f11f53f14b3ad79a8145f638a0` |
| `prom/prometheus:v3.13.3` | `prom/prometheus@sha256:6976aa8a60fec930796ce5772b8d12da7a318a5daa8d40d69c5c7819a05eeed7` |
| `python:3.12.14-slim-bookworm` | `python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e` |

## Candidate paths, ports and networks

Dockge mounts `/volume1/Download/dockge/stacks` to `/opt/stacks`. At observation time, `/volume1/Download/dockge/stacks/tianshu-v2-resident`, `.../tianshu-v2-resident-obs`, and `.../tianshu-v2-resident-registry` did not exist. No containers with the two resident Compose project labels, no resident-named Docker networks, and no registry-named container were found. Suggested deployment root `/volume2/tianshu-v2-resident` and separate registry storage `/volume2/tianshu-v2-resident-registry` did not exist. Do not use the existing validation-wave path.

Suggested site: `tianshu-v2-resident`, `http://192.168.31.210:18446`, bind `192.168.31.210:18446` to platform 8080; OBS Grafana `127.0.0.1:19490:3000`, Guard `127.0.0.1:19491:8443`. `netstat -lnt` had no listeners on these three ports; 18443-18445 were occupied by the control hub. Suggested service IPs in `core`: platform `.10`, companion `.11`, memory `.12`, gateway `.13`.

| Stack/network | Candidate subnet |
|---|---|
| core/core | `10.205.200.0/24` |
| core/egress | `10.205.201.0/24` |
| core/frontend | `10.205.202.0/24` |
| OBS/observe | `10.205.203.0/24` |
| OBS/storage | `10.205.204.0/24` |
| OBS/access | `10.205.205.0/24` |

Those six ranges did not overlap 194 Docker network IPAM subnets or the NAS IPv4 route table at the snapshot. They remain candidate allocations until Docker network creation and must be rechecked immediately beforehand.

## Local registry prerequisite and minimal publication plan

No local `registry:2` or `registry:3` image, registry container, or reusable localhost registry was found. `19550` had no listener and no Docker published binding. Docker already lists `127.0.0.0/8` as an insecure registry CIDR; no daemon configuration change is proposed. The NAS reached `https://registry-1.docker.io/v2/` over TLS and received the expected unauthenticated 401. Docker's [official registry image](https://hub.docker.com/_/registry) lists `3.1.2`/`3`; actual pull, exact upstream digest and local architecture verification remain pending. The NAS has several configured third-party Docker Hub mirrors, so do not rely on a floating tag without checking the official upstream digest and post-pull image identity.

After the coordinator authorizes a new registry path and NAS writes, use official `docker.io/library/registry:3.1.2` pinned by its verified upstream digest; a separate project/container `tianshu-v2-resident-registry`; only `127.0.0.1:19550:5000`; and `/volume2/tianshu-v2-resident-registry:/var/lib/registry` as a writable bind with no automatic host-path creation. Candidate limits: `cpuset=6,7`, memory 256 MiB, no CPU quota and no PID limit. Keep it off both TianShu application networks. Read back its actual image digest, mount, port and resource settings before publication.

For each of the four fixed product images, record the original local image ID, add a new `127.0.0.1:19550/tianshu/<product>:<fixed-commit-prefix>` tag, push, capture the registry's returned digest, pull the exact `repository@sha256:<digest>`, then inspect that `RepoDigests` contains the expected local-registry reference and `.Id` still equals the original ID. Refuse manifest generation and activation on any mismatch. This publication path has not run. A1's nine-image digest gate stays closed until it succeeds.

## Capacity and host service facts

`/volume2` was 906 GiB total, 421 GiB used, 438 GiB available (50% used); Docker RootDir is `/volume2/@docker`, so the registry, application state and Docker images would share this volume. `/volume1` had 47 TiB available. The host reported 8 CPUs, Docker memory and cpuset controllers available, PID limit unsupported; 46 GiB RAM total, 18 GiB available, and 12 GiB swap already used. Nine application memory limits total 7.5 GiB; a 256 MiB registry would bring declared limits to 7.75 GiB. No running container was found with an explicit `cpuset=6,7` in the read-only snapshot. These figures are planning inputs, not live acceptance.

For a staffed candidate run pending A2's guard, take a fresh `/volume2` free-space receipt before start, poll at most every minute, warn below 250 GiB available, and stop the exact two resident Compose projects at or before 200 GiB available or after a monitoring gap over five minutes. Name the responsible operator and notification destination before start. The stop sequence must identify exact project container IDs, disable and read back restart policy, then send SIGTERM and wait; never reboot the host. Other writers can consume the shared volume faster than this policy reacts, so the thresholds do not prove host-wide high-water protection. A2 owns the final systemd capacity guard and acceptance.

DSM PID 1 is `systemd`; `/usr/bin/systemctl` and `/usr/bin/systemd-notify` exist; `synosystemctl` was not found. Version is systemd 219; `/etc/systemd/system` exists. `systemctl is-system-running` reported `degraded` (exit 1), without diagnosis of the failed unit. Type=notify, watchdog and ExecStopPost behavior still require static unit verification and live candidate evidence; no host service was changed.
