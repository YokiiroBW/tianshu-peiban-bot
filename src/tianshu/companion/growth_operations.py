"""One bounded Operations action for a particular failed Growth batch."""

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
        if (not row or row['state'] != 'blocked' or row['waiting_on_original']
                or not manual_retry_allowed(row['last_error']) or not review_available(row['batch'], row['last_error'])):
            return {'eligible': False, 'reason': 'growth_retry_not_available'}
        original = context_from_record(row['batch']['context'])
        current = await self.growth.recovery.reader(original)
        request = await self.growth.generations.observe_request(attempt_for(row['current_material_key']), observer=current)
        if request is None:
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
