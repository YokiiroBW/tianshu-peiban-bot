# Registered SSH node observation executor

This is a read-only JSON process, not a running agent or a web service. It runs on Windows/Linux with Python 3.11+ and OpenSSH. Nothing contacts a default node. The platform invokes one process per request with a deployment-owned configuration:

```text
python -m pip install -r ops/infrastructure/requirements.txt
python -m ops.infrastructure --config /run/infrastructure/executor-config.json
```

Send one request on stdin and close stdin; stdout contains one response. The entry point requires `--config`; `state_dir`, node address, SSH identity and known-host files are explicit registrations. Provision those files outside Git. See the [candidate contract](../../contracts/infrastructure-node/candidate-v1/README.md) and its schema/examples. The configuration example uses an `.invalid` host and synthetic paths and cannot contact a real device as supplied. An explicitly empty `nodes` object gives a working empty catalog.

The process reads no user/system SSH config or implicit agent identity. It uses BatchMode, a pinned configured known-host file and explicit identity. Missing key, failed authentication, wrong host key and offline host are fixed reason codes. It does not enroll or accept a host key. Host-side permissions remain the existing SSH/Docker user's permissions. Requests select registered node IDs and structured read operations only.

The node requires a POSIX shell, `cat`, `uname`, `df`, `find` and `head` (Linux/BusyBox), plus `ubus` for OpenWrt. Docker operations use the registered Docker CLI and explicit Unix socket, so inventory/metrics address the same daemon. Raw read-only HTTP GETs go through Docker CLI's `system dial-stdio`, whose [Docker 24 implementation](https://github.com/docker/cli/blob/v24.0.2/cli/command/system/dial_stdio.go) proxies stdio to the daemon. API version is negotiated, capped at 1.41 and requires at least 1.24; `one-shot` is used from 1.41. The executor holds no platform Docker socket and installs nothing on the remote node. Deployment packaging must supply Python, OpenSSH, this module and the pinned parser dependency; wiring a production image is a later deployment task.

`PyYAML==6.0.3` is the sole runtime dependency, pinned in this module for the actual Compose YAML parsing requirement ([maintainer release](https://pypi.org/project/PyYAML/6.0.3/)). The parser uses a bounded YAML node graph and reads only selected fields, never Python object construction. Compose anchors and merge mappings are bounded; unsupported includes/profiles/build/reference files and unresolved environment variables stay explicit. No `.env`, `env_file`, build context, secret or config resource file is opened. It never invokes `docker compose config`.

Counter state lives only in `state_dir` as bounded per-node JSON, written by atomic replacement under a per-node OS file lock. The same directory has executor-wide slot locks (default four); callers waiting for either lock consume the same request deadline. The directory should be a local filesystem shared by invocations of this executor, not an SMB/NFS lock emulation. Missing state means a first sample; invalid, stale, reset or unwritable state is reported. No historical Control database or API is imported.

CPU and memory reuse the legacy Linux collector's guest/iowait and MemAvailable rules. Docker identity, network attribution, CPU deltas and Compose projection/drift reuse the applicable legacy collector rules, while removing its 64-container ceiling and old database/agent coupling. Sources reviewed: `references/legacy-control` commit `05d503a118d3ba187f3ebd444df019bf73bb90f0`; nas_ops `4bd39eea3ca616a959726edd67f024d1198761f0` was checked for fixed-path and false-empty pitfalls. Unknown values remain null. OpenWrt `ubus.interfaces[].l3_device` links by name to the host `interfaces[]` counters; these are interface totals, never terminal traffic.

The request deadline is 0.5–30 seconds after request/config parsing. Stdin framing separately has a five-second/64 KiB bound. Remote scripts also use `timeout` where supplied by Linux/BusyBox; every local subprocess is killed and reaped under the request deadline/output cap, including descendants via Windows Job Object/POSIX process group. Cleanup has a bounded allowance. Responses cap at 4 MiB; an oversized response becomes an explicit unavailable result. Inventory limit is 10000 full identities; pages are at most 200 and bind to the complete identity-set digest. Container deletion races and partial metadata cannot produce empty or healthy inventories.

Docker logs are a timestamped recent preview: at most 1000 lines, a one-day lookback and 256 KiB combined UTF-8 stdout/stderr. The platform may impose narrower limits. An output cutoff is partial and kills the reading process. It does not claim full persistent business log retention.

Run related checks from this workspace with a Python environment containing the pinned parser and existing contract-validation dependency:

```text
python contracts/infrastructure-node/candidate-v1/validate.py
python -m unittest discover -s tests/infrastructure -v
```

The optional loopback SSH integration test uses Paramiko only to implement an isolated test server, with ephemeral synthetic host/client keys. Test-only versions are in `tests/infrastructure/requirements.txt`; it never touches the user's SSH config, keys or NAS/router. A fake Docker CLI over that real SSH protocol exercises Engine framing, selected inspect fields and bounded logs. This remains synthetic Docker evidence; a real isolated Docker daemon and real NAS/OpenWrt readback must be recorded separately.

For platform/package joint tests, export `tests/infrastructure/helpers.py` and `synthetic_ssh.py` together outside the runtime package. With the exported runtime module on `PYTHONPATH` and the test-only requirements installed, run `python synthetic_ssh.py --directory <absolute-test-directory> --ready-file <absolute-ready.json> --containers 150`. Its readiness JSON points to a generated executor config containing `fixture-nas` and `fixture-router`. Keep its stdin open for the test and close stdin to stop it. All SSH keys/host keys are freshly generated within that test directory. This entry point has no dependency on the original working directory or another developer's worktree.
