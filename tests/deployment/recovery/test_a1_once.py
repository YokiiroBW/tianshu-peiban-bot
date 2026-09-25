"""Offline rehearsal of the real A1 coordinator and its parameter generation."""

import copy
import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import subprocess
import sys

from ops.recovery import a1_once


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)
        self.scope = home / "scope-a1-r2i"
        self.scope.mkdir()
        self.code = home / "tooling"
        driver = self.code / "ops/recovery/a1_once.py"
        driver.parent.mkdir(parents=True)
        driver.write_text("fixture pinned coordinator\n")
        (self.code / "deploy").mkdir()
        self.python = home / "python"
        self.docker = home / "docker"
        self.python.touch()
        self.docker.touch()
        self.uuid = str(uuid.uuid4())
        put(self.scope / ".recovery-scope.json", {"scope_id": self.uuid})
        self.source = self.scope / "deployments/source"
        self.inputs = self.scope / "drill-inputs/clone-inputs"
        self.private = self.scope / "inputs"
        self.source.mkdir(parents=True)
        self.inputs.mkdir(parents=True)
        self.private.mkdir(parents=True)
        allocation = Path(__file__).parent / "fixtures/nas-a1-network-allocation-r2i.json"
        self.allocation_file = home / "preparations/nas-a1-network-allocation-r2i.json"
        self.allocation_file.parent.mkdir()
        shutil.copyfile(allocation, self.allocation_file)
        allocated = json.loads(allocation.read_text(encoding="utf-8"))
        self.nets = ([allocated["source"][key] for key in a1_once.SOURCE_PURPOSES] +
                     [allocated["clone"][key] for key in a1_once.CLONE_PURPOSES])
        self.ports = ([allocated["source_loopback_ports"][key]
                       for key in a1_once.PORT_PURPOSES] +
                      [allocated["clone_loopback_ports"][key]
                       for key in a1_once.PORT_PURPOSES])
        def compose(name, nets, ports):
            return {"name": name, "networks": {f"net{n}": {
                "ipam": {"config": [{"subnet": subnet}]}}
                for n, subnet in enumerate(nets)},
                "services": {f"service{n}": {"ports": [f"127.0.0.1:{port}:8000"]}
                             for n, port in enumerate(ports)}}
        source_docs = [compose("source-core", self.nets[:3], self.ports[:4]),
                       compose("source-obs", self.nets[3:5], self.ports[4:6])]
        clone_docs = [compose("tianshu-qa-a1-fixture", self.nets[5:10], self.ports[6:10]),
                       compose("tianshu-qa-a1-fixture-obs", self.nets[10:12], self.ports[10:12])]
        put(self.inputs / "compose.json", clone_docs[0])
        put(self.inputs / "observability/compose.yaml", clone_docs[1])
        put(self.source / "reports/runtime-identity.json", {
            "projects": {"core": {"compose_json": source_docs[0]},
                         "obs": {"compose_json": source_docs[1]}},
            "services": {}})
        put(self.source / "release-manifest.json", {"products": {}})
        put(self.source / ".recovery-registration.json", {
            "authority_id": self.uuid, "scope_id": self.uuid})
        put(self.source / "deployment.json", {"project_name": "tianshu-qa-a1-source-fixture"})
        put(self.private / "model-publication-template.json", {
            "config_version": 1, "providers": [{"provider_id": "provider-synthetic",
            "base_url": "https://gateway.internal:9443/v1"}]})
        gateway_env = self.inputs / "private/gateway.env"
        gateway_env.parent.mkdir(parents=True)
        gateway_env.write_text("TS_GATEWAY_ORIGIN=origin:clone-config-pending\n")
        assertions = []
        for name in a1_once.ASSERTIONS:
            row = {"id": name}
            if name in {"forgotten", "source_revoked", "model_revoked", "unknown_no_resend"}:
                row["request_json"] = {"query": {"origin": {"assertion_ref": "pending"}}}
            assertions.append(row)
        fixture_files = {}
        for i in range(100):
            file = self.inputs / f"fixture/{i}"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(f"fixture {i}\n")
            fixture_files[f"fixture/{i}"] = a1_once.file_hash(file)
        fixture_files["compose.json"] = a1_once.file_hash(self.inputs / "compose.json")
        fixture_files["observability/compose.yaml"] = a1_once.file_hash(
            self.inputs / "observability/compose.yaml")
        fixture_files["private/gateway.env"] = a1_once.file_hash(gateway_env)
        index = {"assertions": assertions, "files": fixture_files}
        put(self.inputs / "inputs.json", index)
        self.config = {
            "schema_version": "a1-once/1", "scope_root": str(self.scope),
            "scope_id": self.uuid, "code_root": str(self.code),
            "allocation_file": str(self.allocation_file),
            "allocation_sha256": a1_once.ALLOCATION_SHA256,
            "execution_id": allocated["execution_id"],
            "code_tree_sha256": a1_once._code_tree(self.code),
            "code_lock_sha256": "",
            "python": str(self.python), "docker": str(self.docker),
            "source_name": "source", "restored_name": "restored",
            "clone_name": "clone", "inputs_name": "clone-inputs",
            "backup_name": "backup", "permit_name": "permit", "receipt_name": "receipts",
            "registration_sha256": a1_once.file_hash(self.source / ".recovery-registration.json"),
            "source_manifest_sha256": a1_once.file_hash(self.source / "release-manifest.json"),
            "initial_inputs_sha256": a1_once.file_hash(self.inputs / "inputs.json"),
            "source_runtime_sha256": a1_once.file_hash(
                self.source / "reports/runtime-identity.json"),
            "source_deployment_sha256": a1_once.file_hash(self.source / "deployment.json"),
            "model_template_sha256": a1_once.file_hash(
                self.private / "model-publication-template.json"),
            "projects": {"core": "tianshu-qa-a1-fixture",
                         "observability": "tianshu-qa-a1-fixture-obs"},
            "networks": {"source": self.nets[:5], "clone": self.nets[5:]},
            "ports": {"source": self.ports[:6], "clone": self.ports[6:]},
        }
        lock = self.private / "a1-code-files.json"
        put(lock, {"schema_version": "a1-code-lock/1",
                   "tree_sha256": self.config["code_tree_sha256"],
                   "files": dict(a1_once._code_files(self.code))})
        self.config["code_lock_sha256"] = a1_once.file_hash(lock)
        self.path = self.private / "a1-once.json"
        put(self.path, self.config)

    def _mark_safe_seal(self, argv):
        names = {"publish": self.private / "clone-publish-receipt.json",
                 "config": self.inputs / "private/a1-config-origin-issue.json",
                 "actor": self.inputs / "private/a1-actor-origin-issue.json"}
        put(names["publish"], {"action": "publish", "receipt": {"config_version": 5}})
        for name in ("config", "actor"):
            put(names[name], {"action": "issue", "receipt": {
                "assertion_ref": "origin:fixture-" + name,
                "expires_at": "2099-01-01T00:00:00Z"}})
        put(self.private / "a1-seal-actions-complete.json", {
            "schema_version": "a1-seal-actions-complete/1",
            "scope_id": self.uuid,
            "attempt_id": argv[argv.index("--attempt-id") + 1],
            "receipts": {key: a1_once.file_hash(path)
                         for key, path in names.items()}})

    def _runner(self, *, fail=None, remaining=181, mutate=True):
        seen = []
        def run(argv, timeout):
            stage = next(name for name in a1_once.STAGES if
                         (name in argv or (name == "drill-plan" and
                          "drill-clone" in argv and "--execute" not in argv) or
                          (name == "drill-execute" and "drill-clone" in argv and
                           "--execute" in argv)))
            seen.append(stage)
            if stage == fail:
                return 124 if fail == "drill-execute" else 2, {
                    "status": "stop_unconfirmed" if fail == "drill-execute" else "rejected"}
            if stage == "seal":
                self._mark_safe_seal(argv)
                if mutate:
                    index = json.loads((self.inputs / "inputs.json").read_text())
                    index["a1_origin_admission"] = {"minimum_remaining_seconds": 180}
                    put(self.inputs / "inputs.json", index)
                checksum = a1_once.file_hash(self.inputs / "inputs.json")
                put(self.private / "clone-inputs-finalized.json", {"inputs_sha256": checksum})
                return 0, {"status": "sealed", "inputs_sha256": checksum}
            if stage == "linux-rehearse":
                return 0, {"status": "disabled_restore_complete",
                           "runtime_owners_stopped": 9,
                           "verification_sha256": "1" * 64}
            if stage == "host-preflight":
                return 0, {"status": "host_preflight_passed", "clone_ports_checked": 6}
            if stage == "permit":
                self.private.joinpath("permit").write_bytes(b"fixture permit")
                return 0, {"status": "permit_issued",
                           "permit_sha256": a1_once.file_hash(self.private / "permit"),
                           "admission": {"remaining_at_check_seconds": remaining}}
            if stage == "drill-plan":
                return 0, {"status": "planned", "mode": "plan",
                           "actual_owners_and_facts": "not_checked"}
            return 0, {"status": "drill_passed"}
        return seen, run

    def test_config_and_command_binding(self):
        self.assertEqual(a1_once.load_config(self.path), self.config)
        argv = a1_once.command(self.config, "drill-execute", permit_sha="2" * 64)
        self.assertEqual(argv[0], str(self.python))
        self.assertEqual(argv.count("--execute"), 1)
        self.assertIn(str(self.docker), argv)
        self.assertNotIn("r2h", " ".join(argv))

    def test_exact_r2i_allocation_is_required(self):
        self.assertEqual(a1_once.file_hash(self.allocation_file),
                         a1_once.ALLOCATION_SHA256)
        for field, value in (("allocation_sha256", "0" * 64),
                             ("execution_id", str(uuid.uuid4())),
                             ("allocation_file", str(self.path))):
            with self.subTest(field=field):
                bad = copy.deepcopy(self.config)
                bad[field] = value
                put(self.path, bad)
                with self.assertRaises(Exception):
                    a1_once.load_config(self.path)
        for group, index, subnet in (("source", 0, "10.205.48.0/28"),
                                     ("clone", 6, "10.205.49.192/28")):
            with self.subTest(subnet=subnet):
                bad = copy.deepcopy(self.config)
                bad["networks"][group][index] = subnet
                put(self.path, bad)
                with self.assertRaises(Exception):
                    a1_once.load_config(self.path)
        bad = copy.deepcopy(self.config)
        bad["ports"]["clone"][0] = 29921
        put(self.path, bad)
        with self.assertRaises(Exception):
            a1_once.load_config(self.path)
        put(self.path, self.config)
        self.allocation_file.write_bytes(self.allocation_file.read_bytes() + b" ")
        with self.assertRaises(Exception):
            a1_once.load_config(self.path)

    def test_public_cli_requires_explicit_execute(self):
        result = subprocess.run([sys.executable, "-B", "-m", "ops.recovery.a1_once",
                                 "--config", str(self.path)],
                                cwd=Path(__file__).resolve().parents[3],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["reason"], "a1_execute_flag_required")
        self.assertFalse((self.private / "receipts").exists())

    def test_public_execute_rejects_wrong_loaded_code_before_receipt(self):
        result = subprocess.run([sys.executable, "-B", "-m", "ops.recovery.a1_once",
            "--config", str(self.path), "--execute"],
            cwd=Path(__file__).resolve().parents[3], capture_output=True,
            text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "a1_entry_code_root_mismatch")
        self.assertFalse((self.private / "receipts").exists())

    def test_success_one_execute(self):
        seen, runner = self._runner()
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["execute_calls"], 1)
        self.assertEqual(seen, list(a1_once.STAGES))
        receipts = list((self.private / "receipts").glob("*.json"))
        self.assertEqual(len(receipts), 7)
        self.assertTrue((self.private / "receipts/attempt.json").is_file())

    def test_raw_receipt_is_private_and_hashed(self):
        seen, base = self._runner()
        def runner(argv, timeout):
            code, result = base(argv, timeout)
            if "seal" in argv:
                result = a1_once._with_streams(result, b"private fixture\n", b"warning\n")
            return code, result
        a1_once.drive(self.config, runner=runner)
        raw = self.private / "receipts/01-seal.stdout"
        receipt = json.loads((self.private / "receipts/01-seal.json").read_text())
        self.assertEqual(raw.read_bytes(), b"private fixture\n")
        self.assertEqual(receipt["stream_sha256"]["stdout"], a1_once.file_hash(raw))
        self.assertEqual(seen, list(a1_once.STAGES))

    def test_config_mismatch_and_nonempty_target(self):
        bad = copy.deepcopy(self.config)
        bad["networks"]["clone"][0] = self.nets[0]
        put(self.path, bad)
        with self.assertRaises((ValueError, Exception)):
            a1_once.load_config(self.path)
        put(self.path, self.config)
        (self.scope / "deployments/restored").mkdir()
        with self.assertRaises(Exception):
            a1_once.load_config(self.path)

    def test_old_scope_duplicate_permit_and_final_index_are_rejected(self):
        (self.private / "permit").write_text("stale permit")
        with self.assertRaises(Exception):
            a1_once.load_config(self.path)
        (self.private / "permit").unlink()
        index = json.loads((self.inputs / "inputs.json").read_text())
        index["a1_origin_admission"] = {"minimum_remaining_seconds": 180}
        put(self.inputs / "inputs.json", index)
        self.config["initial_inputs_sha256"] = a1_once.file_hash(self.inputs / "inputs.json")
        put(self.path, self.config)
        with self.assertRaises(Exception):
            a1_once.load_config(self.path)
        index.pop("a1_origin_admission")
        put(self.inputs / "inputs.json", index)
        self.config["initial_inputs_sha256"] = a1_once.file_hash(self.inputs / "inputs.json")
        old = self.scope.with_name("scope-a1-r2h")
        self.scope.rename(old)
        self.config["scope_root"] = str(old)
        put(old / "inputs/a1-once.json", self.config)
        with self.assertRaises(Exception):
            a1_once.load_config(old / "inputs/a1-once.json")

    def test_manifest_template_and_code_lock_drift_are_rejected(self):
        put(self.source / "release-manifest.json", {"products": {"wrong": {}}})
        with self.assertRaises(Exception):
            a1_once.load_config(self.path)
        put(self.source / "release-manifest.json", {"products": {}})
        put(self.private / "model-publication-template.json", {"config_version": 2})
        with self.assertRaises(Exception):
            a1_once.load_config(self.path)
        put(self.private / "model-publication-template.json", {
            "config_version": 1, "providers": [{"provider_id": "provider-synthetic",
            "base_url": "https://gateway.internal:9443/v1"}]})
        (self.code / "ops/recovery/unlisted.py").write_text("added after lock\n")
        with self.assertRaises(Exception):
            a1_once.load_config(self.path)

    def test_unmarked_seal_failure_does_not_start_second_writer(self):
        seen, runner = self._runner(fail="seal")
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["failed_stage"], "seal")
        self.assertEqual(seen, ["seal"])
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")
        self.assertFalse((self.private / "receipts/seal-failure-source-stop.json").exists())

    def _bad_seal(self, fault):
        seen, base = self._runner()
        def runner(argv, timeout):
            code, receipt = base(argv, timeout)
            if "seal" in argv:
                self._mark_safe_seal(argv)
                receipt = dict(receipt)
                if fault == "bad_status":
                    receipt["status"] = "unexpected"
                else:
                    receipt["inputs_sha256"] = "e" * 64
            return code, receipt
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["failed_stage"], "seal")
        self.assertEqual(result["source_stop_status"], "disabled_restore_complete")
        self.assertEqual(seen, ["seal", "linux-rehearse"])
        self.assertFalse((self.private / "permit").exists())

    def test_bad_seal_status_takes_exact_source_stop_path(self):
        self._bad_seal("bad_status")

    def test_bad_seal_digest_takes_exact_source_stop_path(self):
        self._bad_seal("bad_digest")

    def test_seal_timeout_keeps_uncertain_source_for_review(self):
        seen = []
        def runner(argv, timeout):
            seen.append("seal")
            return 124, {"status": "stop_unconfirmed"}
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(seen, ["seal"])
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")
        self.assertFalse((self.private / "receipts/seal-failure-source-stop.json").exists())

    def test_seal_timeout_with_exited_child_without_marker_stops(self):
        seen, base = self._runner()
        def runner(argv, timeout):
            if "seal" in argv:
                seen.append("seal")
                return 124, {"status": "child_terminated_after_timeout"}
            return base(argv, timeout)
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(seen, ["seal"])
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")

    def test_previous_seal_marker_rejects_new_attempt_before_child(self):
        self._mark_safe_seal(["--attempt-id", str(uuid.uuid4())])
        with self.assertRaises(Exception):
            a1_once.drive(self.config, runner=lambda argv, timeout: self.fail(
                "a previous marker must stop before any child"))
        self.assertFalse((self.private / "receipts").exists())

    def test_wrong_attempt_id_marker_cannot_start_source_stop(self):
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            self._mark_safe_seal(argv)
            marker = json.loads((self.private / "a1-seal-actions-complete.json").read_text())
            marker["attempt_id"] = str(uuid.uuid4())
            put(self.private / "a1-seal-actions-complete.json", marker)
            return 2, {"status": "rejected"}
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")

    def test_partial_seal_marker_cannot_start_source_stop(self):
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            (self.private / "a1-seal-actions-complete.json").write_bytes(b'{"scope_id":')
            return 124, {"status": "child_terminated_after_timeout"}
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")

    def test_seal_exit_zero_without_marker_cannot_start_second_writer(self):
        seen, base = self._runner()
        def runner(argv, timeout):
            code, result = base(argv, timeout)
            if "seal" in argv:
                (self.private / "a1-seal-actions-complete.json").unlink()
            return code, result
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(seen, ["seal"])
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")

    def test_seal_exit_zero_with_wrong_attempt_cannot_advance(self):
        seen, base = self._runner()
        def runner(argv, timeout):
            code, result = base(argv, timeout)
            if "seal" in argv:
                marker_path = self.private / "a1-seal-actions-complete.json"
                marker = json.loads(marker_path.read_text())
                marker["attempt_id"] = str(uuid.uuid4())
                put(marker_path, marker)
            return code, result
        outcome = a1_once.drive(self.config, runner=runner)
        self.assertEqual(seen, ["seal"])
        self.assertEqual(outcome["source_stop_status"], "stop_unconfirmed")

    def test_seal_exit_zero_with_changed_action_receipt_cannot_advance(self):
        seen, base = self._runner()
        def runner(argv, timeout):
            code, result = base(argv, timeout)
            if "seal" in argv:
                put(self.inputs / "private/a1-actor-origin-issue.json",
                    {"action": "issue", "receipt": {"assertion_ref": "origin:changed",
                        "expires_at": "2099-01-01T00:00:00Z"}})
            return code, result
        outcome = a1_once.drive(self.config, runner=runner)
        self.assertEqual(seen, ["seal"])
        self.assertEqual(outcome["source_stop_status"], "stop_unconfirmed")

    def test_seal_bad_stdout_but_confirmed_exited_and_actions_stops_source(self):
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            if len(calls) == 1:
                self._mark_safe_seal(argv)
                return 2, {"status": "stop_unconfirmed", "_child_process_exited": True}
            return 0, {"status": "disabled_restore_complete",
                       "runtime_owners_stopped": 9}
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["source_stop_status"], "disabled_restore_complete")

    def test_seal_outer_budget_covers_bounded_product_calls(self):
        self.assertGreaterEqual(a1_once.TIMEOUTS["seal"], 4 * 45 + 30)

    def test_source_stop_timeout_does_not_claim_cleanup(self):
        seen = []
        def runner(argv, timeout):
            if "seal" in argv:
                seen.append("seal")
                self._mark_safe_seal(argv)
                return 2, {"status": "sealed"}
            seen.append("linux-rehearse")
            return 124, {"status": "stop_unconfirmed"}
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(seen, ["seal", "linux-rehearse"])
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")

    def test_source_stop_start_failure_is_persisted(self):
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            if len(calls) == 1:
                self._mark_safe_seal(argv)
            return (2, {"status": "sealed"}) if len(calls) == 1 else (
                2, {"status": "child_start_failed"})
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")
        receipt = json.loads((self.private /
            "receipts/seal-failure-source-stop.json").read_text())
        self.assertEqual(receipt["status"], "stop_unconfirmed")

    def test_phase_failure_does_not_advance(self):
        seen, runner = self._runner(fail="host-preflight")
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["failed_stage"], "host-preflight")
        self.assertEqual(seen, list(a1_once.STAGES[:3]))

    def test_insufficient_lifetime(self):
        seen, runner = self._runner(remaining=179)
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["failed_stage"], "permit")
        self.assertEqual(seen, list(a1_once.STAGES[:4]))
        self.assertFalse((self.scope / "deployments/clone").exists())
        self.assertFalse((self.private / "receipts/05-drill-plan.json").exists())

    def test_exact_180_second_boundary_advances_once(self):
        seen, runner = self._runner(remaining=180)
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["execute_calls"], 1)
        self.assertEqual(seen, list(a1_once.STAGES))

    def _permit_rejection(self, reason):
        seen, base = self._runner()
        def runner(argv, timeout):
            if "permit" in argv:
                seen.append("permit")
                return 2, {"status": "rejected", "reason": reason}
            return base(argv, timeout)
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["failed_stage"], "permit")
        self.assertEqual(seen, list(a1_once.STAGES[:4]))
        self.assertFalse((self.private / "receipts/05-drill-plan.json").exists())

    def test_origin_rejection_at_permit_stops_before_plan(self):
        self._permit_rejection("drill_origin_binding_invalid")

    def test_wal_rejection_at_permit_stops_before_plan(self):
        self._permit_rejection("drill_origin_source_invalid")

    def test_timeout_does_not_retry_execute(self):
        seen, runner = self._runner(fail="drill-execute")
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["failed_stage"], "drill-execute")
        self.assertEqual(result["child_status"], "stop_unconfirmed")
        self.assertEqual(seen.count("drill-execute"), 1)

    def test_seal_parameter_generation(self):
        self.assertEqual(a1_once.load_config(self.path), self.config)
        calls = []
        def diagnose(source, request):
            self.assertEqual(source, self.source)
            calls.append("diagnose")
            return {"valid": True}
        def publish(source, action, request):
            calls.append(action)
            if action == "publish":
                return {"action": action, "receipt": {"version": 5}}
            return {"action": "issue", "receipt": {
                "assertion_ref": "origin:" + request.stem,
                "expires_at": "2099-01-01T00:00:00Z"}}
        result = a1_once.seal(self.config, publisher=(diagnose, publish),
                              attempt_id=str(uuid.uuid4()))
        self.assertEqual(calls, ["diagnose", "publish", "issue", "issue"])
        self.assertEqual(result["status"], "sealed")
        index = json.loads((self.inputs / "inputs.json").read_text())
        self.assertEqual(index["a1_origin_admission"]["minimum_remaining_seconds"], 180)
        self.assertIn("origin:a1-config-origin-request", (self.inputs / "private/gateway.env").read_text())
        self.assertFalse((self.scope / "deployments/clone").exists())

    def test_permit_parameter_generation_and_binding(self):
        restored = self.scope / "deployments/restored"
        restored.mkdir()
        put(restored / "RESTORE.json", {"state": "restored_disabled",
            "authority_deployment_id": self.uuid, "snapshot_sha256": "3" * 64})
        registration = json.loads((self.source / ".recovery-registration.json").read_text())
        registration["runtime_identity"] = {"sha256": "4" * 64}
        put(self.source / ".recovery-registration.json", registration)
        self.config["registration_sha256"] = a1_once.file_hash(
            self.source / ".recovery-registration.json")
        index = json.loads((self.inputs / "inputs.json").read_text())
        put(self.private / "clone-inputs-finalized.json", {
            "inputs_sha256": a1_once.file_hash(self.inputs / "inputs.json")})
        facts = {"source": "fixture", "restore": "fixture"}
        verification = a1_once._sha(a1_once.canonical(facts))
        with patch("ops.recovery.engine.Recovery", return_value=object()), \
             patch("ops.recovery.drill_inputs.restoration_facts", return_value=facts), \
             patch("ops.recovery.drill_inputs.isolated_inputs", return_value=(index, {})) \
             as isolated, \
             patch("ops.recovery.drill_origin_admission.check_origin_admission",
                   return_value={"remaining_at_check_seconds": 181}) as admission, \
             patch("ops.recovery.nas_resources.profile_for", return_value=None):
            output = a1_once.make_permit(self.config, verification)
        permit = json.loads((self.private / "permit").read_text())
        self.assertEqual(output["status"], "permit_issued")
        self.assertEqual(permit["verification_sha256"], verification)
        self.assertEqual(permit["projects"], self.config["projects"])
        self.assertEqual(permit["max_runtime_seconds"], 540)
        self.assertEqual(output["permit_sha256"], a1_once.file_hash(self.private / "permit"))
        self.assertTrue(isolated.called)
        self.assertEqual(admission.call_args.kwargs["minimum_remaining"], 180)

    def test_preflight_reads_live_network_ports_and_memory(self):
        calls = []
        def docker(*args):
            calls.append(args)
            return ""
        bound = []
        result = a1_once.preflight(self.config, docker_run=docker,
            route_reader=lambda: "Iface Destination Gateway Flags RefCnt Use Metric Mask\n",
            bind=bound.append,
            memory_reader=lambda: "MemAvailable: 13000000 kB\n")
        self.assertEqual(result["status"], "host_preflight_passed")
        self.assertEqual(bound, self.ports[6:])
        self.assertTrue(any(row[:2] == ("network", "ls") for row in calls))
        with self.assertRaises(ValueError):
            a1_once.preflight(self.config, docker_run=docker,
                route_reader=lambda: "Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
                                     "br0 5031CD0A 00000000 0001 0 0 0 F0FFFFFF\n",
                bind=bound.append,
                memory_reader=lambda: "MemAvailable: 13000000 kB\n")

    def test_timeout_sends_term_and_waits_for_cleanup(self):
        class Child:
            pid = 12345
            returncode = 130
            calls = 0
            signals = []
            def send_signal(self, value):
                self.signals.append(value)
            def communicate(self, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("fixture", timeout)
                return ("{\"status\":\"stopped\"}\n", "")
        child = Child()
        with patch("ops.recovery.a1_once.subprocess.Popen", return_value=child):
            code, result = a1_once._json_child([str(self.python)], 1, cwd=self.code)
        self.assertEqual(code, 124)
        self.assertEqual(result["status"], "child_terminated_after_timeout")
        self.assertEqual(child.calls, 2)
        self.assertEqual(child.signals, [a1_once.signal.SIGTERM])
        self.assertEqual(result["_stream_sha256"]["stdout"],
                         a1_once._sha(b'{"status":"stopped"}\n'))

    def test_child_start_and_communication_oserror_are_structured(self):
        with patch("ops.recovery.a1_once.subprocess.Popen", side_effect=OSError()):
            self.assertEqual(a1_once._json_child([str(self.python)], 1)[1]["status"],
                             "child_start_failed")
        class Child:
            def __init__(self, exited):
                self.exited = exited
                self.signals = []
            def communicate(self, timeout):
                raise OSError("fixture pipe error")
            def poll(self):
                return 2 if self.exited else None
            def send_signal(self, value):
                self.signals.append(value)
            def wait(self, timeout):
                raise subprocess.TimeoutExpired("fixture", timeout)
        for exited, expected in ((True, "child_terminated_after_io_error"),
                                 (False, "stop_unconfirmed")):
            child = Child(exited)
            with patch("ops.recovery.a1_once.subprocess.Popen", return_value=child):
                code, result = a1_once._json_child([str(self.python)], 1)
            self.assertEqual(code, 125)
            self.assertEqual(result["status"], expected)
            self.assertEqual(child.signals, [] if exited else [a1_once.signal.SIGTERM])

    def _oserror_state(self, child_status, source_status, expected_calls):
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            if len(calls) == 1:
                if expected_calls == 2:
                    self._mark_safe_seal(argv)
                return 125, {"status": child_status}
            return 0, {"status": "disabled_restore_complete",
                       "runtime_owners_stopped": 9}
        result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(result["source_stop_status"], source_status)
        self.assertEqual(len(calls), expected_calls)
        self.assertFalse((self.private / "permit").exists())
        self.assertTrue((self.private / "receipts/01-seal.json").exists())

    def test_popen_failure_does_not_start_source_stop_or_permit(self):
        self._oserror_state("child_start_failed", "source_active_child_not_started", 1)

    def test_communication_failure_live_child_does_not_start_second_writer(self):
        self._oserror_state("stop_unconfirmed", "stop_unconfirmed", 1)

    def test_receipt_write_failure_cannot_start_second_writer(self):
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            return 125, {"status": "stop_unconfirmed"}
        actual_write = a1_once._write_once
        def fail_after_attempt(path, value):
            if path.name == "attempt.json":
                return actual_write(path, value)
            raise OSError()
        with patch("ops.recovery.a1_once._write_once", side_effect=fail_after_attempt):
            result = a1_once.drive(self.config, runner=runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["source_stop_status"], "stop_unconfirmed")
        self.assertEqual(result["phase_status"], "receipt_storage_failed")

    def test_communication_failure_exited_child_remains_unconfirmed(self):
        self._oserror_state("child_terminated_after_io_error",
                            "stop_unconfirmed", 1)


if __name__ == "__main__":
    unittest.main()
