import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import nas_a2_probe as probe


class NasA2BoundaryTests(unittest.TestCase):
    def test_tmpfs_ceiling_accepts_the_configured_scope_and_rejects_larger_filesystems(
        self,
    ):
        probe.validate_tmpfs_size(128 * 1024**2)
        probe.validate_tmpfs_size(256 * 1024**2)
        with self.assertRaisesRegex(probe.ProbeError, "tmpfs_budget_exceeded"):
            probe.validate_tmpfs_size(256 * 1024**2 + 1)

    def test_selected_subnet_rejects_overlapping_docker_or_host_ranges(self):
        networks = [
            {
                "Name": "unrelated",
                "IPAM": {"Config": [{"Subnet": "10.204.48.0/20"}]},
            }
        ]
        self.assertEqual(
            probe._subnet_collisions(networks, "default via 192.168.1.1"),
            ["unrelated:10.204.48.0/20"],
        )
        self.assertEqual(
            probe._subnet_collisions(
                [], "default via 192.168.1.1\n10.204.50.128/25 dev test"
            ),
            ["host-route:10.204.50.128/25"],
        )
        self.assertEqual(
            probe._subnet_collisions(
                [], "default via 192.168.1.1\n10.203.230.0/24 dev test"
            ),
            [],
        )

    def test_cpu_affinity_parser_expands_ranges_and_rejects_malformed_values(self):
        self.assertEqual(probe.parse_cpu_list("0-2,6,8-9"), [0, 1, 2, 6, 8, 9])
        with self.assertRaisesRegex(probe.ProbeError, "cpu_affinity_unavailable"):
            probe.parse_cpu_list("0-2,broken")

    def test_existing_owned_network_ignores_only_its_exact_host_route(self):
        routes = (
            "10.204.50.0/24 dev docker-4e9cfff6 proto kernel scope link\n"
            "10.204.50.128/25 dev unrelated proto kernel scope link"
        )
        filtered = probe._ignore_owned_network_route(
            routes, "4e9cfff62e2959003d92e288aeb5f562dc21ae642e9896507aa8048ea41bb71f"
        )
        self.assertEqual(
            probe._subnet_collisions([], filtered),
            ["host-route:10.204.50.128/25"],
        )

    def test_task_resource_plan_stays_below_four_gib(self):
        self.assertLess(probe.MEMORY_PLAN, 4 * 1024**3)
        self.assertEqual(sum(probe.MEMORY_LIMITS.values()), 1280 * 1024**2)
        self.assertEqual(probe.MAX_FILLER_BYTES, 112 * 1024**2)

    def test_report_never_authorizes_source_reclamation(self):
        report = probe._initial_report(
            Path("/volume2/tianshu-v2-validation-wave1/accept-20260925-a2")
        )
        self.assertFalse(report["application_reclamation_authorized"])
        self.assertFalse(report["release_ready"])
        self.assertFalse(report["source_logs_deleted"])
        self.assertEqual(
            report["dimensions"]["application_reclamation_gate"]["status"], "blocked"
        )

    def test_report_write_replaces_atomically_without_stale_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text('{"old":true}\n', encoding="utf-8")
            if sys.platform != "linux":
                with self.assertRaisesRegex(
                    probe.ProbeError, "scope_fd_linux_required"
                ):
                    probe.write_json(path, {"new": True})
                self.assertEqual(json.loads(path.read_text()), {"old": True})
                return
            with patch.object(probe, "EXPECTED_ROOT", Path(directory).resolve()):
                probe.write_json(path, {"new": True})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")), {"new": True}
            )
            self.assertEqual(list(path.parent.glob(".report.json.*.tmp")), [])

    def test_immutable_image_ids_are_pinned_in_the_probe(self):
        self.assertEqual(
            probe.EXPECTED_VECTOR_IMAGE_ID,
            "sha256:92c275b73d880922a265918a7c3c4f2cc0dd87338447ff357809f2d18a64a48e",
        )
        self.assertEqual(
            probe.EXPECTED_LOKI_IMAGE_ID,
            "sha256:ceccdbc45e274f08eb23d6ca6e0b648921d580c592ab47b4304225c0f17a406a",
        )

    def test_buffer_padding_is_marked_as_synthetic_only(self):
        row = probe._record(4, pad=True)
        self.assertEqual(len(row["a2_buffer_fixture"]), 2300)
        self.assertNotIn("a2_buffer_fixture", probe._record(4))


