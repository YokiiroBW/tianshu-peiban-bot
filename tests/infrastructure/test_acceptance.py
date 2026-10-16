from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import OUTPUT, ROOT, FixtureTransport, LoopbackSSH, identity, inspect_value

from ops.infrastructure.acceptance import Peer, compare_receipts, inventory, limited_log, metadata_summary, observe
from ops.infrastructure.protocol import Failure, VERSION


class ReceiptTests(unittest.TestCase):
    def test_receipt_does_not_publish_object_names_or_paths(self):
        from ops.infrastructure.docker import parse_container

        item = parse_container(inspect_value())
        item["name"] = "private-synthetic-name"
        item["compose"]["project"] = "private-project"
        summary = metadata_summary([item])
        encoded = json.dumps(summary)
        for value in ("private-synthetic-name", "private-project", "/srv/stacks", item["id"]):
            self.assertNotIn(value, encoded)
        self.assertEqual(summary["count"], 1)

    def test_pagination_must_account_for_all_ids(self):
        class Pages:
            def __init__(self):
                self.calls = 0

            def invoke(self, operation, params):
                self.calls += 1
                first = self.calls == 1
                rows = [{"id": identity(index)} for index in (range(1, 201) if first else range(201, 251))]
                return {"status": "ok", "data": {"containers": rows}, "completeness": {"returned": len(rows), "total": 250, "next_cursor": "second" if first else None}}

        pages = Pages()
        self.assertEqual(len(inventory(pages, "docker.inventory", "containers")), 250)
        self.assertEqual(pages.calls, 2)

    def test_repeated_or_incomplete_page_is_not_a_pass(self):
        class Repeating:
            def invoke(self, operation, params):
                return {"status": "ok", "data": {"containers": [{"id": identity()}]},
                        "completeness": {"returned": 1, "total": 2, "next_cursor": "again"}}

        with self.assertRaises(Failure):
            inventory(Repeating(), "docker.inventory", "containers")

    def test_selected_log_is_hashed_and_bound_checked(self):
        class Logs:
            def invoke(self, operation, params):
                self.params = params
                return {"status": "ok", "reason_codes": [], "data": {"container_id": identity(), "stdout": "synthetic-private-body\n", "stderr": "",
                        "returned_bytes": len("synthetic-private-body\n".encode()), "tail": 200, "since_seconds": 3600, "max_bytes": 65536, "truncated": False}}

        peer = Logs()
        result = limited_log(peer, identity())
        self.assertNotIn("synthetic-private-body", json.dumps(result))
        self.assertFalse(result["body_recorded"])
        self.assertEqual(peer.params["tail"], 200)
        self.assertEqual(peer.params["max_bytes"], 65536)
        with self.assertRaises(Failure):
            limited_log(peer, "container-name")

    def test_comparison_does_not_call_unknown_boots_equal(self):
        self.assertIsNone(compare_receipts({}, {})["boot_identity_same"])


@unittest.skipUnless(shutil.which("ssh") and importlib.util.find_spec("paramiko"), "optional synthetic SSH dependencies unavailable")
class DriverProcessTest(unittest.TestCase):
    def test_new_driver_invokes_actual_executor_without_reading_logs_by_default(self):
        with tempfile.TemporaryDirectory(dir=OUTPUT) as temporary:
            directory = Path(temporary)
            fixture = FixtureTransport()
            with LoopbackSSH(directory, fixture) as server:
                config = {"version": VERSION, "state_dir": str(directory / "state"), "nodes": {"fixture-nas": {
                    "label": "Synthetic NAS", "kind": "synology", "host": "127.0.0.1", "port": server.port,
                    "user": "synthetic", "identity_file": str(server.key_path), "known_hosts_file": str(server.known_hosts),
                    "docker": {"enabled": True, "command": ["synthetic-docker"], "socket_path": "/var/run/synthetic.sock"},
                    "stacks_root": "/srv/stacks"}}}
                path = directory / "executor.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                peer = Peer(config=path, node_id="fixture-nas", contract_directory=ROOT / "contracts/infrastructure-node/candidate-v1")
                report = observe(peer, .2)
                self.assertEqual(report["docker"]["count"], 1)
                self.assertEqual(report["compose"]["drift_counts"], {"in_sync": 1})
                self.assertNotIn("docker.logs", [call["operation"] for call in peer.calls])
                self.assertGreater(len(server.calls), 5)
                serialized = json.dumps(report)
                for value in ("synthetic-web", "127.0.0.1", "/srv/stacks", "synthetic_key"):
                    self.assertNotIn(value, serialized)


if __name__ == "__main__":
    unittest.main()
