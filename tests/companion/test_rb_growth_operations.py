"""Growth exposes only actionable retry identities to Operations."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import tianshu.companion.growth_operations as operations_module
from tianshu.companion.growth_operations import GrowthOperations
from tianshu.conversation.generation import attempt_for
from tianshu.runtime.errors import AppError


NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def waiting(**changes):
    row = {
        'actor_id': 'actor', 'scope_key': 'scope', 'retry_key': 'retry',
        'current_material_key': 'material', 'revision': 3,
        'due_at': NOW - timedelta(minutes=3), 'waiting_on_original': False,
        'expected_version': 'source-version', 'rejected_material_key': None,
        'executing': False, 'lineage_id': 'lineage', 'dependency': 'models:quota:main',
        'current_version': 'source-version', 'attempts': 1, 'max_attempts': 4,
        'lineage_due_at': NOW - timedelta(minutes=3), 'paused': False,
        'cancelled': False, 'claimed': False, 'cooldown_until': None,
        'probe_token': None, 'probe_status': None,
        'attempt_id': attempt_for('material'), 'attempt_status': 'failed',
    }
    return {**row, **changes}


@pytest.mark.parametrize('changes', [
    {'due_at': NOW + timedelta(minutes=1)},
    {'waiting_on_original': True},
    {'executing': True},
    {'paused': True},
    {'cancelled': True},
    {'claimed': True},
    {'current_version': 'changed'},
    {'attempts': 4},
    {'attempt_status': 'unknown'},
    {'attempt_id': 'different-original'},
    {'lineage_due_at': NOW + timedelta(minutes=1)},
    {'cooldown_until': NOW + timedelta(minutes=1)},
    {'cooldown_until': NOW - timedelta(minutes=1), 'probe_token': 'probe', 'probe_status': 'unknown'},
])
def test_waiting_retry_not_eligible_when_original_or_policy_preconditions_fail(changes):
    assert not GrowthOperations._waiting_ready(waiting(**changes), NOW)[0]


def test_failed_retry_and_schema_continuation_are_distinct_due_cases():
    assert GrowthOperations._waiting_ready(waiting(), NOW)[0]
    corrected = waiting(attempt_status='succeeded', lineage_due_at=None,
                        rejected_material_key='material')
    assert GrowthOperations._waiting_ready(corrected, NOW)[0]
    assert not GrowthOperations._waiting_ready({**corrected, 'rejected_material_key': None}, NOW)[0]


class Database:
    @asynccontextmanager
    async def transaction(self, **kwargs):
        assert kwargs == {'read_only': True}
        yield object()


class ProgressProbe(GrowthOperations):
    def __init__(self, rows, inspections):
        super().__init__(SimpleNamespace(companion=SimpleNamespace(database=Database())))
        self.rows, self.inspections, self.inspected = rows, inspections, []

    async def _waiting_rows_on(self, _connection, *, limit, target=None):
        return self.rows[:limit]

    async def inspect(self, _incident, target):
        self.inspected.append(target['retry_key'])
        result = self.inspections[target['retry_key']]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_progress_reports_only_sanitized_rows_and_checks_the_original():
    # Rows may contain private implementation fields; none are copied to Ops.
    now = datetime.now(UTC)
    rows = [waiting(due_at=now-timedelta(days=1), lineage_due_at=now-timedelta(days=1), secret='do not report'),
            waiting(retry_key='changed', revision=5, due_at=now-timedelta(days=1), lineage_due_at=now-timedelta(days=1)),
            waiting(retry_key='unavailable', due_at=now-timedelta(days=1), lineage_due_at=now-timedelta(days=1)),
            waiting(retry_key='future', due_at=now+timedelta(days=1), lineage_due_at=now-timedelta(days=1))]
    probe = ProgressProbe(rows, {'retry': {'eligible': True, 'version': '3'},
        'changed': {'eligible': True, 'version': '4'},
        'unavailable': AppError('source_unavailable')})
    facts = await probe.progress()
    assert [fact['eligible'] for fact in facts] == [True, False, False, False]
    assert probe.inspected == ['retry', 'changed', 'unavailable']
    assert facts[0]['target'] == {'actor_id': 'actor', 'scope_key': 'scope', 'retry_key': 'retry'}
    assert facts[0]['error_family'] == 'growth_retry_stalled'
    assert 'secret' not in facts[0] and 'do not report' not in repr(facts)


@pytest.mark.asyncio
async def test_operations_wakes_only_the_guarded_current_retry(monkeypatch):
    events = []
    generation = '00000000-0000-4000-8000-000000000001'
    row = {'actor_id': 'actor', 'scope_key': 'scope', 'retry_key': 'retry',
           'current_material_key': 'material', 'state': 'waiting', 'revision': 3,
           'batch': {'context': {}, 'runtime_generation': generation}}
    meta = waiting(due_at=datetime.now(UTC)-timedelta(minutes=3),
                   lineage_due_at=datetime.now(UTC)-timedelta(minutes=3))
    state = {'actor_id': 'actor', 'scope_key': 'scope', 'runtime_generation': generation, 'active_batch': None}
    current = {'latest': row}

    class Cursor:
        def __init__(self, value):
            self.value = value

        async def fetchone(self):
            return self.value

    class Connection:
        async def execute(self, query, _params):
            if 'FROM companion.organize_state' in query:
                return Cursor(state)
            if 'FROM companion.growth_retry_batches' in query:
                return Cursor(current['latest'])
            raise AssertionError(query)

    class WriteDatabase:
        @asynccontextmanager
        async def transaction(self):
            yield Connection()

    async def reader(context):
        return context

    async def request(_attempt_id, *, observer):
        return SimpleNamespace(source_refs=())

    async def validate(_connection, _context, _versions, _refs, *, generation):
        events.append(('validate', generation))

    async def guard(_connection, attempt_id, owner_token):
        events.append(('guard', attempt_id, owner_token))

    async def wake(_connection, value):
        events.append(('wake', value['retry_key']))

    growth = SimpleNamespace(companion=SimpleNamespace(database=WriteDatabase()),
        recovery=SimpleNamespace(reader=reader), generations=SimpleNamespace(observe_request=request),
        retries=SimpleNamespace(schedule_on=wake), _validate_material=validate)
    probe = GrowthOperations(growth)
    probe.guard_on = guard

    async def load(_target):
        return row

    async def metadata(_connection, *, limit, target=None):
        return [meta]

    monkeypatch.setattr(probe, '_row', load)
    monkeypatch.setattr(probe, '_waiting_rows_on', metadata)
    monkeypatch.setattr(operations_module, 'context_from_record', lambda _: 'context')
    monkeypatch.setattr(operations_module, 'frozen_episodes', lambda _: {})
    target = {'actor_id': 'actor', 'scope_key': 'scope', 'retry_key': 'retry'}
    result = await probe.invoke({'owner_token': 'owner'}, target, {'version': '3'}, 'ops-attempt')
    assert result['state'] == 'accepted'
    assert events == [('validate', generation), ('guard', 'ops-attempt', 'owner'), ('wake', 'retry')]

    events.clear()
    current['latest'] = {**row, 'revision': 4}
    with pytest.raises(AppError, match='growth_rule_changed'):
        await probe.invoke({'owner_token': 'owner'}, target, {'version': '3'}, 'ops-attempt')
    assert not any(event[0] == 'wake' for event in events)
