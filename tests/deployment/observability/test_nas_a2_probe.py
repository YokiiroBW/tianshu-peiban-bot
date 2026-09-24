from pathlib import Path
import unittest

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

    def test_buffer_padding_is_marked_as_synthetic_only(self):
        row = probe._record(4, pad=True)
        self.assertEqual(len(row["a2_buffer_fixture"]), 2300)
        self.assertNotIn("a2_buffer_fixture", probe._record(4))


if __name__ == "__main__":
    unittest.main()
