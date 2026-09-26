# NAS A3-R1 fixed-source image publication

Recorded 2026-09-26. This is an input to the resident first install, not an activation or a completed release. The coordinator authorized an isolated loopback registry and four new builds from fixed Git commits. No product container, resident Compose project, host service, production migration, or device was started or changed by this task.

## Source and build lock

- Export tool: root repository commit `0fd6201a5633a9a5a9e8292f3bd3eadbd61a25e9`, `deploy/tianshu/release.py export-sources`.
- Candidate manifest SHA-256: `c962f14430ccf3580f23f9cac3f44182f3da6b3c4de9af5913b925209aa9d765`.
- The manifest's contract inventory was checked against the authoritative raw-byte `C:\YOKI\Codex\tianshu-peiban-bot\contracts` tree. The root Git worktree's older `contracts` copy failed `contract_bytes_changed` and was not used.
- Exported `source-inventory.json` SHA-256: `5ad24b8b9f51929c2f4465374c43a70e77e2b36335b97b1d7adde3e89e9a999d`. It contains each fixed source commit, Git archive SHA-256, per-file hashes, injected platform contract hashes, and the base build command.
- Uploaded context tar SHA-256: `030255807fff9213a0dbacf5429bc76f87a15c8fe5a173ed41189b32cd0704ed`. The NAS read back this hash, extracted only into the new build directory, and independently matched every context file to the inventory: platform 330, companion 112, memory 148, gateway 83 files.
- The four original product Dockerfiles and dependency locks were used without edits. Builds ran one at a time on Docker 24.0.2's legacy builder for `linux/amd64`, with `--memory=2g --memory-swap=2g --cpuset-cpus=6,7`, no CPU quota or PID limit. Each new image carries `org.opencontainers.image.revision=<full product Git SHA>` and `org.tianshu.source_inventory_sha256=<inventory SHA>` labels.

## Resulting product images

The full digest reference is the immutable A1 deployment input. The tag column records the exact new build/push tag. Each digest was pulled from the loopback registry and its image ID matched the immediately preceding build ID; the four digest references remained inspectable after the registry stopped.

| Product | Fixed Git commit | New tag | RepoDigest | Built and pulled image ID |
| --- | --- | --- | --- | --- |
| platform | `c1c7547680911462439ee9c9a09f4e72f44f36a3` | `127.0.0.1:19550/tianshu/platform:c1c754768091-resident1` | `127.0.0.1:19550/tianshu/platform@sha256:244b7cd7948608e8c5a200ff9955e70f881e1bb2a76a097e32470f4b01f302b6` | `sha256:ad0d2803cd4de37483c1f2a207ae466b992114a55ec49542db5170e73864dd13` |
| companion | `e94b609099365f75ca933d9fed03cdfbc83ec235` | `127.0.0.1:19550/tianshu/companion:e94b60909936-resident1` | `127.0.0.1:19550/tianshu/companion@sha256:cb3ad3e8492e4dd187d792ee0feb3e35b67d727ab96caf4160133bd7669b9b5b` | `sha256:72512367d21bbe65611a185252a4ea6892272ef3d468967ea7d6b47189aa70f1` |
| memory | `9a3b2bed6aebff9f0677f2c62e979859769e0c9c` | `127.0.0.1:19550/tianshu/memory:9a3b2bed6aeb-resident1` | `127.0.0.1:19550/tianshu/memory@sha256:7fe62999504ff099bd68220d5488eb6bf917380a9d04785114740d8792b9a076` | `sha256:31048b9faf32516ca567e3c50955d7abcccbed899307cc9488c5d46d1772152b` |
| gateway | `601974194042641c5a85cc3c061cbd1880d7daf1` | `127.0.0.1:19550/tianshu/gateway:601974194042-resident1` | `127.0.0.1:19550/tianshu/gateway@sha256:2e1e37039d5c48bce433e09fb3e52df0006d1118a48dd32f49041931895a8382` | `sha256:2f8a191cd9873c4a041790225a0b7c5bb6e05395f133a75fc5caa00e81a5525e` |

## Evidence and NAS state

The new NAS directory `/volume2/tianshu-v2-resident-build` contains the uploaded source tar, `source-inventory.json`, the independent context verifier, `build-receipt.json`, and complete `<product>-build.log`, `<product>-push.log`, and `<product>-pull.log` files. The receipt contains the exact build commands and per-log SHA-256 values. Its uploaded and read-back SHA-256 is `3991cf241afa1b2fb2dd11c408c6210198563df2cfed7840ecae06a63692f00e`.

The coordinator-pinned official `registry:2` image was used as `registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373` in container `tianshu-v2-resident-registry`. It only published `127.0.0.1:19550:5000`, mounted the new `/volume2/tianshu-v2-resident-registry` data directory, used CPU 6/7 and a 256 MiB memory limit, and had `restart=no`. After all pushes and digest pulls, `docker stop --time 10` completed. Readback showed `exited restart=no`, no listener on 19550, and the container/data were retained. The four locally cached RepoDigests remained after this stop.

The older `tianshu/<product>:<12sha>` images had no RepoDigests or provenance labels. They were not used as publication evidence and were not retagged or pushed. The release manifest remains a candidate; image publication alone does not close application acceptance, configuration, or live readiness gates.
