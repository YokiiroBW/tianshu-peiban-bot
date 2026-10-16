from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import OUTPUT, ROOT, RUNTIME, FixtureTransport, LoopbackSSH, host_sections, identity, private_file_permissions
from jsonschema import Draft202012Validator, FormatChecker

from ops.infrastructure.protocol import VERSION
from ops.infrastructure.transport import Deadline, run_bounded


class ProcessBoundsTest(unittest.TestCase):
    def test_stdout_stderr_flood_is_bounded_without_deadlock(self):
        program = "import os;\nwhile True:\n os.write(1,b'x'*8192); os.write(2,b'y'*8192)"
        started = time.monotonic()
        result = run_bounded([sys.executable, "-c", program], b"", Deadline(2), max_output=32768, stderr_limit=32768)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.stdout) + len(result.stderr), 32768)
        self.assertLess(time.monotonic() - started, 2)

    def test_timeout_reaps_descendant_with_inherited_pipes(self):
        with tempfile.TemporaryDirectory(dir=OUTPUT) as directory:
            pid_file = Path(directory) / "synthetic_child.pid"
            program = "import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
            started = time.monotonic()
            result = run_bounded([sys.executable, "-c", program, str(pid_file)], b"", Deadline(.6))
            self.assertTrue(result.timed_out)
            self.assertLess(time.monotonic() - started, 2)
            child_pid = int(pid_file.read_text())
            if os.name == "nt":
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x100000, False, child_pid)
                if handle:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 100), 0)
                    kernel.CloseHandle(handle)
            else:
                status = Path(f"/proc/{child_pid}/stat")
                if status.exists():
                    self.assertEqual(status.read_text().split()[2], "Z")

    @unittest.skipUnless(shutil.which("ssh") and importlib.util.find_spec("paramiko"), "optional loopback SSH dependencies unavailable")
    def test_exported_standalone_fixture_runs_outside_runtime_package(self):
        with tempfile.TemporaryDirectory(dir=OUTPUT) as directory:
            directory = Path(directory)
            exported = directory / "external-fixture"
            exported.mkdir()
            for name in ("helpers.py", "synthetic_ssh.py"):
                shutil.copy2(Path(__file__).parent / name, exported / name)
            ready_path = directory / "ready.json"
            environment = dict(os.environ)
            environment["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(RUNTIME)])
            server = subprocess.Popen([sys.executable, str(exported / "synthetic_ssh.py"), "--directory", str(directory / "connection"),
                                       "--ready-file", str(ready_path), "--containers", "150"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=directory, env=environment)
            try:
                deadline = time.monotonic() + 5
                while not ready_path.exists() and server.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready_path.exists())
                ready = json.loads(ready_path.read_text())
                self.assertEqual(len(ready["container_ids"]), 150)
                request = {"version": VERSION, "request_id": "outside-package", "node_id": "fixture-router",
                           "operation": "node.snapshot", "params": {}, "timeout_ms": 10000}
                process = subprocess.run([sys.executable, "-m", "ops.infrastructure", "--config", ready["config_path"]],
                                         input=json.dumps(request).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         cwd=directory, env=environment, timeout=12)
                self.assertEqual(process.returncode, 0)
                response = json.loads(process.stdout)
                self.assertEqual(response["status"], "ok", response)
                self.assertEqual(response["data"]["identity"]["kind"], "openwrt")
            finally:
                server.communicate(input=b"stop", timeout=5)
            self.assertEqual(server.returncode, 0)


@unittest.skipUnless(shutil.which("ssh") and importlib.util.find_spec("paramiko"), "optional loopback SSH test dependencies unavailable")
class LoopbackProcessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((ROOT / "contracts/infrastructure-node/candidate-v1/response.schema.json").read_text())
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=OUTPUT)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.fixture = FixtureTransport()
        self.ssh = self.enterContext(LoopbackSSH(self.directory, self.fixture))
        self.node = {"label": "Synthetic OpenWrt", "kind": "openwrt", "host": "127.0.0.1", "port": self.ssh.port, "user": "synthetic",
                     "identity_file": str(self.ssh.key_path), "known_hosts_file": str(self.ssh.known_hosts),
                     "docker": {"enabled": True, "command": ["synthetic-docker"], "socket_path": "/var/run/synthetic.sock"}, "stacks_root": "/srv/stacks"}
        self.config_path = self.directory / "synthetic_executor.json"
        self.save_config()

    def save_config(self):
        config = {"version": VERSION, "state_dir": str(self.directory / "state"), "ssh_command": [shutil.which("ssh")], "nodes": {"synthetic": self.node}}
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def cli(self, operation, params=None, *, raw=None):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(RUNTIME), environment.get("PYTHONPATH", "")])
        request = {"version": VERSION, "request_id": "ssh-process-test", "node_id": None if operation == "node.catalog" else "synthetic",
                   "operation": operation, "params": params or {}, "timeout_ms": 10000}
        process = subprocess.run([sys.executable, "-m", "ops.infrastructure", "--config", str(self.config_path)],
                                 input=raw or json.dumps(request).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 cwd=ROOT, env=environment, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
        self.assertEqual(len(process.stdout.splitlines()), 1)
        response = json.loads(process.stdout)
        self.validator.validate(response)
        return response

    def test_actual_cli_to_openssh_to_synthetic_node_all_read_operations(self):
        catalog = self.cli("node.catalog")
        self.assertEqual(catalog["status"], "ok")
        self.assertEqual(self.ssh.calls, [])
        self.fixture.host = host_sections(openwrt=True)
        first = self.cli("node.snapshot")
        self.assertEqual(first["status"], "ok", first)
        self.assertEqual(first["data"]["cpu"]["state"], "first_sample")
        self.fixture.host = host_sections(openwrt=True, uptime=110, cpu="130 0 90 880 0 0 0 0", received=2000)
        second = self.cli("node.snapshot")
        self.assertEqual(second["data"]["interfaces"][0]["receive_bytes_per_second"], 100)
        self.assertEqual(second["data"]["openwrt"]["interfaces"][0]["l3_device"], second["data"]["interfaces"][0]["name"])
        inventory = self.cli("docker.inventory")
        self.assertEqual(inventory["status"], "ok", inventory)
        self.assertEqual(inventory["data"]["containers"][0]["id"], identity())
        stats = self.cli("docker.stats", {"container_ids": [identity()]})
        self.assertEqual(stats["status"], "ok", stats)
        self.assertEqual(stats["data"]["containers"][0]["memory"]["cache_adjusted_usage_bytes"], 9000)
        self.fixture.cpu += 100
        self.fixture.system += 400
        next_stats = self.cli("docker.stats", {"container_ids": [identity()]})
        self.assertEqual(next_stats["data"]["containers"][0]["cpu"]["usage_ratio"], 2)
        compose = self.cli("compose.inventory")
        self.assertEqual(compose["status"], "ok", compose)
        self.assertEqual(compose["data"]["stacks"][0]["drift_status"], "in_sync")
        logs = self.cli("docker.logs", {"container_id": identity(), "max_bytes": 1024})
        self.assertEqual(logs["status"], "ok", logs)
        self.assertIn("synthetic log", logs["data"]["stdout"])
        self.fixture.log = ("真实SSH合成日志\n" * 2000).encode()
        truncated = self.cli("docker.logs", {"container_id": identity(), "max_bytes": 1024})
        self.assertEqual(truncated["status"], "partial", truncated)
        self.assertLessEqual(truncated["data"]["returned_bytes"], 1024)
        self.assertTrue(truncated["data"]["truncated"])
        self.assertGreater(len(self.ssh.calls), 10)

    def test_invalid_request_never_connects_and_domain_failure_is_json(self):
        invalid = self.cli("node.snapshot", {"command": "reboot"})
        self.assertEqual(invalid["status"], "invalid")
        self.assertEqual(self.ssh.calls, [])
        duplicate = self.cli("node.snapshot", raw=b'{"version":1,"version":2}')
        self.assertEqual(duplicate["reason_codes"], ["invalid_json"])
        self.assertEqual(self.ssh.calls, [])

    def test_wrong_synthetic_identity_and_host_key_are_explained(self):
        import paramiko
        wrong = self.directory / "wrong_synthetic_key"
        paramiko.RSAKey.generate(2048).write_private_key_file(str(wrong))
        private_file_permissions(wrong)
        self.node["identity_file"] = str(wrong)
        self.save_config()
        failure = self.cli("node.snapshot")
        self.assertEqual(failure["reason_codes"], ["ssh_auth_failed"], failure)
        self.node["identity_file"] = str(self.ssh.key_path)
        wrong_hosts = self.directory / "wrong_synthetic_hosts"
        key = paramiko.RSAKey.generate(2048)
        wrong_hosts.write_text(f"[127.0.0.1]:{self.ssh.port} {key.get_name()} {key.get_base64()}\n")
        self.node["known_hosts_file"] = str(wrong_hosts)
        self.save_config()
        failure = self.cli("node.snapshot")
        self.assertEqual(failure["reason_codes"], ["ssh_host_key_failed"], failure)
        self.assertEqual(self.ssh.calls, [])

    def test_missing_configured_identity_never_connects(self):
        self.node["identity_file"] = str(self.directory / "not-created-synthetic-key")
        self.save_config()
        failure = self.cli("node.snapshot")
        self.assertEqual(failure["reason_codes"], ["ssh_identity_unavailable"])
        self.assertEqual(self.ssh.calls, [])


if __name__ == "__main__":
    unittest.main()
