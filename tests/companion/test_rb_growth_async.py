"""Models acceptance does not hold a Growth worker until inference completes."""

from datetime import UTC, datetime
from importlib.resources import files
import json

import pytest

from tianshu.companion.growth import GrowthService
from tianshu.companion.service import CompanionService
from tianshu.conversation.codec import signature
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.memory.types import Episode, EpisodePage
from tianshu.models.types import ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Coverage, OriginKind, SourceRef, TrustedContext

GENERATION = '00000000-0000-4000-8000-000000000001'


class Authority:
    async def check(self, *_args):
        return None

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)

    async def validate_persistence(self, *_args):
        return True

    async def refresh_scheduled_context(self, context, **_kwargs):
        return context


class Jobs:
    def __init__(self):
        self.items = []

    async def enqueue(self, _connection, spec, **_kwargs):
        self.items.append(spec)

    async def current_generation_on(self, _connection):
        return GENERATION

    async def assert_generation_on(self, _connection, generation):
        assert generation == GENERATION


class Memory:
    def __init__(self, episode, sources):
        self.episode, self.indexed, self.sources = episode, [], sources

    async def episodes_by_refs(self, _context, refs, *, connection=None):
        found = tuple(self.episode for identifier, version in refs
                      if (identifier, version) == (self.episode.episode_id, self.episode.revision))
        return EpisodePage(found, None, Coverage(len(found) == len(refs)), self.episode.cursor)

    async def lock_episodes(self, context, refs, *, connection, extra_source_refs=()):
        page = await self.episodes_by_refs(context, refs, connection=connection)
        if len(page.items) != len(refs):
            return page
        closure = tuple(extra_source_refs) + tuple(ref for episode in page.items
            for ref in (*episode.source_refs, *episode.supplement_refs))
        if not await self.sources.validate_persistence(connection, context, closure):
            return EpisodePage((), None, Coverage(False, missing=('unavailable_sources',)), 'e:0')
        return page

    async def relationship(self, *_args):
        return {'frozen': False}

    async def index_learning(self, _context, **kwargs):
        self.indexed.append(kwargs)


class Models:
    def __init__(self):
        self.submitted, self.results = [], {}

    async def submit(self, request, *, completion=None):
        self.submitted.append((request, completion))
        receipt = ModelReceipt(request.attempt_id, request.intent_id, 'queued', 'not_started',
                               'route', 1, 'provider', 'model', 'openai.chat', 'quota')
        self.results[request.attempt_id] = ModelResult((), receipt)
        return receipt

    async def generation_profile(self, request):
        return {'route_id': 'route', 'route_revision': 1, 'quota_group': 'quota'}

    async def result(self, attempt_id, *, context):
        return self.results.get(attempt_id)

    async def status(self, attempt_id):
        result = self.results.get(attempt_id)
        return result.receipt if result else None


class RecoveryPolicy:
    def __init__(self):
        self.reservations, self.continuations, self.successes = [], [], []

    async def register_on(self, _connection, **_kwargs):
        return {'lineage_id': 'lineage'}

    async def reserve_attempt_on(self, _connection, lineage_id, **kwargs):
        from types import SimpleNamespace
        self.reservations.append(kwargs)
        return SimpleNamespace(lineage_id=lineage_id, attempt_id=kwargs['attempt_id'],
                               number=1, status='reserved', probe_token=None)

    async def computation_budget_on(self, _connection, _lineage_id, **_kwargs):
        count = len(self.reservations)
        return {'total_attempts': count, 'computation_attempts': count, 'connection_attempts': 0,
                'max_computation_attempts': 4, 'max_connection_attempts': 16,
                'can_retry': count < 4, 'retry_at': None, 'dependency': 'models:quota:quota',
                'reason': 'not_confirmed_not_sent' if count < 4 else 'recovery_budget_exhausted'}

    async def note_not_sent_on(self, connection, _attempt_id, **_kwargs):
        return await self.computation_budget_on(connection, 'lineage')

    async def succeed_attempt_on(self, _connection, _attempt_id, **_kwargs):
        self.successes.append(_attempt_id)

    async def assert_attempt_applicable_on(self, _connection, _attempt_id, **_kwargs):
        return None

    async def claim_continuation_on(self, _connection, lineage_id, **kwargs):
        self.continuations.append((lineage_id, kwargs))
        return {'lineage_id': lineage_id, 'claim_token': 'continuation-token'}

    async def claim_due_on(self, *_args, **_kwargs):
        pytest.fail('a successful but invalid result must use a continuation, not a failure retry')


async def setup_growth(database):
    await migrate(database, tuple(Migration(name, files(package).joinpath(path).read_text())
        for name, package, path in (('0030_companion', 'tianshu.companion', 'schema.sql'),
            ('0032_conversation', 'tianshu.conversation', 'schema.sql'),
            ('0034_companion_growth_batches', 'tianshu.companion', 'growth_batches.sql'),
            ('0037_companion_growth_retry', 'tianshu.companion', 'growth_retry.sql'),
            ('0039_companion_learning', 'tianshu.companion', 'habit_revisions.sql'))))
    context = TrustedContext('p', 'actor', 'person', 'chat', ('scope',), ('scope',), 1,
                             OriginKind.HUMAN, permissions=frozenset({'companion.manage'}))
    episode = Episode('episode', 'event', 1, 'actor', OriginKind.HUMAN, datetime.now(UTC),
        '以后不要使用这个称呼', (SourceRef('original', 1),), ('scope',), None, Coverage(True), 1, 'e:1',
        person_id='person', speaker_id='person', conversation_id='chat')
    authority = Authority()
    memory, jobs, models = Memory(episode, authority), Jobs(), Models()
    companion = CompanionService(database, CommandStore(database), authority, memory,
        actor_registry=None, actor_changed=None, sources=authority, actor_activated=None)
    async def projection_changed(*_args, **_kwargs):
        return None
    growth = GrowthService(companion, jobs, models, ContextAssembler(authority, None, None),
                           on_projection_changed=projection_changed)
    policy = RecoveryPolicy()
    growth.set_observability(statistics=None, recovery_policy=policy)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO companion.actors(actor_id,name,enabled) VALUES('actor','角色',true)")
    await growth.changed(context, 'event:1', episode=episode, generation=GENERATION)
    key = signature(sorted(context.scope_ids))
    return growth, context, memory, models, policy, key


