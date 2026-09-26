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
never wildcards or prefixes. `images` lists the nine digest-pinned
`.Config.Image` values from the locked resident export. `binds` lists **all**
`type=bind` mounts of each of the nine services as objects with `source`,
`target`, and boolean `read_only`; use the first export for Platform and the
final export for the other eight. The guard matches Docker's selected mount
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

   `arm` requires nine running, digest-pinned services with exact project,
   service, Compose workdir/file, root bind, and `unless-stopped` restart
   identity. It checks both capacity thresholds before atomically locking all
   nine 64-character IDs in `armed.json`. It refuses if the state is already
   armed or latched. If `arm` refuses, the installer must stop its just-started
   exact IDs through its already-reviewed lifecycle path; there is no armed
   marker for this guard to use.
3. Enable/start the unit, then require `systemctl is-active --quiet` and:

   ```text
   python3 -B -m ops.resident_capacity.guard status --config /ABSOLUTE/resident-capacity.json
   ```

   `status` returns `ready` only when the systemd loop produced a fresh
   heartbeat and a new read-only check still sees the same nine IDs and safe
   capacity. If it refuses, run `fail-close` below and read back that every
   exact resident container exited before leaving the first-install process.
   A manual boolean is not a status readback.

## Latch and failure behavior

The loop checks capacity and exact container identity at most every ten
seconds and sends `READY=1` only after the first healthy sample. It then
sends `WATCHDOG=1`. The systemd 219 unit uses `Type=notify`,
`WatchdogSec=30s`, unlimited start retries, and `ExecStopPost` invoking:

```text
python3 -B -m ops.resident_capacity.guard fail-close --state-dir /ABSOLUTE/capacity-state --reason=service_exit
```

Thus a crashed or watchdog-killed main process is stopped by an independent
systemd control process; it does not rely on a Python signal handler. The
fail-close command writes `failure.json` before effects, disables restart on
the nine locked resident IDs only, reads policy back, sends SIGTERM only,
and confirms they exited. It never SIGKILLs or deletes logs. `stop-*.json`
retains each attempt and its fixed-code errors. A partial stop returns
`unconfirmed`. The installer/operator must keep the stack stopped and review
that result; it cannot claim protection on the basis of a TERM request alone.
If another container replaces a locked ID, the guard reports it as unconfirmed
and does not automatically stop that replacement under a reused project name.
Once latched, the loop cannot return to ready or rearm without an explicit
review and a new state generation.

The configured TERM wait is at most 120 seconds. Docker commands time out at
10 seconds each and the batch inspect at 15 seconds. The calculated upper
bound for external Docker waits plus TERM polling is 530 seconds; the unit's
`TimeoutStopSec=600s` leaves 70 seconds for state writes and scheduling.
A hung kernel filesystem operation has no guaranteed Python timeout, so the
systemd stop result and receipt still need live readback. The unit never
claims a stop solely because its deadline elapsed.

For one explicit recovery with the **same nine IDs**, first verify the old
`failure.json` and every `stop-*.json`, read back that all nine are exited with
restart disabled, and confirm the cause is removed, free/budget thresholds are
safe, the fixed images/mounts/Compose labels are unchanged, and no replacement
container exists. Stop the old unit and wait for its `ExecStopPost` to finish
before changing any restart policy. Preserve the old state directory byte for
byte. Under a recorded operator decision, point a newly rendered unit and
root-owned config at a **new empty state directory** for the same deployment;
reload the unit while it is stopped. Use the already-reviewed maintenance
procedure to restore `unless-stopped` on exactly the same nine IDs, read back
the policies, then start those IDs in the approved order. In that supervised
window run `arm`, start the unit, require `is-active` and `status=ready`, and
compare the new `armed.json` IDs with the old receipt. Preserve the old and
new generation receipts. A changed ID, incomplete stop, stale source, or
missing capacity evidence requires a new review, not a silent rearm. This
does not change the deployment scope or delete any source log.

The guard cannot stop containers while the local Docker API itself is
unavailable, and it does not control an independently restarted Dockge
project. The candidate procedure must leave Dockge automatic recreation and
updates disabled and validate the unit's real crash, threshold, and stop
readback on the NAS. Free-space sampling is not a filesystem quota; abrupt
host-wide writes can cross the threshold between polls. This package makes
no 30-day retention or four-source query claim.
