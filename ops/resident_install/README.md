# A1-R1 resident first install

This module prepares one new four-product resident bundle and, on Linux, performs
one first-install authority attempt. It uses the existing bundle builder and the
fixed Memory and Platform public CLIs. It does not use the synthetic bootstrap
or write any product database directly.

## Input contract with A3

- A1 receives a candidate manifest with the four fixed product commits in
  `install.py` and OBS source commit
  `a194fa7b527ac2da0f13b8c3e95e76a1b836b4b3`.
- The site JSON names the project `tianshu-v2-resident`, uses the
  `nas-cpuset-resident-v1` profile, binds the web console to a private LAN IP
  over HTTP, and supplies operator-owned TLS certificates for all four internal
  services. The Platform settings referenced by that site JSON contain the
  chosen real admin username and `{"$password_env":"TS_ADMIN_PASSWORD"}`.
- A1 reads a mode-0600 credentials JSON containing all other named environment
  values and a separate mode-0600 UTF-8 admin password file with one terminating
  newline. The password must be 12–256 characters. The builder stores only its
  scrypt verifier in Platform settings. The admin's Platform token remains
  independent of every service and diagnostics token.
- A1 creates a new absolute bundle root. A3 consumes its ordinary
  `deployment.json`, `compose.json`, `release-manifest.json`, and
  `bundle-integrity.json`; A1 adds no extra prerequisite marker for the
  exporter. A3's source and Dockge export is a separate stage and starts no
  services.

The private credentials JSON contains environment variable names as keys and
values as strings; it must omit `TS_ADMIN_PASSWORD`. Values are never passed
as command arguments or printed. A configured provider must appear explicitly
in the site Platform and Gateway settings, its secret reference must resolve to
an independent value in the credentials file, and activation requires a
mode-0600 publication JSON accepted by the fixed Platform CLI. Publication
timestamps must be fresh at activation. Without a provider, web model dialogue
is unavailable and the report says so; no placeholder provider is created.

## Stages

1. On Linux, use `python -m ops.resident_install.install prepare --manifest MANIFEST
   --site SITE --contracts CONTRACTS --credentials CREDENTIALS
   --admin-password-file PASSWORD --admin-username USER
   --bundle-root NEW_ABSOLUTE_DIRECTORY`. All file arguments are paths.
   The production CLI requires POSIX mode checks for protected inputs.
2. Configure OBS with A3's public adapter, then export the resident/OBS Dockge
   projects to a new output directory. Its mode-0600
   `resident-export.lock.json` must bind the bundle
   manifest and integrity hashes, all four fixed product commits, the fixed
   OBS commit, the deployment root, and nine digest-pinned images. All nine
   image digests must already appear in local Docker `RepoDigests`.
   On the target Linux host, establish UID/GID 10001 access for the prepared
   state, logs, configuration and contracts as required by the existing bundle
   permission checker. Activation refuses mismatches; it never silently
   changes ownership.
3. Copy the complete bundle and this first export to the declared empty Linux
   deployment root. Product data/logs and OBS data must all be empty, and no
   container may belong to either fixed Compose project or mount the deployment
   root, including stopped containers from other projects.
   With stopped writers, use
   `python -m ops.resident_install.install activate --bundle-root BUNDLE
   --export-lock FIRST_EXPORT_LOCK --export-repository FIXED_ROOT_REPO
   --final-export-output NEW_EMPTY_OUTPUT`.
   This validates the integrity-indexed `BUNDLE/compose.json` and recomputes
   the complete first export using every required exporter module from fixed
   A3 Git commit `8e381646cee06f37a61e80c16e9e2b50cd5984a9`.
   It compares the lock and both Compose files byte-for-byte before any Docker
   command, then uses the
   first exported core Compose with image pulls disabled: Compose config,
   Memory schema 1→2→3 public migrations with backups, Platform-only start
   and healthy wait, Platform read-only preflight, optional model config
   publication, then `config-entry` issue for Gateway. The signed ref is
   stored only in `private/gateway.env`, mode 0600, and reindexed. At this
   point only Platform runs; its mounted settings and exported service
   definition have not changed.
4. Issuance makes the first export lock stale. `activate` immediately invokes
   fixed A3 Git exporter from `FIXED_ROOT_REPO` into `NEW_EMPTY_OUTPUT`,
   then calls the same readback as `finalize`. Before any Docker effects it
   checks that the fixed A3 and OBS Git objects and empty output target are
   available. The final lock is rederived from the updated bundle; both Compose
   files must be byte-identical to the first export, only the same Platform
   container with the expected project, image and bind mounts may exist,
   and at least 120 seconds of source lifetime must remain. If export or
   readback fails after issue, leave all other services stopped. An operator
   may diagnose the one attempt and explicitly run
   `python -m ops.resident_install.install finalize --bundle-root BUNDLE
   --export-lock NEW_LOCK --first-export-lock FIRST_EXPORT_LOCK` after a fresh
   A3 export; `issue` is never retried
   by `activate`.
5. Use the final export for Dockge import and separately reviewed startup of
   Memory, Gateway, Companion and OBS. Authenticate live readiness before
   calling the candidate usable. Every stage keeps `release_ready=false`.

Activation writes `reports/resident-install/attempt.json` before Docker and
individual stage markers before every side effect. Any failure leaves the
attempt and a fixed-code `failure.json`; automatic rerun is refused. An
interrupted private ref update leaves `INCOMPLETE` for manual diagnosis.
The short-lived initial origin is never automatically reissued or given a
longer TTL. If it expires while only Platform is running, an operator may
explicitly run `python -m ops.resident_install.install reauthorize
--bundle-root BUNDLE --first-export-lock FIRST_EXPORT_LOCK
--export-repository FIXED_ROOT_REPO --final-export-output NEW_EMPTY_OUTPUT`.
This command
requires the old expiry, original Platform container identity and unchanged
first Compose files, then makes one public-CLI issue attempt with a marker
beforehand. It never retries automatically. It invokes A3 export into the
prechecked new directory and `finalize` in the same run. The second readback
is stored in `final-export-after-renewal.json`. If any other service
has started or the one attempt is uncertain, the command refuses and leaves
the candidate pending manual diagnosis.
In that pending state the module leaves Platform running and all other
services stopped; it never treats an expired ref as readiness. If the operator
chooses to stop Platform, follow A3's exact container-ID restart-disable,
readback, SIGTERM and wait procedure before any maintenance.

## Local verification boundary

The isolated Windows joint test explicitly bypasses only the POSIX input-mode
gate through a test-only Python argument; the production CLI refuses Windows
preparation. The isolated Windows test invokes the installed fixed Memory and Platform
public CLIs against new synthetic-data SQLite files, then checks private ref
injection and bundle integrity. It requires the original-byte contract tree
via `TS_FIXED_CONTRACTS`. It does not prove Docker, Linux file ownership,
NAS resources, a real provider, or live readiness. Those checks belong to
joint A1/A3/A4 acceptance after the resident profile and Dockge export merge.