class NasA2OwnedContainerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for directory in (
            "tmpfs/loki", "tmpfs/vector", "tmpfs/vector-source", "config", "tls",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        for filename in (
            "config/loki.json", "config/vector.json", "tls/loki.pem",
            "tls/loki.key", "tls/client-ca.pem", "tls/ca.pem",
            "tls/client.pem", "tls/client.key",
        ):
            (self.root / filename).write_text("synthetic", encoding="utf-8")
        self.patches = [
            patch.object(probe, "EXPECTED_ROOT", self.root),
            patch.object(probe, "PROJECT", "test-a2-scope"),
            patch.object(probe, "LOKI_NAME", "test-a2-scope-loki"),
            patch.object(probe, "VECTOR_NAME", "test-a2-scope-vector"),
        ]
        for handle in self.patches:
            handle.start()
            self.addCleanup(handle.stop)
        self.image = probe.EXPECTED_LOKI_IMAGE_ID

    def container(self, role="loki", *, container_id="a" * 64, running=False):
        if role == "loki":
            name = probe.LOKI_NAME
            image = probe.EXPECTED_LOKI_IMAGE_ID
            mounts = [
                ("tmpfs/loki", "/var/lib/loki", True),
                ("config/loki.json", "/etc/tianshu/loki.json", False),
                ("tls/loki.pem", "/run/tls/loki.pem", False),
                ("tls/loki.key", "/run/tls/loki.key", False),
                ("tls/client-ca.pem", "/run/tls/client-ca.pem", False),
            ]
        else:
            name = probe.VECTOR_NAME
            image = probe.EXPECTED_VECTOR_IMAGE_ID
            mounts = [
                ("tmpfs/vector", "/var/lib/vector", True),
                ("config/vector.json", "/etc/tianshu/vector.json", False),
                ("tmpfs/vector-source", "/sources", False),
                ("tls/ca.pem", "/run/tls/ca.pem", False),
                ("tls/client.pem", "/run/tls/client.pem", False),
                ("tls/client.key", "/run/tls/client.key", False),
            ]
        return {
            "Id": container_id,
            "Name": "/" + name,
            "Image": image,
            "Config": {"Labels": {
                probe.OWNER_LABEL: probe.TASK,
                probe.SCOPE_LABEL: probe.PROJECT,
            }},
            "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "State": {"Running": running, "ExitCode": 0, "OOMKilled": False},
            "RestartCount": 0,
            "Mounts": [
                {"Type": "bind", "Source": str(self.root / source),
                 "Destination": destination, "RW": writable}
                for source, destination, writable in mounts
            ],
        }

    def test_exact_loki_and_vector_mount_sets_allow_owned_operations(self):
        for role in ("loki", "vector"):
            container = self.container(role)
            name = probe.LOKI_NAME if role == "loki" else probe.VECTOR_NAME
            with patch.object(probe, "_container_by_id", return_value=container), patch.object(
                probe, "docker"
            ) as docker:
                stopped = probe._stop_owned(container["Id"], name, container["Image"])
            self.assertEqual(stopped["exit_code"], 0)
            docker.assert_not_called()

    def test_parent_bind_does_not_gain_ownership_from_symmetric_overlap(self):
        container = self.container()
        container["Mounts"][0]["Source"] = str(self.root.parent)
        self.assertTrue(probe._overlap(self.root.parent, self.root))
        with self.assertRaisesRegex(probe.ProbeError, "scope_path_outside_root"):
            probe._check_owned(container, probe.LOKI_NAME, container["Image"])

    def test_missing_wrong_and_extra_mounts_are_rejected(self):
        variants = []
        no_bind = self.container()
        no_bind["Mounts"] = []
        variants.append(no_bind)
        wrong_destination = self.container()
        wrong_destination["Mounts"][0]["Destination"] = "/var/lib/other"
        variants.append(wrong_destination)
        extra = self.container()
        extra["Mounts"].append({
            "Type": "bind", "Source": str(self.root / "tls/loki.pem"),
            "Destination": "/unexpected", "RW": False,
        })
        variants.append(extra)
        extra_volume = self.container()
        extra_volume["Mounts"].append({
            "Type": "volume", "Source": "unexpected", "Destination": "/data",
            "RW": True,
        })
        variants.append(extra_volume)
        for container in variants:
            with self.subTest(mounts=container["Mounts"]):
                with self.assertRaisesRegex(
                    probe.ProbeError, "container_mount_set_mismatch"
                ):
                    probe._check_owned(container, probe.LOKI_NAME, container["Image"])

    def test_readonly_contract_and_exact_source_are_required(self):
        writable_config = self.container()
        writable_config["Mounts"][1]["RW"] = True
        with self.assertRaisesRegex(probe.ProbeError, "container_mount_access_mismatch"):
            probe._check_owned(
                writable_config, probe.LOKI_NAME, writable_config["Image"]
            )
        wrong_source = self.container()
        wrong_source["Mounts"][1]["Source"] = str(self.root / "tls/loki.pem")
        with self.assertRaisesRegex(probe.ProbeError, "container_mount_source_mismatch"):
            probe._check_owned(wrong_source, probe.LOKI_NAME, wrong_source["Image"])

    def test_replaced_id_never_receives_start_or_term(self):
        replacement = self.container(container_id="b" * 64, running=True)
        with patch.object(probe, "_container_by_id", return_value=replacement), patch.object(
            probe, "docker"
        ) as docker:
            with self.assertRaisesRegex(probe.ProbeError, "container_id_mismatch"):
                probe._stop_owned("a" * 64, probe.LOKI_NAME, replacement["Image"])
            with self.assertRaisesRegex(probe.ProbeError, "container_id_mismatch"):
                probe._start_existing("a" * 64, probe.LOKI_NAME, replacement["Image"])
            docker.assert_not_called()

    def test_identity_is_rechecked_after_start_and_term(self):
        original_running = self.container(running=True)
        original_stopped = self.container(running=False)
        replacement_running = self.container(container_id="b" * 64, running=True)
        replacement_stopped = self.container(container_id="b" * 64, running=False)
        with patch.object(
            probe, "_container_by_id",
            side_effect=[original_running, replacement_stopped],
        ), patch.object(probe, "docker") as docker:
            with self.assertRaisesRegex(probe.ProbeError, "container_id_mismatch"):
                probe._stop_owned("a" * 64, probe.LOKI_NAME, original_running["Image"])
            docker.assert_called_once_with(
                "kill", "--signal", "TERM", "a" * 64, timeout=10
            )
        with patch.object(
            probe, "_container_by_id",
            side_effect=[original_stopped, replacement_running],
        ), patch.object(probe, "docker") as docker:
            with self.assertRaisesRegex(probe.ProbeError, "container_id_mismatch"):
                probe._start_existing(
                    "a" * 64, probe.LOKI_NAME, original_stopped["Image"]
                )
            docker.assert_called_once_with("start", "a" * 64)

    def test_child_symlinks_and_lexical_escape_are_refused_before_directory_changes(self):
        target = self.root / "tmpfs/vector"
        original_detector = probe._is_link_or_reparse
        with patch.object(
            probe, "_is_link_or_reparse",
            side_effect=lambda path: Path(path) == target or original_detector(path),
        ):
            with self.assertRaisesRegex(
                probe.ProbeError, "scope_symlink_or_reparse_refused"
            ):
                probe._base_dirs(self.root)
        self.assertTrue(probe._is_link_or_reparse(
            SimpleNamespace(lstat=lambda: SimpleNamespace(
                st_mode=stat.S_IFLNK, st_file_attributes=0
            ))
        ))
        self.assertTrue(probe._is_link_or_reparse(
            SimpleNamespace(lstat=lambda: SimpleNamespace(
                st_mode=stat.S_IFDIR, st_file_attributes=0x400
            ))
        ))
        with self.assertRaisesRegex(probe.ProbeError, "scope_path_invalid"):
            probe._scope_path(self.root / "tmpfs/../../outside")
        with self.assertRaisesRegex(probe.ProbeError, "scope_path_outside_root"):
            probe._scope_path(self.root.parent / "outside")

    @unittest.skipUnless(sys.platform == "linux", "directory fd boundary requires Linux")
    def test_valid_scope_subdirectories_are_created_inside_root(self):
        with patch.object(probe.os, "chown", create=True), patch.object(
            probe.os, "fchown", create=True
        ):
            paths = probe._base_dirs(self.root)
        self.assertTrue(paths["source"].is_dir())
        self.assertTrue(paths["vector_source"].is_dir())
        self.assertTrue(paths["evidence"].is_dir())
        self.assertEqual(paths["source"].resolve().parents[1], self.root)

    @unittest.skipUnless(sys.platform == "linux", "directory fd boundary requires Linux")
    def test_existing_linked_output_file_is_rejected_before_write_or_append(self):
        config = self.root / "config/loki.json"
        source = self.root / "tmpfs/vector-source/events.jsonl"
        source.write_text("unchanged\n", encoding="utf-8")
        original_detector = probe._is_link_or_reparse
        with patch.object(
            probe, "_is_link_or_reparse",
            side_effect=lambda path: Path(path) in (config, source)
            or original_detector(path),
        ):
            with self.assertRaisesRegex(
                probe.ProbeError, "scope_symlink_or_reparse_refused"
            ):
                probe._write_loki_config(config, "48h")
            with self.assertRaisesRegex(
                probe.ProbeError, "scope_symlink_or_reparse_refused"
            ):
                probe._write_synthetic(source, [{"event_id": "synthetic"}], append=True)
        self.assertEqual(config.read_text(encoding="utf-8"), "synthetic")
        self.assertEqual(source.read_text(encoding="utf-8"), "unchanged\n")

    def test_followup_and_store_entrypoints_use_the_same_guarded_helper(self):
        import nas_a2_followup_probe as followup
        import nas_a2_store_probe as store

        self.assertIs(followup.base, probe)
        self.assertIs(store.base, probe)
        forged = self.container()
        forged["Mounts"] = []
        for imported_helper in (followup.base, store.base):
            with self.assertRaisesRegex(
                probe.ProbeError, "container_mount_set_mismatch"
            ):
                imported_helper._check_owned(
                    forged, probe.LOKI_NAME, forged["Image"]
                )


@unittest.skipUnless(sys.platform == "linux", "directory fd boundary requires Linux")
class NasA2LinuxOutputBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        parent = Path(self.temporary.name).resolve()
        self.root = parent / "scope"
        self.root.mkdir()
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.outside = parent / "outside"
        self.outside.mkdir()
        self.expected_root = patch.object(probe, "EXPECTED_ROOT", self.root)
        self.expected_root.start()
        self.addCleanup(self.expected_root.stop)

    def _swap_evidence(self):
        self.evidence.rename(self.root / "evidence-original")
        self.evidence.symlink_to(self.outside, target_is_directory=True)

    def _assert_swap_after_check_refuses(self, target: Path, write):
        probe._scope_path(target)
        original = probe._scope_path
        swapped = False

        def check_then_swap(path, **kwargs):
            nonlocal swapped
            result = original(path, **kwargs)
            if Path(path) == target and not swapped:
                self._swap_evidence()
                swapped = True
            return result

        with patch.object(probe, "_scope_path", side_effect=check_then_swap):
            with self.assertRaises(probe.ProbeError):
                write()
        self.assertTrue(swapped)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_checkpoint_and_final_report_refuse_swapped_parent(self):
        import nas_a2_followup_probe as followup

        checkpoint = self.evidence / "checkpoint.json"
        self._assert_swap_after_check_refuses(
            checkpoint, lambda: followup._checkpoint(checkpoint, {"status": "failed"})
        )
        self.evidence.unlink()
        (self.root / "evidence-original").rename(self.evidence)
        final = self.evidence / "nas-a2-report.json"
        self._assert_swap_after_check_refuses(
            final, lambda: probe.write_json(final, {"status": "failed"})
        )

    def test_log_output_refuses_swapped_parent(self):
        import nas_a2_followup_probe as followup

        target = self.evidence / "vector-fill-logs.txt"
        self._assert_swap_after_check_refuses(
            target,
            lambda: followup._write_evidence_text(
                {"evidence": self.evidence}, target.name, "synthetic log"
            ),
        )

    def test_archive_output_refuses_swapped_parent(self):
        import nas_a2_followup_probe as followup

        scratch = self.root / "tmpfs"
        source = scratch / "vector-source" / "events.jsonl"
        source.parent.mkdir(parents=True)
        source.write_bytes(b'{"event_id":"synthetic"}\n')
        target = self.evidence / "tmpfs-snapshot-after-probe.tar.gz"
        with patch.object(followup, "RUN_ROOT", self.root):
            self._assert_swap_after_check_refuses(
                target,
                lambda: followup._archive_and_unmount_new_scope(
                    {"scratch": scratch, "vector_source": source.parent,
                     "evidence": self.evidence}, {}
                ),
            )

    def test_open_parent_fd_stays_on_original_directory_after_swap(self):
        target = self.evidence / "checkpoint.json"
        with probe._scope_atomic_stream(target) as stream:
            self._swap_evidence()
            stream.write(b"safe\n")
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertEqual(
            (self.root / "evidence-original" / target.name).read_bytes(), b"safe\n"
        )

    def test_bound_loki_config_keeps_inode_across_mode_switch(self):
        import nas_a2_followup_probe as followup

        config = self.root / "config"
        config.mkdir()
        path = config / "loki.json"
        followup._write_loki_config(path, query_store_only=False)
        original_inode = path.stat().st_ino
        followup._write_loki_config(path, query_store_only=True)
        self.assertEqual(path.stat().st_ino, original_inode)
        self.assertTrue(json.loads(path.read_bytes())["querier"]["query_store_only"])
        tls = self.root / "tls"
        tls.mkdir()
        certificate = tls / "loki.pem"
        with patch.object(probe.os, "fchown"):
            probe._write_certificate(certificate, b"first")
            certificate_inode = certificate.stat().st_ino
            certificate.chmod(0o644)  # Test user lacks probe's root privileges.
            probe._write_certificate(certificate, b"second")
        self.assertEqual(certificate.stat().st_ino, certificate_inode)
        self.assertEqual(certificate.read_bytes(), b"second")


if __name__ == "__main__":
    unittest.main()
