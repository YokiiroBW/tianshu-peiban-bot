from __future__ import annotations

import dataclasses
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import COMPOSE, OUTPUT, ROOT, FixtureTransport, host_sections, identity, inspect_value, sections
from jsonschema import Draft202012Validator, FormatChecker

from ops.infrastructure.compose import compare_stack, parse_compose
from ops.infrastructure.config import Config, Node, parse_config
from ops.infrastructure.docker import DockerCollector, parse_container, parse_http, project_stats
from ops.infrastructure.executor import execute
from ops.infrastructure.linux import parse_cpu, parse_memory
from ops.infrastructure.protocol import Failure, VERSION, decode_json, parse_request
from ops.infrastructure.sampling import SampleStore, _Lock
from ops.infrastructure.transport import CommandResult, parse_sections


class ObservationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((ROOT / "contracts/infrastructure-node/candidate-v1/response.schema.json").read_text())
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=OUTPUT)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.node = Node("fixture", "Synthetic NAS", "synology", "127.0.0.1", 2222, "synthetic",
                         str(self.directory / "synthetic_key"), str(self.directory / "synthetic_known_hosts"),
                         ("synthetic-docker",), "/var/run/synthetic.sock", "/srv/stacks")
        self.config = Config(self.directory / "state", {"fixture": self.node})
        self.fixture = FixtureTransport(node=self.node)

    def request(self, operation, params=None, **extra):
        return parse_request(json.dumps({"version": VERSION, "request_id": "test-1",
                                        "node_id": None if operation == "node.catalog" else self.node.id,
                                        "operation": operation, "params": params or {}, "timeout_ms": 10000, **extra}).encode())

    def run_request(self, operation, params=None, **extra):
        response = execute(self.request(operation, params, **extra), self.config,
                           transport_factory=lambda *_: self.fixture)
        self.validator.validate(response)
        return response

    def test_catalog_is_sanitized_and_does_not_connect(self):
        response = execute(self.request("node.catalog"), self.config,
                           transport_factory=lambda *_: self.fail("catalog connected"))
        self.validator.validate(response)
        self.assertEqual(set(response["data"]["nodes"][0]), {"id", "label", "kind", "capabilities"})
        serialized = json.dumps(response)
        for secret in ("127.0.0.1", "synthetic_key", "synthetic.sock", "stacks_root"):
            self.assertNotIn(secret, serialized)
        self.assertFalse(self.config.state_dir.exists())

    def test_protocol_rejects_commands_short_ids_duplicates_and_nonfinite(self):
        invalid = [
            ("node.snapshot", {"command": "reboot"}),
            ("docker.logs", {"container_id": "web"}),
            ("docker.logs", {"container_id": identity(), "max_bytes": 262145}),
            ("docker.stats", {"container_ids": [identity(), identity()]}),
            ("docker.stats", {"container_ids": [identity(), {"id": identity()}]}),
        ]
        for operation, params in invalid:
            with self.subTest(operation=operation, params=params), self.assertRaises(Failure):
                self.request(operation, params)
        for raw in (b'{"version":1,"version":2}', b'{"n":NaN}', b'{"n":Infinity}'):
            with self.assertRaises(ValueError):
                decode_json(raw)

    def test_config_requires_explicit_paths_and_no_connection_defaults(self):
        value = {"version": VERSION, "state_dir": str(self.directory / "state"), "nodes": {}}
        self.assertEqual(parse_config(value).nodes, {})
        for change in ({"state_dir": "state"}, {"nodes": {"bad": {"host": "nas"}}}, {"shell": "whoami"}):
            with self.subTest(change=change), self.assertRaises(Failure):
                parse_config({**value, **change})

    def test_cpu_guest_iowait_and_memory_unknown(self):
        values, total, idle = parse_cpu(b"cpu 100 10 20 200 30 1 2 3 100 10\n")
        self.assertEqual(total, 366)
        self.assertEqual(idle, 230)
        self.assertEqual(len(values), 8)
        memory = parse_memory(b"MemTotal: 100 kB\nMemFree: 50 kB\n")
        self.assertIsNone(memory["available_bytes"])
        self.assertIsNone(memory["used_bytes"])
        self.assertEqual(memory["state"], "unavailable")

    def test_first_sample_deltas_stale_and_counter_reset(self):
        first = self.run_request("node.snapshot")
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["data"]["cpu"]["state"], "first_sample")
        self.assertIsNone(first["data"]["cpu"]["usage_ratio"])
        old_interface = first["data"]["interfaces"][0]["id"]
        old_filesystem = first["data"]["filesystems"][0]["id"]
        self.fixture.host = host_sections(uptime=110, cpu="130 0 90 880 0 0 0 0 30 0", received=2000, sent=5000)
        second = self.run_request("node.snapshot")
        self.assertAlmostEqual(second["data"]["cpu"]["usage_ratio"], .7)
        self.assertEqual(second["data"]["interfaces"][0]["receive_bytes_per_second"], 100)
        self.assertEqual(second["data"]["interfaces"][0]["transmit_bytes_per_second"], 300)
        self.assertEqual(second["data"]["interfaces"][0]["id"], old_interface)
        self.assertEqual(second["data"]["filesystems"][0]["id"], old_filesystem)
        self.fixture.host = host_sections(uptime=500, cpu="200 0 100 1000 0 0 0 0", received=5000)
        stale = self.run_request("node.snapshot")
        self.assertEqual(stale["data"]["cpu"]["state"], "stale")
        self.assertIsNone(stale["data"]["interfaces"][0]["receive_bytes_per_second"])
        self.fixture.host = host_sections(uptime=510, cpu="1 0 1 10 0 0 0 0", received=100, ifindex=7)
        reset = self.run_request("node.snapshot")
        self.assertEqual(reset["data"]["cpu"]["state"], "counter_reset")
        self.assertEqual(reset["data"]["interfaces"][0]["state"], "counter_reset")
        self.assertEqual(reset["data"]["interfaces"][0]["id"], old_interface)

    def test_openwrt_l3_device_mapping_and_missing_ubus(self):
        self.node = dataclasses.replace(self.node, kind="openwrt")
        self.config = dataclasses.replace(self.config, nodes={self.node.id: self.node})
        self.fixture.host = host_sections(openwrt=True)
        response = self.run_request("node.snapshot")
        self.assertEqual(response["status"], "ok")
        router = response["data"]["openwrt"]
        self.assertEqual(router["interfaces"][0]["l3_device"], response["data"]["interfaces"][0]["name"])
        self.assertEqual(router["per_device_traffic"]["state"], "unsupported")
        self.assertAlmostEqual(router["info"]["load_average"][0], 6553 / 65536)
        self.fixture.host = host_sections(openwrt=True, uptime=110, received=2000)
        second = self.run_request("node.snapshot")
        self.assertEqual(second["data"]["interfaces"][0]["receive_bytes_per_second"], 100)
        parts = parse_sections(self.fixture.host)
        parts["board"] = (127, b"")
        parts["info"] = (127, b"")
        parts["ubus_interfaces"] = (127, b"")
        self.fixture.host = sections(parts)
        missing = self.run_request("node.snapshot")
        self.assertEqual(missing["status"], "partial")
        self.assertIsNone(missing["data"]["openwrt"]["board"])
        self.assertIn("ubus_interfaces_unavailable", missing["reason_codes"])

    def test_openwrt_unmapped_l3_device_is_partial(self):
        self.node = dataclasses.replace(self.node, kind="openwrt")
        self.config = dataclasses.replace(self.config, nodes={self.node.id: self.node})
        parts = parse_sections(host_sections(openwrt=True))
        dump = json.loads(parts["ubus_interfaces"][1])
        dump["interface"][0]["l3_device"] = "pppoe-wan"
        parts["ubus_interfaces"] = (0, json.dumps(dump).encode())
        self.fixture.host = sections(parts)
        response = self.run_request("node.snapshot")
        self.assertEqual(response["status"], "partial")
        self.assertIn("ubus_device_counters_unavailable", response["reason_codes"])
        self.assertEqual(response["data"]["openwrt"]["per_device_traffic"]["items"], [])

    def test_more_than_64_containers_pagination_and_invalidation(self):
        self.fixture.ids = [identity(i) for i in range(1, 151)]
        self.fixture.metadata = {identity(i): inspect_value(i) for i in range(1, 151)}
        first = self.run_request("docker.inventory", {"limit": 70})
        self.assertEqual(first["completeness"]["total"], 150)
        self.assertEqual(len(first["data"]["containers"]), 70)
        second = self.run_request("docker.inventory", {"limit": 70, "cursor": first["completeness"]["next_cursor"]})
        third = self.run_request("docker.inventory", {"limit": 70, "cursor": second["completeness"]["next_cursor"]})
        ids = [item["id"] for result in (first, second, third) for item in result["data"]["containers"]]
        self.assertEqual(ids, self.fixture.ids)
        self.assertIsNone(third["completeness"]["next_cursor"])
        self.fixture.ids.pop()
        changed = self.run_request("docker.inventory", {"cursor": first["completeness"]["next_cursor"]})
        self.assertEqual(changed["status"], "invalid")
        self.assertEqual(changed["reason_codes"], ["cursor_invalidated"])

    def test_disappearing_container_and_health_states(self):
        self.fixture.ids = [identity(i) for i in range(1, 8)]
        self.fixture.metadata = {identity(i): inspect_value(i, health=health) for i, health in enumerate(("none", "healthy", "unhealthy", "starting", "bad"), 1)}
        self.fixture.metadata[identity(6)] = inspect_value(6, state="exited")
        response = self.run_request("docker.inventory")
        self.assertEqual(response["status"], "partial")
        items = response["data"]["containers"]
        self.assertEqual([item["health"] for item in items[:5]], ["none", "healthy", "unhealthy", "starting", "unknown"])
        self.assertFalse(items[5]["running"])
        self.assertEqual(items[6]["availability"], "unavailable")
        self.assertIn("container_disappeared", items[6]["reason_codes"])
        self.assertEqual(response["completeness"]["total"], 7)

    def test_stats_cpu_memory_io_network_and_restart(self):
        with patch("ops.infrastructure.docker.time.monotonic", return_value=100):
            first = self.run_request("docker.stats", {"container_ids": [identity()]})
        self.assertEqual(first["data"]["containers"][0]["cpu"]["state"], "first_sample")
        self.fixture.cpu += 100
        self.fixture.system += 400
        with patch("ops.infrastructure.docker.time.monotonic", return_value=101):
            second = self.run_request("docker.stats", {"container_ids": [identity()]})
        item = second["data"]["containers"][0]
        self.assertEqual(item["cpu"]["usage_ratio"], 2)
        self.assertEqual(item["cpu"]["basis"], "one_core")
        self.assertEqual(item["memory"]["raw_usage_bytes"], 10000)
        self.assertEqual(item["memory"]["cache_adjusted_usage_bytes"], 9000)
        self.assertEqual(item["block_io"]["read_bytes"], 1024)
        self.assertEqual(item["network"]["receive_bytes"], 4000)
        self.fixture.metadata[identity()][5] = "2026-10-16T09:00:00Z"
        self.fixture.metadata[identity()][8] = "host"
        restarted = self.run_request("docker.stats", {"container_ids": [identity()]})["data"]["containers"][0]
        self.assertEqual(restarted["cpu"]["state"], "counter_reset")
        self.assertEqual(restarted["network"]["attribution"], "not_attributable")
        self.assertIsNone(restarted["network"]["receive_bytes"])

    def test_stopped_or_disappeared_stats_keep_requested_ids(self):
        self.fixture.metadata[identity()][7] = False
        response = self.run_request("docker.stats", {"container_ids": [identity(), identity(2)]})
        items = response["data"]["containers"]
        self.assertEqual([item["id"] for item in items], [identity(), identity(2)])
        self.assertEqual([item["availability"] for item in items], ["unavailable", "unavailable"])
        self.assertEqual(items[0]["reason_codes"], ["container_not_running"])

    def test_logs_are_utf8_byte_bounded_and_partial(self):
        self.fixture.log = ("网络日志\n" * 500).encode()
        response = self.run_request("docker.logs", {"container_id": identity(), "max_bytes": 1024})
        data = response["data"]
        self.assertEqual(response["status"], "partial")
        self.assertTrue(data["truncated"])
        self.assertLessEqual(len(data["stdout"].encode()) + len(data["stderr"].encode()), 1024)
        self.assertEqual(data["returned_bytes"], len(data["stdout"].encode()) + len(data["stderr"].encode()))

    def test_compose_real_projection_and_runtime_drift(self):
        response = self.run_request("compose.inventory")
        stack = response["data"]["stacks"][0]
        self.assertEqual(response["status"], "ok")
        self.assertEqual(stack["drift_status"], "in_sync")
        self.fixture.metadata[identity()][2] = "nginx:other"
        changed = self.run_request("compose.inventory")["data"]["stacks"][0]
        self.assertEqual(changed["drift_status"], "drifted")
        self.assertIn("image_mismatch", changed["services"][0]["findings"])
        serialized = "\n".join(str(command) for command in self.fixture.commands)
        self.assertNotIn("compose config", serialized)
        self.assertNotIn(".env", serialized)

    def test_compose_anchors_unresolved_vars_and_unsupported_sources(self):
        raw = b"""name: synthetic
x-defaults: &defaults
  image: nginx:alpine
services:
  web:
    <<: *defaults
    image: '${IMAGE}'
    env_file: '/not-read.env'
    build: '/not-read-build'
    profiles: [optional]
include: ['/not-read.yaml']
"""
        stack = parse_compose(raw, self.node.id, "synthetic", "/srv/stacks/synthetic")
        self.assertEqual(stack["state"], "partial")
        self.assertEqual(stack["services"][0]["image"]["state"], "unresolved")
        self.assertIn("compose_unsupported", stack["reason_codes"])
        compared = compare_stack(stack, [parse_container(inspect_value())])
        self.assertEqual(compared["drift_status"], "unavailable")
        self.assertNotIn("not-read", json.dumps(compared))
        anchored = parse_compose(raw.replace(b"    image: '${IMAGE}'\n", b""), self.node.id, "synthetic", "/srv/stacks/synthetic")
        self.assertEqual(anchored["services"][0]["image"]["reference"], "nginx:alpine")

    def test_compose_duplicate_keys_recursive_alias_and_custom_tags_invalid(self):
        for raw in (b"services: {web: {image: a, image: b}}", b"services: &a {web: *a}",
                    b"services: {web: !!python/object:os.system [whoami]}", b"services: {}\n---\nservices: {}"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_compose(raw, "fixture", "synthetic", "/srv/stacks/synthetic")["state"], "unavailable")

    def test_compose_common_named_volume_and_high_port_are_resolved(self):
        raw = b"""name: synthetic
services:
  web:
    image: nginx
    ports:
      - target: 18080
        published: 28080
    volumes:
      - data:/data
volumes:
  data:
"""
        stack = parse_compose(raw, "fixture", "synthetic", "/srv/stacks/synthetic")
        self.assertEqual(stack["state"], "ok", stack)
        service = stack["services"][0]
        self.assertEqual(service["ports"][0]["target"], 18080)
        self.assertEqual(service["mounts"][0]["source"], "synthetic_data")

    def test_json_sampling_wrong_structure_is_reported(self):
        self.config.state_dir.mkdir()
        store = SampleStore(self.config, self.node)
        store.path.write_text(json.dumps({"version": 1, "host": []}), encoding="utf-8")
        response = self.run_request("node.snapshot")
        self.assertEqual(response["status"], "partial")
        self.assertIn("sample_state_invalid", response["reason_codes"])

    def test_incomplete_runtime_cannot_claim_compose_in_sync(self):
        stack = parse_compose(COMPOSE, "fixture", "synthetic", "/srv/stacks/synthetic")
        compared = compare_stack(stack, [], runtime_complete=False)
        self.assertEqual(compared["drift_status"], "unavailable")
        self.assertEqual(compared["state"], "partial")

    def test_slot_wait_counts_toward_deadline(self):
        self.config = dataclasses.replace(self.config, max_concurrency=1)
        self.config.state_dir.mkdir()
        lock = _Lock(self.config.state_dir / "slot-0.lock")
        self.addCleanup(lock.close)
        self.assertTrue(lock.acquire())
        started = time.monotonic()
        response = self.run_request("node.snapshot", timeout_ms=500)
        self.assertEqual(response["reason_codes"], ["deadline_exceeded"])
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.fixture.commands, [])

    def test_invalid_sampling_file_is_visible_and_recovers(self):
        self.config.state_dir.mkdir()
        store = SampleStore(self.config, self.node)
        store.path.write_text('{"version":1,"host":', encoding="utf-8")
        response = self.run_request("node.snapshot")
        self.assertEqual(response["status"], "partial")
        self.assertIn("sample_state_invalid", response["reason_codes"])
        self.assertEqual(SampleStore(self.config, self.node).reasons, [])

    def test_missing_source_is_partial_and_no_fake_zero(self):
        parts = parse_sections(self.fixture.host)
        parts["cpu"] = (1, b"")
        parts["memory"] = (1, b"")
        self.fixture.host = sections(parts)
        response = self.run_request("node.snapshot")
        self.assertEqual(response["status"], "partial")
        self.assertIsNone(response["data"]["cpu"]["usage_ratio"])
        self.assertIsNone(response["data"]["memory"]["used_bytes"])


if __name__ == "__main__":
    unittest.main()
