import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_packaging as packaging
import linux_reauthorize
from bundle import verify_integrity
from linux_bootstrap import (
    begin_reauthorization,
    request_frame,
    store_reauthorization_receipt,
    update,
)
from manifest import Refused, read_json, write_json


class LinuxReauthorizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = packaging.PackagingTests()
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.fixture.init()
        self.root = self.fixture.output
        (self.root / "reports/bootstrap").mkdir(parents=True)
        self.now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
        self.old_ref = "origin:" + "a" * 32
        self.new_ref = "origin:" + "b" * 32
        self.old_expiry = (self.now - timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        )
        write_json(
            self.root / "reports/bootstrap/result.json",
            {
                "state": "authority_initialized",
                "issuer": "product_platform_cli",
                "expires_at": self.old_expiry,
                "automatic_retry": False,
            },
        )
        gateway = read_json(self.root / "config/gateway/settings.json")
        self.variable = gateway["platform_origin_env"]
        update(
            self.root,
            {
                "private/gateway.env": (
                    self.variable + "='" + self.old_ref + "'\n"
                ).encode()
            },
        )

    def tearDown(self):
        self.fixture.tearDown()

    def test_expired_origin_can_be_replaced_by_new_public_issue_receipt(self):
        marker = begin_reauthorization(self.root, now=self.now)
        expires_at = (self.now + timedelta(seconds=299)).isoformat().replace(
            "+00:00", "Z"
        )
        result = store_reauthorization_receipt(
            self.root,
            {
                "assertion_ref": self.new_ref,
                "expires_at": expires_at,
                "mode": "local_rehearsal",
            },
            now=self.now,
        )
        env = (self.root / "private/gateway.env").read_text()
        self.assertIn(self.variable + "='" + self.new_ref + "'", env)
        self.assertNotIn(self.old_ref, env)
        self.assertNotIn(self.new_ref, json.dumps(result))
        self.assertNotIn(self.old_ref, json.dumps(result))
        self.assertNotIn(self.new_ref, marker.read_text())
        self.assertNotIn(self.old_ref, marker.read_text())
        self.assertEqual(result["state"], "reauthorized")
        self.assertFalse(result["ref_in_report"])
        self.assertEqual(read_json(marker)["state"], "completed")
        verify_integrity(self.root)

    def test_begin_requires_natural_expiry_and_is_one_shot(self):
        not_expired = (self.now + timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        )
        write_json(
            self.root / "reports/bootstrap/result.json",
            {
                "state": "authority_initialized",
                "issuer": "product_platform_cli",
                "expires_at": not_expired,
            },
        )
        with self.assertRaisesRegex(Refused, "bootstrap_origin_not_expired"):
            begin_reauthorization(self.root, now=self.now)
        self.assertFalse(
            (self.root / "reports/bootstrap/reauthorization-attempt.json").exists()
        )

        write_json(
            self.root / "reports/bootstrap/result.json",
            {
                "state": "authority_initialized",
                "issuer": "product_platform_cli",
                "expires_at": self.old_expiry,
            },
        )
        begin_reauthorization(self.root, now=self.now)
        with self.assertRaisesRegex(Refused, "reauthorization_already_attempted"):
            begin_reauthorization(self.root, now=self.now)

    def test_refuses_same_expired_ref_and_leaves_private_input_unchanged(self):
        begin_reauthorization(self.root, now=self.now)
        before = (self.root / "private/gateway.env").read_bytes()
        future = (self.now + timedelta(seconds=299)).isoformat().replace("+00:00", "Z")
        with self.assertRaisesRegex(Refused, "new_assertion_required"):
            store_reauthorization_receipt(
                self.root,
                {"assertion_ref": self.old_ref, "expires_at": future},
                now=self.now,
            )
        self.assertEqual((self.root / "private/gateway.env").read_bytes(), before)
        verify_integrity(self.root)

    def test_refuses_expired_or_short_lived_issue_receipt(self):
        begin_reauthorization(self.root, now=self.now)
        before = (self.root / "private/gateway.env").read_bytes()
        for seconds in (0, 119):
            expires_at = (self.now + timedelta(seconds=seconds)).isoformat().replace(
                "+00:00", "Z"
            )
            with self.subTest(seconds=seconds), self.assertRaisesRegex(
                Refused, "assertion_expired_or_budget_insufficient"
            ):
                store_reauthorization_receipt(
                    self.root,
                    {"assertion_ref": self.new_ref, "expires_at": expires_at},
                    now=self.now,
                )
        self.assertEqual((self.root / "private/gateway.env").read_bytes(), before)
        verify_integrity(self.root)

    def test_execute_streams_public_cli_input_without_docker_cp(self):
        self.old_expiry = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        write_json(
            self.root / "reports/bootstrap/result.json",
            {
                "state": "authority_initialized",
                "issuer": "product_platform_cli",
                "expires_at": self.old_expiry,
                "automatic_retry": False,
            },
        )
        write_json(
            self.root / "reports/linux-executed.json",
            {"result": "passed", "owned_containers": {}},
        )

        def issue(argv, *, timeout=20, input=None):
            self.assertEqual(argv[:4], ["docker", "exec", "-i", "platform-id"])
            self.assertIn("issue", argv)
            self.assertEqual(input, request_frame({"entry_id": "config-entry"}))
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=240))
            return json.dumps(
                {
                    "assertion_ref": self.new_ref,
                    "expires_at": expiry.isoformat().replace("+00:00", "Z"),
                    "mode": "local_rehearsal",
                }
            ).encode()

        with (
            patch.object(
                linux_reauthorize, "_owned_platform", return_value="platform-id"
            ),
            patch.object(linux_reauthorize, "_run", side_effect=issue) as run,
        ):
            result = linux_reauthorize.execute(self.root)

        run.assert_called_once()
        self.assertEqual(result["status"], "reauthorized")
        self.assertFalse(result["ref_in_report"])
        self.assertNotIn(self.new_ref, json.dumps(result))
        self.assertIn(
            self.variable + "='" + self.new_ref + "'",
            (self.root / "private/gateway.env").read_text(),
        )
        verify_integrity(self.root)


if __name__ == "__main__":
    unittest.main()
