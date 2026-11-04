"""Recoverable statistics delivery from Growth's own material/retry ledgers."""

from datetime import UTC, datetime, timedelta
import logging

from tianshu.conversation.codec import document, signature
from tianshu.runtime.jobs import JobSpec


class GrowthStatistics:
    def __init__(self, database, jobs):
        self.database, self.jobs, self.statistics = database, jobs, None

    def bind(self, statistics):
        self.statistics = statistics

    async def _safe(self, connection, callback):
        await connection.execute('SAVEPOINT growth_statistics_effect')
        try:
            result = await callback()
        except Exception:
            await connection.execute('ROLLBACK TO SAVEPOINT growth_statistics_effect')
            logging.getLogger(__name__).warning('growth_statistics_delivery_failed', exc_info=True)
            result = False
        finally:
            await connection.execute('RELEASE SAVEPOINT growth_statistics_effect')
        return result

    async def _mark(self, connection, *, complete, detail):
        return await self._safe(connection, lambda: self.statistics.mark_producer_on(connection, 'growth',
            watermark=datetime.now(UTC).isoformat(), complete=complete, detail=detail))

    @staticmethod
    def material_event(row):
        return {'family': 'growth', 'event_id': 'growth-material:'+signature({
            'actor': row['actor_id'], 'scope': row['scope_key'], 'episode': row['episode_id'],
            'version': row['episode_revision']}), 'revision': row['observation_revision'],
            'occurred_at': row['updated_at'], 'module': 'growth', 'actor_id': row['actor_id'],
            'route_id': None, 'dimensions': {'state': 'blocked' if row['state'] == 'blocked' else 'processed'},
            'metrics': {'growth_material_blocked': int(row['state'] == 'blocked')}}

    @staticmethod
    def review_event(row):
        return {'family': 'growth', 'event_id': 'growth-review:'+signature({
            'actor': row['actor_id'], 'scope': row['scope_key'], 'retry': row['retry_key']}),
            'revision': row['revision'], 'occurred_at': row['updated_at'], 'module': 'growth',
            'actor_id': row['actor_id'], 'route_id': None,
            'dimensions': {'state': {'blocked': 'blocked', 'completed': 'succeeded',
                                      'waiting': 'queued', 'active': 'running'}[row['state']]},
            'metrics': {'growth_review_blocked': int(row['state'] == 'blocked')}}

    async def schedule_on(self, connection, *, generation, cursor=None, delay=0):
        await self.jobs.assert_generation_on(connection, generation)
        await self.jobs.enqueue(connection, JobSpec('companion.growth.statistics',
            'growth-statistics:'+signature({'generation': generation, 'cursor': cursor, 'scheduled': datetime.now(UTC).isoformat()}),
            {'cursor': cursor, 'generation': generation}, scheduled_at=datetime.now(UTC)+timedelta(seconds=delay),
            data_generation=generation))

    async def emit_on(self, connection, event, *, generation, schedule=True):
        await self.jobs.assert_generation_on(connection, generation)
        await connection.execute(
            'INSERT INTO companion.growth_statistics_outbox(runtime_generation,event_id,revision,event) VALUES(%s,%s,%s,%s) '
            'ON CONFLICT(runtime_generation,event_id,revision) DO NOTHING',
            (generation, event['event_id'], event['revision'], document(event)))
        if self.statistics is not None and await self._safe(connection, lambda: self.statistics.record_safely_on(connection, event)):
            await connection.execute('UPDATE companion.growth_statistics_outbox SET delivered=true '
                'WHERE runtime_generation=%s AND event_id=%s AND revision=%s', (generation, event['event_id'], event['revision']))
            return True
        if self.statistics is not None:
            await self._mark(connection, complete=False, detail='growth_statistics_pending')
            if schedule:
                await self._safe(connection, lambda: self.schedule_on(connection, generation=generation, delay=30))
        return False

    async def replay(self, generation, cursor=None):
        """Scan both authoritative ledgers, then drain undelivered event revisions."""
        if self.statistics is None:
            return {'state': 'unavailable', 'reason': 'statistics_not_bound'}
        cursor = cursor or {'phase': 'material', 'after': None}
        phase, after = cursor['phase'], cursor.get('after')
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            marked = await self._mark(connection, complete=False, detail='growth_statistics_replay')
            if marked is False:
                await self.schedule_on(connection, generation=generation, cursor=cursor, delay=30)
                return {'state': 'pending', 'cursor': cursor}
            if phase == 'material':
                after = after or ['', '', '', 0]
                rows = await (await connection.execute(
                    'SELECT * FROM companion.growth_material_failures WHERE runtime_generation=%s AND (actor_id,scope_key,episode_id,episode_revision)>'
                    '(%s,%s,%s,%s) ORDER BY actor_id,scope_key,episode_id,episode_revision LIMIT 257', (generation, *after))).fetchall()
                for row in rows[:256]:
                    await self.emit_on(connection, self.material_event(row), generation=generation, schedule=False)
                if len(rows) > 256:
                    last = rows[255]
                    next_cursor = {'phase': 'material', 'after': [last['actor_id'], last['scope_key'], last['episode_id'], last['episode_revision']]}
                else:
                    next_cursor = {'phase': 'review', 'after': None}
            elif phase == 'review':
                after = after or ['', '', '']
                rows = await (await connection.execute(
                    "SELECT * FROM companion.growth_retry_batches WHERE batch->>'runtime_generation'=%s AND (actor_id,scope_key,retry_key)>"
                    '(%s,%s,%s) ORDER BY actor_id,scope_key,retry_key LIMIT 257', (generation, *after))).fetchall()
                for row in rows[:256]:
                    await self.emit_on(connection, self.review_event(row), generation=generation, schedule=False)
                if len(rows) > 256:
                    last = rows[255]
                    next_cursor = {'phase': 'review', 'after': [last['actor_id'], last['scope_key'], last['retry_key']]}
                else:
                    next_cursor = {'phase': 'outbox', 'after': None}
            elif phase == 'outbox':
                rows = await (await connection.execute(
                    'SELECT * FROM companion.growth_statistics_outbox WHERE runtime_generation=%s AND NOT delivered '
                    'ORDER BY created_at,event_id,revision LIMIT 256 FOR UPDATE SKIP LOCKED', (generation,))).fetchall()
                for row in rows:
                    event = row['event']
                    if isinstance(event['occurred_at'], str):
                        event['occurred_at'] = datetime.fromisoformat(event['occurred_at'].replace('Z', '+00:00'))
                    if await self._safe(connection, lambda: self.statistics.record_safely_on(connection, event)):
                        await connection.execute('UPDATE companion.growth_statistics_outbox SET delivered=true '
                            'WHERE runtime_generation=%s AND event_id=%s AND revision=%s', (generation, row['event_id'], row['revision']))
                pending = await (await connection.execute(
                    'SELECT EXISTS(SELECT 1 FROM companion.growth_statistics_outbox WHERE runtime_generation=%s AND NOT delivered) AS pending',
                    (generation,))).fetchone()
                if not pending['pending']:
                    marked = await self._mark(connection, complete=True, detail='growth_ledgers_replayed')
                    if marked is not False:
                        return {'state': 'complete'}
                next_cursor = {'phase': 'outbox', 'after': None}
            else:
                raise ValueError('invalid_growth_statistics_cursor')
            await self.schedule_on(connection, generation=generation, cursor=next_cursor, delay=30 if phase == 'outbox' else 0)
            return {'state': 'pending', 'cursor': next_cursor}
