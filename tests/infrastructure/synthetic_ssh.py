"""Standalone loopback fixture for package/platform joint verification.

Run from an exported copy of these test files with the same ops module on
PYTHONPATH. JSON readiness goes to stdout and optionally --ready-file. Keep
stdin open; EOF or one input byte stops the server. All identity material is
freshly generated synthetic test material in --directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from helpers import FixtureTransport, LoopbackSSH, host_sections, identity, inspect_value


class AdvancingFixture(FixtureTransport):
    def __init__(self, count):
        super().__init__()
        self.ids = [identity(index) for index in range(1, count + 1)]
        self.metadata = {identity(index): inspect_value(index) for index in range(1, count + 1)}
        self.tick = 0

    def script(self, script, **kwargs):
        if "cat /proc/stat" in script:
            tick = self.tick
            self.tick += 1
            self.host = host_sections(openwrt=True, uptime=100 + tick * 10,
                                      cpu=f"{100 + tick * 30} 0 {50 + tick * 40} {850 + tick * 30} 0 0 0 0",
                                      received=1000 + tick * 1000, sent=2000 + tick * 2000)
        result = super().script(script, **kwargs)
        return result

    def command(self, argv, payload=b"", **kwargs):
        result = super().command(argv, payload, **kwargs)
        if b"/stats?" in payload:
            self.cpu += 100
            self.system += 400
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--containers", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.containers <= 10000:
        parser.error("containers must be 1..10000")
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    fixture = AdvancingFixture(args.containers)
    with LoopbackSSH(directory, fixture) as server:
        connection = {"host": "127.0.0.1", "port": server.port, "user": "synthetic",
                      "identity_file": str(server.key_path), "known_hosts_file": str(server.known_hosts),
                      "docker": {"enabled": True, "command": ["synthetic-docker"], "socket_path": "/var/run/synthetic.sock"}, "stacks_root": "/srv/stacks"}
        config = {"version": "infrastructure-node.v1", "state_dir": str(directory / "state"), "ssh_command": [shutil.which("ssh") or "ssh"],
                  "nodes": {"fixture-nas": {**connection, "label": "Synthetic NAS", "kind": "synology"},
                            "fixture-router": {**connection, "label": "Synthetic OpenWrt", "kind": "openwrt"}}}
        config_path = directory / "executor-config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        ready = {"state": "ready", "synthetic": True, "config_path": str(config_path),
                 "node_ids": list(config["nodes"]), "container_ids": fixture.ids, "host": "127.0.0.1", "port": server.port}
        if args.ready_file:
            args.ready_file.write_text(json.dumps(ready), encoding="utf-8")
        print(json.dumps(ready), flush=True)
        try:
            sys.stdin.buffer.read(1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
