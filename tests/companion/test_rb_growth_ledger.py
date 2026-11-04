"""Behavioral regressions for the Companion learning owner."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import pytest

from tianshu.companion.dependencies import CompanionDependencies
from tianshu.companion.behavior_resolver import BehaviorResolver
from tianshu.companion.expression_examples import ExpressionExamples
from tianshu.companion.habits import HabitLedger, expiry
from tianshu.companion.reflection_output import FrozenEpisode, PreparedReflection
from tianshu.companion.types import ReflectionProposal
from tianshu.conversation.codec import document
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, SourceRef, TrustedContext


class Identity:
    async def check(self, _context, *_args):
        return None

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)


class Companion:
    def __init__(self, database):
        self.database, self.identity = database, Identity()
        self.commands = CommandStore(database)

    async def validate_persistence(self, _connection, _context, _refs=()):
        return None


async def setup_ledger(database):
    await migrate(database, tuple(Migration(name, files('tianshu.companion').joinpath(path).read_text())
        for name, path in (('0030_companion', 'schema.sql'), ('0034_companion_growth_batches', 'growth_batches.sql'),
                           ('0037_companion_growth_retry', 'growth_retry.sql'), ('0039_companion_learning', 'habit_revisions.sql'))))
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO companion.actors(actor_id,name,enabled) VALUES('a','角色',true)")
    context = TrustedContext('p', 'a', 'person', 'chat', ('scope',), ('scope',), 1,
                             OriginKind.HUMAN, permissions=frozenset({'companion.manage'}))
    return HabitLedger(Companion(database)), context


def proposal(identifier, episode, deps, *, action='create', target=None, content='以后不要使用这个称呼'):
    value = ReflectionProposal(identifier, 'a', (episode.episode_id,), content, 'style', episode.occurred_at,
        tuple(deps), ('scope',), conditions={'when': ['称呼该用户'], 'unless': []}, counterexamples=())
    return PreparedReflection('称呼偏好', value, None, (episode.episode_id,), action, target,
                              'explicit', 'persistent', 'conversation')


@pytest.mark.asyncio
async def test_independent_support_semantic_revision_history_and_revocation(database):
    ledger, context = await setup_ledger(database)
    at = datetime(2026, 11, 5, 3, tzinfo=UTC)
    source1, source2 = SourceRef('source-1', 1), SourceRef('source-2', 1)
    first = FrozenEpisode('e1', 1, at, (source1,), 1, 'e:1', 'event-1', 'human', 'person')
    first_proposal = proposal('rule-1', first, (source1,))
    async with database.transaction() as connection:
        created = await ledger.apply(connection, context, first_proposal, {'e1': first}, attempt_id='attempt-1')
    assert (created['revision'], created['semantic_revision'], created['support_count']) == (1, 1, 1)

    # A different derived Episode from the same original is not new support.
    duplicate = replace(first, episode_id='derived', event_id='derived-event')
    target = {'rule_id': 'rule-1', 'semantic_revision': 1}
    repeated = proposal('proposal-2', duplicate, (source1,), action='support', target=target)
    async with database.transaction() as connection:
        assert await ledger.apply(connection, context, repeated, {'derived': duplicate}, attempt_id='attempt-2') is None

    second = FrozenEpisode('e2', 1, at + timedelta(minutes=1), (source2,), 2, 'e:2', 'event-2', 'human', 'person')
    supported = proposal('proposal-3', second, (source1, source2), action='support', target=target)
    async with database.transaction() as connection:
        updated = await ledger.apply(connection, context, supported, {'e2': second}, attempt_id='attempt-3')
    assert (updated['revision'], updated['semantic_revision'], updated['support_count']) == (2, 1, 2)
    original_ref = {'rule_id': 'rule-1', 'revision': 1, 'semantic_revision': 1}
    current = (await ledger.resolve(context, (original_ref,), purpose='conversation'))[0]
    assert current['eligible'] and current['compatible'] and current['revision'] == 2
    mixed = await ledger.resolve(context, (original_ref, {'rule_id': 'rule-1', 'revision': 9,
        'semantic_revision': 99}), purpose='conversation')
    assert [(item['requested_revision'], item['requested_semantic_revision'], item['compatible'])
            for item in mixed] == [(1, 1, True), (9, 99, False)]
    lightweight = BehaviorResolver(database, ledger.companion.identity,
                                   SimpleNamespace(check_many=ledger.companion.identity.check_many))
    assert await lightweight.resolve_behaviors(context, (original_ref,), purpose='conversation') == (current,)

    result = await ledger.command(context, {'id': 'rule-1', 'client_request_id': 'revoke', 'expected_revision': 2}, state='revoked')
    assert result.revision == 3
    assert not await ledger.material(context, query='其他话题')
    current = (await ledger.resolve(context, (original_ref,), purpose='conversation'))[0]
    assert not current['compatible'] and current['state'] == 'revoked'
    history = (await ledger.resolve(context, (original_ref,), purpose='history'))[0]
    assert history['historical_revision'] == 1 and history['historical_state'] == 'active'
    assert history['text'] == first_proposal.proposal.content and history['state'] == 'revoked'

    # Even an exact restatement with a new proposal ID cannot revive the rule.
    new = replace(second, episode_id='e3', event_id='event-3')
    restated = proposal('new-id', new, (source1, source2))
    async with database.transaction() as connection:
        decision = await ledger.apply(connection, context, restated, {'e3': new}, attempt_id='attempt-4')
    assert decision['_unresolved'] == 'rule_not_active'
    paraphrased = proposal('new-paraphrase', new, (source1, source2), content='以后不要使用这个昵称')
    async with database.transaction() as connection:
        decision = await ledger.apply(connection, context, paraphrased, {'e3': new}, attempt_id='attempt-5')
    assert decision['_unresolved'] == 'similar_rule_identity_unresolved'
    async with database.transaction(read_only=True) as connection:
        count = await (await connection.execute("SELECT count(*) AS n FROM companion.style_rules WHERE state='active'" )).fetchone()
    assert count['n'] == 0
    with pytest.raises(AppError):
        await ledger.command(context, {'id': 'rule-1', 'client_request_id': 'stale', 'expected_revision': 2}, state='active')


@pytest.mark.asyncio
async def test_runtime_candidates_filter_before_limit_and_source_invalidation(database):
    ledger, context = await setup_ledger(database)
    async with database.transaction() as connection:
        for index in range(41):
            await connection.execute(
                'INSERT INTO companion.style_rules(id,actor_id,person_id,kind,title,content,conditions,counterexamples,'
                'episode_ids,source_refs,scope_ids,state,conversation_id,preference_kind) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (f'r{index:02}', 'a', 'person', 'style', '偏好', '请不要使用这个称呼',
                 document({'when': [], 'unless': []}), document([]), ['episode'],
                 document((SourceRef('source', 1),)), ['scope'], 'active' if index == 40 else 'revoked',
                 'chat', 'explicit'))
    selected = await ledger.material(context, query='完全不同的话题')
    assert len(selected) == 1 and selected[0].behavior_ref['rule_id'] == 'r40'
    assert selected[0].behavior_ref['semantic_role'] == 'constraint'
    assert selected[0].source_refs == (SourceRef('source', 1),)
    assert not await ledger.material(replace(context, conversation_id='other'), query='称呼')

    async with database.transaction() as connection:
        await CompanionDependencies().invalidate(connection, (SourceRef('source', 1),), 'source_deleted')
    assert not await ledger.material(context, query='称呼')
    status = (await ledger.resolve(context, ({'rule_id': 'r40', 'revision': 1, 'semantic_revision': 1},),
                                   purpose='conversation'))[0]
    assert not status['eligible'] and status['state'] == 'invalidated'


@pytest.mark.asyncio
async def test_source_change_is_dispatched_to_each_actor_without_cross_actor_write(database):
    ledger, context = await setup_ledger(database)
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO companion.actors(actor_id,name,enabled) VALUES('b','另一角色',true)")
        for actor in ('a', 'b'):
            await connection.execute(
                'INSERT INTO companion.style_rules(id,actor_id,person_id,kind,title,content,conditions,counterexamples,'
                'episode_ids,source_refs,scope_ids,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (actor+'-rule', actor, 'person', 'style', '标题', '内容', document({}), document([]),
                 ['episode'], document((SourceRef('shared', 1),)), ['scope'], 'active'))
        deps = CompanionDependencies()
        assert await deps.actors_for_source(connection, (SourceRef('shared', 1),), 'source_deleted') == ('a', 'b')
        await deps.invalidate_actor(connection, (SourceRef('shared', 1),), 'source_deleted', actor_id='a')
        rows = await (await connection.execute('SELECT actor_id,state FROM companion.style_rules ORDER BY actor_id')).fetchall()
    assert [(row['actor_id'], row['state']) for row in rows] == [('a', 'invalidated'), ('b', 'active')]


@pytest.mark.asyncio
async def test_non_support_generation_dependency_still_invalidates_rule(database):
    ledger, context = await setup_ledger(database)
    at = datetime.now(UTC)
    support, other = SourceRef('support', 1), SourceRef('generation-context', 1)
    evidence = FrozenEpisode('e1', 1, at, (support,), 1, 'e:1', 'event', 'human', 'person')
    async with database.transaction() as connection:
        rule = await ledger.apply(connection, context, proposal('rule', evidence, (support, other)),
                                  {'e1': evidence}, attempt_id='attempt')
        assert rule['support_count'] == 1
        await CompanionDependencies().invalidate_actor(connection, (other,), 'source_deleted', actor_id='a')
    resolved = (await ledger.resolve(context, ({'rule_id': 'rule', 'revision': 1, 'semantic_revision': 1},),
                                     purpose='conversation'))[0]
    assert not resolved['eligible'] and resolved['reason'] == 'source_unavailable'


def test_local_today_expires_at_original_day_midnight():
    occurred = datetime(2026, 11, 5, 15, 30, tzinfo=UTC)
    assert expiry('today', occurred, 'Asia/Shanghai') == datetime(2026, 11, 5, 16, tzinfo=UTC)
    assert expiry('turn', occurred) == occurred
    assert expiry('persistent', occurred) is None


def test_example_requires_real_delivered_relation_in_same_conversation():
    context = TrustedContext('p', 'a', 'person', 'group-a', ('scope',), ('scope',), 1, OriginKind.HUMAN)
    reply = {'turn_id': 'merged-turn', 'expression_id': 'expression', 'conversation_id': 'group-a',
             'person_id': 'person', 'scope_ids': ['scope'], 'source_refs': [{'source_id': 's', 'revision': 1}],
             'generated': True, 'delivery_state': 'delivered',
             'input_messages': [{'message_id': 1}, {'message_id': 2}],
             'output_messages': [{'message_id': 3}, {'message_id': 4}]}
    assert ExpressionExamples._usable(context, reply, 'expression')
    assert not ExpressionExamples._usable(context, {**reply, 'delivery_state': 'unknown'}, 'expression')
    assert not ExpressionExamples._usable(context, {**reply, 'conversation_id': 'group-b'}, 'expression')
    assert not ExpressionExamples._usable(context, {**reply, 'input_messages': []}, 'expression')
