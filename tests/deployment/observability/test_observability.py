import copy
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import Mock

from helpers import SNAPSHOT, deployment_fixture, emit, event
from alerts import AlertStore
from configure import confined, manifest_logs, prepare, verify_tls
from configs import vector, loki, alert_rules
from monitor import Ledger
from policy import Policy, canonical, InvalidEvent, segments
from query import LokiClient, QueryError
from reconcile import compare, read_logs
from snapshot import extract

RUNTIME = Path(__file__).parent / ".runtime"
CONTRACT = (
    Path(os.environ["DEP_B_CONTRACT"]) if "DEP_B_CONTRACT" in os.environ else None
)


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        RUNTIME.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.policy = Policy(SNAPSHOT)


class PolicyTests(WorkspaceTest):
    def test_published_contract_positive_and_negative_examples(self):
        if CONTRACT is None:
            self.skipTest("DEP_B_CONTRACT required")
        for value in json.loads((CONTRACT / "examples.json").read_bytes()):
            self.policy.validate(value)
        for value in json.loads((CONTRACT / "negative-examples.json").read_bytes()):
            with self.assertRaises(InvalidEvent):
                self.policy.validate(value)

    def test_all_registered_vocabulary(self):
        for service, spec in SNAPSHOT["products"].items():
            for registered in spec["events"]:
                value = event(service=service)
                value["event"] = registered
                self.policy.line(canonical(value) + b"\n")

    def test_secret_canaries_never_accepted(self):
        canary = "dep_b_secret_canary_do_not_log"
        invalid = [
            dict(event(), message=canary),
            dict(event(), event=canary),
            dict(event(), error_code=canary),
            dict(event(), correlation_id=canary),
            dict(event(), timestamp=canary),
            dict(event(), duration_ms=float("nan")),
            dict(event(), sequence=True),
        ]
        for value in invalid:
            with self.subTest(case=list(value)):
                with self.assertRaisesRegex(InvalidEvent, "^invalid_safe_event$"):
                    self.policy.validate(value)
        with self.assertRaises(InvalidEvent):
            self.policy.line(b'{"message":"' + canary.encode() + b'"}\n')

    def test_duplicate_keys_crlf_oversize_and_partial_refused(self):
        line = canonical(event())
        bad = [
            line[:-1] + b',"event":"runtime.started"}\n',
            line + b"\r\n",
            line,
            b"x" * 4096 + b"\n",
            b"\xff\n",
        ]
        for raw in bad:
            with self.assertRaises(InvalidEvent):
                self.policy.line(raw)

    def test_contract_original_bytes(self):
        if CONTRACT is None:
            self.skipTest(
                "DEP_B_CONTRACT is required for authoritative byte verification"
            )
        self.policy.verify_contract(CONTRACT)
        for name in SNAPSHOT["contract"]["files"]:
            (self.root / name).write_bytes((CONTRACT / name).read_bytes())
        target = self.root / "README.md"
        target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
        with self.assertRaisesRegex(ValueError, "contract_bytes_mismatch"):
            self.policy.verify_contract(self.root)

    def test_static_extraction_does_not_execute_product(self):
        source = 'raise RuntimeError("should not execute")\nEVENTS=frozenset({"a"}|{"b"})\nERROR_CODES={"x"}\n'
        self.assertEqual(
            extract(source, "memory"), {"events": ["a", "b"], "error_codes": ["x"]}
        )


