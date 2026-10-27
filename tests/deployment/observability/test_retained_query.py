"""Retention applies before every log/metric pipeline, without huge UUID lists."""

import unittest
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

import helpers  # noqa: F401 - establish the standalone observer import path

# isort: split
from retained_query import PREFIX, constrain, query_path, window_filter


class RetainedQueryTests(unittest.TestCase):
    def test_all_real_selectors_are_filtered_but_strings_templates_and_comments_are_not(
        self,
    ):
        expression = ' |~ "RETENTION_MARKER" '
        original = (
            'sum(rate({stack="tianshu"} |= `literal {braces}` | line_format "{{.event}}" [5m])) '
            '+ sum(rate({stack="tianshu",service="tianshu_memory"}[5m]))'
        )
        changed = constrain(original, expression)
        self.assertEqual(changed.count("RETENTION_MARKER"), 2)
        self.assertIn("`literal {braces}`", changed)
        self.assertIn('"{{.event}}"', changed)
        self.assertEqual(
            constrain('# {ignored "quote}\n{stack="tianshu"}', expression).count(
                "RETENTION_MARKER"
            ),
            1,
        )
        self.assertEqual(constrain("vector(1)", expression), "vector(1)")

    def test_ten_thousand_normal_events_compress_to_five_ranges(self):
        rows = [
            (str(uuid.uuid4()), service, str(uuid.UUID(int=index + 1)), sequence, False)
            for index, service in enumerate(
                ("platform", "gateway", "memory", "memory-knowledge", "companion")
            )
            for sequence in range(1, 2001)
        ]
        expression = window_filter(rows)
        self.assertLess(len(expression), 2000)
        self.assertEqual(expression.count(" and ("), 5)
        self.assertNotIn(rows[0][0], expression)
        self.assertIn(
            f"| drop {PREFIX}instance,{PREFIX}sequence,{PREFIX}id", expression
        )
        path = query_path(
            "/loki/api/v1/query_range?"
            + urlencode(
                {"query": 'count_over_time({stack="tianshu"}[30d])', "limit": "5000"}
            ),
            expression,
        )
        self.assertEqual(parse_qs(urlsplit(path).query)["limit"], ["5000"])
        self.assertIn(expression, parse_qs(urlsplit(path).query)["query"][0])

    def test_gap_ranges_and_detected_sequence_conflicts_require_exact_accepted_id(self):
        instance = str(uuid.uuid4())
        accepted = str(uuid.uuid4())
        rows = [
            (str(uuid.uuid4()), "platform", instance, 1, False),
            (accepted, "platform", instance, 3, True),
            (str(uuid.uuid4()), "platform", instance, 5, False),
        ]
        expression = window_filter(rows)
        self.assertIn(f'{PREFIX}id="{accepted}"', expression)
        self.assertNotIn(f"{PREFIX}sequence >= 1 and {PREFIX}sequence <= 5", expression)
        self.assertNotIn(f"{PREFIX}sequence >= 2", expression)

    def test_empty_window_is_empty_and_metadata_keeps_its_physical_meaning(self):
        self.assertEqual(window_filter([]), ' |~ "a^" ')
        for path in (
            "/loki/api/v1/labels",
            "/loki/api/v1/index/stats?query=%7Bstack%3D%22tianshu%22%7D",
        ):
            self.assertEqual(query_path(path, window_filter([])), path)


if __name__ == "__main__":
    unittest.main()
