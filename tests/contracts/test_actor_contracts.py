"""Isolated actor contracts: real schema validation, local refs and immutable package bytes."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ('actor-self-memory', 'actor-capabilities')
RFC3339_PATTERN = (
    r'[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])'
    r'[Tt](?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]'
    r'(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])'
)
FORMATS = FormatChecker()


@FORMATS.checks('date-time')
def strict_datetime(value):
    """Validate real dates without relying on jsonschema's optional RFC3339 extra."""
    if not isinstance(value, str):
        return True
    if re.fullmatch(RFC3339_PATTERN, value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('t', 'T').replace('z', '+00:00').replace('Z', '+00:00'))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


class ActorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = {}
        for name in PACKAGES:
            directory = ROOT / 'contracts' / name / 'candidate-v1'
            schema = json.loads((directory/'schema.json').read_text(encoding='utf-8'))
            registry = Registry().with_resource(schema['$id'], Resource.from_contents(schema))
            cls.packages[name] = (directory, schema, registry)

    def validator(self, name, definition):
        _, schema, registry = self.packages[name]
        return Draft202012Validator(
            {'$ref': schema['$id']+'#/$defs/'+definition},
            registry=registry, format_checker=FORMATS)

    def examples(self, name):
        directory, _, _ = self.packages[name]
        return json.loads((directory/'examples.json').read_text(encoding='utf-8'))

    def test_schemas_and_manifest_hashes(self):
        for name, (directory, schema, _) in self.packages.items():
            with self.subTest(package=name):
                Draft202012Validator.check_schema(schema)
                manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(manifest['state'], 'isolated-development-candidate')
                self.assertEqual(set(manifest['sha256']), {'schema.json','examples.json','README.md'})
                for filename, expected in manifest['sha256'].items():
                    actual = hashlib.sha256((directory/filename).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
                    self.assertEqual(actual, expected, filename)

    def test_consumer_examples_and_negative_contracts(self):
        for name in PACKAGES:
            cases = self.examples(name)
            identifiers = [case['id'] for group in cases.values() for case in group]
            self.assertEqual(len(identifiers), len(set(identifiers)))
            for case in cases['positive']:
                with self.subTest(package=name,positive=case['id']):
                    self.validator(name,case['definition']).validate(case['document'])
            for case in cases['negative']:
                with self.subTest(package=name,negative=case['id']):
                    with self.assertRaises(ValidationError):
                        self.validator(name,case['definition']).validate(case['document'])

    def test_every_operation_has_a_consumable_example(self):
        for name in PACKAGES:
            _, schema, _ = self.packages[name]
            operations = {item['$ref'].rsplit('/',1)[1] for item in schema['$defs']['request']['oneOf']}
            examples = {case['definition']:case for case in self.examples(name)['positive']}
            self.assertLessEqual(operations,set(examples))
            for operation in operations:
                self.validator(name,'request').validate(examples[operation]['document'])

    def test_memory_response_envelope_covers_every_operation_result(self):
        cases = self.examples('actor-self-memory')['positive']
        definitions = {'command_receipt', 'get_episode_result', 'check_episode_result', 'error'}
        represented = set()
        for case in cases:
            if case['definition'] in definitions:
                with self.subTest(result=case['id']):
                    self.validator('actor-self-memory', 'response').validate(case['document'])
                represented.add(case['definition'])
        self.assertEqual(represented, definitions)

    def test_actor_append_cannot_supply_authoritative_facts(self):
        value = self.examples('actor-self-memory')['positive'][0]['document']
        for field in ('person_id','summary','outcome','provenance','audience','coverage',
                      'source_constraints','content_reads','allowed_operations'):
            with self.subTest(field=field):
                changed=copy.deepcopy(value)
                changed[field]='forged'
                with self.assertRaises(ValidationError):
                    self.validator('actor-self-memory','request').validate(changed)

    def test_disclosure_requires_an_explicit_audience_proof(self):
        for kind in ('private','group'):
            audience={'kind':kind,'conversation_id':'synthetic-conversation'}
            if kind=='private':
                audience['person_id']='person:synthetic'
            context={'purpose':'dialogue','audience':audience}
            with self.subTest(kind=kind):
                with self.assertRaises(ValidationError):
                    self.validator('actor-self-memory','context').validate(context)
                audience['assertion_ref']='synthetic-proof'
                self.validator('actor-self-memory','context').validate(context)

    def test_durable_constraints_do_not_accept_request_credentials(self):
        for kind in ('private', 'group'):
            constraints = {'kind': kind, 'conversation_id': 'synthetic-conversation'}
            if kind == 'private':
                constraints['person_id'] = 'person:synthetic'
            with self.subTest(kind=kind):
                self.validator('actor-self-memory', 'source_constraints').validate(constraints)
                constraints['assertion_ref'] = 'expired-request-proof'
                with self.assertRaises(ValidationError):
                    self.validator('actor-self-memory', 'source_constraints').validate(constraints)

    def test_check_binds_the_revision_used_by_the_consumer(self):
        cases = {case['id']: case for case in self.examples('actor-self-memory')['positive']}
        value = copy.deepcopy(cases['check-before-share']['document'])
        del value['episodes'][0]['expected_revision']
        with self.assertRaises(ValidationError):
            self.validator('actor-self-memory', 'check_episode').validate(value)
        result = copy.deepcopy(cases['episode-check-changed']['document'])
        result['items'][0]['disclosure_allowed'] = True
        with self.assertRaises(ValidationError):
            self.validator('actor-self-memory', 'check_episode_result').validate(result)

    def test_every_read_range_identifies_its_content_version(self):
        cases = {case['id']: case for case in self.examples('actor-self-memory')['positive']}
        fact = copy.deepcopy(cases['authority-partial-reading']['document'])
        del fact['content_reads'][0]['content_ref']
        with self.assertRaises(ValidationError):
            self.validator('actor-self-memory', 'source_fact').validate(fact)
        fact = copy.deepcopy(cases['authority-partial-reading']['document'])
        del fact['content_reads'][0]['content_ref']['content_hash']
        with self.assertRaises(ValidationError):
            self.validator('actor-self-memory', 'source_fact').validate(fact)

    def test_timestamps_have_timezone_and_valid_calendar_date(self):
        valid = ('2024-02-29T23:59:59Z', '2026-10-23T14:00:00.123+08:00')
        invalid = ('yesterday', '2026-02-30T12:00:00Z', '2026-02-29T12:00:00Z',
                   '2026-10-23T12:00:00', '2026-10-23 12:00:00Z',
                   '2026-10-23T24:00:00Z', '2026-10-23T12:00:00+24:00')
        for name in PACKAGES:
            validator = self.validator(name, 'timestamp')
            for value in valid:
                with self.subTest(package=name, valid=value):
                    validator.validate(value)
            for value in invalid:
                with self.subTest(package=name, invalid=value):
                    with self.assertRaises(ValidationError):
                        validator.validate(value)

    def test_unknown_write_outcome_is_not_directly_retryable(self):
        cases = {case['id']: case for case in self.examples('actor-self-memory')['positive']}
        original = cases['authority-unavailable']['document']
        for field in ('code', 'execution'):
            value = copy.deepcopy(original)
            value[field] = 'unknown'
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    self.validator('actor-self-memory', 'error').validate(value)
                value['retryable'] = False
                self.validator('actor-self-memory', 'error').validate(value)

    def test_actor_capability_cannot_override_execution_owner(self):
        value=self.examples('actor-capabilities')['positive'][1]['document']
        for field in ('principal','operator','cookie','token','account_id','workspace_id'):
            with self.subTest(field=field):
                changed=copy.deepcopy(value)
                changed[field]='forged'
                with self.assertRaises(ValidationError):
                    self.validator('actor-capabilities','request').validate(changed)

    def test_packages_do_not_require_person_identity_for_actor_work(self):
        for name in PACKAGES:
            for example in self.examples(name)['positive']:
                if example['definition'] in ('append_episode','invoke','discover'):
                    self.assertNotIn('person_id',example['document'])
                    self.validator(name,'request').validate(example['document'])


if __name__ == '__main__':
    unittest.main()
