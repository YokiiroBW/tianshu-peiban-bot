"""Bounded Growth wake repair, using only domain metadata and native jobs."""

from datetime import UTC, datetime, timedelta

from tianshu.life.validation import timestamp
from tianshu.runtime.errors import AppError

from .growth_recovery import AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED
from .reflection_output import REVIEW_ERRORS


LANES = ('retry', 'input', 'organize')
SCAN_DELAY = timedelta(minutes=2)


async def _todo_covers(connection, generation, lock, task, payload, due):
    # The native queue has a unique partial index for a todo queueing_lock.
    pending = await (await connection.execute(
        "SELECT task_name,args,scheduled_at FROM procrastinate_jobs "
        "WHERE status='todo' AND queueing_lock=%s",
        (lock + ':generation:' + generation,))).fetchone()
    if not pending or pending['task_name'] != task or pending['args'] != payload:
        return False
    return pending['scheduled_at'] is None or pending['scheduled_at'] <= due


async def _mark(connection, generation, lane, row, key, revision):
    await connection.execute(
        'INSERT INTO companion.rbi_growth_maintenance_scans '
        '(runtime_generation,lane,actor_id,scope_key,work_key,observed_revision,next_scan_at) '
        'VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(runtime_generation,lane,actor_id,scope_key,work_key) '
        'DO UPDATE SET observed_revision=excluded.observed_revision,next_scan_at=excluded.next_scan_at',
        (generation, lane, row['actor_id'], row['scope_key'], key, revision, datetime.now(UTC) + SCAN_DELAY))


async def _cursor(connection, generation, lane):
    await connection.execute(
        'INSERT INTO companion.rbi_growth_maintenance_cursors(runtime_generation,lane) '
        'VALUES(%s,%s) ON CONFLICT DO NOTHING', (generation, lane))
    return await (await connection.execute(
        'SELECT * FROM companion.rbi_growth_maintenance_cursors '
        'WHERE runtime_generation=%s AND lane=%s FOR UPDATE', (generation, lane))).fetchone()


async def _advance(connection, generation, lane, rows, limit):
    last = rows[-1] if len(rows) == limit else None
    due = last['due_at'] if last and lane != 'organize' else None
    key = (last['retry_key'] if lane == 'retry' else last['failure_key'] if lane == 'input' else '') if last else None
    await connection.execute(
        'UPDATE companion.rbi_growth_maintenance_cursors SET due_at=%s,actor_id=%s,scope_key=%s,work_key=%s '
        'WHERE runtime_generation=%s AND lane=%s',
        (due, last['actor_id'] if last else None, last['scope_key'] if last else None, key, generation, lane))


async def _retry_rows(connection, generation, limit, cursor):
    after = ''
    params = [generation, generation]
    if cursor['actor_id'] is not None:
        after = 'AND (r.due_at,r.actor_id,r.scope_key,r.retry_key)>(%s,%s,%s,%s) '
        params.extend((cursor['due_at'], cursor['actor_id'], cursor['scope_key'], cursor['work_key']))
    params.append(limit)
    return await (await connection.execute(
        "SELECT r.*,c.observed_revision AS scan_revision,c.next_scan_at AS scan_next_at "
        "FROM companion.growth_retry_batches r "
        "LEFT JOIN companion.rbi_growth_maintenance_scans c ON c.runtime_generation=%s "
        "AND c.lane='retry' AND c.actor_id=r.actor_id AND c.scope_key=r.scope_key AND c.work_key=r.retry_key "
        "WHERE r.batch->>'runtime_generation'=%s AND r.due_at IS NOT NULL AND r.due_at<=now() "
        "AND (r.state='waiting' OR (r.state='blocked' AND r.batch->'rbi_review'->>'state'='requested')) "
        + after + "ORDER BY r.due_at,r.actor_id,r.scope_key,r.retry_key "
        "LIMIT %s FOR UPDATE OF r SKIP LOCKED", params)).fetchall()


async def _input_rows(connection, generation, limit, cursor):
    after = ''
    params = [generation, generation]
    if cursor['actor_id'] is not None:
        after = 'AND (f.due_at,f.actor_id,f.scope_key,f.failure_key)>(%s,%s,%s,%s) '
        params.extend((cursor['due_at'], cursor['actor_id'], cursor['scope_key'], cursor['work_key']))
    params.append(limit)
    return await (await connection.execute(
        "SELECT f.*,c.observed_revision AS scan_revision,c.next_scan_at AS scan_next_at "
        "FROM companion.growth_material_failures f "
        "LEFT JOIN companion.rbi_growth_maintenance_scans c ON c.runtime_generation=%s "
        "AND c.lane='input' AND c.actor_id=f.actor_id AND c.scope_key=f.scope_key AND c.work_key=f.failure_key "
        "WHERE f.runtime_generation=%s AND f.state='blocked' AND f.due_at IS NOT NULL AND f.due_at<=now() "
        + after + "ORDER BY f.due_at,f.actor_id,f.scope_key,f.failure_key "
        "LIMIT %s FOR UPDATE OF f SKIP LOCKED", params)).fetchall()


