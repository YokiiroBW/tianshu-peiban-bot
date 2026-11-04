"""One bounded Operations action for a particular failed Growth batch."""

from datetime import UTC, datetime

import aiohttp

from tianshu.conversation.codec import context_from_record, signature
from tianshu.conversation.generation import attempt_for
from tianshu.runtime.errors import AppError
from .growth_output_repair import review_available
from .growth_retry import manual_retry_allowed
from .reflection_output import frozen_episodes


class GrowthOperations:
    def __init__(self, growth):
        self.growth = growth
        self.guard_on = None

    async def _waiting_rows_on(self, connection, *, limit, target=None):
        """Read only mechanical recovery state, never return stored batch text."""
        generation = await self.growth.jobs.current_generation_on(connection)
        target = target or {}
        return await (await connection.execute(
            "SELECT r.actor_id,r.scope_key,r.retry_key,r.current_material_key,r.revision,r.due_at,"
            "r.waiting_on_original,r.batch->>'recovery_version' AS expected_version,"
            "r.batch->'output_repair'->>'rejected_material_key' AS rejected_material_key,"
            "(s.active_batch IS NOT NULL) AS executing,l.lineage_id,l.dependency,l.current_version,"
            "l.attempts,l.max_attempts,l.due_at AS lineage_due_at,l.paused,l.cancelled,"
            "(l.claim_token IS NOT NULL AND l.claim_expires_at>now()) AS claimed,"
            "d.cooldown_until,d.probe_token,p.status AS probe_status,a.attempt_id,a.status AS attempt_status "
            "FROM companion.growth_retry_batches r "
            "JOIN companion.organize_state s ON s.actor_id=r.actor_id AND s.scope_key=r.scope_key "
            "LEFT JOIN runtime.recovery_lineages l ON l.lineage_id=r.batch->>'lineage_id' "
            "AND l.data_generation=%s AND l.owner='companion.growth' "
            "LEFT JOIN runtime.recovery_dependencies d ON d.data_generation=l.data_generation AND d.dependency=l.dependency "
            "LEFT JOIN runtime.recovery_attempts p ON p.attempt_id=d.probe_token "
            "LEFT JOIN LATERAL (SELECT attempt_id,status FROM runtime.recovery_attempts "
            "WHERE lineage_id=l.lineage_id ORDER BY attempt_number DESC LIMIT 1) a ON true "
            "WHERE r.state='waiting' AND r.last_error IS NOT NULL AND r.batch->>'runtime_generation'=%s "
            "AND s.runtime_generation=%s AND r.due_at<=now() AND NOT r.waiting_on_original "
            "AND s.active_batch IS NULL AND l.lineage_id IS NOT NULL AND NOT l.paused AND NOT l.cancelled "
            "AND l.current_version=r.batch->>'recovery_version' AND l.attempts<l.max_attempts "
            "AND (l.claim_token IS NULL OR l.claim_expires_at<=now()) "
            "AND (d.cooldown_until IS NULL OR (d.cooldown_until<=now() AND "
            "(d.probe_token IS NULL OR p.status IS NULL OR p.status NOT IN ('reserved','unknown')))) "
            "AND ((a.status='failed' AND l.due_at<=now()) OR "
            "(a.status='succeeded' AND r.batch->'output_repair'->>'rejected_material_key'=r.current_material_key)) "
            "AND (%s::text IS NULL OR r.actor_id=%s) "
            "AND (%s::text IS NULL OR r.scope_key=%s) AND (%s::text IS NULL OR r.retry_key=%s) "
            "ORDER BY r.due_at NULLS LAST,r.actor_id,r.scope_key,r.retry_key LIMIT %s",
            (generation, generation, generation, target.get('actor_id'), target.get('actor_id'),
             target.get('scope_key'), target.get('scope_key'), target.get('retry_key'), target.get('retry_key'), limit))).fetchall()

    @staticmethod
    def _waiting_ready(row, now):
        waiting_dependency = bool(row['cooldown_until'] and (
            row['cooldown_until'] > now or row['probe_token'] and row['probe_status'] in {'reserved', 'unknown'}))
        previous_ready = (row['attempt_status'] == 'failed' and row['lineage_due_at'] is not None
                          and row['lineage_due_at'] <= now) or (
            row['attempt_status'] == 'succeeded' and row['rejected_material_key'] == row['current_material_key'])
        eligible = bool(row['lineage_id'] and row['due_at'] and row['due_at'] <= now
            and not row['waiting_on_original'] and not row['executing'] and not waiting_dependency
            and not row['paused'] and not row['cancelled'] and not row['claimed']
            and row['current_version'] == row['expected_version'] and row['attempts'] < row['max_attempts']
            and row['attempt_id'] == attempt_for(row['current_material_key']) and previous_ready)
        return eligible, waiting_dependency

    async def progress(self, limit=64):
        """Report due retry identities only; inspect their original before claiming eligibility."""
        if type(limit) is not int or not 1 <= limit <= 128:
            raise AppError('invalid_growth_progress_limit')
        async with self.growth.companion.database.transaction(read_only=True) as connection:
            rows = await self._waiting_rows_on(connection, limit=limit)
        now = datetime.now(UTC)
        facts = []
        for row in rows:
            target = {key: row[key] for key in ('actor_id', 'scope_key', 'retry_key')}
            eligible, waiting_dependency = self._waiting_ready(row, now)
            if eligible:
                try:
                    inspected = await self.inspect(None, target)
                    eligible = bool(inspected.get('eligible') and inspected.get('version') == str(row['revision']))
                except (AppError, TimeoutError, aiohttp.ClientError):
                    eligible = False
            facts.append({'role': 'bot-background', 'material_id': row['retry_key'],
                'target': target, 'dependency': row['dependency'], 'due_at': row['due_at'],
                'eligible': eligible, 'version': str(row['revision']), 'error_family': 'growth_retry_stalled',
                'paused': bool(row['paused']), 'cancelled': bool(row['cancelled']),
                'executing': bool(row['executing'] or row['claimed']), 'waiting_dependency': waiting_dependency})
        return tuple(facts)

    async def _row(self, target):
        keys = ('actor_id', 'scope_key', 'retry_key')
        if not isinstance(target, dict) or any(not isinstance(target.get(key), str) or not target[key] for key in keys):
            raise AppError('invalid_growth_recovery_target')
        async with self.growth.companion.database.transaction(read_only=True) as connection:
            return await (await connection.execute(
                'SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s',
                tuple(target[key] for key in keys))).fetchone()

    async def inspect(self, _incident_or_recovery, target):
        row = await self._row(target)
        if not row or row['waiting_on_original']:
            return {'eligible': False, 'reason': 'growth_retry_not_available'}
        if row['state'] == 'waiting':
            async with self.growth.companion.database.transaction(read_only=True) as connection:
                candidates = await self._waiting_rows_on(connection, limit=1, target=target)
            if (not candidates or candidates[0]['revision'] != row['revision']
                    or not self._waiting_ready(candidates[0], datetime.now(UTC))[0]):
                return {'eligible': False, 'reason': 'growth_retry_not_due'}
        elif (row['state'] != 'blocked' or not manual_retry_allowed(row['last_error'])
              or not review_available(row['batch'], row['last_error'])):
            return {'eligible': False, 'reason': 'growth_retry_not_available'}
        original = context_from_record(row['batch']['context'])
        current = await self.growth.recovery.reader(original)
        request = await self.growth.generations.observe_request(attempt_for(row['current_material_key']), observer=current)
        if request is None or request.data_generation != row['batch']['runtime_generation']:
            return {'eligible': False, 'reason': 'original_request_unavailable'}
        result = await self.growth.generations.read(request.attempt_id, request.context, observer=current)
        if result is None or result.receipt.status in {'queued', 'running', 'unknown'}:
            return {'eligible': False, 'reason': 'original_attempt_unresolved', 'original_state': 'unknown'}
        if getattr(result.receipt, 'cancellation_reason', None) == 'explicit_cancel':
            return {'eligible': False, 'reason': 'explicit_cancel', 'cancelled': True}
        versions = frozen_episodes(request)
        async with self.growth.companion.database.transaction() as connection:
            await self.growth._validate_material(connection, current, versions, request.source_refs,
                                                 generation=row['batch']['runtime_generation'])
        return {'eligible': True, 'version': str(row['revision']), 'material_id': row['retry_key'],
            'material_digest': signature({'inputs': row['batch']['inputs'], 'source_refs': request.source_refs}),
            'dependency': row['batch']['recovery_dependency'],
            'original_state': 'succeeded' if result.receipt.status == 'succeeded' else 'failed',
            'paused': False, 'cancelled': False}

    async def invoke(self, recovery, target, facts, attempt_id):
        if self.guard_on is None:
            raise AppError('growth_recovery_guard_unavailable', status=503)
        row = await self._row(target)
        if not row:
            raise AppError('reflection_material_not_found', status=404)
        original = context_from_record(row['batch']['context'])
        current = await self.growth.recovery.reader(original)
        request = await self.growth.generations.observe_request(attempt_for(row['current_material_key']), observer=current)
        if request is None:
            raise AppError('reflection_original_attempt_unresolved', status=409)
        versions = frozen_episodes(request)
        async with self.growth.companion.database.transaction() as connection:
            await self.growth._validate_material(connection, current, versions, request.source_refs,
                                                 generation=row['batch']['runtime_generation'])
            await self.guard_on(connection, attempt_id, recovery['owner_token'])
            state = await (await connection.execute(
                'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE',
                (row['actor_id'], row['scope_key']))).fetchone()
            latest = await (await connection.execute(
                'SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE',
                (row['actor_id'], row['scope_key'], row['retry_key']))).fetchone()
            if (not state or not latest or str(latest['revision']) != facts['version']
                    or latest['current_material_key'] != row['current_material_key']):
                raise AppError('growth_rule_changed', status=409)
            if latest['state'] == 'waiting':
                candidates = await self._waiting_rows_on(connection, limit=1, target=target)
                if (not candidates or candidates[0]['revision'] != latest['revision']
                        or not self._waiting_ready(candidates[0], datetime.now(UTC))[0]):
                    raise AppError('reflection_retry_not_available', status=409)
                # Wake the existing owner only; it rechecks the original receipt,
                # source closure and shared physical-attempt budget itself.
                await self.growth.retries.schedule_on(connection, latest)
            else:
                await self.growth.retries.resume_on(connection, state, current, row['retry_key'])
        return {'state': 'accepted', 'attempt_id': attempt_id}

    async def observe(self, _recovery, target, _facts, _attempt_id):
        row = await self._row(target)
        if not row:
            return {'state': 'failed', 'reason': 'retry_not_found'}
        if row['state'] == 'completed' and row['outcome'] == 'applied':
            original = context_from_record(row['batch']['context'])
            current = await self.growth.recovery.reader(original)
            request = await self.growth.generations.observe_request(attempt_for(row['current_material_key']), observer=current)
            if request is None:
                return {'state': 'unknown', 'reason': 'original_request_unavailable'}
            async with self.growth.companion.database.transaction() as connection:
                await self.growth._validate_material(connection, current, frozen_episodes(request), request.source_refs,
                                                     generation=row['batch']['runtime_generation'])
            return {'state': 'verified', 'retry_key': row['retry_key'], 'outcome': row['outcome']}
        if row['state'] == 'blocked':
            return {'state': 'failed', 'reason': row['last_error']}
        return {'state': 'pending' if row['state'] in {'waiting', 'active'} else 'unknown',
                'reason': row['outcome']}
