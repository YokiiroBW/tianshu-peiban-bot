import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import linux_origin_lease as lease


class OriginLeaseTests(unittest.TestCase):
    def test_first_candidate_source_ref_preserves_existing_path(self):
        source = "origin:" + "a" * 32
        helper = SimpleNamespace(
            ORIGIN_READ="original-read", replace_origin_line=Mock()
        )
        with patch.object(lease, "program") as program:
            current, previous = lease.candidate_reference(
                helper,
                ("MODEL_REF='" + source + "'\r\n").encode(),
                "MODEL_REF",
                source,
                {},
                {},
                {},
            )
        self.assertEqual(current, source)
        self.assertIsNone(previous)
        program.assert_not_called()

    def test_previous_clone_ref_same_scope_allows_expired_lease(self):
        source, current = "origin:" + "a" * 32, "origin:" + "b" * 32
        scope = dict(
            zip(
                lease.SCOPE,
                (
                    "model",
                    "digest",
                    "operator",
                    "TOKEN",
                    [{"purpose": "config.snapshot"}],
                    300,
                    False,
                ),
            )
        )
        previous = {**scope, "expired": True}
        helper = SimpleNamespace(
            ORIGIN_READ="original-read", replace_origin_line=Mock()
        )
        with patch.object(lease, "program", return_value=previous) as program:
            actual, proof = lease.candidate_reference(
                helper,
                ("MODEL_REF=" + current + "\n").encode(),
                "MODEL_REF",
                source,
                scope,
                {"clone": True},
                {"owned": "environment"},
            )
        self.assertEqual((actual, proof), (current, previous))
        self.assertEqual(program.call_args.args[0], helper.ORIGIN_READ)
        self.assertEqual(program.call_args.kwargs["REF"], current)

    def test_previous_clone_ref_mismatched_owner_route_or_ttl_is_refused(self):
        source, current = "origin:" + "a" * 32, "origin:" + "b" * 32
        scope = dict(
            zip(
                lease.SCOPE,
                (
                    "model",
                    "digest",
                    "operator",
                    "TOKEN",
                    [{"purpose": "config.snapshot"}],
                    300,
                    False,
                ),
            )
        )
        helper = SimpleNamespace(
            ORIGIN_READ="original-read", replace_origin_line=Mock()
        )
        for key, value in (
            ("owner", "different"),
            ("routes", []),
            ("ttl_seconds", 301),
        ):
            with (
                self.subTest(key=key),
                patch.object(
                    lease,
                    "program",
                    return_value={**scope, key: value, "expired": False},
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "candidate_previous_origin_scope_changed"
                ):
                    lease.candidate_reference(
                        helper,
                        ("MODEL_REF=" + current + "\n").encode(),
                        "MODEL_REF",
                        source,
                        scope,
                        {},
                        {},
                    )

    def test_original_program_uses_stdin_settings_and_env_secret_not_argv(self):
        source = "import json,os\nfrom pathlib import Path\n" + lease.SETTINGS_LINE
        source += "\nprint(json.dumps({'database_path':settings['database_path'],'credential_present':len(os.environ['SYNTHETIC_SECRET'])>20}))\n"
        secret = "synthetic-secret-never-in-argv-or-stdout"
        with patch.object(lease.subprocess, "run", wraps=subprocess.run) as run:
            result = lease.program(
                source, {"database_path": "isolated"}, {"SYNTHETIC_SECRET": secret}
            )
        self.assertEqual(
            result, {"database_path": "isolated", "credential_present": True}
        )
        self.assertNotIn(secret, repr(run.call_args.args))
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(run.call_args.args[0][-1], "-")

    def test_original_program_changed_settings_read_and_child_error_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, "original_settings_read_changed"):
            lease.program("print('not original')", {}, {})
        source = "import json\nfrom pathlib import Path\n" + lease.SETTINGS_LINE
        source += "\nraise RuntimeError('synthetic-private-child-error')\n"
        with self.assertRaisesRegex(
            RuntimeError, "original_origin_program_failed"
        ) as error:
            lease.program(source, {}, {})
        self.assertNotIn("synthetic-private-child-error", str(error.exception))

    def test_reviewed_loader_and_candidate_paths_do_not_follow_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"
            config.mkdir()
            path = config / "synthetic.py"
            path.write_bytes(b"VALUE=1\n")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(
                lease.load(path, "synthetic_origin_helper", digest).VALUE, 1
            )
            self.assertEqual(lease.candidate_file(root, "/config/synthetic.py"), path)
            path.write_bytes(b"VALUE=2\n")
            with self.assertRaisesRegex(RuntimeError, "reviewed_origin_tool_required"):
                lease.load(path, "synthetic_origin_helper", digest)
            with self.assertRaises(RuntimeError):
                lease.candidate_file(root, "/etc/unrelated")

    def test_changed_candidate_env_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gateway.env"
            path.write_bytes(b"unchanged-current")
            with self.assertRaisesRegex(RuntimeError, "candidate_gateway_env_changed"):
                lease.atomic_env(path, b"stale", b"new")
            self.assertEqual(path.read_bytes(), b"unchanged-current")


if __name__ == "__main__":
    unittest.main()
