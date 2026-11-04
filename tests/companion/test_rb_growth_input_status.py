"""Per-input status uses original evidence and explicit revision links."""

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from types import SimpleNamespace

import pytest

from tianshu.companion.growth_input_status import GrowthInputStatus
from tianshu.companion.habits import HabitLedger
from tianshu.companion.reflection_output import FrozenEpisode, PreparedReflection
from tianshu.companion.types import ReflectionProposal
from tianshu.conversation.codec import document, signature
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, SourceRef, TrustedContext


GENERATION = '00000000-0000-4000-8000-000000000001'


class Identity:
    async def check(self, _context, *_refs):
        return None

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)


class Sources:
    def __init__(self, events):
        self.events = events
        self.denied = set()

    async def describe_for_write_on(self, _connection, _context, refs):
        if any(ref.source_id in self.denied for ref in refs):
            raise AppError('source_unavailable', status=403)
        return tuple({'source_id': ref.source_id, 'revision': ref.revision,
                      'locator': {'event_id': self.events[ref.source_id]}} for ref in refs)

    async def validate_persistence(self, _connection, _context, refs):
        return all(ref.source_id not in self.denied for ref in refs)


class Memory:
    def __init__(self, episodes):
        self.episodes = episodes

    async def episode_at(self, _context, episode_id):
        if episode_id not in self.episodes:
            raise AppError('episode_unavailable', status=404)
        return self.episodes[episode_id]

    async def lock_episodes(self, _context, refs, *, connection):
        items = tuple(self.episodes[identifier] for identifier, revision in refs
                      if identifier in self.episodes and self.episodes[identifier].revision == revision)
        return SimpleNamespace(items=items)


class Jobs:
    async def current_generation_on(self, connection):
        row = await (await connection.execute(
            'SELECT id FROM runtime.installation_generation WHERE singleton=true')).fetchone()
        return row['id']

    async def assert_generation_on(self, connection, generation):
        row = await (await connection.execute(
            'SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE')).fetchone()
        assert row['id'] == generation


def episode(input_id, event_id, source, *, revision=1, conversation='chat'):
    return SimpleNamespace(episode_id='input:'+input_id, event_id=event_id, revision=revision,
        actor_id='actor', conversation_id=conversation, person_id='person',
        source_refs=(source,), supplement_refs=())


async def setup(database):
    await migrate(database, tuple(Migration(name, files('tianshu.companion').joinpath(path).read_text())
        for name, path in (('0030_companion', 'schema.sql'), ('0034_companion_growth_batches', 'growth_batches.sql'),
            ('0037_companion_growth_retry', 'growth_retry.sql'), ('0039_companion_learning', 'habit_revisions.sql'),
            ('0040_companion_remove_legacy_growth', 'growth_remove_legacy.sql'))))
    context = TrustedContext('p', 'actor', 'person', 'chat', ('scope',), ('scope',), 1,
                             OriginKind.HUMAN, conversation_kind='private')
    async with database.transaction() as connection:
        await connection.execute('CREATE TABLE IF NOT EXISTS runtime.installation_generation '
            '(singleton boolean PRIMARY KEY DEFAULT true, id text NOT NULL)')
        await connection.execute('INSERT INTO runtime.installation_generation(singleton,id) VALUES(true,%s) '
                                 'ON CONFLICT(singleton) DO UPDATE SET id=excluded.id', (GENERATION,))
        await connection.execute("INSERT INTO companion.actors(actor_id,name,enabled) VALUES('actor','角色',true)")
        await connection.execute('INSERT INTO companion.organize_state(actor_id,scope_key,context,runtime_generation) '
            'VALUES(%s,%s,%s,%s)', ('actor', signature(sorted(context.scope_ids)), document(context), GENERATION))
    sources = Sources({'source-a': 'event-a', 'source-b': 'event-b', 'source-c': 'event-c',
                       'source-d': 'event-d', 'source-e': 'event-e'})
    episodes = {
        'input:a': episode('a', 'event-a', SourceRef('source-a', 1), revision=2),
        'input:b': episode('b', 'event-b', SourceRef('source-b', 1)),
        'input:c': episode('c', 'event-c', SourceRef('source-c', 1)),
        'input:d': episode('d', 'event-d', SourceRef('source-d', 1)),
        'input:e': episode('e', 'event-e', SourceRef('source-e', 1)),
    }
    companion = SimpleNamespace(database=database, identity=Identity(), sources=sources, memory=Memory(episodes))
    return companion, context


