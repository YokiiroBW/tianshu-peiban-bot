"""Verify the additive wire contract without changing established package pins."""
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[2] / 'contracts'
PACKAGE = ROOT / 'life-autonomy/v1'


class LifeAutonomyContractTests(unittest.TestCase):
    def test_examples_and_package(self):
        manifest = json.loads((PACKAGE / 'manifest.json').read_text(encoding='utf-8'))
        for name, expected in manifest['sha256'].items():
            raw = (PACKAGE / name).read_bytes().replace(b'\r\n', b'\n')
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected, name)
        dependency = (ROOT / 'text-dialogue/v1/manifest.json').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(hashlib.sha256(dependency).hexdigest(), manifest['dependencies']['text-dialogue/v1'])
        schema = json.loads((PACKAGE / 'schemas/autonomy.json').read_text(encoding='utf-8'))
        common = json.loads((ROOT / 'text-dialogue/v1/schemas/common.json').read_text(encoding='utf-8'))
        Draft202012Validator.check_schema(schema)
        registry = Registry().with_resources([
            (document['$id'], Resource.from_contents(document)) for document in (schema, common)
        ])
        examples = json.loads((PACKAGE / 'examples.json').read_text(encoding='utf-8'))
        for group, expected in (('valid', True), ('invalid', False)):
            for case in examples[group]:
                validator = Draft202012Validator(
                    {'$ref': schema['$id'] + '#/$defs/' + case['definition']},
                    registry=registry, format_checker=FormatChecker(),
                )
                with self.subTest(group=group, case=case):
                    self.assertEqual(validator.is_valid(case['value']), expected)


if __name__ == '__main__':
    unittest.main()
