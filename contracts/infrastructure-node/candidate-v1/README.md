# Infrastructure node process contract (candidate v1)

Candidate only. TS-176 produces this contract; TS-177 consumes its fixed commit in isolated tests. Publication and integration belong to the coordinator.

Run `python -m ops.infrastructure --config <executor-config.json>` in the workspace root. Send exactly one UTF-8 JSON object on stdin and close stdin. The process emits exactly one UTF-8 JSON response plus a newline on stdout. No SSH credentials, addresses, commands or remote paths come from a request. Configuration is an executor-owned file and `node.catalog` returns only registered IDs, labels, kinds and capabilities.

Requests use `version: "infrastructure-node.v1"`, `request_id`, `node_id`, `operation`, `params`, and `timeout_ms` (500–30000). `node_id` is null for `node.catalog`. Operations are:

| Operation | Params | Data |
| --- | --- | --- |
| `node.catalog` | `{}` | `nodes` with `id`, `label`, `kind`, `capabilities` |
| `node.snapshot` | `{}` | `identity`, `system`, `cpu`, `memory`, `filesystems`, `interfaces`, `openwrt` |
| `docker.inventory` | `limit` 1–200 (default 100), optional opaque `cursor` | `containers`, using complete 64-character Docker IDs |
| `docker.stats` | `container_ids`, 1–200 unique full IDs | `containers`, one entry for each requested identity, including unavailable entries |
| `docker.logs` | full `container_id`, `tail` 1–1000 (default 200), `since_seconds` 1–86400 (default 3600), `max_bytes` 1024–262144 (default 65536) | Object identity, separate stdout/stderr preview strings, `truncated`, requested bounds and byte count |
| `compose.inventory` | `limit` 1–200 (default 100), optional opaque `cursor` | `stacks`, restricted desired services and their runtime differences |

Responses retain the request identity and operation, plus `status` (`ok`, `partial`, `unavailable`, `unsupported`, `invalid`), `reason_codes`, `observed_at` (UTC RFC3339), configured `capabilities`, `completeness`, and `data`. An invalid, unsupported or unavailable result has null data. The CLI exits 0 after emitting any valid protocol response, including domain failures. Exit 2 is reserved for inability to write the response/CLI usage errors.

`completeness` has `state`, `returned`, nullable `total`, and nullable `next_cursor`. A complete page can have a next cursor: completeness describes that page, not acquisition of every page. Inventory cursors bind the node, operation and a digest of the sorted full identity set. A changing set yields `cursor_invalidated`; the caller restarts pagination. No limit of 64 containers applies. The bounded inventory ceiling is 10000 identities; exceeding it yields partial/unavailable evidence, never an assertion that omitted objects do not exist. A disappearing container is an unavailable item and a partial response, not an empty inventory.

Host CPU has `basis: "host_total"` and a ratio of busy time to all host CPU time. Linux guest counters are already included in user/nice and are not added twice; idle includes iowait. Docker CPU has `basis: "one_core"`; `usage_ratio` may exceed 1.0 and is delta container CPU / delta host system CPU × online CPUs. Docker memory includes both `raw_usage_bytes` and `cache_adjusted_usage_bytes` with the exact cache source; no available cache counter means null adjusted usage. Block IO and network counters are cumulative bytes, with unknown represented by null. Host network/container shared namespace has `attribution: "not_attributable"` and null network totals. Load average is a separate system field.

Derived rates use the executor's local sampling state, keyed by registered node and actual boot/container identity. First sample, counter reset, stale interval, missing source and unsupported source retain null rates plus explicit state/reason. Snapshots always carry observation time. Filesystem/interface IDs are stable hashes of node and source/mountpoint or interface name; changing boot/ifindex resets rate calculation without inventing a new device. Interface counters do not imply per-terminal traffic. OpenWrt exposes board/info/interface data from `ubus`, and explicitly unsupported per-device traffic until an actual source is registered.

Compose discovery reads only the registered stacks directory's direct child directories and the standard compose filenames. It does not invoke `docker compose config`, load `.env`, follow includes, read referenced secret/config files or execute build directives. The restricted YAML projection resolves bounded YAML anchors/merge mappings but leaves interpolation unresolved. Profiles/extends/includes/build and unsupported structures produce explicit partial or unresolved observations. Only names, images, ports and mount summaries leave the executor. Runtime comparison is based on the same node's actual Docker Compose labels and full container IDs; uncertain desired/runtime input cannot be reported as in sync.

The process request limit is 64 KiB; response limit 4 MiB; transport output limits and the request deadline apply across all commands, queueing and sampling-state access. Stdout/stderr are read concurrently and bounded; timeout/output overflow kills and reaps the local process tree. Executor-wide slots and a node sampling lock bound concurrent calls. Log previews are bounded in time, lines and bytes and never claim to replace full persistent logs.

Validate schemas and positive/negative examples with `python contracts/infrastructure-node/candidate-v1/validate.py`. Runtime Python is 3.11+; the Compose parser dependency is declared separately in `ops/infrastructure/requirements.txt`. The node side requires POSIX shell and standard Linux/BusyBox read tools, plus `ubus` for OpenWrt and Docker CLI for Docker; it requires no Python. SSH uses explicit identity and known-host files and disables implicit user/system SSH configuration and agent identities.
