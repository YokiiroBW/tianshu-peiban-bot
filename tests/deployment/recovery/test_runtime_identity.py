import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from fixtures import fixture, put
from runtime_fixtures import runtime_fixture

from ops.recovery.engine import Recovery
from ops.recovery.lifecycle_binding import describe
from ops.recovery.runtime_identity import load_identity
from ops.recovery.safety import RecoveryError, file_hash


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "scope"
        self.scope, _ = fixture(self.root)
        self.source = self.root / "deployments/source"
        self.path, self.value = runtime_fixture(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def test_fixed_schema_normalized_and_original_bytes_then_local_image_binding(self):
        runtime = load_identity(self.source, self.path, file_hash(self.path))
        binding = describe(
            Recovery(self.root, self.scope),
            self.source,
            runtime["project_name"],
            ["compose.json", "observability/compose.yaml"],
            "compose",
            runtime_pin={
                "path": "reports/runtime-identity.json",
                "sha256": file_hash(self.path),
            },
        )
        self.assertEqual(len(binding["services"]), 9)
        self.assertEqual(binding["services"]["platform"]["repo_digests"], [])

    def test_qa_prefix_without_fixed_identity_still_rejected(self):
        with self.assertRaisesRegex(RecoveryError, "synthetic_project_required"):
            describe(
                Recovery(self.root, self.scope),
                self.source,
                "tianshu-qa-other",
                ["compose.json", "observability/compose.yaml"],
                "compose",
            )

    def test_forged_sources_mounts_project_and_compose_rejected(self):
        changes = [
            lambda v: v["integrity"]["sources"]["memory"].update(commit="f" * 40),
            lambda v: v["mounts"][0].update(owner_service="gateway"),
            lambda v: v["projects"]["core"].update(directory=str(self.root)),
            lambda v: v["projects"]["core"]["compose_json"]["services"][
                "memory"
            ].update(image="other"),
            lambda v: v["services"]["platform"].update(extra="forbidden"),
            lambda v: v["lease"].update(path=str(self.root / "other.lock")),
        ]
        for change in changes:
            value = deepcopy(self.value)
            change(value)
            put(self.path, value)
            with self.subTest(change=change), self.assertRaises(RecoveryError):
                load_identity(self.source, self.path, file_hash(self.path))

    def test_report_hash_and_extra_private_file_rejected(self):
        with self.assertRaisesRegex(RecoveryError, "runtime_identity_digest_mismatch"):
            load_identity(self.source, self.path, "a" * 64)
        put(self.source / "private/extra.env", {"synthetic": "extra"})
        with self.assertRaisesRegex(RecoveryError, "unlisted_bundle_file"):
            load_identity(self.source, self.path, file_hash(self.path))

    def test_unobserved_image_and_missing_log_compose_are_not_execution_evidence(self):
        self.value["services"]["platform"]["image_id"] = None
        put(self.path, self.value)
        with self.assertRaisesRegex(RecoveryError, "runtime_image_not_observed"):
            load_identity(self.source, self.path, file_hash(self.path), observed=True)
        self.value["projects"]["observability"]["compose_json"] = None
        put(self.path, self.value)
        with self.assertRaisesRegex(RecoveryError, "runtime_compose_missing"):
            load_identity(self.source, self.path, file_hash(self.path))