async def input_row(connection, context, identifier, *, revision=1, reviewed=0, state='pending', missing=(), retry_key=None):
    await connection.execute(
        'INSERT INTO companion.growth_inputs(actor_id,scope_key,episode_id,revision,sequence,reviewed_revision,state,missing,context,retry_key) '
        'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        ('actor', signature(sorted(context.scope_ids)), 'input:'+identifier, revision, revision,
         reviewed, state, list(missing), document(context), retry_key))


@pytest.mark.asyncio
async def test_reviewed_input_is_not_a_revision_without_its_exact_proposal_event(database):
    companion, context = await setup(database)
    at = datetime.now(UTC)
    source_a, source_b = SourceRef('source-a', 1), SourceRef('source-b', 1)
    original = FrozenEpisode('input:a', 2, at, (source_a,), 2, 'e:2', 'event-a', 'human', 'person')
    proposal = ReflectionProposal('create', 'actor', ('input:a',), '旧称呼', 'style', at,
        (source_a,), ('scope',), conditions={'when': [], 'unless': []}, counterexamples=())
    create = PreparedReflection('称呼', proposal, None, ('input:a',), 'create', None, 'explicit')
    ledger = HabitLedger(companion)
    async with database.transaction() as connection:
        await input_row(connection, context, 'a', revision=2, reviewed=2, state='reviewed')
        await input_row(connection, context, 'b', reviewed=1, state='reviewed')
        await input_row(connection, context, 'c')
        rule = await ledger.apply(connection, context, create, {'input:a': original}, attempt_id='attempt-create')
        correction = FrozenEpisode('input:b', 1, at, (source_b,), 1, 'e:1', 'event-b', 'human', 'person')
        update = ReflectionProposal('proposal-revise', 'actor', ('input:a', 'input:b'), '新称呼', 'style', at,
            (source_a, source_b), ('scope',), conditions={'when': [], 'unless': []}, counterexamples=())
        item = PreparedReflection('称呼', update, None, ('input:b',), 'revise',
                                  {'rule_id': rule['id'], 'semantic_revision': rule['semantic_revision']}, 'explicit')
        await ledger.apply(connection, context, item, {'input:a': original, 'input:b': correction}, attempt_id='attempt-revise')
    inputs = tuple({'input_id': key, 'event_id': 'event-'+key, 'event_revision': 1} for key in ('a', 'b', 'c'))
    a, b, c = await GrowthInputStatus(companion, Jobs()).read(context, inputs=inputs)
    assert (a['state'], a['revision_state'], a['episode_revision']) == ('reviewed', 'not_recorded', 2)
    assert b['state'] == 'reviewed' and b['revision_state'] == 'applied'
    assert b['revision_evidence'] == ({'rule_id': 'create', 'revision': 2, 'semantic_revision': 2,
        'action': 'revise', 'proposal_id': 'proposal-revise', 'attempt_id': 'attempt-revise'},)
    assert c['state'] == 'pending' and c['revision_state'] == 'not_recorded'
    # Event revision is the original Source version, not the supplemented Episode revision.
    mismatch = await GrowthInputStatus(companion, Jobs()).read(context,
        inputs=({'input_id': 'a', 'event_id': 'event-a', 'event_revision': 2},))
    assert mismatch[0]['state'] == 'unavailable' and not mismatch[0]['revision_evidence']
    companion.sources.denied.add('source-a')
    # A rule's entire derivation must still be authorized, even when its selected support is readable.
    hidden = await GrowthInputStatus(companion, Jobs()).read(context, inputs=(inputs[1],))
    assert hidden[0]['state'] == 'reviewed' and hidden[0]['revision_state'] == 'not_recorded'
    assert not hidden[0]['revision_evidence']
    withdrawn = await GrowthInputStatus(companion, Jobs()).read(context, inputs=(inputs[0],))
    assert withdrawn[0]['state'] == 'unavailable' and not withdrawn[0]['source_refs']
    other_scene = await GrowthInputStatus(companion, Jobs()).read(replace(context, conversation_id='other'), inputs=(inputs[1],))
    assert other_scene[0]['state'] == 'not_recorded' and not other_scene[0]['source_refs']
    other_scope = await GrowthInputStatus(companion, Jobs()).read(replace(context, scope_ids=('other',)), inputs=(inputs[1],))
    assert other_scope[0]['state'] == 'not_recorded' and not other_scope[0]['revision_evidence']


