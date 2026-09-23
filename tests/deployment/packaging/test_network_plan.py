import unittest
import test_packaging as packaging  # noqa: F401
from network_plan import validate
from manifest import Refused


class NetworkPlanTests(unittest.TestCase):
    def test_overlap_public_and_wrong_names_rejected(self):
        for value in (
            {"x": "10.203.101.0/24"},
            {"egress": "8.8.8.0/24", "frontend": "10.203.102.0/24"},
            {"egress": "10.203.100.0/24", "frontend": "10.203.102.0/24"},
            {"egress": "10.203.101.0/24", "frontend": "10.203.101.0/24"},
        ):
            with self.subTest(value=value), self.assertRaises(Refused):
                validate(value, "10.203.100.0/24")

    def test_explicit_plan(self):
        value = {"egress": "10.203.101.0/24", "frontend": "10.203.102.0/24"}
        self.assertEqual(validate(value, "10.203.100.0/24"), value)
        self.assertIsNone(validate(None, "10.203.100.0/24"))
