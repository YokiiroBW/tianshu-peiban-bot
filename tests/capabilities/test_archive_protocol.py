"""Exact Archive proof regressions staged for integrated acceptance."""

import unittest
from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.resources.archive_protocol import unique_records, current_records


class ArchiveProtocol(unittest.TestCase):
    def test_digest_is_the_archive_proof_not_a_local_text_hash(self):
        rows = [
            {"locator": "one", "stored_content_sha256": "opaque-archive-hash", "text": "真实原文"},
            {"locator": "two", "stored_content_sha256": "older", "text": "旧原文"},
        ]
        result = current_records(
            rows,
            {
                "results": [
                    {
                        "locator": "one",
                        "status": "archive_current",
                        "current_stored_content_sha256": "opaque-archive-hash",
                    },
                    {"locator": "two", "status": "changed", "current_stored_content_sha256": "new"},
                ]
            },
        )
        self.assertEqual(result, [rows[0]])

    def test_partial_currentness_response_cannot_silently_authorize_missing_proof(self):
        with self.assertRaises(Fault):
            current_records(
                [{"locator": "missing", "stored_content_sha256": "opaque"}], {"results": []}
            )

    def test_conflicting_overlap_is_rejected_before_source_intake(self):
        response = {
            "records": [
                {"locator": "same", "text": "first"},
                {"locator": "same", "text": "changed"},
            ],
            "coverage": {"returned": 2},
        }
        with self.assertRaises(Fault):
            unique_records("search", response, {"limit": 20})

    def test_read_locator_does_not_accept_a_different_target(self):
        response = {
            "status": "archive_current",
            "target": {"locator": "other", "stored_content_sha256": "opaque"},
            "context": {"before": [], "after": []},
        }
        with self.assertRaises(Fault):
            unique_records(
                "read_locator",
                response,
                {
                    "locator": "requested",
                    "expected_stored_content_sha256": "opaque",
                    "before": 2,
                    "after": 2,
                },
            )
