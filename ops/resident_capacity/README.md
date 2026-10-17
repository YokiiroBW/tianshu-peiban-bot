# Resident capacity fail-close guard

This is one fixed-deployment guard for `tianshu-v2-resident` and
`tianshu-v2-resident-obs`. It does not collect logs, delete data, or read
secret contents. It counts file metadata below the deployment root and checks
free space on every deployment filesystem and on the configured `free_paths`.
The configuration requires a deployment budget no greater than 20 GiB and
at least 20 GiB free on **each** checked filesystem; stricter values are
allowed. A threshold breach or measurement/identity
failure latches the deployment and stops only precisely identified resident
containers. There is no automatic resume.

## Exact configuration

The root-owned JSON must match `config.schema.json` and the code's strict
validator. The expected deployment root is `/volume2/tianshu-v2-resident`;
the real path is still an explicit input. `compose` contains the actual
`com.docker.compose.project.working_dir` and
`com.docker.compose.project.config_files` label values for both final stacks.
Because the installer uses `--project-directory`, all three `workdir` values
are the deployment root while their `file` values point to separate first/final
export files. `platform_first_compose` contains the **different, exact**
first-export file of the already-running Platform container. Those values are
never wildcards or prefixes. Without `service_profile`, the original nine
services remain the exact required set. The only expanded profile is
`"service_profile": "knowledge-ten"`; it also requires the
`tianshu-v2-resident/knowledge` image and all its binds. The legacy profile
rejects those entries. `images` lists the nine or ten digest-pinned
`.Config.Image` values from the locked resident export. `binds` lists **all**
`type=bind` mounts of every service as objects with `source`,
`target`, and boolean `read_only`; use the first export for Platform and the
final export for the other eight or nine. The guard matches Docker's selected mount
metadata, including target and RW bit. `free_paths` must name all host filesystems
whose free space the operator wants protected, including the Docker data root
if it is outside the deployment tree. The guard also checks the deployment
root and any mounted filesystems found below it. Include the external Compose
volume in `free_paths` when it needs the same floor. It reads file metadata,
never file data.

The current expected root and project names are fixed in the code. The first
export path and Docker binary path must be read from the actual host; do not
replace them with guessed values. The suggested host state directory is
`/volume2/tianshu-v2-resident-tooling/capacity-state`, outside the deployment
root, root-owned mode 0700. The configuration file may be placed at
`/volume2/tianshu-v2-resident-tooling/resident-capacity.json`, root-owned and
not group/world writable. Thresholds are explicit bytes; 20 GiB is
`21474836480`.

## First installation and service sequence

1. Install the fixed code, root-owned configuration, and systemd unit template.
   Replace `@PYTHON@`, `@CODE_ROOT@`, `@CONFIG@`, and `@STATE_DIR@` with
   verified absolute paths. The resulting unit is
   `/etc/systemd/system/tianshu-resident-capacity.service`. Do not enable or
   start it while only Platform runs. On DSM systemd 219, syntax and behavior
   still require a live candidate check; the host's existing degraded state
   is not changed by this package.
2. Start the remaining eight fixed services in the controlled installation
   process. Immediately run:

   ```text
   python3 -B -m ops.resident_capacity.guard arm --config /ABSOLUTE/resident-capacity.json
   ```

   `arm` requires all nine or ten running, digest-pinned services with exact project,
   service, Compose workdir/file, root bind, and `unless-stopped` restart
   identity. It checks both capacity thresholds before atomically locking all
   their 64-character IDs in `armed.json`. It refuses if the state is already
   armed or latched. If `arm` refuses, the installer must stop its just-started
   exact IDs through its already-reviewed lifecycle path; there is no armed
   marker for this guard to use.
3. Enable/start the unit, then require `systemctl is-active --quiet` and:

   ```text
   python3 -B -m ops.resident_capacity.guard status --config /ABSOLUTE/resident-capacity.json
   ```

   `status` returns `ready` only when the systemd loop produced a fresh
   heartbeat and a new read-only check still sees the same locked IDs and safe
   capacity. If it refuses, run `fail-close` below and read back that every
   exact resident container exited before leaving the first-install process.
   A manual boolean is not a status readback.

## Latch and failure behavior

After each healthy cycle the loop waits the configured 1–10 seconds before
checking capacity and exact container identity again. It sends `READY=1` only
after the first healthy sample. It then
sends `WATCHDOG=1`. The systemd 219 unit uses `Type=notify`,
`WatchdogSec=30s`, unlimited start retries, and `ExecStopPost` invoking:

```text
python3 -B -m ops.resident_capacity.guard fail-close --state-dir /ABSOLUTE/capacity-state --reason=service_exit
```

