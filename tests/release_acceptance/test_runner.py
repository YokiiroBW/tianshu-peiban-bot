"""Behavior tests; all writable fixtures are confined to this task's ignored directory."""

import json
import os
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from acceptance.diagnostics import causal, validate_events
from acceptance.evidence import Report, canonical, digest, read_json, verify_report
from acceptance.inputs import (
    ROLES,
    export_sources,
    inside,
    load_binding,
    verify_snapshot,
)
from acceptance.health import CHECKS, validate_ready
from acceptance.catalog import literal_registry
from acceptance.observation import observe
from acceptance.suite import Suite, validate_config
from acceptance import transport
from acceptance.transport import (
    Adapter,
    Client,
    Failed,
    Missing,
    deadline_scope,
    endpoint,
)
from fixtures import Harness, certificates

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime" / "unit"
RUNTIME.mkdir(parents=True, exist_ok=True)


def binding():
    return {
        "release_id": "runner-selftest-only",
        "manifest_sha256": "a" * 64,
        "release_status": "candidate",
        "products": {
            r: {
                "repo": "fixture-" + r,
                "commit": str(i + 1) * 40,
                "image": None,
                "digest": None,
            }
            for i, r in enumerate(ROLES)
        },
        "contracts": {},
        "identity_basis": "static_input_only",
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def suite(self, harness):
        report = Report("local", harness.binding, "synthetic")
        return Suite(harness.config, harness.binding, report), report

    def harness(self, **kwargs):
        return self.enterContext(Harness(self.directory, binding(), **kwargs))


class SlowWire:
    """Real loopback bytes, with bounded test-owned server threads and no mocks of I/O."""

    def __init__(self, directory, mode, tls=False):
        self.mode, self.tls = mode, tls
        self.stop = threading.Event()
        self.connections = 0
        self.context = None
        if tls:
            self.ca, key = certificates(directory)
            self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.context.load_cert_chain(self.ca, key)

    def __enter__(self):
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                owner.connections += 1
                sock = self.request
                sock.settimeout(0.5)
                try:
                    if owner.mode == "stall_tls":
                        owner.stop.wait(1)
                        return
                    if owner.context:
                        sock = owner.context.wrap_socket(sock, server_side=True)
                    request = bytearray()
                    while b"\r\n\r\n" not in request and len(request) < 65536:
                        chunk = sock.recv(4096)
                        if not chunk:
                            return
                        request.extend(chunk)
                    body = b'{"ok":true}'
                    if owner.mode == "slow_header":
                        sock.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                        for _ in range(40):
                            if owner.stop.wait(0.04):
                                return
                            sock.sendall(b"a")
                        sock.sendall(b"\r\n")
                    else:
                        sock.sendall(b"HTTP/1.1 200 OK\r\n")
                    if owner.mode == "oversize":
                        body = b"x" * (2 * transport.OUTPUT_LIMIT)
                    sock.sendall(
                        b"Content-Type: application/json\r\nContent-Length: "
                        + str(len(body)).encode()
                        + b"\r\n\r\n"
                    )
                    if owner.mode == "slow_body":
                        for byte in body:
                            sock.sendall(bytes([byte]))
                            if owner.stop.wait(0.04):
                                return
                    else:
                        if owner.mode == "delay_body" and owner.stop.wait(0.08):
                            return
                        sock.sendall(body)
                except (OSError, ssl.SSLError):
                    pass
                finally:
                    sock.close()

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.thread.start()
        self.config = {
            "url": f"{'https' if self.tls else 'http'}://127.0.0.1:{self.server.server_address[1]}",
            "timeout_seconds": 0.1,
        }
        if self.tls:
            self.config["ca_file"] = str(self.ca)
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()  # joins every non-daemon request handler
        self.thread.join(1)


class TransportBudgetTests(Base):
    def assert_drip_deadline(self, mode, tls=False):
        with SlowWire(self.directory, mode, tls) as server:
            opened = []
            original = transport._DeadlineConnection.connect

            def connect(connection):
                original(connection)
                opened.append(connection.sock.sock)

            with patch.object(transport._DeadlineConnection, "connect", connect):
                started = time.monotonic()
                with self.assertRaisesRegex(Failed, "transport_deadline_exceeded"):
                    Client(server.config, synthetic=True).request("GET", "/")
                elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.07)
            self.assertLess(elapsed, 0.3)
            self.assertTrue(opened)
            self.assertTrue(all(sock.fileno() == -1 for sock in opened))
            self.evidence_facts = {
                "request_timeout_seconds": 0.1,
                "elapsed_seconds": round(elapsed, 6),
                "request_attempts": server.connections,
                "closed_client_connections": len(opened),
            }

    def test_real_slow_response_header_total_deadline(self):
        self.assert_drip_deadline("slow_header")

    def test_real_slow_response_body_total_deadline(self):
        self.assert_drip_deadline("slow_body")

    def test_real_tls_slow_body_total_deadline(self):
        self.assert_drip_deadline("slow_body", tls=True)

    def test_real_tls_handshake_is_in_request_deadline(self):
        with SlowWire(self.directory, "stall_tls", tls=True) as server:
            started = time.monotonic()
            with self.assertRaisesRegex(Failed, "transport_deadline_exceeded"):
                Client(server.config).request("GET", "/")
            self.assertLess(time.monotonic() - started, 0.3)
            self.evidence_facts = {
                "request_timeout_seconds": 0.1,
                "elapsed_seconds": round(time.monotonic() - started, 6),
            }

    def test_parent_deadline_is_not_refilled_by_second_request(self):
        with SlowWire(self.directory, "delay_body") as server:
            client = Client({**server.config, "timeout_seconds": 1}, True)
            started = time.monotonic()
            with deadline_scope(started + 0.13):
                self.assertEqual(client.request("GET", "/")[0], 200)
                with self.assertRaisesRegex(Failed, "transport_deadline_exceeded"):
                    client.request("GET", "/")
            self.assertLess(time.monotonic() - started, 0.3)
            self.evidence_facts = {
                "parent_budget_seconds": 0.13,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "request_attempts": server.connections,
            }

    def test_suite_case_budget_covers_multiple_requests(self):
        with SlowWire(self.directory, "delay_body") as server:
            suite = Suite(
                {"runtime_kind": "synthetic", "case_timeout_seconds": 0.13},
                binding(),
                Report("local", binding(), "synthetic"),
            )
            client = Client({**server.config, "timeout_seconds": 1}, True)

            def budget_probe():
                client.request("GET", "/")
                client.request("GET", "/")
                return {}

            suite.budget_probe = budget_probe
            with patch("acceptance.suite.CASES", ("budget_probe",)):
                suite.execute()
            row = suite.report.data["results"][0]
            self.assertEqual(row["code"], "transport_deadline_exceeded")
            self.assertLess(row["duration_seconds"], 0.3)
            self.evidence_facts = {
                "case_budget_seconds": 0.13,
                "elapsed_seconds": row["duration_seconds"],
            }

    def test_expired_fault_action_leaves_restore_time_inside_case_budget(self):
        from types import SimpleNamespace

        with SlowWire(self.directory, "slow_body") as server:
            suite = Suite(
                {"runtime_kind": "synthetic", "case_timeout_seconds": 0.2},
                binding(),
                Report("local", binding(), "synthetic"),
            )
            client = Client({**server.config, "timeout_seconds": 1}, True)
            calls, restore_remaining, restore_deadlines = [], [], []

            def control(operation, **arguments):
                calls.append(arguments["enabled"])
                if not arguments["enabled"]:
                    restore_deadlines.append(transport._DEADLINE.get())
                    restore_remaining.append(
                        transport._remaining(transport._DEADLINE.get())
                    )
                return {"restored": True}

            suite.adapter = SimpleNamespace(call=control)
            started = time.monotonic()
            with deadline_scope(started + 0.2):
                with self.assertRaisesRegex(Failed, "transport_deadline_exceeded"):
                    suite.with_fault("synthetic", lambda: client.request("GET", "/"))
            self.assertEqual(calls, [True, False])
            self.assertFalse(suite.stop_mutations)
            self.assertGreater(restore_remaining[0], 0)
            self.assertLessEqual(restore_deadlines[0], started + 0.2)
            self.assertLess(time.monotonic() - started, 0.3)
            self.evidence_facts = {
                "case_budget_seconds": 0.2,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "restore_remaining_seconds": round(restore_remaining[0], 6),
                "fault_restored": True,
            }

    def test_observation_does_not_restart_budget_for_each_service(self):
        with SlowWire(self.directory, "slow_body") as server:
            config = {
                "runtime_kind": "synthetic",
                "endpoints": {
                    r: dict(
                        server.config,
                        timeout_seconds=1,
                        diagnostics_token_env="DEP_D_BUDGET_TOKEN",
                    )
                    for r in ROLES
                },
            }
            report = Report("local", binding(), "synthetic")
            started = time.monotonic()
            original = Client.request
            parent_deadlines, waited = [], []

            def consume_first_budget(client, *args, **kwargs):
                parent = transport._DEADLINE.get()
                parent_deadlines.append(parent)
                try:
                    return original(client, *args, **kwargs)
                except Failed as error:
                    self.assertEqual(str(error), "transport_deadline_exceeded")
                    # Windows socket timeout can precede GetTickCount64's next tick.
                    # This fixture promises the first probe consumes the parent budget;
                    # synchronize to that SAME deadline, never give another probe more time.
                    if len(parent_deadlines) == 1:
                        before = time.monotonic()
                        while (remaining := parent - time.monotonic()) > 0:
                            time.sleep(remaining)
                        waited.append(time.monotonic() - before)
                    raise

            with (
                patch.dict(os.environ, {"DEP_D_BUDGET_TOKEN": "synthetic-only"}),
                patch.object(Client, "request", consume_first_budget),
            ):
                observe(
                    config,
                    report,
                    self.directory / "observation",
                    duration=0.13,
                    interval=1,
                )
            self.assertLess(time.monotonic() - started, 0.4)
            self.assertEqual(server.connections, 1)
            self.assertEqual(len(parent_deadlines), 4)
            self.assertEqual(len(set(parent_deadlines)), 1)
            self.assertEqual(report.data["results"][0]["facts"]["probe_failures"], 4)
            self.evidence_facts = {
                "observation_budget_seconds": 0.13,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "request_attempts": server.connections,
                "failed_probes": 4,
                "shared_parent_deadline": True,
                "fixture_deadline_sync_seconds": waited[0],
            }

    def test_http_body_limit_still_applies(self):
        with SlowWire(self.directory, "oversize") as server:
            with self.assertRaisesRegex(Failed, "response_budget_exceeded"):
                Client({**server.config, "timeout_seconds": 1}, True).request(
                    "GET", "/"
                )

    def command(self, source):
        return Adapter(
            {
                "command": [sys.executable, "-B", "-c", source],
                "cwd": str(self.directory),
            },
            True,
        )

    def track_processes(self):
        processes, read_sizes = [], []
        original_popen, original_read = subprocess.Popen, os.read
        stdout_fds = set()

        def popen(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            processes.append(child)
            stdout_fds.add(child.stdout.fileno())
            return child

        def read(fd, size):
            data = original_read(fd, size)
            if fd in stdout_fds:
                read_sizes.append(len(data))
            return data

        self.enterContext(patch.object(transport.subprocess, "Popen", popen))
        self.enterContext(patch.object(transport.os, "read", read))
        return processes, read_sizes

    def assert_reaped(self, children):
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdin.closed)
        self.assertTrue(children[0].stdout.closed)
        self.evidence_facts = {
            **getattr(self, "evidence_facts", {}),
            "child_invocations": len(children),
            "child_reaped": True,
            "stdin_closed": True,
            "stdout_closed": True,
        }

    def test_command_normal_json_and_redacted_stderr(self):
        children, reads = self.track_processes()
        adapter = self.command(
            "import sys,json; r=json.load(sys.stdin);sys.stderr.write('secret-canary');print(json.dumps({'adapter_version':'dep-d/1','operation':r['operation']}))"
        )
        self.assertEqual(adapter.call("normal")["operation"], "normal")
        self.assertLess(sum(reads), transport.OUTPUT_LIMIT)
        self.assert_reaped(children)

    def test_command_two_megabytes_stops_reading_at_limit_plus_one(self):
        children, reads = self.track_processes()
        threads = set(threading.enumerate())
        started = time.monotonic()
        with self.assertRaisesRegex(Failed, "adapter_output_budget_exceeded"):
            self.command("import os; os.write(1,b'x'*(2*1024*1024))").call("oversize")
        self.assertEqual(sum(reads), transport.OUTPUT_LIMIT + 1)
        self.assertLess(time.monotonic() - started, 2)
        self.evidence_facts = {
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout_captured_bytes": sum(reads),
            "stdout_limit_bytes": transport.OUTPUT_LIMIT,
            "overflow_sentinel_bytes": 1,
        }
        self.assert_reaped(children)
        self.assertEqual(set(threading.enumerate()), threads)
        self.evidence_facts["reader_threads_left"] = 0

    def test_command_continuous_output_is_stopped_and_reaped(self):
        children, reads = self.track_processes()
        started = time.monotonic()
        with self.assertRaisesRegex(Failed, "adapter_output_budget_exceeded"):
            self.command("import os\nwhile True: os.write(1,b'x'*8192)").call("flood")
        self.assertEqual(sum(reads), transport.OUTPUT_LIMIT + 1)
        self.assertLess(time.monotonic() - started, 2)
        self.evidence_facts = {
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout_captured_bytes": sum(reads),
            "stdout_limit_bytes": transport.OUTPUT_LIMIT,
            "overflow_sentinel_bytes": 1,
        }
        self.assert_reaped(children)

    def test_command_no_output_and_stdin_backpressure_have_total_deadline(self):
        children, _ = self.track_processes()
        started = time.monotonic()
        with deadline_scope(started + 0.15):
            with self.assertRaisesRegex(Failed, "adapter_timeout"):
                self.command("import time; time.sleep(10)").call(
                    "silent", padding="x" * (512 * 1024)
                )
        self.assertLess(time.monotonic() - started, 0.5)
        self.evidence_facts = {
            "parent_budget_seconds": 0.15,
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
        self.assert_reaped(children)

    def test_command_cancel_reaps_child_without_reader_threads(self):
        children, _ = self.track_processes()
        threads = set(threading.enumerate())
        started = time.monotonic()
        cancel = threading.Event()
        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        try:
            with self.assertRaisesRegex(Failed, "adapter_cancelled"):
                self.command("import time; time.sleep(10)").call(
                    "cancel", cancel_event=cancel
                )
        finally:
            timer.cancel()
            timer.join(1)
        self.assert_reaped(children)
        self.assertEqual(set(threading.enumerate()), threads)
        self.evidence_facts.update(
            elapsed_seconds=round(time.monotonic() - started, 6), reader_threads_left=0
        )

    def test_keyboard_interrupt_closes_command_pipes_and_reaps(self):
        children, _ = self.track_processes()
        with patch.object(transport.time, "sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.command("import time; time.sleep(10)").call("interrupt")
        self.assert_reaped(children)


class InputTests(Base):
    def test_no_nas_no_dns_no_https_downgrade(self):
        for url in (
            "http://127.0.0.1:8765",
            "https://192.168.31.210:77",
            "https://localhost:443",
            "https://127.0.0.1:443@192.168.31.210:77",
            "file:///tmp/a",
            "https://127.0.0.1:443/a",
            "https://127.0.0.1:443?token=x",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                endpoint(url)
        self.assertEqual(endpoint("https://[::1]:8443"), "https://[::1]:8443")

    def test_paths_reject_escape_and_links(self):
        for name in ("../a", "/a", "C:/a", "a\\b", "a/../../b", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                inside(self.directory, name)

    def test_duplicate_json_and_nonfinite_rejected(self):
        file = self.directory / "a.json"
        for content in ('{"x":1,"x":2}', '{"x": NaN}'):
            file.write_text(content)
            with self.assertRaises(ValueError):
                read_json(file)

    def test_config_cannot_claim_paid_or_container_fixture(self):
        base = {
            "input_version": "dep-d/1",
            "mode": "local",
            "runtime_kind": "synthetic",
            "model_kind": "recorded",
            "scope": "synthetic_isolated",
        }
        for override in (
            {"model_kind": "paid"},
            {"mode": "container"},
            {"scope": "production"},
            {"skip": {"typo": "x"}},
        ):
            with self.assertRaises(ValueError):
                validate_config({**base, **override})

    def contract_input(self):
        contract = self.directory / "contracts" / "diagnostics" / "v1"
        contract.mkdir(parents=True)
        schema = b'{"test":"raw CRLF fixture"}\r\n'
        (contract / "event.schema.json").write_bytes(schema)
        manifest = canonical({"files": {"event.schema.json": digest(schema)}})
        (contract / "manifest.json").write_bytes(manifest)
        document = {
            "schema_version": "1.0.0",
            "release_id": "test",
            "status": "candidate",
            "products": {
                r: {
                    "source": {"repo": r, "commit": str(i + 1) * 40},
                    "image": {"reference": "test:fixed", "digest": None},
                }
                for i, r in enumerate(ROLES)
            },
            "contracts": [
                {
                    "path": "contracts/diagnostics/v1",
                    "manifest_sha256": digest(manifest),
                    "files": [{"path": "event.schema.json", "sha256": digest(schema)}],
                }
            ],
        }
        path = self.directory / "manifest.json"
        path.write_bytes(canonical(document))
        return path, contract, document

    def test_depa_mapping_and_raw_hash_tamper(self):
        path, contract, _ = self.contract_input()
        mapped = load_binding(
            path, ROOT / "depa-mapping.json", self.directory / "contracts"
        )
        self.assertIsNone(mapped["products"]["platform"]["digest"])
        self.assertEqual(mapped["release_status"], "candidate")
        (contract / "event.schema.json").write_bytes(
            (contract / "event.schema.json").read_bytes().replace(b"\r\n", b"\n")
        )
        with self.assertRaisesRegex(ValueError, "contract_hash_mismatch"):
            load_binding(path, ROOT / "depa-mapping.json", self.directory / "contracts")

    def test_unbound_member_and_short_commit_fail(self):
        path, contract, document = self.contract_input()
        document["products"]["memory"]["source"]["commit"] = "abc123"
        path.write_bytes(canonical(document))
        with self.assertRaisesRegex(ValueError, "full_commit_required"):
            load_binding(path, ROOT / "depa-mapping.json", self.directory / "contracts")

    def test_git_snapshot_ignores_working_tree_and_refuses_reuse(self):
        repo = self.directory / "repo"
        repo.mkdir()

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL
            )

        git("init")
        (repo / "source.txt").write_text("committed")
        git("add", "source.txt")
        git(
            "-c",
            "user.name=DEP-D synthetic",
            "-c",
            "user.email=dep-d@example.invalid",
            "commit",
            "-m",
            "fixture",
        )
        commit = git("rev-parse", "HEAD").decode().strip()
        (repo / "source.txt").write_text("ACTIVE UNCOMMITTED BYTES")
        pin = binding()
        for role in ROLES:
            pin["products"][role]["commit"] = commit
        output = self.directory / "snapshot"
        export_sources(pin, dict.fromkeys(ROLES, str(repo)), output, self.directory)
        for role in ROLES:
            self.assertEqual((output / role / "source.txt").read_text(), "committed")
        verify_snapshot(output)
        (output / "platform/source.txt").write_text("mutated after export")
        with self.assertRaisesRegex(ValueError, "snapshot_files_changed"):
            verify_snapshot(output)
        with self.assertRaisesRegex(ValueError, "snapshot_target_must_be_new"):
            export_sources(pin, dict.fromkeys(ROLES, str(repo)), output, self.directory)


class LiveTests(Base):
    def test_config_loading_cannot_be_inferred_from_static_arguments(self):
        h = self.harness()
        suite, _ = self.suite(h)
        suite.success["runtime_binding"] = True
        original = h.control

        def incorrect(request):
            result = original(request)
            if request["operation"] == "configuration":
                result["loaded_config_sha256"] = dict(
                    result["loaded_config_sha256"], memory="f" * 64
                )
            return result

        h.control = incorrect
        with self.assertRaisesRegex(Failed, "loaded_configuration_mismatch"):
            suite.configuration_loading()
        h.config["expected_config_sha256"] = {}
        with self.assertRaisesRegex(Missing, "effective_configuration_inputs_missing"):
            suite.configuration_loading()

    def test_full_synthetic_tls_suite_never_claims_product_acceptance(self):
        h = self.harness(tls=True)
        suite, report = self.suite(h)
        suite.execute()
        document = report.save(self.directory / "report")
        failed = [
            r for r in document["results"] if r["status"] not in {"pass", "not_run"}
        ]
        self.assertEqual(failed, [])
        self.assertEqual(sum(x["status"] == "pass" for x in document["results"]), 17)
        self.assertEqual(document["runtime_kind"], "synthetic")
        self.assertEqual(document["verdict"], "incomplete")
        self.assertEqual(document["claims"]["real_model_quality"], "not_run")
        raw = (self.directory / "report" / "report.json").read_text()
        self.assertNotIn(h.password, raw)
        self.assertNotIn(h.token, raw)
        self.assertNotIn("Synthetic recorded reply", raw)
        self.assertTrue(verify_report(self.directory / "report" / "report.json"))

    def test_wrong_ca_is_rejected(self):
        h = self.harness(tls=True)
        from fixtures import certificates

        other = self.directory / "other"
        other.mkdir()
        ca, _ = certificates(other)
        config = {**h.config["endpoints"]["platform"], "ca_file": str(ca)}
        with self.assertRaisesRegex(Failed, "transport_or_tls_failure"):
            Client(config).request("GET", "/health/live")

    def test_loopback_connect_preserves_tls_server_name(self):
        h = self.harness(tls=True)
        config = dict(h.config["endpoints"]["platform"], connect_host="127.0.0.1")
        self.assertEqual(Client(config).request("GET", "/health/live")[0], 200)
        config["url"] = config["url"].replace("127.0.0.1", "wrong-name.invalid")
        with self.assertRaisesRegex(Failed, "transport_or_tls_failure"):
            Client(config).request("GET", "/health/live")

    def test_redirects_are_not_followed(self):
        h = self.harness()
        original = h.handle

        def redirect(role, path, body, headers):
            if path == "/redirect":
                return 302, {"redirect": "https://192.168.31.210:77"}, None
            return original(role, path, body, headers)

        h.handle = redirect
        self.assertEqual(
            Client(h.config["endpoints"]["platform"], True).request("GET", "/redirect")[
                0
            ],
            302,
        )

    def test_positive_queue_does_not_prove_final_memory(self):
        h = self.harness(mutation="queued_only")
        suite, report = self.suite(h)
        suite.execute()
        rows = {r["case"]: r for r in report.data["results"]}
        self.assertEqual(rows["memory_candidate"]["status"], "pass")
        self.assertEqual(
            rows["memory_finalized"]["code"], "candidate_is_not_final_memory"
        )

    def test_disabled_memory_still_blocks_growing_queue(self):
        h = self.harness(mutation="queue_growth")
        h.config["features"]["automatic_memory"] = False
        suite, report = self.suite(h)
        suite.execute()
        rows = {r["case"]: r for r in report.data["results"]}
        self.assertEqual(rows["memory_finalized"]["status"], "dependency_missing")
        self.assertEqual(
            rows["memory_backlog_boundary"]["code"], "unconsumed_memory_queue_growing"
        )

    def test_unknown_resend_is_detected(self):
        h = self.harness(mutation="resend_unknown")
        suite, report = self.suite(h)
        suite.execute()
        self.assertIn("unknown_resent", [r["code"] for r in report.data["results"]])

    def test_false_readiness_and_success_logs_detected(self):
        h = self.harness(mutation="false_success")
        suite, report = self.suite(h)
        suite.execute()
        self.assertEqual(
            next(
                r for r in report.data["results"] if r["case"] == "failure_truthfulness"
            )["status"],
            "fail",
        )
        h.mutation = "false_ready"
        with self.assertRaisesRegex(Failed, "fault_ready_false_success"):
            suite.abnormal_readiness()

    def test_cleanup_failure_stops_remaining_mutations(self):
        h = self.harness(mutation="cleanup_failure")
        suite, report = self.suite(h)
        suite.execute()
        rows = {r["case"]: r for r in report.data["results"]}
        self.assertEqual(rows["unknown_no_resend"]["code"], "fault_restore_failed")
        self.assertEqual(rows["source_revocation"]["status"], "not_run")

    def test_lost_fault_enable_response_still_restores(self):
        from types import SimpleNamespace

        report = Report("local", binding(), "synthetic")
        suite = Suite({"runtime_kind": "synthetic"}, binding(), report)
        calls = []

        def call(operation, **arguments):
            calls.append(arguments["enabled"])
            if arguments["enabled"]:
                raise Failed("transport_or_tls_failure")
            return {"restored": True}

        suite.adapter = SimpleNamespace(call=call)
        with self.assertRaisesRegex(Failed, "transport_or_tls_failure"):
            suite.with_fault("synthetic", lambda: self.fail("must not execute"))
        self.assertEqual(calls, [True, False])

    def test_plan_no_network_and_missing_adapter_not_success(self):
        h = self.harness()
        suite, report = self.suite(h)
        suite.execute(plan=True)
        self.assertEqual(h.counts["model_calls"], 0)
        self.assertTrue(all(r["status"] == "not_run" for r in report.data["results"]))
        h.config["adapter"] = None
        h.config["skip"] = {"health_and_auth": "test explicit skip"}
        suite, report = self.suite(h)
        suite.execute()
        rows = {r["case"]: r for r in report.data["results"]}
        self.assertEqual(rows["runtime_binding"]["status"], "dependency_missing")
        self.assertEqual(rows["health_and_auth"]["status"], "skipped")
        self.assertEqual(rows["dialogue_model_reply"]["status"], "dependency_missing")

    def test_short_observation_records_actual_duration_not_24h(self):
        h = self.harness()
        _, report = self.suite(h)
        observe(
            h.config,
            report,
            self.directory / "observation",
            duration=0.5,
            interval=0.25,
        )
        result = report.data["results"][0]
        self.assertEqual(result["status"], "not_run")
        self.assertLess(result["facts"]["elapsed_seconds"], 5)
        self.assertGreaterEqual(result["facts"]["samples"], 2)
        self.assertEqual(report.data["claims"]["observation_24h"], "not_run")


class LogAndReportTests(Base):
    def test_hash_encoding_vector(self):
        self.assertEqual(
            canonical({"z": 1, "a": "天枢"}), '{"a":"天枢","z":1}'.encode("utf-8")
        )
        with self.assertRaises(ValueError):
            canonical({"x": float("inf")})

    def test_static_catalog_union_never_executes_code(self):
        import ast

        self.assertEqual(
            literal_registry(
                ast.parse("ERROR_CODES=frozenset({'a'} | {'b'})"), "ERROR_CODES"
            ),
            ["a", "b"],
        )
        with self.assertRaises(ValueError):
            literal_registry(
                ast.parse("ERROR_CODES=__import__('os').getcwd()"), "ERROR_CODES"
            )

    def test_readiness_requires_real_product_keys_and_required_values(self):
        checks = dict.fromkeys(CHECKS["gateway"], "ok")
        checks.update(
            platform="not_verified", model="not_verified", native="not_configured"
        )
        body = {"service": "gateway", "status": "ready", "checks": checks}
        validate_ready("gateway", 200, body)
        checks["logging"] = "not_verified"
        with self.assertRaisesRegex(Failed, "readiness_false_success"):
            validate_ready("gateway", 200, body)
        with self.assertRaisesRegex(Failed, "readiness_check_vocabulary"):
            validate_ready("gateway", 200, {**body, "checks": {"logs": "ok"}})

    def event(self):
        return {
            "schema_version": "1.0.0",
            "timestamp": "2026-09-22T00:00:00Z",
            "service": "platform",
            "instance_id": str(uuid.uuid4()),
            "sequence": 1,
            "event_id": str(uuid.uuid4()),
            "level": "INFO",
            "event": "http.finished",
            "outcome": "failed",
            "correlation_id": "a" * 32,
            "duration_ms": 1,
            "error_code": "internal_error",
        }

    def test_closed_fields_static_catalog_and_duplicates(self):
        row = self.event()
        catalog = {
            "platform": {"events": ["http.finished"], "error_codes": ["internal_error"]}
        }
        line = json.dumps(row) + "\n"
        records, duplicates = validate_events([line, line], catalog)
        self.assertEqual((len(records), duplicates), (1, 1))
        for change in (
            {"message": "secret"},
            {"sequence": True},
            {"duration_ms": float("nan")},
            {"event": "user.controlled"},
            {"outcome": "accepted"},
            {"correlation_id": "user-account"},
        ):
            with self.subTest(change=change), self.assertRaises(Failed):
                validate_events([json.dumps({**row, **change}) + "\n"], catalog)
        with self.assertRaisesRegex(Failed, "changed_duplicate_event"):
            validate_events(
                [line, json.dumps({**row, "outcome": "succeeded"}) + "\n"], catalog
            )

    def test_wrong_causal_chain_and_fake_success_fail(self):
        row = self.event()
        catalog = {
            "platform": {"events": ["http.finished"], "error_codes": ["internal_error"]}
        }
        with self.assertRaisesRegex(Failed, "causal_event_missing"):
            causal(
                [json.dumps(row) + "\n"],
                catalog,
                "b" * 32,
                [["platform", "http.finished", "failed"]],
            )
        with self.assertRaisesRegex(Failed, "failure_logged_as_success"):
            causal(
                [json.dumps({**row, "outcome": "succeeded"}) + "\n"],
                catalog,
                "a" * 32,
                [],
                [["platform", "http.finished", "succeeded"]],
            )

    def test_report_tampering_detected(self):
        report = Report("local", binding(), "synthetic")
        report.add("test", "pass", "assertions_satisfied")
        report.save(self.directory)
        path = self.directory / "report.json"
        data = read_json(path)
        data["runtime_kind"] = "product"
        path.write_bytes(canonical(data))
        self.assertFalse(verify_report(path))


if __name__ == "__main__":
    unittest.main()
