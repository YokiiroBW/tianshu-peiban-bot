"""R1 independent malicious/late Docker inventory and restart/exit injections."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_packaging as packaging
from fake_linux_docker import Docker, clock_patches
from linux_runtime import leased_execute
from manifest import Refused


class LifecycleTests(unittest.TestCase):
    def test_empty_attach_reads_same_exited_container_without_replaying(self):
        report, fake = self.exercise(expected_error=False, empty_attach=True)
        self.assertTrue(report["stop_confirmed"])
        starts = [c[-1] for c in fake.calls if c[:3] == ["docker", "start", "-ai"]]
        self.assertEqual(len(starts), len(set(starts)))
        logs = [c[-1] for c in fake.calls if c[:2] == ["docker", "logs"]]
        self.assertTrue(logs)
        self.assertTrue(set(logs) <= set(starts))
        self.assertTrue(
            all(
                v == "container_stdout"
                for v in report["oneoff_output_sources"].values()
            )
        )

    def test_missing_or_invalid_output_retains_container_and_fails(self):
        for flag in ("invalid_logs", "empty_logs"):
            with self.subTest(flag=flag):
                report, fake = self.exercise(empty_attach=True, **{flag: True})
                self.assertFalse(any(c[:2] == ["docker", "rm"] for c in fake.calls))
                self.assertEqual(
                    len([c for c in fake.calls if c[:3] == ["docker", "start", "-ai"]]),
                    1,
                )
                self.assertNotIn("completed_oneoffs", report)

    def exercise(self, expected_error=True, dialogue=False, **faults):
        fixture = packaging.PackagingTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            fixture.init()
            root = fixture.output
            (root / "reports").mkdir()
            receipt = dict(
                assertion_ref="origin:" + "a" * 32,
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(seconds=299)
                ).isoformat(),
            )
            fake = Docker(root, receipt, **faults)
            report = {"results": []}
            with (
                patch("linux_runtime.preflight"),
                patch("linux_runtime.subprocess.run", side_effect=fake),
                clock_patches(),
            ):
                if expected_error:
                    with self.assertRaises(Refused):
                        leased_execute(
                            root,
                            None,
                            [],
                            report,
                            root / "reports/r1.json",
                            dialogue=dialogue,
                        )
                else:
                    leased_execute(
                        root,
                        None,
                        [],
                        report,
                        root / "reports/r1.json",
                        dialogue=dialogue,
                    )
            return report, fake
        finally:
            fixture.tearDown()

    def test_same_label_new_id_duplicate_owner_never_receives_signal(self):
        for dialogue in (False, True):
            with self.subTest(dialogue=dialogue):
                report, fake = self.exercise(unknown=True, dialogue=dialogue)
                self.assertFalse(report["stop_confirmed"])
                self.assertEqual(report["stop_error"], "owned_container_set_changed")
                self.assertFalse(
                    any(
                        c[:2] in (["docker", "kill"], ["docker", "update"])
                        for c in fake.calls
                    )
                )

    def test_replaced_identity_never_receives_signal(self):
        report, fake = self.exercise(replace=True)
        self.assertFalse(report["stop_confirmed"])
        self.assertFalse(any(c[:2] == ["docker", "kill"] for c in fake.calls))

    def test_same_id_changed_image_or_owner_never_receives_signal(self):
        for field in ("image", "owner"):
            with self.subTest(field=field):
                report, fake = self.exercise(**{field: True})
                self.assertEqual(
                    report["stop_error"], "owned_container_identity_changed"
                )
                self.assertFalse(any(c[:2] == ["docker", "kill"] for c in fake.calls))

    def test_restart_policy_update_must_be_observed_before_term(self):
        report, fake = self.exercise(ignore_update=True)
        self.assertEqual(report["stop_error"], "restart_policy_not_disabled")
        self.assertFalse(any(c[:2] == ["docker", "kill"] for c in fake.calls))

    def test_transient_exit_then_restart_is_never_green(self):
        report, _ = self.exercise(restart=True)
        self.assertFalse(report["stop_confirmed"])
        self.assertEqual(report["stop_error"], "container_restarted_during_stop")

    def test_exit_2_143_137_and_oom_are_not_normal_stop(self):
        for faults in (
            {"exit_code": 2},
            {"exit_code": 143},
            {"exit_code": 137},
            {"oom": True},
        ):
            with self.subTest(faults=faults):
                report, _ = self.exercise(**faults)
                self.assertFalse(report["stop_confirmed"])
                self.assertEqual(report["stop_error"], "abnormal_product_exit")

    def test_partial_create_registers_created_ids_but_remains_uncertain(self):
        report, fake = self.exercise(partial_create=True)
        self.assertEqual(len(report["owned_containers"]), 1)
        self.assertEqual(report["stop_error"], "uncertain_create_requires_review")
        self.assertFalse(any(c[:2] == ["docker", "kill"] for c in fake.calls))

    def test_partial_start_only_stops_the_pre_registered_ids(self):
        report, fake = self.exercise(partial_start=True)
        signaled = [c[-1] for c in fake.calls if c[:2] == ["docker", "kill"]]
        self.assertEqual(len(report["owned_containers"]), 4)
        self.assertEqual(len(signaled), 1)
        self.assertTrue(set(signaled) <= set(report["owned_containers"]))

    def test_oneoff_timeout_is_registered_retained_and_not_auto_removed(self):
        report, fake = self.exercise(oneoff_timeout=True)
        self.assertEqual(len(report["owned_containers"]), 1)
        cid = next(iter(report["owned_containers"]))
        self.assertIn(cid, fake.active)
        self.assertNotIn(cid, report["completed_oneoffs"])
        self.assertFalse(any(c == ["docker", "rm", cid] for c in fake.calls))

    def test_successful_oneoffs_retired_after_evidence_core_identity_unchanged(self):
        report, fake = self.exercise(expected_error=False)
        self.assertEqual(len(fake.active), 4)
        self.assertEqual(len(report["completed_oneoffs"]), 7)
        for cid, proof in report["completed_oneoffs"].items():
            self.assertEqual(proof["state"]["ExitCode"], 0)
            self.assertIs(proof["state"]["OOMKilled"], False)
            self.assertEqual(proof["restart_count"], 0)
            self.assertIn(["docker", "rm", cid], fake.calls)
        for value in fake.active.values():
            self.assertEqual(value["HostConfig"]["RestartPolicy"]["Name"], "no")
            self.assertTrue(
                value["Config"]["Labels"][
                    "com.docker.compose.project.config_files"
                ].endswith("compose.json")
            )
        self.assertTrue(report["stop_confirmed"])