Thus a crashed or watchdog-killed main process is stopped by an independent
systemd control process; it does not rely on a Python signal handler. When
`run` detects an unsafe sample under this unit's notify/30-second watchdog
environment, it latches the cause and exits for the same `ExecStopPost` path.
It does not spend the remaining main-process watchdog interval performing the
potentially long stop. A standalone `run` invocation keeps synchronous
fail-close, including configuration-mismatch and already-latched exits.
The fail-close command writes `failure.json` before effects, disables restart on
the nine or ten locked resident IDs only, reads policy back, sends SIGTERM only,
and confirms they exited. It never SIGKILLs or deletes logs. `stop-*.json`
retains each attempt and its fixed-code errors. A partial stop returns
`unconfirmed`. The installer/operator must keep the stack stopped and review
that result; it cannot claim protection on the basis of a TERM request alone.
If another container replaces a locked ID, the guard reports it as unconfirmed
and does not automatically stop that replacement under a reused project name.
Once latched, the loop cannot return to ready or rearm without an explicit
review and a new state generation.

An interrupted atomic write can leave a complete `failure.json.<32hex>.tmp`
before the canonical latch exists. Startup and `status` also recognize a
valid pending latch in this state directory. Fail-close publishes its earliest
valid cause before effects, and keeps that cause on later `service_exit` or
`latched_failure` invocations. Only the exact temporary name, bounded expected
JSON structure, regular file, matching private-state owner and private mode
are accepted; incomplete, malformed or differently located files are not
recovery evidence. The pending file remains available for incident review.

The configured TERM wait is at most 120 seconds. Docker commands time out at
10 seconds each and the batch inspect at 15 seconds. A standalone Docker
snapshot has one monotonic 25-second deadline, shared by any resample.
The calculated upper
bound for external Docker waits plus TERM polling is 570 seconds with ten
services; the unit's `TimeoutStopSec=700s` leaves 70 seconds after the
additional 60-second state-write/scheduling allowance.
A hung kernel filesystem operation has no guaranteed Python timeout, so the
systemd stop result and receipt still need live readback. The unit never
claims a stop solely because its deadline elapsed.

## Docker sampling and private diagnostics

Each snapshot still enumerates **all host container IDs**, up to the existing
4096-container bound. Cold and newly seen IDs are inspected in batches of at
most 32. The Docker instance retains only unrelated containers' immutable
creation metadata: selected Compose labels, configured image and mounts.
It discards their status and restart-policy observations. Every resident
project container and every locked ID is inspected afresh on every sample,
including fail-close exit confirmation on a previously used Docker instance.
This avoids repeatedly inspecting hundreds of unchanged unrelated containers
while preserving the complete foreign-mount and exact-ownership checks.
Removed IDs leave the cache after a complete valid snapshot; a failed attempt
never publishes partial cache updates. No cache is persisted across processes.

A nonzero batch inspect, a batch-inspect timeout or an enumerated/inspected ID
mismatch permits **one** new complete enumeration and refresh. Both attempts,
including every 32-ID batch, share the original 25-second budget; each command
is also limited by its original 10/15-second ceiling and the remaining budget.
Fresh observations from an incomplete attempt are discarded. `ps` failures,
process creation failures, invalid IDs and invalid metadata fail immediately.
A second incomplete/failed attempt also fails. A complete resample still goes
through the original exact-service, locked-ID, running-state, restart-policy,
mount and capacity checks. A retry cannot extend an already exhausted budget.

The running loop uses a stricter **whole-cycle** deadline: the start of its
previous successful `WATCHDOG=1` send plus 25 seconds. That send's own elapsed
time, the configured poll wait, both Docker attempts, capacity measurement and
state persistence all consume this one budget. The first loop cycle starts
with 25 seconds. Snapshot calls take the earlier of their own 25-second limit
and this cycle deadline. For example, a 10-second poll and an instantaneous
previous notify leave at most 15 seconds for measurement and persistence; a
previous notify taking two seconds leaves at most 13 seconds. Neither a retry
nor the end of the poll wait resets that deadline.

This reserves the original five-second margin before the unchanged 30-second
unit watchdog. A late Docker call fails with its existing Docker reason.
Capacity or persistence exceeding the cycle deadline raises the fixed
`capacity_sample_timeout`, latches the deployment and uses the original stop
path. Atomic healthy-state writes check the deadline after temporary-file
fsync and before publishing; the loop also checks after persistence and before
notifying. It never sends a new watchdog notification to extend time for an
unfinished or overdue check. Standalone `arm`, `status`, and `fail-close`
retain their original snapshot budgets. As with the existing guard, a blocked
kernel filesystem operation cannot be preempted by a Python deadline; the
independent systemd watchdog and `ExecStopPost` remain its fallback.

A first nonzero inspect is only evidence that the CLI failed. Successful
resampling establishes a complete current sample; it does not establish that
a container disappeared or diagnose the original production incident.
Repeated permission or Docker API failures still latch the guard. The fixed
reason remains `docker_command_failed` for a nonzero exit and
`docker_unavailable` for a timeout, an exhausted snapshot deadline or a process
creation failure. The diagnostics distinguish those cases without interpreting
or saving stderr.

`docker_diagnostics` records contain only these fields:

