"""Behavior tests; all writable fixtures are confined to this task's ignored directory."""

import json
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

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
from acceptance.transport import Client, Failed, Missing, endpoint
from fixtures import Harness

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
            duration=0.1,
            interval=0.05,
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
