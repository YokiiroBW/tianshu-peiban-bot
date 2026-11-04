# Independent operations watcher

This directory contains an installable host-side watcher. Nothing here is run
or installed by building the application. The deployment owner supplies an
absolute spool path, explicit container names, role names and deployment
generation, then starts `python3 host_watch.py --config /absolute/config.json`
under the host's normal service manager. `host-watch.example.json` is an empty
template; it deliberately contains no recipients, container names or secrets.

Each optional `targets` entry has `role`, `container`, `generation`, and
`restart_on_stopped`. The role must be one of interactive, model-broker,
bot-foreground, bot-background, independent, operations. Automatic start is
off unless explicitly selected; even then it is capped at three and only an
exited/created container whose actual Docker labels `com.tianshu.product`,
`com.tianshu.role` and `com.tianshu.generation` match the registration and
whose ID remains the same on immediate reinspection can be started. Docker
inspection failure, degraded aggregate health and an owner/generation mismatch
do not authorize any restart. Shared host and other product containers are
never targets. A shared container with several roles should be monitored using
the heartbeat endpoint rather than pretending it has one role label.

Optional `heartbeat_url` reads the product health response including
`missing_roles`, and optional `database_probe` is an object with `host`,
`port`, `generation`. The latter is only a TCP reachability observation; it
does not prove database readiness. Heartbeats and container availability do
not prove business progress or recovery. The operations process imports spool
events through `HostSpool`; mount the same spool into both processes and pass
its container-visible path as `host_spool_directory` to `build_operations`.
Import preserves incident identity and any SMTP receipt. A fresh in-flight
send is fenced by a file lock; a sender that died after submission is unknown,
not silently resent. Delivery has at-least-once limits across isolated host and
database failures and SMTP acceptance is not a read receipt.

For mail, `smtp` takes `host`, `port`, `tls` (`tls` or `starttls`), `sender`,
`recipient`, optional `username` and `password_env`. Passwords are read only
from an explicitly named `TIANSHU_OPS_SMTP_*` environment variable, never
stored in spool files. Without a target the event is retained but no address is
guessed. Provision host service access to the spool and the narrowly scoped
Docker read/start capability separately; no socket or command interface is
made available to the model or operations HTTP API. An external monitor is
still required to report an entire host or NAS power outage immediately.
