"""Docker protocol substitutes; no claim of Linux/container execution."""

import copy
import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import helpers  # noqa: F401
from acceptance import Evidence, LocalDocker
from container_lifecycle import OwnedContainers


def row(owner="obs-vector", ident="a" * 64, exit_code=None):
    return {
        "Id": ident,
        "Image": "sha256:" + "e" * 64,
        "Config": {
            "Labels": {
                "com.docker.compose.project": "tianshu-qa-test-obs",
                "com.docker.compose.service": owner,
            }
        },
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
        "State": {
            "Status": "running" if exit_code is None else "exited",
            "Running": exit_code is None,
            "ExitCode": 0 if exit_code is None else exit_code,
            "OOMKilled": False,
        },
    }


class FakeDocker:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.commands = []
        self.exit_code = 0
        self.stuck = False
        self.partial_start = False
        self.recreate_error = False

    def call(self, args, timeout=180):
        self.commands.append((list(args), timeout))
        if args[0] == "ps":
            return "\n".join(r["Id"] for r in self.rows).encode()
        if args[0] == "inspect":
            found = [r for r in self.rows if r["Id"] in args[1:]]
            if len(found) != len(args) - 1:
                raise ValueError("container_missing")
            return json.dumps(found).encode()
        if args[:2] == ["image", "inspect"]:
            return json.dumps([{"Id": "sha256:" + "e" * 64}]).encode()
        target = next(r for r in self.rows if r["Id"] == args[-1])
        if args[0] == "update":
            target["HostConfig"]["RestartPolicy"]["Name"] = "no"
        elif args[0] == "kill":
            assert args[1:3] == ["--signal", "TERM"]
            if not self.stuck:
                target["State"].update(
                    Status="exited", Running=False, ExitCode=self.exit_code
                )
        elif args[0] == "start":
            target["State"].update(Status="running", Running=True)
        elif args[0] == "rm":
            assert len(args) == 2 and not target["State"]["Running"]
            self.rows.remove(target)
        else:
            raise AssertionError(args)
        return b""

    def compose(self, root, project, *args):
        self.commands.append((["compose", *args], 180))
        assert "--no-recreate" in args and "--force-recreate" not in args
        if self.partial_start:
            self.rows = [row()]
            raise ValueError("partial_start")
        self.rows.append(row(ident="b" * 64))
        if self.recreate_error:
            raise ValueError("partial_recreate")


def binding():
    owners = ("obs-vector", "obs-loki", "obs-grafana", "obs-prometheus", "obs-guard")

    def check(actual, project, owner, spec):
        labels = actual["Config"]["Labels"]
        if (
            labels["com.docker.compose.project"] != project
            or labels["com.docker.compose.service"] != owner
        ):
            raise ValueError("owner_mismatch")

    return SimpleNamespace(
        obs_project="tianshu-qa-test-obs",
        obs_root=Path("synthetic"),
        stack={"services": {owner: {"image": "pinned:synthetic"} for owner in owners}},
        document={
            "services": {owner: {"image_id": "sha256:" + "e" * 64} for owner in owners}
        },
        check_container=check,
        preflight=lambda _: {},
        running=lambda _: [],
    )


class ContainerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.evidence = Evidence(Path(self.temp.name) / "evidence")
        self.docker = FakeDocker([row()])
        self.binding = binding()
        self.lifecycle = OwnedContainers(
            self.docker, self.binding, self.evidence, timeout=0.05
        )
        self.lifecycle.capture_initial()

    def test_fixed_term_and_zero_exit_only(self):
        self.lifecycle.stop(["obs-vector"])
        commands = [c for c, _ in self.docker.commands]
        self.assertIn(["kill", "--signal", "TERM", "a" * 64], commands)
        self.assertIn(["update", "--restart=no", "a" * 64], commands)
        proof = json.loads(
            (self.evidence.directory / "lifecycle-02-stop.json").read_bytes()
        )
        self.assertEqual(proof["status"], "normal_exit_confirmed")
        self.assertFalse(proof["sigkill_fallback"])
        self.assertTrue(all(timeout <= 0.051 for _, timeout in self.docker.commands))

    def test_143_2_137_each_reject_recreate_and_keep_exit_facts(self):
        for code in (143, 2, 137):
            with self.subTest(code=code):
                self.docker.rows = [row()]
                self.docker.exit_code = code
                self.lifecycle.failed = False
                self.lifecycle.signalled.clear()
                before = len(self.docker.commands)
                with self.assertRaisesRegex(ValueError, "abnormal_container_exit"):
                    self.lifecycle.recreate_vector()
                self.assertFalse(
                    any(
                        c[0] in {"rm", "compose"}
                        for c, _ in self.docker.commands[before:]
                    )
                )
                last = sorted(self.evidence.directory.glob("*-stop.json"))[-1]
                proof = json.loads(last.read_bytes())
                self.assertEqual(proof["observations"][-1][0]["exit_code"], code)
                self.assertNotEqual(proof["status"], "normal_exit_confirmed")

    def test_timeout_keeps_live_owner_and_has_no_escalation(self):
        self.docker.stuck = True
        with self.assertRaisesRegex(ValueError, "stop_unconfirmed"):
            self.lifecycle.stop(["obs-vector"])
        self.assertTrue(self.docker.rows[0]["State"]["Running"])
        with self.assertRaisesRegex(ValueError, "previous_stop_unconfirmed"):
            self.lifecycle.recreate_vector()
        self.assertEqual(
            [c for c, _ in self.docker.commands if c[0] == "kill"],
            [["kill", "--signal", "TERM", "a" * 64]],
        )

    def test_id_replacement_is_not_signalled(self):
        self.docker.rows[0]["Id"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "stop_container_id_changed"):
            self.lifecycle.stop(["obs-vector"])
        self.assertFalse(
            any(c[0] in {"kill", "update"} for c, _ in self.docker.commands)
        )

    def test_unknown_term_result_is_not_automatically_repeated(self):
        original = self.docker.call

        def uncertain(args, timeout=180):
            if args[0] == "kill":
                self.docker.commands.append((list(args), timeout))
                raise ValueError("synthetic_signal_result_unknown")
            return original(args, timeout)

        self.docker.call = uncertain
        with self.assertRaisesRegex(ValueError, "synthetic_signal_result_unknown"):
            self.lifecycle.stop(["obs-vector"])
        with self.assertRaisesRegex(ValueError, "stop_unconfirmed"):
            self.lifecycle.stop(["obs-vector"], cleanup=True)
        self.assertEqual(len([c for c, _ in self.docker.commands if c[0] == "kill"]), 1)

    def test_image_replacement_is_not_signalled(self):
        self.docker.rows[0]["Image"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "stop_image_mismatch"):
            self.lifecycle.stop(["obs-vector"])
        self.assertFalse(any(c[0] == "kill" for c, _ in self.docker.commands))

    def test_stopped_vector_nonforce_remove_then_no_recreate_up(self):
        self.lifecycle.recreate_vector()
        commands = [c for c, _ in self.docker.commands]
        self.assertIn(["rm", "a" * 64], commands)
        up = next(c for c in commands if c[0] == "compose")
        self.assertIn("--no-recreate", up)
        self.assertFalse(
            any(
                flag in up
                for flag in ("--force-recreate", "-v", "--renew-anon-volumes")
            )
        )
        self.assertEqual(self.lifecycle.pins["obs-vector"], "b" * 64)

    def test_partial_recreate_captures_new_id_for_term_cleanup(self):
        self.docker.recreate_error = True
        with self.assertRaisesRegex(ValueError, "partial_recreate"):
            self.lifecycle.recreate_vector()
        self.assertEqual(self.lifecycle.pins["obs-vector"], "b" * 64)
        self.lifecycle.stop(["obs-vector"], cleanup=True)
        self.assertFalse(self.docker.rows[0]["State"]["Running"])

    def test_partial_start_cli_fails_and_stops_only_captured_ids(self):
        from run_linux import main

        fake = FakeDocker([])
        fake.partial_start = True
        output = Path(self.temp.name) / "partial-start"
        identity = {
            "project_name": "tianshu-qa-test",
            "deployment_root": "/synthetic",
            "lease": {"path": "/synthetic/.runtime-owner.lock"},
        }
        with (
            patch("run_linux.load_identity", return_value=identity),
            patch("run_linux.LocalDocker", return_value=fake),
            patch("run_linux.Binding", return_value=self.binding),
            patch("run_linux.lifecycle_lease", return_value=nullcontext()),
        ):
            result = main(
                [
                    "--identity",
                    "synthetic.json",
                    "--identity-sha256",
                    "a" * 64,
                    "--output",
                    str(output),
                    "--execute-synthetic",
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads((output / "report.json").read_bytes())["status"], "failed"
        )
        self.assertFalse(fake.rows[0]["State"]["Running"])
        self.assertEqual(
            [c for c, _ in fake.commands if c[0] == "kill"],
            [["kill", "--signal", "TERM", "a" * 64]],
        )

    def test_guard_exit_2_cannot_leave_cli_partial(self):
        from run_linux import main

        fake = FakeDocker([])
        fake.exit_code = 2
        output = Path(self.temp.name) / "guard-exit"
        identity = {
            "project_name": "tianshu-qa-test",
            "deployment_root": "/synthetic",
            "lease": {"path": "/synthetic/.runtime-owner.lock"},
        }
        fake.compose = lambda *args: fake.rows.append(row("obs-guard"))
        with (
            patch("run_linux.load_identity", return_value=identity),
            patch("run_linux.LocalDocker", return_value=fake),
            patch("run_linux.Binding", return_value=self.binding),
            patch("run_linux.lifecycle_lease", return_value=nullcontext()),
            patch("linux_scenarios.Scenarios", return_value=Mock()),
        ):
            result = main(
                [
                    "--identity",
                    "synthetic.json",
                    "--identity-sha256",
                    "a" * 64,
                    "--output",
                    str(output),
                    "--execute-synthetic",
                ]
            )
        self.assertEqual(result, 1)
        report = json.loads((output / "report.json").read_bytes())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_code"], "abnormal_container_exit")

    def test_compose_cannot_implicitly_stop_or_recreate(self):
        docker = object.__new__(LocalDocker)
        with patch.object(docker, "call") as call:
            for args in (
                ("stop",),
                ("restart",),
                ("start",),
                ("up", "-d"),
                ("up", "--no-recreate", "--force-recreate"),
            ):
                with self.assertRaises(ValueError):
                    docker.compose(Path("."), "synthetic-obs", *args)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
