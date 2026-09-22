"""Owned product processes; restart facts are separate from normal shutdown claims."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from acceptance.evidence import digest
from acceptance.transport import Client, Missing, check
from product_inputs import ROLES


class ProductLifecycle:
    def command(self, role):
        config = str(self.root / "config" / (role + ".json"))
        host = ["--host", "127.0.0.1", "--port", str(self.ports[role])]
        cert, key = [
            str(self.root / "tls" / (role + suffix)) for suffix in (".pem", ".key")
        ]
        if role == "platform":
            return "services.platform", ["--settings", config, "serve", *host]
        if role == "companion":
            return "tianshu_companion.runtime_cli", [
                "--config",
                config,
                "--contracts",
                str(self.snapshots / "contracts/text-dialogue/v1"),
                "--database",
                self.configs[role]["database_path"],
                "--log-dir",
                str(self.root / "logs" / role),
                *host,
                "--tls-cert",
                cert,
                "--tls-key",
                key,
            ]
        if role == "memory":
            return "tianshu_memory.cli", [
                "--config",
                config,
                "serve",
                *host,
                "--tls-certfile",
                cert,
                "--tls-keyfile",
                key,
                "--allowed-host",
                "127.0.0.1:" + str(self.ports[role]),
                "--diagnostics-contract",
                str(self.snapshots / "contracts/diagnostics/v1"),
            ]
        return "tianshu_gateway", [
            "--settings",
            config,
            *host,
            "--tls-cert",
            cert,
            "--tls-key",
            key,
        ]

    def client(self, role):
        return Client(
            {"url": self.urls[role], "ca_file": str(self.root / "tls/ca.pem")}, False
        )

    def initialize(self):
        for operation in ("migrate-profiles", "migrate-sources"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tianshu_memory.cli",
                    "--config",
                    str(self.root / "config/memory.json"),
                    operation,
                    "--backup",
                    str(self.root / (operation + ".sqlite")),
                ],
                env=self.envs["memory"],
                capture_output=True,
                timeout=30,
            )
            check(result.returncode == 0, "memory_initial_schema_failed")

    def start(self):
        self.initialize()
        for role in ROLES:
            self.start_role(role)

    def start_role(self, role):
        module, arguments = self.command(role)
        evidence = self.root / (role + "-loaded.json")
        argv = [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("product_worker.py")),
            role,
            str(self.root / "config" / (role + ".json")),
            str(evidence),
            module,
            *arguments,
        ]
        process = subprocess.Popen(
            argv,
            env=self.envs[role],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        self.children[role] = process
        self.launch[role] = {
            "pid": process.pid,
            "source": self.binding["products"][role],
            "module": module,
            "config_sha256": digest(
                (self.root / "config" / (role + ".json")).read_bytes()
            ),
            "worker_sha256": digest(
                Path(__file__).with_name("product_worker.py").read_bytes()
            ),
            "python": sys.version.split()[0],
        }
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            check(process.poll() is None, "product_process_exited_" + role)
            try:
                status, _ = self.client(role).request("GET", "/health/live")
                if status == 200:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            raise Missing("product_listener_unavailable_" + role)

    def close(self):
        stopped = {}
        for role, child in reversed(list(self.children.items())):
            if child.poll() is None:
                child.send_signal(
                    signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM
                )
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                    stopped[role] = "forced_owned_test_process"
            stopped.setdefault(role, "exited")
        if self.model:
            self.model.close()
        return stopped
