"""Reading carries the real source revision and stops at actual source coverage."""

from unittest import TestCase

from tianshu.capabilities.content.continuation import continuation_step
from tianshu.life.reading import ReadingService
from tianshu.types import Coverage


class ReadingContinuation(TestCase):
    def test_receipt_carries_revision_source_and_exact_next_position(self):
        row = {"id": "reading", "source_ref": {"source_id": "text", "revision": 4}}
        fetched = {"content": "actual excerpt", "coverage": Coverage(False, 100,
            ranges=({"start": 10, "end": 30},)), "next_position": 30}
        value = ReadingService._result(row, fetched, {"position": 10, "limit": 20}, 5, "reading")
        self.assertFalse(value["analysis_completed"])
        self.assertEqual(value["source_ref"], row["source_ref"])
        next_step = continuation_step("life.reading", {}, {"reading": value})
        self.assertEqual(next_step["arguments"], {"operation": "read", "id": "reading",
            "expected_revision": 5, "requested": {"position": 30, "limit": 20}})

    def test_last_excerpt_does_not_manufacture_more_reading(self):
        value = ReadingService._result({"id": "reading", "source_ref": {"source_id": "text", "revision": 1}},
            {"content": "end", "coverage": Coverage(True, 3), "next_position": None}, {}, 2, "completed")
        self.assertIsNone(continuation_step("life.reading", {}, {"reading": value}))
