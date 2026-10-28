"""Synthetic data and optional loopback SSH server; no personal configuration."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(__file__).parent / ".runtime"
if RUNTIME.is_dir():
    sys.path.insert(0, str(RUNTIME))
OUTPUT = Path(__file__).parent / ".output"
OUTPUT.mkdir(exist_ok=True)

from ops.infrastructure.transport import CommandResult, Deadline


def identity(index=1):
    return f"{index:064x}"


def inspect_value(index=1, *, health="none", state="running", project="synthetic", service="web"):
    return [identity(index), "/synthetic-web", "nginx:alpine", "sha256:" + "b" * 64,
            "2026-10-16T08:00:00Z", "2026-10-16T08:00:00Z", state, state == "running", "bridge",
            project, service, "/srv/stacks/synthetic", "/srv/stacks/synthetic/compose.yaml", "False",
            {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}]},
            [{"Type": "bind", "Source": "/srv/stacks/synthetic/data", "Destination": "/data", "RW": False}], health]


def engine_stats(index=1, *, cpu=1000, system=10000):
    return {"id": identity(index), "read": "2026-10-16T08:00:00Z",
            "cpu_stats": {"cpu_usage": {"total_usage": cpu}, "system_cpu_usage": system, "online_cpus": 8},
            "memory_stats": {"usage": 10000, "limit": 100000, "stats": {"inactive_file": 1000}},
            "blkio_stats": {"io_service_bytes_recursive": [{"op": "Read", "value": 1024}, {"op": "Write", "value": 2048}]},
            "networks": {"eth0": {"rx_bytes": 4000, "tx_bytes": 8000}}}


def http_response(value, status=200):
    body = json.dumps(value, separators=(",", ":")).encode()
    return f"HTTP/1.1 {status} Synthetic\r\nContent-Length: {len(body)}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n".encode() + body


def sections(values):
    output = bytearray()
    for name, value in values.items():
        code, raw = value if isinstance(value, tuple) else (0, value)
        if isinstance(raw, str):
            raw = raw.encode()
        output.extend(f"\n__TIANSHU_BEGIN_{name}__\n".encode() + raw + f"\n__TIANSHU_END_{name}__:{code}\n".encode())
    return bytes(output)


def host_sections(*, uptime=100, cpu="100 0 50 850 0 0 0 0 20 0", received=1000, sent=2000, ifindex=2, openwrt=False):
    values = {"hostname": "synthetic-node\n", "kernel": "Linux 6.1\n", "boot": "synthetic-boot\n",
              "uptime": f"{uptime}.00 50.00\n", "load": "0.10 0.20 0.30 1/100 5\n",
              "cpu": "cpu  " + cpu + "\ncpu0 1 0 0 1\n",
              "memory": "MemTotal: 10000 kB\nMemAvailable: 4000 kB\n",
              "interfaces": "Inter-| Receive | Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n" + f"eth0: {received} 1 0 0 0 0 0 0 {sent} 1 0 0 0 0 0 0\n",
              "filesystems": "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/synthetic 10000 2000 7000 22% /data pool\n",
              "ifindices": f"eth0\t{ifindex}\n"}
    if openwrt:
        values.update(board=json.dumps({"model": "Synthetic router", "board_name": "fixture", "system": "Synthetic SoC", "release": {"distribution": "OpenWrt", "version": "fixture", "revision": "test", "target": "test/generic", "description": "Synthetic OpenWrt"}}),
                      info=json.dumps({"uptime": uptime, "load": [6553, 13107, 19660], "memory": {"total": 10240000, "free": 2048000, "shared": 0, "buffered": 102400, "available": 4096000}}),
                      ubus_interfaces=json.dumps({"interface": [{"interface": "wan", "up": True, "pending": False, "available": True, "autostart": True, "device": "eth0", "l3_device": "eth0", "proto": "dhcp", "uptime": 100, "ipv4-address": [{"address": "192.0.2.10", "mask": 24}], "ipv6-address": []}]}))
    return sections(values)


COMPOSE = b"""name: synthetic
services:
  web:
    image: nginx:alpine
    ports:
      - '127.0.0.1:18080:80'
    volumes:
      - './data:/data:ro'
