"""Bounded history and topic candidates share actual producer/consumer documents."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from test_actor_contracts import FORMATS

ROOT = Path(__file__).resolve().parents[2] / 'contracts'


class MemoryExtensionContracts(unittest.TestCase):
    def package(self, name):
        root = ROOT / name / 'candidate-v1'
        schema = json.loads((root / 'schema.json').read_text(encoding='utf-8'))
        common = json.loads((ROOT / 'text-dialogue/v1/schemas/common.json').read_text(encoding='utf-8'))
        registry = Registry().with_resources([(s['$id'], Resource.from_contents(s)) for s in (schema, common)])
        examples = json.loads((root / 'examples.json').read_text(encoding='utf-8'))
        return root, schema, registry, examples

    def validate(self, name, definition, value):
        _, schema, registry, _ = self.package(name)
        Draft202012Validator({'$ref': schema['$id'] + '#/$defs/' + definition}, registry=registry, format_checker=FORMATS).validate(value)

    def test_every_manifest_byte_and_positive_negative_example(self):
        for name in ('actor-self-memory', 'chat-history', 'group-memory-projection', 'archive-memory-check'):
            root, schema, registry, examples = self.package(name)
            Draft202012Validator.check_schema(schema)
            for file, expected in json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['sha256'].items():
                self.assertEqual(hashlib.sha256((root / file).read_bytes().replace(b'\r\n', b'\n')).hexdigest(), expected, (name, file))
            for group, cases in examples.items():
                for case in cases:
                    with self.subTest(package=name, group=group, example=case['id']):
                        validator = Draft202012Validator({'$ref': schema['$id'] + '#/$defs/' + case['definition']}, registry=registry, format_checker=FORMATS)
                        if group == 'positive':
                            validator.validate(case['document'])
                        else:
                            self.assertFalse(validator.is_valid(case['document']))

    def test_archive_current_requires_a_real_hash_changed_may_be_unknown(self):
        _, _, _, examples = self.package('chat-history')
        value = copy.deepcopy(next(c['document'] for c in examples['positive'] if c['id'] == 'check_response')['results'][0])
        value['current_stored_content_sha256'] = None
        with self.assertRaises(ValidationError):
            self.validate('chat-history', 'check_result', value)
        value['status'] = 'changed'
        self.validate('chat-history', 'check_result', value)

    def test_derived_negative_cannot_remain_readable_or_replace_episode(self):
        value = {'ref': {'kind': 'derived', 'id': 'memory:example', 'revision': 1}, 'current_revision': 2, 'state': 'changed', 'read_allowed': True, 'disclosure_allowed': False}
        with self.assertRaises(ValidationError):
            self.validate('actor-self-memory', 'memory_check_item', value)
        value['read_allowed'] = False
        self.validate('actor-self-memory', 'memory_check_item', value)

    def test_topic_projection_cannot_leak_raw_material_or_merge_bot_with_person(self):
        _, _, _, examples = self.package('group-memory-projection')
        response = copy.deepcopy(next(c['document'] for c in examples['positive'] if c['definition'] == 'response'))
        response['items'][0]['topics'][0]['member_refs'][0]['actor_id'] = 'actor:foreign'
        with self.assertRaises(ValidationError):
            self.validate('group-memory-projection', 'response', response)
        response['items'][0]['topics'][0]['member_refs'][0]['actor_id'] = None
        response['items'][0]['topics'][0]['member_refs'][0]['raw_text'] = 'not a browser projection field'
        with self.assertRaises(ValidationError):
            self.validate('group-memory-projection', 'response', response)