class ReconciliationTests(WorkspaceTest):
    def test_full_numbered_rotation_restart_duplicates(self):
        produced = [event(n) for n in range(1, 101)]
        produced += [
            event(n, instance="00000000-0000-4000-8000-000000000003")
            for n in range(1, 11)
        ]
        emit(self.root / "platform.jsonl", produced[:60])
        emit(self.root / "platform.jsonl.1", produced[60:])
        landed, invalid, partial = read_logs({"platform": self.root}, self.policy)
        result = compare(produced, landed, landed + [produced[7]], invalid, partial)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["retrieval_duplicates"], 1)
        self.assertFalse(result["application_reclamation_authorized"])

    def test_loss_tail_sequence_identity_and_mutation_detected(self):
        produced = [event(n) for n in range(1, 5)]
        result = compare(produced, produced[:-1], produced[:-1])
        self.assertEqual(result["missing_at_source"], 1)
        result = compare(produced, produced, produced[1:])
        self.assertGreater(result["sequence_gaps"], 0)
        self.assertEqual(result["missing_in_loki"], 1)
        mutated = copy.deepcopy(produced)
        mutated[0]["outcome"] = "failed"
        self.assertEqual(compare(produced, produced, mutated)["content_mismatches"], 1)
        collision = event(1)
        self.assertGreater(
            compare(produced, produced + [collision], produced)["identity_conflicts"], 0
        )

    def test_partial_and_invalid_stay_unverified_without_secret_echo(self):
        (self.root / "x.jsonl").write_bytes(
            canonical(event()) + b"\nsecret_canary\n" + canonical(event(2))
        )
        records, invalid, partial = read_logs({"platform": self.root}, self.policy)
        self.assertEqual((len(records), invalid, partial), (1, 1, 1))
        self.assertEqual(
            compare(records, records, records, invalid, partial)["status"], "failed"
        )

    def test_saturated_query_bucket_refuses_false_completeness(self):
        client = object.__new__(LokiClient)
        client.request = lambda *a: (
            200,
            json.dumps(
                {
                    "status": "success",
                    "data": {
                        "resultType": "streams",
                        "result": [{"values": [["1", "{}"], ["1", "{}"]]}],
                    },
                }
            ).encode(),
            "application/json",
        )
        with self.assertRaisesRegex(QueryError, "timestamp_bucket_saturated"):
            client.range("x", 1, 1, limit=2)

    def test_query_splits_at_capacity_without_losing_equal_timestamps(self):
        from urllib.parse import parse_qs, urlsplit

        client = object.__new__(LokiClient)
        data = [(1, "a"), (1, "b"), (2, "c"), (3, "d"), (5, "e")]

        def request(path):
            args = parse_qs(urlsplit(path).query)
            values = [
                [str(t), line]
                for t, line in data
                if int(args["start"][0]) <= t <= int(args["end"][0])
            ][: int(args["limit"][0])]
            return (
                200,
                json.dumps(
                    {
                        "status": "success",
                        "data": {
                            "resultType": "streams",
                            "result": [{"values": values}],
                        },
                    }
                ).encode(),
                "application/json",
            )

        client.request = request
        self.assertEqual(client.range("x", 1, 5, limit=3), data)


class LedgerTests(WorkspaceTest):
    def test_regular_rename_rotation_does_not_raise_mutation_alarm(self):
        path = self.root / "active.jsonl"
        emit(path, [event(1)])
        ledger = Ledger(self.root / "ledger.sqlite", self.policy)
        self.addCleanup(ledger.close)
        ledger.scan({"platform": self.root})
        path.rename(self.root / "active.jsonl.1")
        emit(path, [event(2)])
        ledger.scan({"platform": self.root})
        metrics = ledger.metrics()
        self.assertEqual(metrics["landed_events"], 2)
        self.assertEqual(metrics.get("source_changed_total", 0), 0)
        self.assertEqual(metrics["source_replacements_total"], 1)

    def test_previously_verified_central_loss_becomes_pending(self):
        import time

        value = event()
        emit(self.root / "x.jsonl", [value])
        ledger = Ledger(self.root / "ledger.sqlite", self.policy)
        self.addCleanup(ledger.close)
        ledger.scan({"platform": self.root})
        client = Mock()
        client.range.return_value = [(1, canonical(value).decode())]
        ledger.reconcile(client)
        self.assertEqual(ledger.metrics()["pending_events"], 0)
        ledger.db.execute("UPDATE events SET last_checked=?", (time.time() - 3601,))
        ledger.db.commit()
        client.range.return_value = []
        ledger.reconcile(client)
        self.assertIn(value["event_id"], client.range.call_args[0][0])
        self.assertEqual(ledger.metrics()["pending_events"], 1)

    def test_missing_old_event_does_not_starve_later_events(self):
        records = [event(1), event(2)]
        emit(self.root / "x.jsonl", records)
        ledger = Ledger(self.root / "ledger.sqlite", self.policy)
        self.addCleanup(ledger.close)
        ledger.scan({"platform": self.root})
        client = Mock()
        client.range.return_value = []
        ledger.reconcile(client, batch=1)
        client.range.return_value = [(1, canonical(records[1]).decode())]
        ledger.reconcile(client, batch=1)
        self.assertIn(records[1]["event_id"], client.range.call_args[0][0])
        self.assertEqual(ledger.metrics()["pending_events"], 1)

    def test_durable_offsets_restart_replay_and_no_source_deletion(self):
        roots = {"platform": self.root}
        produced = [event(n) for n in range(1, 11)]
        emit(self.root / "one.jsonl", produced[:5])
        ledger_path = self.root / "ledger.sqlite"
        ledger = Ledger(ledger_path, self.policy)
        ledger.scan(roots)
        ledger.close()
        emit(self.root / "one.jsonl.1", produced[5:])
        ledger = Ledger(ledger_path, self.policy)
        self.addCleanup(ledger.close)
        ledger.scan(roots)
        self.assertEqual(ledger.metrics()["landed_events"], 10)
        emit(self.root / "replay.jsonl", [produced[0]])
        ledger.scan(roots)
        self.assertEqual(ledger.metrics()["source_duplicates_total"], 1)
        self.assertEqual(len(list(segments(self.root))), 3)
        failed = Mock()
        failed.range.side_effect = QueryError("offline")
        with self.assertRaises(QueryError):
            ledger.reconcile(failed)
        self.assertEqual(ledger.metrics()["pending_events"], 10)
        restored = Mock()
        restored.range.return_value = [
            (n, canonical(e).decode()) for n, e in enumerate(produced)
        ]
        ledger.reconcile(restored)
        self.assertEqual(ledger.metrics()["pending_events"], 0)

    def test_invalid_input_and_truncation_are_counted_without_payload(self):
        source = self.root / "a.jsonl"
        emit(source, [event(1), event(2)])
        ledger = Ledger(self.root / "ledger.sqlite", self.policy)
        self.addCleanup(ledger.close)
        ledger.scan({"platform": self.root})
        source.write_bytes(b"SECRET_CANARY\n")
        ledger.scan({"platform": self.root})
        self.assertGreater(ledger.metrics()["source_changed_total"], 0)
        self.assertGreater(ledger.metrics()["invalid_source_lines_total"], 0)
        self.assertNotIn("SECRET_CANARY", "\n".join(ledger.db.iterdump()))

    def test_capacity_rejection_and_alert_transitions_persist(self):
        emit(self.root / "x.jsonl", [event(1), event(2)])
        ledger = Ledger(self.root / "ledger.sqlite", self.policy, max_events=1)
        self.addCleanup(ledger.close)
        ledger.scan({"platform": self.root})
        self.assertGreater(ledger.metrics()["ledger_capacity_rejected_total"], 0)
        store = AlertStore(self.root / "alerts.sqlite")
        values = store.update(
            {
                "monitor_success": 0,
                "source_platform_ratio": 0.9,
                "storage_loki_ratio": 0.95,
            }
        )
        self.assertEqual(values["alert_application_capacity"], 1)
        self.assertEqual(values["alert_disk_capacity"], 1)
        store.close()
        store = AlertStore(self.root / "alerts.sqlite")
        self.addCleanup(store.close)
        self.assertEqual(
            store.db.execute(
                "SELECT active FROM current WHERE name='disk_capacity'"
            ).fetchone()[0],
            1,
        )
        values = store.update({"monitor_success": 1})
        self.assertEqual(values["alert_disk_capacity"], 0)


