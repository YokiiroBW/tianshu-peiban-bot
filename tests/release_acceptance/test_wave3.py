"""Decision boundaries: disabled is observed, never inferred from a successful response."""

import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from acceptance.capabilities import disabled_facts
from acceptance.transport import Failed, Missing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/tianshu"))
from bootstrap import remaining, first_install  # noqa: E402
from manifest import Refused  # noqa: E402


class Client:
    def __init__(self, status=200, body=None):
        self.status, self.body = status, body

    def request(self, *args, **kwargs):
        return self.status, self.body


class DisabledTests(unittest.TestCase):
    def setUp(self):
        self.body = {
            "schema_version": 1,
            "service": "companion",
            "automatic_memory_candidates": {
                "enabled": False,
                "generation": "disabled",
                "submission": "paused",
                "backlog_policy": "preserve",
                "retained_outbox": dict.fromkeys(
                    ("pending", "blocked_scope", "submitting", "unknown"), False
                ),
                "memory_write_verification": "not_verified",
            },
            "chat_audit": {"enabled": False, "state": "not_integrated"},
        }

    def test_disabled_facts_keep_backlog_and_never_prove_memory_or_archive(self):
        self.body["automatic_memory_candidates"]["retained_outbox"]["unknown"] = True
        facts = disabled_facts(Client(body=self.body), "test-token")
        self.assertTrue(facts["retained_outbox"]["unknown"])
        self.assertFalse(facts["memory_write_proven"])
        self.assertFalse(facts["chat_archive_proven"])

    def test_200_with_enabled_or_fake_success_rejected(self):
        for key, value in (
            ("enabled", True),
            ("enabled", 0),
            ("generation", "enabled"),
            ("submission", "enabled"),
            ("backlog_policy", "clear"),
            ("memory_write_verification", "verified"),
            ("retained_outbox", {}),
        ):
            with self.subTest(key=key, value=value):
                body = copy.deepcopy(self.body)
                body["automatic_memory_candidates"][key] = value
                with self.assertRaises(Failed):
                    disabled_facts(Client(body=body), "test-token")

    def test_missing_old_product_endpoint_is_dependency_missing(self):
        with self.assertRaises(Missing):
            disabled_facts(Client(404, {}), "test-token")
        with self.assertRaises(Failed):
            disabled_facts(Client(401, {}), "test-token")

    def test_integer_or_boolean_aliases_do_not_prove_capabilities(self):
        for body in (
            {**self.body, "schema_version": True},
            {**self.body, "chat_audit": {"enabled": 0, "state": "not_integrated"}},
        ):
            with self.assertRaises(Failed):
                disabled_facts(Client(body=body), "test-token")


class BootstrapTests(unittest.TestCase):
    def test_expiry_budget_and_timezone_fail_closed(self):
        now = datetime(2026, 9, 22, tzinfo=timezone.utc).timestamp()
        receipt = {
            "assertion_ref": "origin:" + "a" * 32,
            "expires_at": "2026-09-22T00:01:00Z",
        }
        self.assertEqual(60, remaining(receipt, now=now, minimum_seconds=30))
        for delay in (31, 60, 100):
            with self.assertRaisesRegex(
                Refused, "assertion_expired_or_budget_insufficient"
            ):
                remaining(receipt, now=now + delay, minimum_seconds=30)
        with self.assertRaisesRegex(Refused, "assertion_expiry_timezone_required"):
            remaining({**receipt, "expires_at": "2026-09-22T00:01:00"}, now=now)

    def test_existing_database_is_refused_before_product_execution(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "present.sqlite"
            db.write_bytes(b"untouched")
            settings = root / "settings.json"
            settings.write_text(json.dumps({"database_path": str(db)}))
            with self.assertRaisesRegex(Refused, "fresh_platform_database_required"):
                first_install(
                    Path("must-not-run"), root, settings, {}, {}, root / "bootstrap"
                )
            self.assertEqual(b"untouched", db.read_bytes())
            self.assertFalse((root / "bootstrap").exists())
