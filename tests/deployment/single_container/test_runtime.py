import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from deploy.single_container.comics_control import ComicsController, ControlError
from deploy.single_container.health import check_state, probe, validate_probe
from deploy.single_container.process import ProcessGroup, dependency_order, environment_for, validate_process
from deploy.single_container.runtime import Runtime, load_config, write_state


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
POSIX = os.name == "posix"
LINUX_PEER = POSIX and hasattr(socket, "SO_PEERCRED")


def port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.events = self.root / "events.jsonl"
        self.groups = []

    def tearDown(self):
        for group in self.groups:
            if group.enabled:
                group.stop()
        self.directory.cleanup()

    def specification(self, name="memory", depends=None, extra=(), **options):
        number = port()
        raw = {"name": name, "argv": [sys.executable, "-B", str(HERE / "worker.py"),
               "--name", name, "--port", str(number), "--events", str(self.events), *extra],
               "depends_on": depends or [], "health": {"type": "http", "url": f"http://127.0.0.1:{number}/ready", "timeout_seconds": 0.2, "interval_seconds": 0.02},
               "startup_timeout_seconds": 4, "stop_timeout_seconds": 0.5, **options}
        return validate_process(raw)

    def group(self, *specifications):
        group = ProcessGroup(list(specifications))
        self.groups.append(group)
        group.start()
        return group

    def until(self, condition, group=None, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if group:
                group.tick()
            if condition():
                return
            time.sleep(0.02)
        self.fail("timed out waiting for isolated runtime")

    def recorded(self):
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def test_dependencies_wait_for_actual_http_readiness(self):
        first = self.specification("memory", extra=("--ready-after", "0.25"))
        second = self.specification("companion", ["memory"])
        group = self.group(second, first)
        group.tick()
        self.assertIsNone(group.processes["companion"].child)
        self.until(lambda: group.status() == "ready", group)
        self.assertEqual([row["name"] for row in self.recorded() if row["event"] == "started"], ["memory", "companion"])

    def test_missing_or_cyclic_dependencies_and_missing_probe_rejected(self):
        for dependencies in (["missing"], ["memory"]):
            with self.assertRaises(ValueError):
                dependency_order([self.specification(depends=dependencies)])
        raw = self.specification()
        del raw["health"]
        with self.assertRaises(ValueError):
            validate_process(raw)
        with self.assertRaises(ValueError):
            dependency_order([self.specification(), self.specification()])

    def test_env_files_are_literal_ordered_and_explicit_env_wins(self):
        first, second = self.root / "first.env", self.root / "second.env"
        first.write_text("#fixture\nFIXTURE_VALUE=first\nEMPTY=\nLITERAL=$HOME\n", encoding="utf-8")
        second.write_text("FIXTURE_VALUE=second\n", encoding="utf-8")
        spec = self.specification(env_file=[str(first), str(second)], env={"FIXTURE_VALUE": "explicit"})
        environment = environment_for(spec)
        self.assertEqual(environment["LITERAL"], "$HOME")
        self.assertEqual(environment["EMPTY"], "")
        group = self.group(spec)
        self.until(lambda: group.status() == "ready", group)
        self.assertEqual(self.recorded()[0]["fixture"], "explicit")
        self.assertNotIn("explicit", json.dumps(group.snapshot()))

    def test_env_file_syntax_failure_does_not_spawn_or_disclose_values(self):
        private = self.root / "private.env"
        private.write_text("not an assignment secret_fixture", encoding="utf-8")
        group = self.group(self.specification(env_file=str(private)))
        group.tick()
        self.assertEqual(group.status(), "failed")
        self.assertIsNone(group.processes["memory"].child)
        self.assertNotIn("secret_fixture", json.dumps(group.snapshot()))

    def test_child_exit_marks_failed_keeps_diagnostics_and_blocks_dependents(self):
        exiting = self.specification("memory", extra=("--exit-after", "0.4"))
        diagnostic = self.specification("logs")
        blocked = self.specification("companion", ["memory"])
        # A late dependency waits for memory and also a deliberately not-ready service.
        delayed = self.specification("gateway", extra=("--ready-after", "5"))
        blocked["depends_on"].append("gateway")
        group = self.group(exiting, diagnostic, delayed, blocked)
        self.until(lambda: group.processes["memory"].state == "failed", group)
        group.tick()
        self.assertEqual(group.processes["companion"].state, "blocked")
        self.assertIsNone(group.processes["companion"].child)
        self.assertIsNone(group.processes["logs"].child.poll())
        self.assertEqual(group.processes["memory"].exit_code, 7)

    def test_probe_failure_and_recovery_changes_aggregate_health(self):
        readiness = self.root / "readiness"
        readiness.write_text("ready")
        group = self.group(self.specification(extra=("--readiness-file", str(readiness))))
        self.until(lambda: group.status() == "ready", group)
        readiness.write_text("down")
        self.until(lambda: group.status() == "failed", group)
        self.assertEqual(group.processes["memory"].health["code"], "http_503")
        readiness.write_text("ready")
        self.until(lambda: group.status() == "ready", group)

    def test_stalled_probe_cannot_leave_previous_ready_state_green(self):
        group = self.group(self.specification())
        self.until(lambda: group.status() == "ready", group)
        began, release = threading.Event(), threading.Event()

        def stalled(*_):
            began.set()
            release.wait(2)
            return {"ok": True, "code": "late_success"}

        with patch("deploy.single_container.process.probe", stalled):
            try:
                self.until(began.is_set, group)
                self.until(lambda: group.status() == "failed", group)
                self.assertEqual(group.processes["memory"].health["code"], "probe_timeout")
                started = time.monotonic()
                self.assertTrue(group.stop())
                self.assertLess(time.monotonic() - started, 0.8)
            finally:
                release.set()

    def test_startup_deadline_is_not_pid_readiness(self):
        group = self.group(self.specification(extra=("--ready-after", "5"), startup_timeout_seconds=0.15))
        self.until(lambda: group.status() == "failed", group)
        self.assertEqual(group.processes["memory"].health["code"], "startup_timeout")
        self.assertIsNone(group.processes["memory"].child.poll())

    def test_executable_failure_is_reported_without_argv(self):
        spec = self.specification()
        spec["argv"] = [str(self.root / "missing-secret-fixture")]
        group = self.group(spec)
        group.tick()
        self.assertEqual(group.processes["memory"].health["code"], "start_failed")
        self.assertNotIn("missing-secret-fixture", json.dumps(group.snapshot()))

    def test_http_authentication_redirect_and_tcp_probes(self):
        spec = self.specification(extra=("--token", "fixture-token"))
        spec["health"]["bearer_token_env"] = "FIXTURE_TOKEN"
        spec["env"] = {"FIXTURE_TOKEN": "fixture-token"}
        group = self.group(spec)
        self.until(lambda: group.status() == "ready", group)
        check = spec["health"]
        self.assertEqual(probe(check, {})["code"], "missing_token")
        self.assertEqual(probe(check, {"FIXTURE_TOKEN": "wrong"})["code"], "http_401")
        token_file = self.root / "probe-token"
        token_file.write_text("fixture-token\n")
        file_check = {key: value for key, value in check.items() if key != "bearer_token_env"}
        file_check["bearer_token_file"] = str(token_file)
        self.assertTrue(probe(file_check, {})["ok"])
        token_file.write_text("rotated-token")
        self.assertEqual(probe(file_check, {})["code"], "http_401")
        self.assertEqual(probe(check | {"url": check["url"].replace("/ready", "/redirect")}, spec["env"])["code"], "http_302")
        self.assertTrue(probe(validate_probe({"type": "tcp", "host": "127.0.0.1", "port": int(check["url"].split(":")[2].split("/")[0])}), {})["ok"])

    @unittest.skipUnless(POSIX, "POSIX graceful signals and process groups")
    def test_reverse_dependency_shutdown_and_forced_stop_are_real(self):
        group = self.group(self.specification("base"), self.specification("front", ["base"]))
        self.until(lambda: group.status() == "ready", group)
        self.assertTrue(group.stop())
        self.assertEqual([row["name"] for row in self.recorded() if row["event"] == "stopped"], ["front", "base"])
        forced = self.group(self.specification("stubborn", extra=("--ignore-stop",), stop_timeout_seconds=0.1))
        self.until(lambda: forced.status() == "ready", forced)
        self.assertTrue(forced.stop())
        self.assertTrue(forced.processes["stubborn"].forced)
        self.assertIsNotNone(forced.processes["stubborn"].child.poll())

    @unittest.skipUnless(POSIX, "POSIX descendant process groups")
    def test_stop_covers_descendants_after_leader_exits(self):
        group = self.group(self.specification(extra=("--spawn-child",), stop_timeout_seconds=0.15))
        self.until(lambda: group.status() == "ready", group)
        self.assertIsNotNone(self.recorded()[0]["descendant"])
        self.assertTrue(group.stop())
        self.assertTrue(group.processes["memory"].forced)
        self.assertFalse(group.processes["memory"].group_running())

    @unittest.skipUnless(POSIX, "POSIX orphan worker cleanup")
    def test_unexpected_exit_cleans_its_descendants_without_stopping_other_services(self):
        group = self.group(self.specification(extra=("--spawn-child", "--exit-after", "0.3"), stop_timeout_seconds=0.1),
                           self.specification("logs"))
        item = group.processes["memory"]
        self.until(lambda: item.state == "failed", group)
        self.until(lambda: not item.group_running(), group)
        self.assertIsNone(group.processes["logs"].child.poll())

    @unittest.skipUnless(POSIX and os.geteuid() == 0, "Linux/root identity drop")
    def test_business_process_uid_gid_and_supplementary_groups_are_dropped(self):
        self.root.chmod(0o777)
        group = self.group(self.specification(uid=10001, gid=10001))
        self.until(lambda: group.status() == "ready", group)
        row = self.recorded()[0]
        self.assertEqual((row["uid"], row["gid"], row["groups"]), (10001, 10001, []))

    def test_runtime_publishes_fresh_state_and_cleans_up_real_children(self):
        state = self.root / "run" / "state.json"
        config = {"version": 1, "state_file": str(state), "poll_interval_seconds": 0.02,
                  "processes": [self.specification()]}
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(config))
        runtime = Runtime(load_config(config_path))
        failures = []

        def run():
            try:
                runtime.run(install_signals=False)
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.until(lambda: check_state(state))
            recorded = json.loads(state.read_text())
            self.assertEqual(recorded["status"], "ready")
            self.assertNotIn("argv", state.read_text())
        finally:
            runtime.stop_event.set()
            thread.join(timeout=8)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertFalse(check_state(state))
        self.assertEqual(json.loads(state.read_text())["status"], "stopped")
        self.assertIsNotNone(runtime.group.processes["memory"].child.poll())

    def test_health_rejects_stale_future_partial_or_dead_pid_state(self):
        path = self.root / "state.json"
        state = {"version": 1, "pid": os.getpid(), "status": "ready", "updated_at": 100,
                 "processes": {"memory": {"state": "ready", "health": {"ok": True}}}}
        write_state(path, state)
        self.assertTrue(check_state(path, now=101))
        self.assertFalse(check_state(path, now=111))
        self.assertFalse(check_state(path, now=99))
        state["processes"]["memory"]["health"]["ok"] = False
        write_state(path, state)
        self.assertFalse(check_state(path, now=101))
        state["processes"] = {}
        write_state(path, state)
        self.assertFalse(check_state(path, now=101))

    def test_status_write_failure_still_stops_real_children(self):
        config = {"state_file": str(self.root / "state.json"), "poll_interval_seconds": 0.02,
                  "processes": [self.specification()]}
        runtime = Runtime(config)
        original = runtime.publish

        def publish(status=None):
            if status == "starting":
                return original(status)
            raise OSError("synthetic status disk failure")

        with patch.object(runtime, "publish", publish):
            with self.assertRaises(OSError):
                runtime.run(install_signals=False)
        self.assertIsNotNone(runtime.group.processes["memory"].child.poll())

    def test_comics_are_opt_in_owner_bound_and_restartable(self):
        config = {"socket": str(self.root / "control.sock"), "client_uid": 10001,
                  "processes": [self.specification("comic-inputs"), self.specification("comic-publisher")]}
        controller = ComicsController(config)
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertEqual(controller.status()["state"], "stopped")
        self.assertFalse(self.events.exists())
        try:
            self.assertEqual(controller.action("start", first)["state"], "starting")
            self.assertEqual(controller.action("start", first)["state"], "starting")
            with self.assertRaises(ControlError) as conflict:
                controller.action("stop", second)
            self.assertEqual((conflict.exception.code, conflict.exception.status), ("comic_file_owner_conflict", 409))
            self.until(lambda: (controller.tick() or controller.status()["state"]) == "ready")
            self.assertEqual(controller.status()["roles"], {"inputs": "running", "publisher": "running"})
            self.assertEqual(controller.action("stop", first)["state"], "stopped")
            self.assertEqual(controller.action("stop", first)["state"], "stopped")
            self.assertEqual(controller.action("start", second)["state"], "starting")
            self.until(lambda: (controller.tick() or controller.status()["state"]) == "ready")
        finally:
            self.assertTrue(controller.close())

    @unittest.skipUnless(LINUX_PEER, "Linux Unix socket peer credentials")
    def test_comics_http_checks_peer_and_preserves_exact_response(self):
        config = {"socket": str(self.root / "control.sock"), "client_uid": os.getuid(),
                  "processes": [self.specification("comic-inputs"), self.specification("comic-publisher")]}
        controller = ComicsController(config)

        class Connection(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(2)
                self.sock.connect(config["socket"])

        def request(owner):
            connection = Connection("localhost")
            try:
                connection.request("POST", "/v1/comics/status", json.dumps({"owner": owner}), {"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            controller.open()
            status, body = request(str(uuid.uuid4()))
            self.assertEqual(status, 200)
            self.assertEqual(body, {"state": "stopped", "roles": {"inputs": "stopped", "publisher": "stopped"}})
            self.assertTrue(probe(validate_probe({"type": "unix", "path": config["socket"]}), {})["ok"])
            controller.config["client_uid"] = os.getuid() + 1
            self.assertEqual(request(str(uuid.uuid4()))[0], 403)
        finally:
            controller.close()
        self.assertFalse(Path(config["socket"]).exists())

    @unittest.skipUnless(POSIX, "POSIX supervisor SIGTERM")
    def test_cli_sigterm_stops_children_before_supervisor_exit(self):
        state = self.root / "state.json"
        config = self.root / "runtime.json"
        config.write_text(json.dumps({"version": 1, "state_file": str(state),
                                     "poll_interval_seconds": 0.02, "processes": [self.specification()]}))
        supervisor = subprocess.Popen([sys.executable, "-B", "-m", "deploy.single_container.runtime", "--config", str(config)], cwd=ROOT)
        try:
            self.until(lambda: check_state(state))
            supervisor.send_signal(signal.SIGTERM)
            self.assertEqual(supervisor.wait(timeout=8), 0)
            self.assertEqual([row["event"] for row in self.recorded()], ["started", "stopped"])
            self.assertFalse(check_state(state))
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait()


if __name__ == "__main__":
    unittest.main()
