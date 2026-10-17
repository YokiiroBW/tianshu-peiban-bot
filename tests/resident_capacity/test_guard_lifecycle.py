"""Production-incident regressions using only isolated Docker/state fixtures."""

import json
import os
import stat
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_guard as fixtures
from ops.resident_capacity import guard


class GuardLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResidentCapacityTests()
        self.fixture.setUp()
        self.fixture._enable_knowledge()
        self.addCleanup(self.fixture.doCleanups)
        self.config = self.fixture.config
        self.state = self.config.state_dir

    def sample(self, docker):
        with patch.object(guard, "capacity", return_value=(100, 30 * guard.GIB)):
            return guard.sample(self.config, self.fixture._marker(), docker)

    def private_state(self):
        return patch.object(guard, "_state_dir", side_effect=lambda path, create=False: Path(path))

    @staticmethod
    def rewrite(path, function):
        value = json.loads(path.read_text())
        function(value)
        path.write_text(json.dumps(value))

    def test_stable_387_id_inventory_only_reinspects_ten_owners(self):
        unrelated = [replace(self.fixture.containers[0], id=f"{index:064x}", project="external", mounts=())
                     for index in range(1000, 1377)]
        docker, path = self.fixture._cli(containers=[*self.fixture.containers, *unrelated])
        first = self.sample(docker)
        self.assertEqual(first["services"], 10)
        initial = self.fixture._cli_events(path)
        self.assertEqual(sum(len(item["ids"]) for item in initial), 387)
        self.assertTrue(all(len(item["ids"]) <= 32 for item in initial))
        self.assertEqual(len(docker._foreign_creation), 377)
        self.assertTrue(all(item.status is None and item.restart is None for item in docker._foreign_creation.values()))
        self.sample(docker)
        current = self.fixture._cli_events(path)[len(initial):]
        self.assertEqual([event["stage"] for event in current], ["ps", "batch_inspect"])
        self.assertEqual(set(current[-1]["ids"]), set(self.fixture._marker()["ids"].values()))

    def test_owner_status_restart_image_and_mounts_never_come_from_cache(self):
        original = self.fixture.containers[0]
        for index, value, reason in (
            (6, "exited", "resident_service_not_running"),
            (7, "no", "resident_restart_policy_changed"),
            (5, "sha256:" + "f" * 64, "resident_identity_conflict"),
            (8, [], "resident_identity_conflict"),
        ):
            with self.subTest(field=index):
                docker, path = self.fixture._cli()
                self.sample(docker)
                self.rewrite(path, lambda data: next(item for item in data["containers"] if item[0] == original.id).__setitem__(index, value))
                with self.assertRaisesRegex(guard.Unsafe, reason):
                    self.sample(docker)

    def test_new_foreign_mount_duplicate_and_replacement_are_inspected(self):
        original = self.fixture.containers[0]
        external = replace(original, id="f" * 64, project="external")
        duplicate = replace(original, id="e" * 64)
        for added, remove, reason in (
            (external, False, "foreign_deployment_mount"),
            (duplicate, False, "resident_identity_conflict"),
            (duplicate, True, "resident_container_identity_changed"),
        ):
            with self.subTest(reason=reason):
                docker, path = self.fixture._cli()
                self.sample(docker)

                def change(data):
                    if remove:
                        data["containers"] = [item for item in data["containers"] if item[0] != original.id]
                    data["containers"].append(self.fixture._raw(added))

                self.rewrite(path, change)
                with self.assertRaisesRegex(guard.Unsafe, reason):
                    self.sample(docker)
                self.assertIn(added.id, self.fixture._cli_events(path)[-1]["ids"])

    def test_failed_refresh_does_not_publish_new_cache_or_hide_removed_ids(self):
        old = replace(self.fixture.containers[0], id="f" * 64, project="external", mounts=())
        new = replace(old, id="e" * 64)
        docker, path = self.fixture._cli(containers=[*self.fixture.containers, old])
        self.sample(docker)

        def change(data):
            data["containers"] = [item for item in data["containers"] if item[0] != old.id]
            data["containers"].append(self.fixture._raw(new))
            data["faults"] = {"batch_inspect": [{"stdout": "\n".join("invalid-json" for _ in data["containers"])}]}

        self.rewrite(path, change)
        with self.assertRaisesRegex(guard.Unsafe, "docker_metadata_invalid"):
            self.sample(docker)
        self.assertIn(old.id, docker._foreign_creation)
        self.assertNotIn(new.id, docker._foreign_creation)
        self.sample(docker)
        self.assertNotIn(old.id, docker._foreign_creation)
        self.assertIn(new.id, docker._foreign_creation)

    def test_fail_close_rechecks_locked_ids_after_warm_snapshot(self):
        self.fixture._write_marker()
        unrelated = replace(self.fixture.containers[0], id="f" * 64, project="external", mounts=())
        docker, path = self.fixture._cli(containers=[*self.fixture.containers, unrelated])
        self.sample(docker)
        initial = len(self.fixture._cli_events(path))
        with self.private_state():
            receipt = guard.fail_close(self.state, "fixture_failure", docker)
        self.assertEqual(receipt["status"], "stopped")
        self.assertEqual(set(receipt["exited_or_absent_ids"]), set(self.fixture._marker()["ids"].values()))
        events = self.fixture._cli_events(path)[initial:]
        snapshots = [item for item in events if item["stage"] == "batch_inspect"]
        self.assertEqual(len(snapshots), 2)
        self.assertTrue(all(set(item["ids"]) == set(self.fixture._marker()["ids"].values()) for item in snapshots))
        self.assertNotIn(unrelated.id, receipt["restart_disabled_ids"])
        updated = json.loads(path.read_text())["containers"]
        self.assertTrue(all(item[6] == "exited" and item[7] == "no" for item in updated if item[0] != unrelated.id))

    def test_batch_timeout_recovers_once_inside_original_25_second_cycle(self):
        result, limits, deadlines, notifications, stops = self.fixture._run_with_clock(
            [(0.1, 0), (0.2, 0), (0.1, 0), (16, 0), (0.1, 0), (0.3, 0)],
            [0.1, 0.1], notify_seconds=0.5, poll_seconds=5, stop_after_notifications=2,
        )
        self.assertEqual(result, 2)  # Controlled fixture_loop_end after recovery.
        self.assertEqual(len(notifications), 2)
        self.assertLess(notifications[1][0] - notifications[0][0], 25)
        self.assertEqual(limits[3], 15)
        self.assertLess(limits[-1], 5)
        self.assertEqual(deadlines[:2], [25, 25.4])
        recovery = json.loads((self.state / "docker-resample.json").read_text())
        self.assertEqual([item["attempt"] for item in recovery["docker_diagnostics"]], [1, 1, 2, 2])
        self.assertEqual(recovery["docker_diagnostics"][1]["outcome"], "timeout")
        self.assertEqual(len(stops), 1)

    def test_real_subprocess_timeout_can_recover_without_resetting_deadline(self):
        docker, path = self.fixture._cli(faults={"batch_inspect": [{"sleep_seconds": 0.4}]})
        with patch.object(guard, "DOCKER_BATCH_INSPECT_SECONDS", 0.15):
            result = self.sample(docker)
        self.assertEqual(result["services"], 10)
        self.assertEqual(len(self.fixture._cli_events(path)), 4)
        self.assertEqual([event["outcome"] for event in result["docker_diagnostics"]], ["ok", "timeout", "ok", "ok"])

    def test_repeated_timeouts_preserve_last_heartbeat_and_still_stop(self):
        result, _, deadlines, notifications, stops = self.fixture._run_with_clock(
            [(0.1, 0), (0.2, 0), (0.1, 0), (16, 0), (0.1, 0), (16, 0)],
            [0.1], notify_seconds=0.5, poll_seconds=5,
        )
        self.assertEqual(result, 2)
        self.assertEqual(len(notifications), 1)
        self.assertEqual((self.state / "heartbeat.json").read_bytes(), notifications[0][2])
        self.assertEqual(stops, [deadlines[-1]])
        self.assertLess(stops[0] - notifications[0][0], 30)
        self.assertEqual(len(self.fixture.docker.termed), 10)

    def pending(self, reason, timestamp, token="a", **changes):
        value = {"reason": reason, "time": timestamp, "docker_diagnostics": []}
        value.update(changes)
        path = self.state / ("failure.json." + token * 32 + ".tmp")
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def test_stop_post_recovers_first_pending_cause_and_keeps_it_on_later_calls(self):
        self.fixture._write_marker()
        diagnostics = [{"stage": "batch_inspect", "attempt": 1, "batch_size": 387, "returncode": None,
                        "timed_out": True, "outcome": "timeout", "elapsed_seconds": 15.017968}]
        self.pending("docker_unavailable", 100, docker_diagnostics=diagnostics)
        self.pending("host_free_floor_reached", 200, token="b")
        with self.private_state():
            first = guard.fail_close(self.state, "service_exit", self.fixture.docker)
            canonical = (self.state / "failure.json").read_bytes()
            second = guard.fail_close(self.state, "latched_failure", self.fixture.docker)
        self.assertEqual(first["status"], "stopped")
        self.assertTrue(first["pending_failure_recovered"])
        self.assertEqual(first["reason"], "docker_unavailable")
        self.assertEqual(first["invoked_by"], "service_exit")
        self.assertEqual(first["trigger_docker_diagnostics"], diagnostics)
        self.assertEqual(second["reason"], "docker_unavailable")
        self.assertEqual(second["invoked_by"], "latched_failure")
        self.assertEqual((self.state / "failure.json").read_bytes(), canonical)

    def test_supervised_restart_with_pending_failure_never_becomes_ready(self):
        self.fixture._write_marker()
        self.pending("docker_unavailable", 100)
        with self.private_state(), patch.dict(os.environ, {"NOTIFY_SOCKET": "/synthetic-notify", "WATCHDOG_USEC": "30000000"}), patch.object(
            guard, "sample"
        ) as sample, patch.object(guard, "notify") as notify:
            self.assertEqual(guard.run(self.config, "a" * 64, self.fixture.docker), 0)
            sample.assert_not_called()
            notify.assert_not_called()
            self.assertFalse(self.fixture.docker.updated)
            receipt = guard.fail_close(self.state, "service_exit", self.fixture.docker)
        self.assertEqual(receipt["status"], "stopped")
        self.assertEqual(receipt["reason"], "docker_unavailable")
        self.assertTrue(receipt["pending_failure_recovered"])
        self.assertEqual(len(self.fixture.docker.termed), 10)

    def test_standalone_restart_recovers_pending_failure_and_stops_synchronously(self):
        self.fixture._write_marker()
        self.pending("docker_unavailable", 100)
        with self.private_state(), patch.dict(os.environ, {"NOTIFY_SOCKET": "", "WATCHDOG_USEC": ""}), patch.object(
            guard, "sample"
        ) as sample, patch.object(guard, "notify") as notify:
            self.assertEqual(guard.run(self.config, "a" * 64, self.fixture.docker), 0)
            sample.assert_not_called()
            notify.assert_not_called()
        self.assertEqual(json.loads((self.state / "failure.json").read_text())["reason"], "docker_unavailable")
        self.assertEqual(len(self.fixture.docker.termed), 10)

    def test_status_rejects_pending_failure_despite_fresh_heartbeat(self):
        self.fixture._write_marker()
        self.pending("docker_unavailable", 100)
        (self.state / "heartbeat.json").write_text(json.dumps({"status": "healthy", "time": guard.time.time()}))
        with self.private_state(), patch.object(guard, "sample") as sample:
            with self.assertRaisesRegex(guard.Unsafe, "capacity_guard_not_ready"):
                guard.status(self.config, "a" * 64, self.fixture.docker)
            sample.assert_not_called()

    def test_recovery_ignores_wrong_shape_names_and_records_outside_this_state(self):
        self.fixture._write_marker()
        self.pending("docker_unavailable", 100, extra="private value must not be adopted")
        self.pending("docker_unavailable", 101, token="b", docker_diagnostics=[{"stage": []}])
        wrong_name = self.pending("host_free_floor_reached", 1, token="c")
        wrong_name.rename(self.state / "unrelated.tmp")
        outside = self.state.parent / ("failure.json." + "d" * 32 + ".tmp")
        outside.write_text(json.dumps({"reason": "host_free_floor_reached", "time": 1, "docker_diagnostics": []}))
        with self.private_state():
            receipt = guard.fail_close(self.state, "service_exit", self.fixture.docker)
        self.assertEqual(receipt["reason"], "service_exit")
        self.assertFalse(receipt["pending_failure_recovered"])
        self.assertNotIn("private value", (self.state / "failure.json").read_text())

    def test_pending_permission_owner_and_symlink_checks_precede_content_read(self):
        path = self.pending("docker_unavailable", 100)
        owner = path.parent.stat().st_uid
        for mode, uid in ((stat.S_IFREG | 0o644, owner), (stat.S_IFREG | 0o600, owner + 1), (stat.S_IFLNK | 0o600, owner)):
            with self.subTest(mode=mode, uid=uid), patch.object(
                guard, "os", types.SimpleNamespace(name="posix")
            ), patch.object(type(path), "lstat", return_value=types.SimpleNamespace(st_mode=mode, st_uid=uid)), patch.object(
                guard, "_read_json"
            ) as read:
                with self.assertRaisesRegex(guard.Unsafe, "failure_receipt_owner_or_mode_invalid"):
                    guard._read_failure(path)
                read.assert_not_called()

    def test_supervised_run_hands_long_stop_to_existing_exec_stop_post(self):
        self.fixture._write_marker()
        with self.private_state(), patch.dict(os.environ, {"NOTIFY_SOCKET": "/synthetic-notify", "WATCHDOG_USEC": "30000000"}), patch.object(
            guard, "sample", side_effect=guard.Unsafe("host_free_floor_reached")
        ), patch.object(guard, "notify") as notify:
            result = guard.run(self.config, "a" * 64, self.fixture.docker)
            self.assertEqual(result, 2)
            self.assertFalse(self.fixture.docker.updated)
            self.assertFalse(self.fixture.docker.termed)
            notify.assert_not_called()
            receipt = guard.fail_close(self.state, "service_exit", self.fixture.docker)
            self.assertEqual(receipt["status"], "stopped")
            self.assertEqual(receipt["reason"], "host_free_floor_reached")
            self.assertEqual(receipt["invoked_by"], "service_exit")
            self.assertEqual(guard.run(self.config, "a" * 64, self.fixture.docker), 0)
        self.assertEqual(len(self.fixture.docker.termed), 10)
        unit = (Path(__file__).parents[2] / "ops/resident_capacity/tianshu-resident-capacity.service.in").read_text()
        for setting in ("ExecStopPost=", "fail-close --state-dir", "WatchdogSec=30s", "TimeoutStopSec=700s"):
            self.assertIn(setting, unit)

    def test_supervised_failed_latch_write_still_exits_for_stop_post(self):
        self.fixture._write_marker()
        with self.private_state(), patch.dict(os.environ, {"NOTIFY_SOCKET": "/synthetic-notify", "WATCHDOG_USEC": "30000000"}), patch.object(
            guard, "sample", side_effect=guard.Unsafe("docker_unavailable")
        ), patch.object(guard, "_latch_failure", side_effect=OSError("synthetic fsync failure")):
            self.assertEqual(guard.run(self.config, "a" * 64, self.fixture.docker), 3)
        self.assertFalse(self.fixture.docker.updated)
        with self.private_state():
            self.assertEqual(guard.fail_close(self.state, "service_exit", self.fixture.docker)["status"], "stopped")
        self.assertEqual(len(self.fixture.docker.termed), 10)

    def test_supervised_config_mismatch_keeps_armed_stop_identity(self):
        self.fixture._write_marker()
        with self.private_state(), patch.dict(os.environ, {"NOTIFY_SOCKET": "/synthetic-notify", "WATCHDOG_USEC": "30000000"}), patch.object(
            guard, "Docker", return_value=self.fixture.docker
        ):
            self.assertEqual(guard.run(self.config, "b" * 64, object()), 2)
            self.assertFalse(self.fixture.docker.updated)
            receipt = guard.fail_close(self.state, "service_exit")
        self.assertEqual(receipt["reason"], "capacity_config_changed_after_arm")
        self.assertEqual(set(receipt["target_ids"]), set(self.fixture._marker()["ids"].values()))


if __name__ == "__main__":
    unittest.main()