class ConfigurationTests(WorkspaceTest):
    def test_confined_paths(self):
        for path in ("../escape", "a/../b", "/abs", "C:/abs", "a\\b", "./b", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                confined(self.root, path, False)

    def test_generated_stack_invariants(self):
        cfg = vector(SNAPSHOT)
        self.assertEqual(cfg["sinks"]["loki"]["buffer"]["when_full"], "block")
        self.assertTrue(cfg["sinks"]["loki"]["acknowledgements"]["enabled"])
        self.assertEqual(
            cfg["sinks"]["loki"]["encoding"]["only_fields"], sorted(event())
        )
        self.assertNotIn("remove_after_secs", json.dumps(cfg))
        self.assertEqual(loki()["limits_config"]["retention_period"], "720h")
        self.assertTrue(loki()["compactor"]["retention_enabled"])
        self.assertTrue(
            all(
                r["noDataState"] == "Alerting"
                for r in alert_rules()["groups"][0]["rules"]
            )
        )

    def test_bundle_binding_tls_missing_fields_wrong_commit_and_no_overwrite(self):
        if CONTRACT is None:
            self.skipTest("DEP_B_CONTRACT required")
        manifest, settings = deployment_fixture(self.root, CONTRACT)
        bundle = prepare(
            self.root, settings, manifest, "bundle", SNAPSHOT, candidate=True
        )
        generated = json.loads((bundle / "compose.yaml").read_bytes())
        self.assertEqual(len(generated["services"]), 5)
        self.assertNotIn("docker.sock", json.dumps(generated))
        for service in generated["services"].values():
            self.assertNotEqual(service["user"], "0")
            for mount in service["volumes"]:
                if mount["target"].startswith("/sources/"):
                    self.assertTrue(mount["read_only"])
        with self.assertRaisesRegex(ValueError, "new_bundle_directory_required"):
            prepare(self.root, settings, manifest, "bundle", SNAPSHOT, candidate=True)
        manifest["products"]["platform"]["source"]["commit"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "vocabulary_source_commit_mismatch"):
            manifest_logs(manifest, SNAPSHOT, self.root)
        with self.assertRaises(ssl.SSLError):
            verify_tls(
                self.root / "tls/ca.pem",
                self.root / "tls/guard.pem",
                self.root / "tls/guard.key",
                "wrong.invalid",
            )

    def test_production_does_not_accept_unverified_tags(self):
        if CONTRACT is None:
            self.skipTest("DEP_B_CONTRACT required")
        manifest, settings = deployment_fixture(self.root, CONTRACT)
        with self.assertRaisesRegex(ValueError, "verified_image_digests_required"):
            prepare(self.root, settings, manifest, "bundle", SNAPSHOT)
        self.assertFalse((self.root / "bundle").exists())


if __name__ == "__main__":
    unittest.main()