"""


def private_file_permissions(path):
    if os.name == "nt":
        # Only the freshly generated synthetic test key is modified. No user
        # SSH directory/configuration or existing identity is touched.
        environment = dict(os.environ, TIANSHU_SYNTHETIC_KEY=str(path))
        script = "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; $acl=New-Object System.Security.AccessControl.FileSecurity; $acl.SetOwner($sid); $acl.SetAccessRuleProtection($true,$false); $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow'); $acl.AddAccessRule($rule); [System.IO.File]::SetAccessControl($env:TIANSHU_SYNTHETIC_KEY,$acl)"
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=environment,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        path.chmod(0o600)


class FixtureTransport:
    def __init__(self, config=None, node=None, deadline=None):
        self.deadline = deadline or Deadline(10)
        self.ids = [identity()]
        self.metadata = {identity(): inspect_value()}
        self.cpu = 1000
        self.system = 10000
        self.host = host_sections(openwrt=node and node.kind == "openwrt")
        self.log = b"2026-10-16T08:00:00Z synthetic log\n"
        self.commands = []

    def command(self, argv, payload=b"", **kwargs):
        self.commands.append((argv, payload))
        if "ps" in argv:
            return CommandResult(("\n".join(self.ids) + ("\n" if self.ids else "")).encode(), exit_code=0)
        if "inspect" in argv:
            ids = [value for value in argv if re.fullmatch("[a-f0-9]{64}", value)]
            present = [json.dumps(self.metadata[value]) for value in ids if value in self.metadata]
            missing = len(present) != len(ids)
            return CommandResult(("\n".join(present) + "\n").encode(), b"Error: No such object" if missing else b"", 1 if missing else 0)
        if "logs" in argv:
            cap = kwargs["max_output"]
            return CommandResult(self.log[:cap], exit_code=0, truncated=len(self.log) > cap)
        if "dial-stdio" in argv:
            selected = re.findall(rb"GET /v[0-9.]+/containers/([a-f0-9]{64})/stats\?", payload)
            if selected:
                return CommandResult(b"".join(http_response(engine_stats(int(value, 16), cpu=self.cpu, system=self.system)) for value in selected), exit_code=0)
            return CommandResult(http_response({"ApiVersion": "1.43"}), exit_code=0)
        raise AssertionError(argv)

    def script(self, script, **kwargs):
        self.commands.append((script, b""))
        if "cat /proc/stat" in script:
            return CommandResult(self.host, exit_code=0)
        if "find " in script:
            return CommandResult(b"/srv/stacks/synthetic/compose.yaml\n", exit_code=0)
        if "head -c" in script:
            return CommandResult(sections({"stack_0": COMPOSE}), exit_code=0)
        raise AssertionError(script)


class LoopbackSSH:
    """Actual SSH transport with synthetic command responses on loopback only."""

    def __init__(self, directory, fixture):
        import paramiko
        logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)
        self.paramiko = paramiko
        self.fixture = fixture
        self.host_key = paramiko.RSAKey.generate(2048)
        self.client_key = paramiko.RSAKey.generate(2048)
        self.key_path = Path(directory) / "synthetic_key"
        self.client_key.write_private_key_file(str(self.key_path))
        private_file_permissions(self.key_path)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.known_hosts = Path(directory) / "synthetic_known_hosts"
        self.known_hosts.write_text(f"[127.0.0.1]:{self.port} {self.host_key.get_name()} {self.host_key.get_base64()}\n", encoding="ascii")
        self.stopped = threading.Event()
        self.transports = []
        self.calls = []
        self.thread = threading.Thread(target=self._accept, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def _accept(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        paramiko = self.paramiko
        owner = self

        class Server(paramiko.ServerInterface):
            def __init__(self):
                self.command = None
                self.ready = threading.Event()

            def check_auth_publickey(self, username, key):
                return paramiko.AUTH_SUCCESSFUL if username == "synthetic" and key == owner.client_key else paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "publickey"

            def check_channel_request(self, kind, chanid):
                return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_channel_exec_request(self, channel, command):
                self.command = command.decode("utf-8")
                self.ready.set()
                return True

        transport = paramiko.Transport(connection)
        self.transports.append(transport)
        transport.add_server_key(self.host_key)
        server = Server()
        try:
            transport.start_server(server=server)
            channel = transport.accept(5)
            if channel is None or not server.ready.wait(5):
                return
            payload = bytearray()
            while True:
                raw = channel.recv(8192)
                if not raw:
                    break
                payload.extend(raw)
                if len(payload) > 1024 * 1024:
                    return
                if "system dial-stdio" in server.command and payload.endswith(b"Connection: close\r\n\r\n"):
                    break  # HTTP framing, rather than stdin EOF, ends a request.
            self.calls.append((server.command, bytes(payload)))
            if "sh -s" in server.command:
                result = self.fixture.script(payload.decode("utf-8"))
            else:
                argv = shlex.split(server.command)
                if argv[0] == "LC_ALL=C":
                    argv = argv[1:]
                result = self.fixture.command(argv, bytes(payload), max_output=262144)
            if result.stdout:
                channel.sendall(result.stdout)
            if result.stderr:
                channel.sendall_stderr(result.stderr)
            channel.send_exit_status(result.exit_code or 0)
            channel.shutdown_write()
            channel.close()
        except (OSError, EOFError, paramiko.SSHException):
            pass
        finally:
            transport.close()

    def __exit__(self, *_):
        self.stopped.set()
        self.listener.close()
        for transport in self.transports:
            transport.close()
        self.thread.join(timeout=1)