| Field | Meaning |
| --- | --- |
| `stage` | `ps`, `batch_inspect`, `inspect_one`, `update`, or `term` |
| `attempt` | Snapshot attempt 1 or 2; individual stop calls use 1 |
| `batch_size` | Number of requested inspect IDs; 1 for individual calls, 0 for enumeration |
| `returncode` | Actual numeric exit code, or null if the process did not return |
| `timed_out` | Command timeout or exhausted snapshot deadline |
| `outcome` | `ok`, `nonzero_exit`, `timeout`, `deadline_exhausted`, `spawn_failed`, or `invalid_output` |
| `elapsed_seconds` | Nonnegative duration measured with the monotonic clock |
| `os_error_number` | Numeric OS error, present only for process creation failure |

The loop writes current sampling diagnostics to the existing `heartbeat.json`.
Its first `failure.json` preserves the triggering sample, including the first
failed attempt and final result; subsequent stop attempts do not rewrite it.
Each existing `stop-*.json` preserves the original failure `reason` and
`trigger_docker_diagnostics`; `invoked_by` identifies this call and
`pending_failure_recovered` records whether it published a pending latch.
The separate `docker_diagnostics` describes that stop attempt, alongside its
original status, fixed errors and exact-ID readback. A successfully resampled healthy loop
sample also updates **one bounded file**, `docker-resample.json`, so the next
normal heartbeat does not erase the latest recovery evidence. It records the
sample time, healthy result and both attempts. All files use the existing
private state directory and atomic mode-0600 writer. No raw stdout/stderr,
command arguments, new container IDs, paths or credentials enter diagnostic
records; the pre-existing armed/stop identity fields are unchanged.

`arm` and `status` return the same limited diagnostics in their JSON results
or fixed-code refusals. They do not write a diagnostic file into an unarmed
state directory, so a failed first `arm` does not acquire a new latch or
prevent the original installation procedure from retrying. A recovered
snapshot does not bypass any installation or readiness requirement.

## Maintenance and container controllers

An ordinary stop, restart or recreate of a protected resident container can
violate the guard's running-state or locked-ID checks. A future generic
container/Compose controller must recognize these exact registered resident
stacks and route the **whole maintenance operation** through the existing
reviewed maintenance/update lifecycle. Stopping the unit runs `ExecStopPost`;
it is not a way to leave containers running without protection. The controller
must await that stop attempt and read back the exact old IDs, disabled restart
policies and confirmed exits before continuing. An unconfirmed receipt does
not authorize a replacement or an automatic retry of an uncertain action.

For one explicit recovery with the **same locked IDs**, first verify the old
`failure.json` and every `stop-*.json`, read back that all are exited with
restart disabled, and confirm the cause is removed, free/budget thresholds are
safe, the fixed images/mounts/Compose labels are unchanged, and no replacement
container exists. Stop the old unit and wait for its `ExecStopPost` to finish
before changing any restart policy. Preserve the old state directory byte for
byte. Under a recorded operator decision, point a newly rendered unit and
root-owned config at a **new empty state directory** for the same deployment;
reload the unit while it is stopped. Use the already-reviewed maintenance
procedure to restore `unless-stopped` on exactly the same locked IDs, read back
the policies, then start those IDs in the approved order. In that supervised
window run `arm`, start the unit, require `is-active` and `status=ready`, and
compare the new `armed.json` IDs with the old receipt. Preserve the old and
new generation receipts. A changed ID, incomplete stop, stale source, or
missing capacity evidence requires a new review, not a silent rearm. This
does not change the deployment scope or delete any source log.

A planned image update or recreate is a **new deployment generation**. Preserve
the old failure/stop evidence, finish the existing controlled stop, and use
the reviewed deployment plan to validate the intended replacements, digest
images, exact Compose labels, all binds, restart policies and capacity.
Start in the approved order and arm the complete expected service set using
the matching root-owned config and a fresh empty state directory. Require
unit active, `status=ready` and continuing heartbeats; compare replaced IDs
with the approved replacement plan and unchanged IDs with the old marker.
Do not silently edit a running marker/config or apply the same-ID recovery
rule to intended replacements. The controller should retain the original
stop and new arm/readiness receipts as its operation result. External stacks
continue to use their own lifecycle; they still must not mount the protected
deployment root. No online maintenance switch or new privilege is provided
by this guard change.

Changing a running nine-service deployment to `knowledge-ten` is a new
installation generation, not a config edit in place. Stop and read back the
old exact IDs under the reviewed lifecycle, preserve its state and receipts,
prepare the new locked ten-service export and root-owned config, then arm all
ten into a fresh empty state directory before accepting `status=ready`.
The guard refuses a config digest change; it never silently adds an ID to
an existing marker. The new service also needs separate log, readiness, and
backup acceptance before use.

The guard cannot stop containers while the local Docker API itself is
unavailable, and it does not control an independently restarted Dockge
project. The candidate procedure must leave Dockge automatic recreation and
updates disabled and validate the unit's real crash, threshold, and stop
readback on the NAS. Free-space sampling is not a filesystem quota; abrupt
host-wide writes can cross the threshold between polls. This package makes
no 30-day retention or four-source query claim.