@pytest.mark.asyncio
async def test_submit_wakes_same_frozen_batch_and_commits_once(database):
    growth, context, memory, models, _policy, key = await setup_growth(database)
    waiting = await growth.organize('actor', key, 1, GENERATION)
    assert waiting['state'] == 'waiting' and len(models.submitted) == 1
    request, completion = models.submitted[0]
    assert completion.task == 'companion.organize' and completion.payload['actor_id'] == 'actor'
    assert request.data_generation == completion.data_generation == completion.payload['generation'] == GENERATION
    assert all(spec.data_generation == GENERATION for spec in growth.jobs.items)
    review = (await growth.view(context))['review']
    assert review['pending_original_requests'] == 1 and 'legacy_state' not in review

    note = {'kind': 'style', 'title': '称呼', 'content': '以后不要使用这个称呼',
        'episode_ids': ['e1'], 'conditions': {'when': ['称呼'], 'unless': []}, 'counterexamples': [],
        'affect': None, 'action': 'create', 'rule_ref': None, 'preference_kind': 'explicit',
        'validity': 'persistent', 'purpose': 'conversation'}
    receipt = ModelReceipt(request.attempt_id, request.intent_id, 'succeeded', 'submitted',
                           'route', 1, 'provider', 'model', 'openai.chat', 'quota')
    models.results[request.attempt_id] = ModelResult((ModelPart('text', text=json.dumps({'notes': [note]}, ensure_ascii=False)),), receipt)
    reviewed = await growth.organize('actor', key, 1, GENERATION)
    assert reviewed['state'] == 'reviewed' and len(models.submitted) == 1
    assert len(memory.indexed) == 1 and memory.indexed[0]['kind'] == 'style'
    assert '以后不要使用这个称呼' in memory.indexed[0]['search_text']
    assert len(await growth.material(context, query='其他话题')) == 1
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute('SELECT state,revision,semantic_revision,support_count FROM companion.style_rules')).fetchone()
    assert row == {'state': 'active', 'revision': 1, 'semantic_revision': 1, 'support_count': 1}
    assert (await growth.view(context))['review']['pending_original_requests'] == 0


@pytest.mark.asyncio
async def test_bad_json_keeps_success_receipt_and_uses_one_bounded_continuation(database):
    growth, _context, _memory, models, policy, key = await setup_growth(database)
    await growth.organize('actor', key, 1, GENERATION)
    original, _completion = models.submitted[0]
    receipt = ModelReceipt(original.attempt_id, original.intent_id, 'succeeded', 'submitted',
                           'route', 1, 'provider', 'model', 'openai.chat', 'quota')
    models.results[original.attempt_id] = ModelResult((ModelPart('text', text='{'),), receipt)
    outcome = await growth.organize('actor', key, 1, GENERATION)
    assert outcome['state'] == 'rejected' and policy.successes == [original.attempt_id]
    await growth.retries.run('actor', key, outcome['retry_key'], GENERATION)
    assert len(models.submitted) == 2
    assert policy.continuations[0][1]['prior_attempt_id'] == original.attempt_id
    assert policy.continuations[0][1]['reason'] == 'schema_correction'
    assert policy.reservations[-1]['original_state'] == 'succeeded'
    assert policy.reservations[-1]['claim_token'] == 'continuation-token'
    assert models.submitted[1][0].attempt_id != original.attempt_id
    assert models.submitted[1][0].data_generation == models.submitted[1][1].data_generation == GENERATION


@pytest.mark.asyncio
async def test_stale_generation_does_not_start_an_attempt(database):
    growth, _context, _memory, models, _policy, key = await setup_growth(database)
    with pytest.raises(AssertionError):
        await growth.organize('actor', key, 1, '00000000-0000-4000-8000-000000000099')
    assert not models.submitted


@pytest.mark.asyncio
async def test_authorization_resume_observes_the_same_current_attempt(database):
    growth, context, _memory, models, _policy, key = await setup_growth(database)
    await growth.organize('actor', key, 1, GENERATION)
    original = models.submitted[0][0]
    async with database.transaction(read_only=True) as connection:
        state = await (await connection.execute(
            'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s', ('actor', key))).fetchone()
    blocked = await growth.recovery.block(state, AppError('access_changed', status=403))
    assert blocked['recovery'] == 'observe_original_attempt'
    command = await growth.request_review(context, {'client_request_id': 'resume-same-attempt',
                                                     'expected_revision': state['revision']})
    assert command.data['recovery'] == 'observe_original_attempt'
    async with database.transaction(read_only=True) as connection:
        resumed = await (await connection.execute(
            'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s', ('actor', key))).fetchone()
    assert resumed['active_batch']['material_key'] == state['active_batch']['material_key']
    assert resumed['blocked_material']['recovery'] == 'observe_original_attempt'
    await growth.organize('actor', key, resumed['revision'], GENERATION)
    assert len(models.submitted) == 1 and models.submitted[0][0].attempt_id == original.attempt_id
