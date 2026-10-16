"""Non-target metadata equivalence must ignore ordering, never real changes."""

import copy
import json
import unittest

from deploy.infrastructure.resident_metadata import normalize_inventory


class ResidentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.before = [{"id": "a" * 64, "project": "other", "status": "running", "image": "sha256:unchanged", "restart": "always",
                        "mounts": (("bind", "/source-a", "/target-a", True), ("volume", "named-volume", "/target-b", False)),
                        "extra_observed_field": {"kept": True}}]

    def test_mount_order_and_tuple_json_roundtrip_are_equivalent_without_mutating_input(self):
        original = copy.deepcopy(self.before)
        after = json.loads(json.dumps(self.before))
        after[0]["mounts"].reverse()
        self.assertEqual(normalize_inventory(self.before), normalize_inventory(after))
        self.assertEqual(self.before, original)
        self.assertEqual(normalize_inventory(after)[0]["extra_observed_field"], {"kept": True})

    def test_each_mount_field_and_extra_field_remain_significant(self):
        for field, changed in enumerate(("volume", "/different-source", "/different-target", False)):
            after = json.loads(json.dumps(self.before))
            after[0]["mounts"][0][field] = changed
            with self.subTest(field=field):
                self.assertNotEqual(normalize_inventory(self.before), normalize_inventory(after))
        after = copy.deepcopy(self.before)
        after[0]["extra_observed_field"]["kept"] = False
        self.assertNotEqual(normalize_inventory(self.before), normalize_inventory(after))

    def test_running_state_image_identity_and_membership_remain_significant(self):
        for field, changed in (("status", "exited"), ("image", "sha256:changed"), ("id", "b" * 64), ("restart", "no")):
            after = copy.deepcopy(self.before)
            after[0][field] = changed
            with self.subTest(field=field):
                self.assertNotEqual(normalize_inventory(self.before), normalize_inventory(after))
        self.assertNotEqual(normalize_inventory(self.before), normalize_inventory([]))


if __name__ == "__main__":
    unittest.main()
