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
                  mock.patch.object(run_candidate, "_clean_install"),
                  mock.patch.object(run_candidate, "load_manifest", return_value={}),
                  mock.patch.object(run_candidate, "check_contracts")):
                receipt = run_candidate._resume_prepared(root, args)
                self.assertIn("prepare_receipt_sha256", receipt)
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

    def test_explicit_resume_skips_prepare_and_writes_new_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plan = base / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            prior = base / "prior-evidence"
            prior.mkdir()
            args = SimpleNamespace(
                plan=plan, resume_after_prepare=True, prior_evidence=prior,
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
