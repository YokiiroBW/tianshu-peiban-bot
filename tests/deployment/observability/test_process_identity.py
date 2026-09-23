import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import helpers  # noqa: F401
from process_identity import process_status
from nas_resources import network_plan


class ProcessIdentityTests(unittest.TestCase):
    def test_distroless_host_pid_and_restart_rejected(self):
        row = {
            "Id": "fixed",
            "Image": "sha256:fixed",
            "State": {"Pid": 321, "Running": True, "StartedAt": "fixed"},
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "321").mkdir()
            (root / "321/stat").write_text(
                "321 (name with space) " + " ".join(["0"] * 19 + ["12345"])
            )
            (root / "321/status").write_text(
                "Uid:\t10001\t10001\t10001\t10001\nGid:\t10001\t10001\t10001\t10001\n"
            )
            docker = Mock()
            docker.call.return_value = json.dumps([row]).encode()
            self.assertIn("Uid:", process_status(docker, row, root))
            self.assertEqual(docker.call.call_args.args[0], ["inspect", "fixed"])
            changed = copy.deepcopy(row)
            changed["State"]["StartedAt"] = "restarted"
            docker.call.return_value = json.dumps([changed]).encode()
            with self.assertRaisesRegex(ValueError, "identity_changed"):
                process_status(docker, row, root)

    def test_observe_networks_do_not_overlap_core_or_auxiliary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "deployment.json").write_text(
                json.dumps(
                    {
                        "compose_inputs": {
                            "subnet": "10.203.100.0/24",
                            "auxiliary_subnets": {
                                "egress": "10.203.101.0/24",
                                "frontend": "10.203.102.0/24",
                            },
                        }
                    }
                )
            )
            plan = {"observe": "10.203.103.0/24", "storage": "10.203.104.0/24"}
            self.assertEqual(network_plan(root, plan), plan)
            for changed in (
                dict(plan, observe="10.203.101.0/24"),
                dict(plan, storage=plan["observe"]),
            ):
                with self.assertRaisesRegex(ValueError, "overlap"):
                    network_plan(root, changed)