async def _organize_rows(connection, generation, limit, cursor):
    after = ''
    params = [generation, generation, sorted({AUTHORIZATION_BLOCKED, IDENTITY_BLOCKED}), sorted(REVIEW_ERRORS)]
    if cursor['actor_id'] is not None:
        after = 'AND (s.actor_id,s.scope_key)>(%s,%s) '
        params.extend((cursor['actor_id'], cursor['scope_key']))
    params.append(limit)
    return await (await connection.execute(
        "SELECT s.*,c.observed_revision AS scan_revision,c.next_scan_at AS scan_next_at "
        "FROM companion.organize_state s "
        "LEFT JOIN companion.rbi_growth_maintenance_scans c ON c.runtime_generation=%s "
        "AND c.lane='organize' AND c.actor_id=s.actor_id AND c.scope_key=s.scope_key AND c.work_key='' "
        "WHERE s.runtime_generation=%s AND (s.last_error IS NULL OR s.last_error<>ALL(%s)) "
        "AND (s.active_batch IS NOT NULL OR ((s.last_error IS NULL OR s.last_error<>ALL(%s)) "
        "AND EXISTS(SELECT 1 FROM companion.growth_inputs i WHERE i.actor_id=s.actor_id "
        "AND i.scope_key=s.scope_key AND i.reviewed_revision<i.revision AND i.retry_key IS NULL))) "
        + after + "ORDER BY s.actor_id,s.scope_key "
        "LIMIT %s FOR UPDATE OF s SKIP LOCKED", params)).fetchall()


async def _wake(growth, connection, generation, lane, row):
    actor, scope = row['actor_id'], row['scope_key']
    if lane == 'retry':
        key, revision, due = row['retry_key'], row['revision'], row['due_at']
        lock = f'growth-retry:{generation}:{actor}:{scope}:{key}'
        task = 'companion.growth.retry'
        payload = {'actor_id': actor, 'scope_key': scope, 'retry_key': key, 'generation': generation}
        async def schedule():
            await growth.retries.schedule_on(connection, row)
    elif lane == 'input':
        key, revision, due = row['failure_key'], row['observation_revision'], row['due_at']
        lock = f'growth-input:{generation}:{actor}:{scope}:{key}:{revision}'
        task = 'companion.growth.input_retry'
        payload = {'actor_id': actor, 'scope_key': scope, 'failure_key': key,
                   'expected_revision': revision, 'generation': generation}
        async def schedule():
            await growth._schedule_input_retry_on(connection, row)
    else:
        key, revision = '', row['revision']
        blocked = row['blocked_material'] or {}
        due = (timestamp(blocked['retry_at']) if blocked.get('recovery') == 'dependency_wait'
               else datetime.now(UTC))
        lock = f'growth-organize:{actor}:{scope}:{revision}'
        task = 'companion.organize'
        payload = {'actor_id': actor, 'scope_key': scope, 'expected_revision': revision, 'generation': generation}
        async def schedule():
            await growth._enqueue(connection, actor, scope, revision, 'maintenance',
                                  generation=generation, scheduled_at=due)
    covered = await _todo_covers(connection, generation, lock, task, payload, due)
    if not covered:
        await schedule()
    await _mark(connection, generation, lane, row, key, revision)
    return not covered


async def reconcile_due(growth, *, limit=32):
    if type(limit) is not int or not 1 <= limit <= 128:
        raise AppError('invalid_growth_maintenance_limit')
    async with growth.companion.database.transaction() as connection:
        generation = await growth.jobs.current_generation_on(connection)
        await growth.jobs.assert_generation_on(connection, generation)
        await connection.execute(
            'INSERT INTO companion.rbi_growth_maintenance_turn(runtime_generation) VALUES(%s) ON CONFLICT DO NOTHING',
            (generation,))
        turn = await (await connection.execute(
            'SELECT next_lane FROM companion.rbi_growth_maintenance_turn WHERE runtime_generation=%s '
            'FOR UPDATE SKIP LOCKED', (generation,))).fetchone()
        if not turn:
            return {'scanned': 0, 'woken': 0, 'complete': False, 'reason': 'maintenance_in_progress'}
        start = turn['next_lane']
        await connection.execute(
            'UPDATE companion.rbi_growth_maintenance_turn SET next_lane=%s WHERE runtime_generation=%s',
            ((start + 1) % len(LANES), generation))
    allocations = {name: limit // len(LANES) for name in LANES}
    for offset in range(limit % len(LANES)):
        allocations[LANES[(start + offset) % len(LANES)]] += 1
    readers = {'retry': _retry_rows, 'input': _input_rows, 'organize': _organize_rows}
    counts = {}
    began = {}
    scanned = woken = 0
    for offset in range(len(LANES)):
        lane = LANES[(start + offset) % len(LANES)]
        maximum = allocations[lane]
        if not maximum:
            counts[lane] = 0
            continue
        # Do not hold a retry/input lock while taking an organize_state lock:
        # the normal owner takes organize_state first.
        async with growth.companion.database.transaction() as connection:
            await growth.jobs.assert_generation_on(connection, generation)
            cursor = await _cursor(connection, generation, lane)
            began[lane] = cursor['actor_id'] is None
            rows = await readers[lane](connection, generation, maximum, cursor)
            counts[lane] = len(rows)
            scanned += len(rows)
            for row in rows:
                revision = row['observation_revision'] if lane == 'input' else row['revision']
                if (row['scan_revision'] != revision or row['scan_next_at'] is None
                        or row['scan_next_at'] <= datetime.now(UTC)):
                    woken += int(await _wake(growth, connection, generation, lane, row))
            await _advance(connection, generation, lane, rows, maximum)
    complete = all(allocations[lane] and began[lane] and counts[lane] < allocations[lane] for lane in LANES)
    return {'scanned': scanned, 'woken': woken, 'complete': complete, 'lanes': counts,
            'complete_scope': 'current generation, unlocked eligible candidates observed from lane starts'}
