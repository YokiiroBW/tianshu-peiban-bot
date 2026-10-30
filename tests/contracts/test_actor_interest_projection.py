"""The optional ledger minor package preserves old consumers and closes new payloads."""

import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "contracts/actor-self-memory/v1.1"
FORMATS = FormatChecker()


@FORMATS.checks("date-time")
def calendar_datetime(value):
    # jsonschema's RFC3339 extra is optional; exercise calendar semantics here too.
    if not isinstance(value, str):
        return True
    try:
        parsed = datetime.fromisoformat(
            value.replace("t", "T").replace("z", "+00:00").replace("Z", "+00:00")
        )
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


class ActorInterestContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((PACKAGE / "schema.json").read_bytes())

    def validator(self, name):
        return Draft202012Validator(
            {**self.schema, "$ref": "#/$defs/" + name}, format_checker=FORMATS
        )

    def test_immutable_candidate_and_new_manifest_contents(self):
        old = ROOT / "contracts/actor-self-memory/candidate-v1/manifest.json"
        self.assertEqual(
            hashlib.sha256(old.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "34fec40dbf16ab0109026dd721873e238e5a6fecd86ad3d58495e99103d9b3e5",
        )
        manifest = json.loads((PACKAGE / "manifest.json").read_bytes())
        for name, expected in manifest["sha256"].items():
            self.assertEqual(
                hashlib.sha256(
                    (PACKAGE / name).read_bytes().replace(b"\r\n", b"\n")
                ).hexdigest(),
                expected,
            )

    def test_all_original_positive_and_negative_examples_keep_their_verdict(self):
        examples = json.loads((PACKAGE / "examples.json").read_bytes())
        for kind, rows in examples.items():
            for row in rows:
                with self.subTest(kind=kind, case=row["id"]):
                    self.assertEqual(
                        self.validator(row["definition"]).is_valid(row["document"]),
                        kind == "positive",
                    )

    def test_opt_in_request_is_additive_and_rejects_profile_or_arbitrary_fields(self):
        request = dict(
            schema_version=1,
            request_id="interests:one",
            actor_id="actor:a",
            role_version=1,
            operation="interests",
            context=dict(purpose="plan", audience=dict(kind="actor_only")),
            limit=8,
            budget_bytes=8192,
        )
        self.validator("interests").validate(request)
        self.validator("interests").validate(dict(request, projection="ledger"))
        self.assertFalse(
            self.validator("interests").is_valid(dict(request, projection="profile"))
        )
        self.assertFalse(
            self.validator("interests").is_valid(dict(request, user_profile={}))
        )

    def test_default_and_extended_response_shapes_cannot_be_confused(self):
        ref = dict(kind="derived", id="preference:one", revision=1)
        source = dict(
            producer="companion",
            object_id="reading:one",
            revision="1",
            content_hash="a" * 64,
        )
        item = dict(
            ref=ref,
            kind="preference",
            text="Actual supported interest",
            episode_refs=[dict(episode_id="episode:one", expected_revision=1)],
            source_refs=[source],
            recorded_at="2026-10-30T00:00:00Z",
            expires_at=None,
            topic="astronomy",
            stance="curious",
            support_count=1,
            history=[],
            history_omitted=False,
        )
        response = dict(
            schema_version=1,
            request_id="interests:one",
            actor_id="actor:a",
            role_version=1,
            operation="interests",
            domain_version=1,
            checked_at="2026-10-30T00:00:00Z",
            context=dict(purpose="plan", audience=dict(kind="actor_only")),
            items=[item],
            omitted=False,
        )
        self.validator("interests_result").validate(response)
        extended = copy.deepcopy(response)
        extended["items"][0].update(
            strength=0.2,
            evidence_count=1,
            last_touch="2026-10-30T00:00:00Z",
            valence=0.0,
            memory_refs=[ref],
            evidence_incomplete=False,
        )
        self.assertFalse(self.validator("interests_result").is_valid(extended))
        extended["projection"] = "ledger"
        self.validator("interests_result").validate(extended)
        extended["items"][0]["strength"] = 1.01
        self.assertFalse(self.validator("interests_result").is_valid(extended))


if __name__ == "__main__":
    unittest.main()
