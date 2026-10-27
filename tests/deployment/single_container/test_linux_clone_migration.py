"""Local semantic marker and explicit partial-migration continuation tests."""

import hashlib
import importlib.util
import json
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import linux_clone as clone
import linux_clone_migration_continue as continuation


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.marker = self.root / "clone/root/.tianshu-storage-platform.json"
        self.marker.parent.mkdir(parents=True)
        self.source = self.root / "snapshot/backup/.tianshu-storage-platform.json"
        self.source.parent.mkdir(parents=True)
        self.original = {
            "version": 1,
            "service": "platform",
            "backend": "postgresql",
            "state": "active",
            "namespace": "original",
            "installation_id": "a" * 32,
            "epoch": "b" * 32,
            "databases": {"logical_" + str(i): "schema_" + str(i) for i in range(17)},
        }
        self.source.write_text(json.dumps(self.original))
        self.receipt = {"cache_generation": "c" * 32}
        self.expected = {**self.original, "epoch": self.receipt["cache_generation"]}
        self.marker.write_text(json.dumps(self.expected, separators=(",", ":")))
        self.ctx = SimpleNamespace(
            stage=self.root,
            root=self.marker.parent,
            snapshot=self.source.parent.parent,
            manifest={
                "postgresql": [
                    {
                        "service": "platform",
                        "binding": self.marker.name,
                        "schemas": self.original["databases"],
                    }
                ],
                "inventory": {
                    "entries": {
                        self.marker.name: {
                            "kind": "file",
                            "uid": self.marker.stat().st_uid,
                            "gid": self.marker.stat().st_gid,
                            "mode": stat.S_IMODE(self.marker.stat().st_mode),
                        }
                    }
                },
            },
            backend=SimpleNamespace(
                canonical=lambda value: json.dumps(
                    value, sort_keys=True, separators=(",", ":")
                ).encode()
            ),
        )
        (self.root / "clone/receipt.json").write_text(json.dumps(self.receipt))

    def test_expected_marker_preserves_entire_original_object_with_only_clone_epoch(
        self,
    ):
        _, expected = clone.expected_platform_marker(self.ctx, self.receipt)
        self.assertEqual(expected, self.expected)
        self.assertEqual(clone.read(self.source), self.original)

    def test_representation_change_is_recorded_and_identity_registry_change_is_rejected(
        self,
    ):
        before = self.marker.read_bytes()
        self.marker.write_text(json.dumps(self.expected, sort_keys=True) + "\n")
        proof = clone.marker_proof(self.marker, self.expected, before)
        self.assertTrue(proof["semantic_unchanged"])
        self.assertTrue(proof["representation_changed"])
        self.assertNotEqual(proof["before_sha256"], proof["after_sha256"])
        for key, value in (
            ("epoch", "d" * 32),
            ("installation_id", "d" * 32),
            ("namespace", "other"),
            ("databases", {"logical_0": "schema_0"}),
            ("unexpected", True),
        ):
            self.marker.write_text(json.dumps({**self.expected, key: value}))
            with self.subTest(key=key), self.assertRaises(clone.CloneError):
                clone.marker_proof(self.marker, self.expected, before)

    @unittest.skipUnless(
        importlib.util.find_spec("tianshu_storage"),
        "installed storage wheel not selected",
    )
    def test_real_storage_save_normalizes_bytes_without_changing_marker_semantics(self):
        from tianshu_storage.runtime import StorageRuntime

        runtime = StorageRuntime(service="platform", state_directory=self.marker.parent)
        runtime._state = self.expected.copy()
        before = self.marker.read_bytes()
        try:
            # Only actual marker writer; no start/database/PG/pool or Redis used.
            runtime._save()
        finally:
            runtime.close()
        self.assertEqual(
            self.marker.read_bytes(),
            (json.dumps(self.expected, sort_keys=True) + "\n").encode(),
        )
        self.assertTrue(
            clone.marker_proof(self.marker, self.expected, before)[
                "representation_changed"
            ]
        )

    def test_prior_failure_must_match_exact_source_and_original_reason(self):
        source = self.root / "old-tool.py"
        failure = self.root / "failure.log"
        raw = b"# synthetic pinned old tool\n"
        source.write_bytes(raw.replace(b"\n", b"\r\n"))
        failure.write_text(
            json.dumps(
                {
                    "status": "refused",
                    "reason": "migration_changed_platform_marker",
                    "exception_type": "CloneError",
                }
            )
            + "\n"
        )
        with patch.object(
            continuation, "PRIOR_TOOLS", {hashlib.sha256(raw).hexdigest()}
        ):
            proof = continuation.prior_evidence(source, failure)
            self.assertFalse(proof["prior_original_snapshots_reconstructed"])
            self.assertFalse(proof["prior_original_snapshots_available"])
            failure.write_text(json.dumps({"status": "refused", "reason": "other"}))
            with self.assertRaises(clone.CloneError):
                continuation.prior_evidence(source, failure)
        with self.assertRaises(clone.CloneError):
            continuation.prior_evidence(source, failure)

    def test_marker_metadata_restores_only_exact_new_clone_file(self):
        platform, expected = clone.expected_platform_marker(self.ctx, self.receipt)
        before = self.marker.read_bytes()
        wanted = self.ctx.manifest["inventory"]["entries"][self.marker.name]
        self.marker.chmod(0o400)
        proof = clone.restore_clone_marker_metadata(
            self.ctx, platform, self.marker, expected
        )
        self.assertEqual(
            proof["after"], {key: wanted[key] for key in ("mode", "uid", "gid")}
        )
        self.assertEqual(self.marker.read_bytes(), before)
        with self.assertRaises(clone.CloneError):
            clone.restore_clone_marker_metadata(
                self.ctx, platform, self.source, expected
            )

    def test_continuation_does_not_restore_or_reverify_pre_ddl_pg_fingerprint(self):
        evidence = {"prior_original_snapshots_reconstructed": False}
        with (
            patch.object(continuation, "prior_evidence", return_value=evidence),
            patch.object(clone, "context", return_value=self.ctx),
            patch.object(clone, "verified_clone_receipt", return_value=self.receipt),
            patch.object(
                clone, "verify_clone", side_effect=AssertionError("must not rerun")
            ) as verify,
            patch.object(
                clone, "prepare_clone", side_effect=AssertionError("must not restore")
            ) as prepare,
            patch.object(
                clone, "verify_pg", side_effect=AssertionError("DDL changed")
            ) as fingerprint,
            patch.object(
                clone, "run_platform_migration", return_value={"status": "fixture"}
            ) as run,
        ):
            result = continuation.continue_migration(
                self.root,
                "binding",
                "state",
                snapshot="snapshot",
                recovery_bundle="bundle",
                settings="settings",
                readonly_probe="probe",
                observation_sha256="sha",
                prior_tool="old",
                prior_failure="failure",
            )
        self.assertEqual(result["status"], "fixture")
        verify.assert_not_called()
        prepare.assert_not_called()
        fingerprint.assert_not_called()
        self.assertEqual(
            run.call_args.kwargs["output_name"], "platform-migration-continuation"
        )
        self.assertIs(run.call_args.kwargs["continuation_evidence"], evidence)


if __name__ == "__main__":
    unittest.main()
