"""Intentional dependency updates stay tied to both immutable release inputs."""

import copy
import unittest

from deploy.infrastructure.bundle import Refused, digest
from deploy.infrastructure.image import DEPENDENCY_FILES, dependency_delta


class DependencyInputsTests(unittest.TestCase):
    def setUp(self):
        self.previous = {name: (name + " original").encode() for name in DEPENDENCY_FILES}
        self.source = {**self.previous, "pyproject.toml": b"new fixed book dependencies",
                       "scripts/build/runtime.lock": b"new hashed runtime wheels"}
        self.revisions = {"base_revision": "a" * 40, "revision": "b" * 40}
        self.manifest = {**self.revisions, "files": {
            name: {"before": digest(self.previous[name]), "after": digest(self.source[name])}
            for name in ("pyproject.toml", "scripts/build/runtime.lock")}}

    def test_original_policy_requires_unchanged_inputs(self):
        self.assertEqual(dependency_delta(self.previous, self.previous, **self.revisions), {})
        with self.assertRaises(Refused):
            dependency_delta(self.source, self.previous, **self.revisions)

    def test_exact_changes_are_recorded_and_stale_inputs_are_refused(self):
        self.assertEqual(dependency_delta(self.source, self.previous, **self.revisions,
                                         manifest=self.manifest), self.manifest["files"])
        for field in ("before", "after"):
            wrong = copy.deepcopy(self.manifest)
            wrong["files"]["pyproject.toml"][field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(Refused):
                dependency_delta(self.source, self.previous, **self.revisions, manifest=wrong)
        for key in self.revisions:
            wrong = {**self.manifest, key: "c" * 40}
            with self.subTest(key=key), self.assertRaises(Refused):
                dependency_delta(self.source, self.previous, **self.revisions, manifest=wrong)

    def test_manifest_cannot_hide_additional_changes_or_be_reused_for_other_scope(self):
        additional = {**self.source, "Dockerfile": b"unexpected recipe"}
        with self.assertRaises(Refused):
            dependency_delta(additional, self.previous, **self.revisions, manifest=self.manifest)
        with self.assertRaises(Refused):
            dependency_delta(self.previous, self.previous, **self.revisions, manifest=self.manifest)


if __name__ == "__main__":
    unittest.main()
