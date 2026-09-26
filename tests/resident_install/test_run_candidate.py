"""Isolated runner receipts; starts no Docker or NAS services."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ops.resident_install import run_candidate


class ResidentRunnerReceiptTests(unittest.TestCase):
    def test_post_prepare_gate_requires_exact_prior_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "prepared-root"
            root.mkdir()
            for category in ("data", "logs"):
                for product in run_candidate.PRODUCTS:
                    (root / category / product).mkdir(parents=True)
            manifest = root / "release-manifest.json"
            manifest.write_bytes(b"{}")
            integrity = root / "bundle-integrity.json"
            integrity.write_bytes(b"{}")
            prior = base / "first-install"
            prior.mkdir()
            (prior / "run-attempt.json").write_text(json.dumps({
                "state": "started", "automatic_retry": False,
                "deployment_root": str(root), "plan_sha256": "fixed-plan",
                "start_remaining_requested": True,
            }))
            (prior / "prepare-attempt.json").write_text(json.dumps({
                "stage": "prepare", "state": "started", "automatic_retry": False,
            }))
            (prior / "prepare.stdout").write_text(json.dumps({
                "status": "resident_candidate_prepared", "bundle_root": str(root),
                "release_ready": False, "provider": "not_configured",
                "next_stage": "export_fixed_sources_and_resident_compose",
            }))
            (prior / "prepare.stderr").write_bytes(b"")
            (prior / "run-stopped.json").write_text(json.dumps({
                "state": "needs_diagnosis", "stage": "runtime",
                "code": "unexpected_or_interrupted_stage_failure",
                "automatic_retry": False, "manual_stop_required": False,
                "cleanup": {"exact_stop": "no_activation_attempt"},
                "release_ready": False,
            }))
            args = SimpleNamespace(prior_evidence=prior, start_remaining=True)
            hashes = {"plan": "fixed-plan", "release-manifest.json":
                      hashlib.sha256(manifest.read_bytes()).hexdigest()}
            with (mock.patch.object(run_candidate, "FIXED_INPUT_HASHES", hashes),
                  mock.patch.object(run_candidate, "PREPARED_BUNDLE_INTEGRITY_SHA256",
                                    hashlib.sha256(integrity.read_bytes()).hexdigest()),
                  mock.patch.object(run_candidate, "verify_integrity"),
                  mock.patch.object(run_candidate, "load_manifest", return_value={}),
                  mock.patch.object(run_candidate, "check_contracts")):
                receipt = run_candidate._resume_prepared(root, args)
                self.assertIn("prepare_receipt_sha256", receipt)
                (root / "data" / "platform" / "unexpected.sqlite").write_bytes(b"x")
                with self.assertRaisesRegex(run_candidate.Stopped,
                                            "prepared_mutable_layout_changed"):
                    run_candidate._resume_prepared(root, args)
                (root / "data" / "platform" / "unexpected.sqlite").unlink()
                (prior / "first_export-attempt.json").write_text("{}")
                with self.assertRaisesRegex(run_candidate.Stopped,
                                            "later_stage_evidence_present"):
                    run_candidate._resume_prepared(root, args)

    def test_real_child_json_receipt_returns_bytes_and_parses_full_document(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            command = [
                sys.executable, "-c",
                "import json; print(json.dumps({'status': 'resident_candidate_prepared', "
                "'release_ready': False}, indent=2))",
            ]
            receipt = run_candidate._receipt(
                "prepare", command, evidence, "resident_candidate_prepared",
                timeout=15,
            )
            self.assertEqual(receipt["status"], "resident_candidate_prepared")
            self.assertIs(receipt["release_ready"], False)
            self.assertEqual(json.loads((evidence / "prepare.stdout").read_bytes()), receipt)
            self.assertEqual((evidence / "prepare.stderr").read_bytes(), b"")
            self.assertTrue((evidence / "prepare-attempt.json").is_file())

    def test_child_product_timeout_preserves_uncertain_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            command = [
                sys.executable, "-c",
                "import json,sys; print(json.dumps({'status':'refused',"
                "'code':'product_effects_unconfirmed_timeout'})); sys.exit(2)",
            ]
            with self.assertRaises(run_candidate.Stopped) as caught:
                run_candidate._command("activate", command, evidence, timeout=15)
            self.assertEqual(caught.exception.code, "child_effects_unconfirmed_timeout")
            self.assertTrue(caught.exception.effects_unconfirmed)
            self.assertEqual(json.loads((evidence / "activate.stdout").read_bytes())["code"],
                             "product_effects_unconfirmed_timeout")

    def test_schema2_history_requires_exact_failed_child_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            prior = base / "prior"
            work = root / "reports/resident-install"
            export = base / "first"
            for path in (work, prior, export):
                path.mkdir(parents=True)

            def write(path, value):
                path.write_text(json.dumps(value), encoding="utf-8")

            write(prior / "run-attempt.json", {
                "deployment_root": str(root), "plan_sha256": "fixed-plan",
                "start_remaining_requested": True, "resume_after_prepare": True,
                "prior_evidence": str(prior),
            })
            write(prior / "run-stopped.json", {
                "state": "needs_diagnosis", "stage": "activate",
                "code": "command_failed", "automatic_retry": False,
                "cleanup": {"exact_stop": "exact_containers_stopped"},
                "manual_stop_required": False, "release_ready": False,
            })
            write(prior / "first_export.stdout", {
                "status": "resident_candidate", "release_ready": False,
            })
            write(prior / "activate-attempt.json", {
                "stage": "activate", "state": "started", "automatic_retry": False,
            })
            write(prior / "activate.stdout", {
                "status": "refused", "code": "product_command_failed",
            })
            (prior / "activate.stderr").write_bytes(b"")
            for name in ("attempt.json", "compose_config-attempt.json",
                         "memory_schema_2-attempt.json", "failure.json"):
                (work / name).write_bytes(name.encode())
            (root / "bundle-integrity.json").write_bytes(b"bundle")
            (export / "resident-export.lock.json").write_bytes(b"lock")
            hashes = {
                "bundle-integrity.json": run_candidate._sha(root / "bundle-integrity.json"),
                "first-lock": run_candidate._sha(export / "resident-export.lock.json"),
                "attempt.json": run_candidate._sha(work / "attempt.json"),
                "failure.json": run_candidate._sha(work / "failure.json"),
            }
            args = SimpleNamespace(prior_evidence=prior, first_export=export)
            with (mock.patch.object(run_candidate, "PREPARE_RESUME_EVIDENCE", prior),
                  mock.patch.object(run_candidate, "FIXED_INPUT_HASHES", {"plan": "fixed-plan"}),
                  mock.patch.object(run_candidate, "SCHEMA2_RESUME_HASHES", hashes),
                  mock.patch.object(run_candidate, "verify_integrity")):
                self.assertIn("activation_failure_sha256",
                              run_candidate._resume_schema2_history(root, args))
                write(prior / "activate.stdout", {
                    "status": "refused", "code": "different_failure",
                })
                with self.assertRaisesRegex(run_candidate.Stopped,
                                            "fixed_pre_migration_failure_required"):
                    run_candidate._resume_schema2_history(root, args)

    def test_remaining_services_uses_renewed_final_receipt_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, first, final, evidence = (base / name for name in
                                            ("root", "first", "final", "evidence"))
            work = root / "reports/resident-install"
            work.mkdir(parents=True)
            evidence.mkdir()
            hashes = {}
            for project in (run_candidate.CORE_PROJECT, run_candidate.OBS_PROJECT):
                raw = project.encode()
                for output in (first, final):
                    path = output / project / "compose.yaml"
                    path.parent.mkdir(parents=True)
                    path.write_bytes(raw)
                hashes[project] = hashlib.sha256(raw).hexdigest()

            def write(path, value):
                path.write_text(json.dumps(value), encoding="utf-8")

            final_lock = final / "resident-export.lock.json"
            write(final_lock, {"compose_sha256": hashes})
            write(work / "attempt.json", {"export_compose_sha256": hashes})
            write(work / "result.json", {
                "state": "authority_initialized_pending_final_export",
                "platform_container_id": "a" * 64,
                "expires_at": "old-expiry", "gateway_ref_sha256": "old-hash",
            })
            write(work / "reauthorization-result.json", {
                "state": "manual_reauthorized", "expires_at": "new-expiry",
                "gateway_ref_sha256": "b" * 64,
            })
            final_receipt = {
                "state": "final_export_verified", "manual_reauthorization": True,
                "source_expires_at": "new-expiry", "gateway_ref_sha256": "b" * 64,
                "export_lock_sha256": run_candidate._sha(final_lock),
            }
            write(work / "final-export-after-renewal.json", final_receipt)
            capacity = base / "capacity.json"
            capacity.write_bytes(b"{}")
            result = {"status": "manual_reauthorized_export_verified",
                      "expires_at": "new-expiry", "final_export": str(final)}
            with (mock.patch.object(run_candidate, "_capacity_config_matches"),
                  mock.patch.object(run_candidate, "_platform_id") as platform_id,
                  mock.patch.object(run_candidate, "_remaining", return_value=180),
                  mock.patch.object(run_candidate, "_command",
                                    side_effect=run_candidate.Stopped(
                                        "final_core_config", "deliberate_stop"))):
                with self.assertRaisesRegex(run_candidate.Stopped, "deliberate_stop"):
                    run_candidate._start_remaining(
                        root, first, final, result, evidence, capacity, "unit.service"
                    )
                self.assertEqual(platform_id.call_args.args[1], "a" * 64)
                attempt = json.loads((evidence / "remaining-services-attempt.json").read_text())
                self.assertEqual(attempt["source_expires_at"], "new-expiry")
                result["expires_at"] = "old-expiry"
                with self.assertRaisesRegex(run_candidate.Stopped,
                                            "final_export_or_platform_identity_invalid"):
                    run_candidate._start_remaining(
                        root, first, final, result, evidence, capacity, "unit.service"
                    )

    def test_explicit_resume_skips_prepare_and_writes_new_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plan = base / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            prior = base / "prior-evidence"
            prior.mkdir()
            args = SimpleNamespace(
                plan=plan, resume_after_prepare=True, resume_after_schema2=False,
                prior_evidence=prior,
                start_remaining=False, evidence=base / "resume-evidence",
            )
            with (mock.patch.object(run_candidate, "_local_docker_environment"),
                  mock.patch.object(run_candidate, "_check_paths", return_value=base),
                  mock.patch.object(run_candidate, "_resume_prepared",
                                    return_value={"prior_run_stopped_sha256": "fixed"}),
                  mock.patch.object(run_candidate, "_live_preflight"),
                  mock.patch.object(run_candidate, "_set_runtime_permissions",
                                    side_effect=run_candidate.Stopped("permissions", "deliberate")),
                  mock.patch.object(run_candidate, "_receipt") as receipt):
                with self.assertRaisesRegex(run_candidate.Stopped, "deliberate"):
                    run_candidate.run(args)
            receipt.assert_not_called()
            self.assertEqual(list(prior.iterdir()), [])
            self.assertTrue((args.evidence / "resume-readback.json").is_file())
            self.assertEqual(
                json.loads((args.evidence / "run-stopped.json").read_text())["stage"],
                "permissions",
            )


if __name__ == "__main__":
    unittest.main()
