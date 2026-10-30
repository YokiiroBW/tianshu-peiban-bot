"""Producer/collector joint fixtures for the optional Companion event extension."""

import copy
import hashlib
import json
import os
from pathlib import Path
import unittest

from helpers import SNAPSHOT, event
from configs import vector
from policy import InvalidEvent, Policy
from snapshot import extract


class PeerAttributesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = os.environ.get("PC01_COMPANION_SOURCE")
        if not source:
            raise unittest.SkipTest(
                "PC01_COMPANION_SOURCE required for producer/collector joint check"
            )
        cls.snapshot = copy.deepcopy(SNAPSHOT)
        cls.snapshot["products"]["companion"].update(
            extract(Path(source).read_text("utf-8"), "companion")
        )
        cls.legacy_snapshot = copy.deepcopy(SNAPSHOT)
        cls.legacy_snapshot["products"]["companion"].pop("peer_attribute_schema", None)

    def sample(self):
        value = event(service="companion")
        value.update(
            schema_version="1.1.0",
            event="peer.call.finished",
            outcome="failed",
            error_code="forbidden",
            attributes=dict(
                target="platform",
                route="/internal/v1/actor-self-memory/authority",
                principal="sha256:" + "a" * 64,
                operation="query",
                http_status=403,
                error_code="forbidden",
                attempt=1,
            ),
        )
        return value

    def test_companion_encoder_and_collector_accept_same_closed_shape(self):
        from tianshu_companion import observability

        value = self.sample()
        raw = observability.encode_record(value)
        self.assertEqual(Policy(self.snapshot).line(raw), value)
        schema = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "contracts/diagnostics/v1.1/event.schema.json"
            ).read_text("utf-8")
        )
        directory = Path(__file__).resolve().parents[3] / "contracts/diagnostics/v1.1"
        manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
        for name, expected in manifest["files"].items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), expected)
        from jsonschema import Draft202012Validator

        Draft202012Validator(schema).validate(value)
        # Existing v1 events and registries do not need an extension.
        old = event()
        self.assertEqual(Policy(SNAPSHOT).validate(old), old)

    def test_unknown_fields_secret_routes_wrong_version_and_error_mismatch_are_rejected(
        self,
    ):
        for change in (
            lambda v: v["attributes"].update(body="secret-canary"),
            lambda v: v["attributes"].update(route="/secret-canary"),
            lambda v: v["attributes"].update(principal="secret-canary"),
            lambda v: v["attributes"].update(http_status=True),
            lambda v: v["attributes"].update(attempt=9),
            lambda v: v.update(service="memory"),
            lambda v: v.update(schema_version="1.0.0"),
            lambda v: v.update(error_code="timeout"),
        ):
            value = self.sample()
            change(value)
            with self.subTest(value=value), self.assertRaises(InvalidEvent):
                Policy(self.snapshot).validate(value)
        with self.assertRaises(InvalidEvent):
            Policy(self.legacy_snapshot).validate(self.sample())

    def test_vector_uses_producer_attribute_registry_and_denies_old_unknown_extensions(
        self,
    ):
        generated = vector(self.snapshot)["transforms"]["companion_safe"]["source"]
        self.assertIn("exists(.attributes.route)", generated)
        self.assertIn("/internal/v1/actor-self-memory/authority", generated)
        self.assertIn("sha256:", generated)
        old = vector(self.legacy_snapshot)["transforms"]["companion_safe"]["source"]
        self.assertIn("!exists(.attributes)", old)


if __name__ == "__main__":
    unittest.main()