@pytest.mark.asyncio
async def test_unavailable_and_unresolved_inputs_do_not_claim_success(database):
    companion, context = await setup(database)
    async with database.transaction() as connection:
        await input_row(connection, context, 'a', revision=1)
        await input_row(connection, context, 'b', reviewed=1, state='reviewed', missing=('learning_identity_unresolved',))
        await input_row(connection, context, 'c', reviewed=1, state='unavailable', missing=('unavailable_sources',))
        await input_row(connection, context, 'd', retry_key='failure-d')
        await input_row(connection, context, 'e', retry_key='retry-e')
        scope = signature(sorted(context.scope_ids))
        await connection.execute(
            'INSERT INTO companion.growth_material_failures(actor_id,scope_key,episode_id,episode_revision,failure_key,'
            'runtime_generation,error_code,details,context) VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s)',
            ('actor', scope, 'input:d', 'failure-d', GENERATION, 'input_failed', document({}), document(context)))
        await connection.execute(
            'INSERT INTO companion.growth_retry_batches(actor_id,scope_key,retry_key,batch,current_material_key,state,waiting_on_original) '
            "VALUES(%s,%s,%s,%s,%s,'blocked',true)", ('actor', scope, 'retry-e', document({}), 'original'))
    values = await GrowthInputStatus(companion, Jobs()).read(context, inputs=(
        {'input_id': 'a', 'event_id': 'event-a', 'event_revision': 1},
        {'input_id': 'b', 'event_id': 'event-b', 'event_revision': 1},
        {'input_id': 'c', 'event_id': 'event-c', 'event_revision': 1},
        {'input_id': 'd', 'event_id': 'event-d', 'event_revision': 1},
        {'input_id': 'e', 'event_id': 'event-e', 'event_revision': 1},
        {'input_id': 'missing', 'event_id': 'missing', 'event_revision': 1}))
    assert [(item['state'], item['revision_state']) for item in values] == [
        ('pending', 'not_recorded'), ('reviewed', 'unresolved'),
        ('unavailable', 'not_recorded'), ('failed', 'not_recorded'),
        ('pending', 'not_recorded'), ('not_recorded', 'not_recorded')]
    assert values[1]['coverage_missing'] == ('learning_identity_unresolved',)
    assert values[4]['reason'] == 'original_attempt_unresolved'
    async with database.transaction() as connection:
        await connection.execute("UPDATE runtime.installation_generation SET id='00000000-0000-4000-8000-000000000099'")
    stale = await GrowthInputStatus(companion, Jobs()).read(context, inputs=(
        {'input_id': 'b', 'event_id': 'event-b', 'event_revision': 1},))
    assert stale[0]['state'] == 'not_recorded' and not stale[0]['revision_evidence']


@pytest.mark.asyncio
async def test_retired_inventory_cleanup_keeps_current_inputs_and_attempts(database):
    _companion, context = await setup(database)
    scope = signature(sorted(context.scope_ids))
    async with database.transaction() as connection:
        await input_row(connection, context, 'a')
        await connection.execute("UPDATE companion.organize_state SET active_batch=%s WHERE actor_id='actor'",
                                 (document({'material_key': 'current-attempt'}),))
        await connection.execute('INSERT INTO companion.growth_retry_batches(actor_id,scope_key,retry_key,batch,'
            "current_material_key,state) VALUES(%s,%s,%s,%s,%s,'waiting')",
            ('actor', scope, 'current-retry', document({'runtime_generation': GENERATION}), 'current-attempt'))
        await connection.execute("ALTER TABLE companion.organize_state ADD COLUMN legacy_state text, "
            "ADD COLUMN legacy_request_cursor text, ADD COLUMN legacy_episode_cursor text, "
            "ADD COLUMN legacy_missing text[], ADD COLUMN legacy_started_at timestamptz")
        await connection.execute('CREATE TABLE companion.growth_legacy_requests(material_key text)')
        await connection.execute("INSERT INTO companion.growth_legacy_requests VALUES('old-attempt')")
        await connection.execute(files('tianshu.companion').joinpath('growth_remove_legacy.sql').read_text(), prepare=False)
        schema = await (await connection.execute(
            "SELECT to_regclass('companion.growth_legacy_requests') AS old_table, "
            "(SELECT count(*) FROM information_schema.columns WHERE table_schema='companion' "
            "AND table_name='organize_state' AND column_name LIKE 'legacy_%') AS old_columns, "
            "(SELECT count(*) FROM companion.growth_inputs) AS current_inputs, "
            "(SELECT count(*) FROM companion.growth_retry_batches) AS current_retries, "
            "(SELECT active_batch->>'material_key' FROM companion.organize_state WHERE actor_id='actor') AS active")).fetchone()
    assert schema == {'old_table': None, 'old_columns': 0, 'current_inputs': 1,
                      'current_retries': 1, 'active': 'current-attempt'}
