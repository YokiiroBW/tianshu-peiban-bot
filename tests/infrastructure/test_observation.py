from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import COMPOSE, OUTPUT, ROOT, FixtureTransport, engine_stats, host_sections, http_response, identity, inspect_value, sections
from jsonschema import Draft202012Validator, FormatChecker

from ops.infrastructure.compose import compare_stack, parse_compose
from ops.infrastructure.config import Config, Node, parse_config
from ops.infrastructure.docker import DockerCollector, parse_container, parse_http, project_stats
from ops.infrastructure.executor import execute
from ops.infrastructure.linux import parse_cpu, parse_memory
from ops.infrastructure.protocol import Failure, VERSION, decode_json, parse_request
from ops.infrastructure.sampling import SampleStore, _Lock
from ops.infrastructure.transport import CommandResult, Deadline, parse_sections, run_bounded


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
        def transport_factory(_config, _node, deadline):
            self.fixture.deadline = deadline
            return self.fixture

        response = execute(self.request(operation, params, **extra), self.config,
                           transport_factory=transport_factory)
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

    def test_stats_pipeline_keeps_input_and_decodes_chunked_and_length_bodies(self):
        self.fixture.metadata[identity(2)] = inspect_value(2)
        original = self.fixture.command

        def command(argv, payload=b"", **kwargs):
            if b"/stats?" not in payload:
                return original(argv, payload, **kwargs)
            self.assertTrue(kwargs["keep_stdin_open"])
            self.assertEqual(payload.count(b"Connection: keep-alive"), 1)
            self.assertTrue(payload.endswith(b"Connection: close\r\n\r\n"))
            body = json.dumps(engine_stats()).encode()
            chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
            return CommandResult(chunked + http_response(engine_stats(2)), exit_code=0)

        self.fixture.command = command
        with patch("ops.infrastructure.docker.STATS_WORKERS", 1):
            response = self.run_request("docker.stats", {"container_ids": [identity(), identity(2)]})
        self.assertEqual(response["status"], "ok")
        self.assertEqual([item["id"] for item in response["data"]["containers"]], [identity(), identity(2)])
        self.assertTrue(all(item["cpu"]["state"] == "first_sample" for item in response["data"]["containers"]))

    def test_stats_empty_or_wrong_identity_body_never_becomes_zero_metrics(self):
        self.fixture.metadata[identity(2)] = inspect_value(2)
        original = self.fixture.command
        for tail in (b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", http_response(engine_stats(3))):
            with self.subTest(tail=tail[:30]):
                def command(argv, payload=b"", **kwargs):
                    if b"/stats?" in payload:
                        return CommandResult(http_response(engine_stats()) + tail, exit_code=0)
                    return original(argv, payload, **kwargs)

                self.fixture.command = command
                with patch("ops.infrastructure.docker.STATS_WORKERS", 1):
                    response = self.run_request("docker.stats", {"container_ids": [identity(), identity(2)]})
                items = response["data"]["containers"]
                self.assertEqual(response["status"], "partial")
                self.assertEqual(items[0]["availability"], "ok")
                self.assertEqual(items[1]["reason_codes"], ["docker_stats_invalid"])
                self.assertIsNone(items[1]["cpu"])
                self.assertIsNone(items[1]["memory"])

    def test_stats_groups_share_budget_and_skip_all_stopped_objects(self):
        self.fixture.metadata = {identity(index): inspect_value(index, state="running" if index <= 78 else "created") for index in range(1, 201)}
        original = self.fixture.command
        barrier = threading.Barrier(8)
        lock = threading.Lock()
        calls = []

        def command(argv, payload=b"", **kwargs):
            if b"/stats?" in payload:
                with lock:
                    calls.append((payload, kwargs["deadline"], kwargs["max_output"]))
                barrier.wait(timeout=2)
            return original(argv, payload, **kwargs)

        self.fixture.command = command
        response = self.run_request("docker.stats", {"container_ids": list(self.fixture.metadata)})
        self.assertEqual(len(calls), 8)
        self.assertEqual(sum(item[2] for item in calls), 3 * 1024 * 1024)
        self.assertTrue(all(item[1] is calls[0][1] for item in calls))
        fetched = [value.decode() for payload, _, _ in calls for value in re.findall(rb"/containers/([a-f0-9]{64})/stats", payload)]
        self.assertEqual(set(fetched), {identity(index) for index in range(1, 79)})
        self.assertEqual(len(fetched), 78)
        rows = response["data"]["containers"]
        self.assertEqual(len(rows), 200)
        self.assertEqual(sum(item["availability"] == "ok" for item in rows), 78)
        self.assertTrue(all(item["reason_codes"] == ["container_not_running"] for item in rows[78:]))
        self.assertFalse(any(thread.name.startswith("infrastructure-stats") for thread in threading.enumerate()))

    def test_stats_output_cap_retains_completed_prefix_and_all_identities(self):
        self.fixture.metadata[identity(2)] = inspect_value(2)
        original = self.fixture.command
        oversized = engine_stats(2)
        oversized["unused_padding"] = "x" * (4 * 1024 * 1024)
        raw = http_response(engine_stats()) + http_response(oversized)

        def command(argv, payload=b"", **kwargs):
            if b"/stats?" in payload:
                cap = kwargs["max_output"]
                self.assertEqual(cap, 3 * 1024 * 1024)
                return CommandResult(raw[:cap], exit_code=0, truncated=True)
            return original(argv, payload, **kwargs)

        self.fixture.command = command
        with patch("ops.infrastructure.docker.STATS_WORKERS", 1):
            response = self.run_request("docker.stats", {"container_ids": [identity(), identity(2)]})
        rows = response["data"]["containers"]
        self.assertEqual(response["status"], "partial")
        self.assertEqual(rows[0]["availability"], "ok")
        self.assertEqual(rows[1]["reason_codes"], ["output_limit_exceeded"])
        self.assertIsNone(rows[1]["memory"])
        self.assertNotIn("unused_padding", json.dumps(response))

    def test_stats_shared_deadline_reaps_real_worker_processes_and_keeps_prefix(self):
        self.fixture.metadata = {identity(index): inspect_value(index) for index in range(1, 17)}
        original = self.fixture.command
        processes = []
        actual_popen = subprocess.Popen

        def popen(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            processes.append(process)
            return process

        def command(argv, payload=b"", **kwargs):
            selected = re.findall(rb"/containers/([a-f0-9]{64})/stats", payload)
            if not selected:
                return original(argv, payload, **kwargs)
            prefix = http_response(engine_stats(int(selected[0], 16)))
            program = "import sys,time; sys.stdin.buffer.read(int(sys.argv[1])); sys.stdout.buffer.write(bytes.fromhex(sys.argv[2])); sys.stdout.buffer.flush(); time.sleep(30)"
            return run_bounded([sys.executable, "-c", program, str(len(payload)), prefix.hex()], payload, kwargs["deadline"],
                               max_output=kwargs["max_output"], keep_stdin_open=kwargs["keep_stdin_open"])

        self.fixture.command = command
        started = time.monotonic()
        with patch("ops.infrastructure.transport.subprocess.Popen", side_effect=popen):
            response = self.run_request("docker.stats", {"container_ids": list(self.fixture.metadata)}, timeout_ms=2000)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(processes), 8)
        self.assertTrue(all(process.returncode is not None for process in processes))
        self.assertFalse(any(thread.name.startswith("infrastructure-stats") for thread in threading.enumerate()))
        rows = response["data"]["containers"]
        self.assertEqual(response["status"], "partial")
        self.assertEqual(len(rows), 16)
        self.assertEqual(sum(item["availability"] == "ok" for item in rows), 8)
        self.assertTrue(all(item["reason_codes"] == ["deadline_exceeded"] for item in rows[8:]))

    def test_stats_finished_rows_survive_deadline_at_dispatch_return(self):
        original = self.fixture.command

        def command(argv, payload=b"", **kwargs):
            result = original(argv, payload, **kwargs)
            if b"/stats?" in payload:
                self.fixture.deadline.expires = time.monotonic() - 1
            return result

        self.fixture.command = command
        response = self.run_request("docker.stats", {"container_ids": [identity()]})
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["data"]["containers"][0]["availability"], "ok")
        self.assertIn("deadline_exceeded", response["reason_codes"])
        self.assertIn("sample_state_not_saved", response["reason_codes"])

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

    def test_compose_partial_find_retains_readable_and_marks_denied_stack(self):
        original = self.fixture.script

        def script(source, **kwargs):
            if "find " in source:
                return CommandResult(b"/srv/stacks/synthetic/compose.yaml\n", b"find: `/srv/stacks/zzz-denied': Permission denied\n", 1)
            return original(source, **kwargs)

        self.fixture.script = script
        response = self.run_request("compose.inventory")
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["completeness"]["state"], "partial")
        self.assertEqual(response["completeness"]["total"], 2)
        self.assertIn("compose_discovery_partial", response["reason_codes"])
        readable, denied = response["data"]["stacks"]
        self.assertEqual(readable["drift_status"], "in_sync")
        self.assertEqual(denied["state"], "unavailable")
        self.assertEqual(denied["reason_codes"], ["permission_denied"])
        self.assertNotIn("zzz-denied/compose.yaml", "\n".join(str(item) for item in self.fixture.commands))

    def test_compose_root_denied_remains_unavailable(self):
        self.fixture.script = lambda *_, **__: CommandResult(b"", b"find: `/srv/stacks': Permission denied\n", 1)
        response = self.run_request("compose.inventory")
        self.assertEqual(response["status"], "unavailable")
        self.assertEqual(response["reason_codes"], ["permission_denied"])
        self.assertIsNone(response["data"])

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

    def test_compose_parsing_uses_the_request_deadline(self):
        with self.assertRaises(Failure) as raised:
            parse_compose(COMPOSE, "fixture", "synthetic", "/srv/stacks/synthetic", deadline=Deadline(0))
        self.assertEqual(raised.exception.code, "deadline_exceeded")

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
