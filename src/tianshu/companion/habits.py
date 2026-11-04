"""The current learning rules, their history, and purpose-specific reading."""

from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo
import json
import re

from tianshu.conversation.codec import document, signature, source_refs
from tianshu.life.validation import revision
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import ContextContribution


def canonical_purpose(purpose):
    return {'chat': 'conversation', 'planning': 'life.plan'}.get(purpose, purpose)


def management_scope(context):
    return context.conversation_id is None and bool(context.permissions & {'*', 'companion.manage'})


def applicable(row, context, purpose, now=None):
    purpose = canonical_purpose(purpose)
    now = now or datetime.now(UTC)
    if row['state'] != 'active':
        return False, row.get('reason') or row['state']
    if not set(row['scope_ids']).issubset(context.scope_ids):
        return False, 'scope_changed'
    if row['person_id'] is not None and row['person_id'] != context.person_id:
        return False, 'person_changed'
    if row.get('conversation_id') is not None and row['conversation_id'] != context.conversation_id:
        return False, 'conversation_changed'
    if purpose not in (row.get('purposes') or ('conversation',)):
        return False, 'purpose_changed'
    if row.get('valid_from') and now < row['valid_from']:
        return False, 'not_yet_valid'
    if row.get('valid_until') and now >= row['valid_until']:
        return False, 'expired'
    return True, None


def _relevance(row, query):
    # Explicit continuing preferences are constraints, not topic search hits.
    explicit = row.get('preference_kind') in {'explicit', 'temporary'}
    text = (row['title'] + ' ' + row['content']).casefold()
    query = (query or '').strip().casefold()
    terms = {query[i:i+2] for i in range(max(0, len(query)-1))} if query else set()
    match = bool(query and (query in text or any(term in text for term in terms)))
    return (int(explicit), int(match), min(row.get('support_count', 0), 20), row['updated_at'])


def may_restate(left, right):
    left = re.sub(r'[^\w\u4e00-\u9fff]+', '', left.casefold())
    right = re.sub(r'[^\w\u4e00-\u9fff]+', '', right.casefold())
    return bool(left and right and SequenceMatcher(None, left, right).ratio() >= 0.65)


def expiry(validity, occurred_at, timezone='Asia/Shanghai'):
    """Resolve a bounded date against the evidence time, never review time."""
    if validity == 'persistent':
        return None
    if validity == 'turn':
        # The immediate turn is handled by Conversation.  A later background
        # batch must not turn this instruction into a durable preference.
        return occurred_at
    if validity == 'today':
        zone = ZoneInfo(timezone)
        local = occurred_at.astimezone(zone)
        return (local.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)).astimezone(UTC)
    raise AppError('reflection_conditions_invalid')


class HabitLedger:
    def __init__(self, companion, *, on_changed=None, source_reader=None):
        self.companion = companion
        self.on_changed = on_changed
        self.source_reader = source_reader or companion.identity.check_many

    async def candidates(self, context, *, include_inactive=False):
        await self.companion.identity.check(context)
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.style_rules WHERE actor_id=%s "
                "AND (person_id IS NULL OR person_id=%s) AND scope_ids<@%s::text[] "
                "AND (conversation_id IS NULL OR conversation_id=%s) "
                "AND (%s OR state='active') ORDER BY id",
                (context.actor_id, context.person_id, list(context.scope_ids),
                 context.conversation_id, include_inactive))).fetchall()
        permitted = await self.source_reader(
            context, tuple(source_refs(row['source_refs']) for row in rows))
        return tuple(row for row, valid in zip(rows, permitted, strict=True) if valid)

    async def material(self, context, *, purpose='conversation', query=None, budget=None):
        purpose = canonical_purpose(purpose)
        rows = [row for row in await self.candidates(context)
                if applicable(row, context, purpose)[0]]
        rows.sort(key=lambda row: _relevance(row, query), reverse=True)
        result = []
        for row in rows:
            # Each rule remains a separate optional, independently rechecked unit.
            body = {key: row[key] for key in ('id', 'kind', 'title', 'content',
                    'conditions', 'counterexamples', 'preference_kind', 'valid_until')}
            body['instruction'] = '当前用户明确要求优先；此材料仅是有条件的历史表达偏好。'
            semantic_role = ('constraint' if row['preference_kind'] in {'explicit', 'temporary'}
                             else 'interest' if row['kind'] == 'interest' else 'style')
            result.append(ContextContribution(
                'learned_habit', json.dumps(wire(body), ensure_ascii=False),
                source_refs(row['source_refs']), tuple(row['scope_ids']),
                65 if semantic_role == 'constraint' else 45, False,
                str(row['semantic_revision']), material_id='growth:'+row['id'],
                semantic_role=semantic_role,
                behavior_ref={'rule_id': row['id'], 'revision': row['revision'],
                              'semantic_revision': row['semantic_revision'], 'semantic_role': semantic_role}))
        return tuple(result)

    async def resolve(self, context, refs, *, purpose):
        purpose = canonical_purpose(purpose)
        await self.companion.identity.check(context)
        refs = tuple(refs)
        ids = sorted({str(ref['rule_id']) for ref in refs})
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                'SELECT * FROM companion.style_rules WHERE actor_id=%s AND id=ANY(%s)',
                (context.actor_id, ids))).fetchall()
            historical = await (await connection.execute(
                'SELECT * FROM companion.habit_revisions WHERE rule_id=ANY(%s) AND revision=ANY(%s)',
                (ids, [ref.get('revision') for ref in refs if type(ref.get('revision')) is int]))).fetchall() if purpose == 'history' else []
        by_id = {row['id']: row for row in rows}
        groups = tuple(source_refs(row['source_refs']) for row in rows)
        allowed = dict(zip((row['id'] for row in rows),
                           await self.source_reader(context, groups), strict=True))
        history_by_id = {(row['rule_id'], row['revision']): row for row in historical}
        history_allowed = dict(zip(((row['rule_id'], row['revision']) for row in historical),
            await self.source_reader(context,
                tuple(source_refs(row['source_refs']) for row in historical)), strict=True))
        answers = []
        for ref in sorted(refs, key=lambda value: str(value['rule_id'])):
            identifier = str(ref['rule_id'])
            row = by_id.get(identifier)
            visible_scope = (row and set(row['scope_ids']).issubset(context.scope_ids)
                and row['person_id'] in (None, context.person_id)
                and row['conversation_id'] in (None, context.conversation_id))
            if not visible_scope:
                answers.append({'rule_id': identifier, 'revision': None, 'semantic_revision': None,
                                'requested_revision': ref.get('revision'),
                                'requested_semantic_revision': ref.get('semantic_revision'),
                                'state': 'unavailable', 'eligible': False, 'compatible': False,
                                'reason': 'scope_unavailable'})
                continue
            requested_revision = ref.get('revision')
            historical_row = history_by_id.get((identifier, requested_revision)) if purpose == 'history' else None
            if purpose == 'history' and (not historical_row or not history_allowed.get((identifier, requested_revision))):
                answers.append({'rule_id': identifier, 'revision': row['revision'],
                    'semantic_revision': row['semantic_revision'], 'requested_revision': ref.get('revision'),
                    'requested_semantic_revision': ref.get('semantic_revision'), 'state': row['state'],
                    'eligible': False, 'compatible': False, 'reason': 'historical_source_unavailable'})
                continue
            eligible, reason = applicable(row, context, ref.get('purpose', 'conversation') if purpose == 'history' else purpose)
            if not allowed.get(identifier):
                eligible, reason = False, 'source_unavailable'
            expected_semantic = historical_row['semantic_revision'] if historical_row else ref.get('semantic_revision')
            compatible = eligible and expected_semantic == row['semantic_revision']
            body = historical_row['snapshot'] if historical_row else row
            if not allowed.get(identifier) and purpose != 'history':
                body = {}
            answers.append({'rule_id': identifier, 'revision': row['revision'],
                            'semantic_revision': row['semantic_revision'], 'requested_revision': ref.get('revision'),
                            'requested_semantic_revision': ref.get('semantic_revision'), 'state': row['state'],
                            'eligible': eligible, 'compatible': compatible,
                            'reason': reason or (None if compatible else 'semantic_changed'),
                            'source_refs': source_refs(historical_row['source_refs'] if historical_row else row['source_refs']) if body else (),
                            'scope_ids': tuple(body.get('scope_ids', ())), 'text': body.get('content'),
                            'conditions': body.get('conditions'),
                            **({'historical_revision': historical_row['revision'],
                                'historical_state': body.get('state')} if historical_row else {})})
        return tuple(answers)

    async def snapshot(self, connection, row, action, *, proposal_id=None, attempt_id=None):
        await connection.execute(
            'INSERT INTO companion.habit_revisions(rule_id,revision,semantic_revision,action,snapshot,source_refs,proposal_id,attempt_id) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(rule_id,revision) DO NOTHING',
            (row['id'], row['revision'], row['semantic_revision'], action, document(row),
             document(row['source_refs']), proposal_id, attempt_id))

    async def unresolved(self, connection, context, item, reason):
        await connection.execute(
            'INSERT INTO companion.habit_unresolved(proposal_id,actor_id,person_id,conversation_id,scope_ids,source_refs,proposal,reason) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(proposal_id) DO NOTHING',
            (item.proposal.proposal_id, context.actor_id, context.person_id, context.conversation_id, list(context.scope_ids),
             document(item.proposal.source_refs), document(item), reason))
        return {'_unresolved': reason}

    async def apply(self, connection, context, item, episodes, *, attempt_id, frozen=False, timezone='Asia/Shanghai'):
        """Apply a model proposal under the same transaction as its source checks."""
        proposal = item.proposal
        await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                 ('habit:'+context.actor_id,))
        independent = [episodes[key] for key in item.support_ids if episodes[key].event_id
                       and episodes[key].origin_kind in {'human', 'observation', 'tool_result'}
                       and episodes[key].source_refs]
        if not independent:
            return None
        direct = any(ep.origin_kind == 'human' and ep.speaker_id and ep.speaker_id == context.person_id
                     for ep in independent)
        preference = item.preference_kind if direct else 'inferred'
        if item.validity != 'persistent':
            preference = 'temporary'
        if item.action == 'unresolved':
            return await self.unresolved(connection, context, item, 'rule_identity_unresolved')
        if item.action == 'retract' and not direct:
            return await self.unresolved(connection, context, item, 'explicit_correction_required')
        if frozen and not (direct and item.action in {'revise', 'retract'}):
            return None
        target = None
        if item.target:
            target = await (await connection.execute(
                'SELECT * FROM companion.style_rules WHERE id=%s AND actor_id=%s FOR UPDATE',
                (item.target['rule_id'], context.actor_id))).fetchone()
            if (not target or not set(target['scope_ids']).issubset(context.scope_ids)
                    or target['person_id'] not in (None, context.person_id)
                    or target['conversation_id'] not in (None, context.conversation_id)
                    or target['semantic_revision'] != item.target['semantic_revision']):
                return await self.unresolved(connection, context, item, 'rule_changed_since_generation')
        elif item.action != 'create':
            return await self.unresolved(connection, context, item, 'rule_identity_unresolved')
        if target is None:
            # Exact restatements preserve identity, including a revoked one.
            # Other similarities are never silently merged or used to delete.
            same = await (await connection.execute(
                'SELECT * FROM companion.style_rules WHERE actor_id=%s AND kind=%s AND content=%s '
                'AND conditions=%s AND counterexamples=%s AND person_id IS NOT DISTINCT FROM %s '
                'AND conversation_id IS NOT DISTINCT FROM %s AND scope_ids=%s FOR UPDATE',
                (context.actor_id, proposal.kind, proposal.content, document(proposal.conditions),
                 document(proposal.counterexamples), context.person_id, context.conversation_id,
                 sorted(context.scope_ids)))).fetchall()
            if len(same) > 1:
                return await self.unresolved(connection, context, item, 'multiple_matching_rules')
            target = same[0] if same else None
        if target is None and not item.rules_complete:
            return await self.unresolved(connection, context, item, 'rule_candidates_incomplete')
        if target is None:
            similar = await (await connection.execute(
                'SELECT content,conditions FROM companion.style_rules WHERE actor_id=%s AND kind=%s '
                'AND person_id IS NOT DISTINCT FROM %s AND conversation_id IS NOT DISTINCT FROM %s '
                'AND scope_ids=%s AND state IN (%s,%s)',
                (context.actor_id, proposal.kind, context.person_id, context.conversation_id,
                 sorted(context.scope_ids), 'active', 'revoked'))).fetchall()
            if any(row['conditions'] == dict(proposal.conditions) and may_restate(row['content'], proposal.content)
                   for row in similar):
                return await self.unresolved(connection, context, item, 'similar_rule_identity_unresolved')
        if target and target['state'] != 'active':
            return await self.unresolved(connection, context, item, 'rule_not_active')
        if target and target['preference_kind'] in {'explicit', 'temporary'} and not direct and item.action in {'revise', 'retract'}:
            return await self.unresolved(connection, context, item, 'explicit_preference_precedes_inference')
        if target and not {signature(ref) for ref in source_refs(target['source_refs'])}.issubset(
                {signature(ref) for ref in proposal.source_refs}):
            return await self.unresolved(connection, context, item, 'rule_dependency_changed')
        occurred = max(ep.occurred_at for ep in independent)
        until = expiry(item.validity, occurred, timezone)
        action = item.action if item.target else ('support' if target else 'create')
        if target is None:
            target = await (await connection.execute(
                'INSERT INTO companion.style_rules(id,actor_id,person_id,kind,title,content,conditions,counterexamples,'
                'episode_ids,source_refs,scope_ids,state,preference_kind,conversation_id,purposes,valid_from,valid_until) '
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s) RETURNING *",
                (proposal.proposal_id, context.actor_id, context.person_id, proposal.kind, item.title,
                 proposal.content, document(proposal.conditions), document(proposal.counterexamples),
                 list(proposal.episode_ids), document(proposal.source_refs), sorted(context.scope_ids),
                 preference, context.conversation_id, [canonical_purpose(item.purpose)], occurred, until))).fetchone()
        supports = 0
        for episode in independent:
            ids = sorted({ref.source_id for ref in episode.source_refs})
            # Source revisions and derived episodes from the same original do
            # not count as new independent events.
            duplicate = await (await connection.execute(
                'SELECT 1 FROM companion.habit_supports WHERE rule_id=%s AND '
                '(event_id=%s OR source_ids && %s::text[]) LIMIT 1',
                (target['id'], episode.event_id, ids))).fetchone()
            if duplicate:
                continue
            inserted = await (await connection.execute(
                'INSERT INTO companion.habit_supports(rule_id,event_id,source_ids,source_refs,proposal_id) '
                'VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING event_id',
                (target['id'], episode.event_id, ids, document(episode.source_refs), proposal.proposal_id))).fetchone()
            supports += bool(inserted)
        if action == 'support' and not supports:
            return None
        refs = unique_refs(target['source_refs'], proposal.source_refs)
        semantic = action in {'revise', 'retract'}
        if action == 'create':
            updated = await (await connection.execute(
                'UPDATE companion.style_rules SET support_count=%s WHERE id=%s RETURNING *',
                (supports, target['id']))).fetchone()
        else:
            values = {**target}
            if action == 'revise':
                values.update(title=item.title, content=proposal.content, conditions=proposal.conditions,
                              counterexamples=proposal.counterexamples, preference_kind=preference,
                              purposes=[canonical_purpose(item.purpose)], valid_from=occurred, valid_until=until,
                              reason='evidence_revision')
            elif action == 'retract':
                values.update(state='revoked', reason='explicit_user_correction')
            updated = await (await connection.execute(
                'UPDATE companion.style_rules SET title=%s,content=%s,conditions=%s,counterexamples=%s,'
                'preference_kind=%s,purposes=%s,valid_from=%s,valid_until=%s,state=%s,reason=%s,'
                'source_refs=%s,support_count=support_count+%s,revision=revision+1,'
                'semantic_revision=semantic_revision+%s,updated_at=now() WHERE id=%s RETURNING *',
                (values['title'], values['content'], document(values['conditions']), document(values['counterexamples']),
                 values['preference_kind'], list(values['purposes']), values['valid_from'], values['valid_until'],
                 values['state'], values['reason'], document(refs), supports, int(semantic), target['id']))).fetchone()
        await self.snapshot(connection, updated, action, proposal_id=proposal.proposal_id, attempt_id=attempt_id)
        return updated

    async def command(self, context, body, *, state=None):
        await self.companion.identity.check(context)
        async def apply(connection):
            row = await (await connection.execute(
                'SELECT * FROM companion.style_rules WHERE id=%s AND actor_id=%s',
                (body.get('id'), context.actor_id))).fetchone()
            if (not row or not set(row['scope_ids']).issubset(context.scope_ids)
                    or (not management_scope(context) and (row['person_id'] not in (None, context.person_id)
                        or row['conversation_id'] not in (None, context.conversation_id)))):
                raise AppError('growth_item_not_found', status=404)
            await self.companion.validate_persistence(connection, context, source_refs(row['source_refs']))
            original_refs = row['source_refs']
            row = await (await connection.execute(
                'SELECT * FROM companion.style_rules WHERE id=%s AND actor_id=%s FOR UPDATE',
                (body.get('id'), context.actor_id))).fetchone()
            if not row or row['source_refs'] != original_refs:
                raise AppError('growth_rule_changed', status=409)
            revision(row, body.get('expected_revision'))
            if state:
                if state not in {'active', 'revoked'} or (state == 'active' and row['state'] == 'invalidated'):
                    raise AppError('invalid_growth_state', status=409)
                values = {**row, 'state': state, 'reason': 'explicit_user_change'}
            else:
                values = {**row}
                for key in ('title', 'content', 'conditions', 'counterexamples', 'purposes', 'valid_until'):
                    if key in body:
                        values[key] = body[key]
                if not isinstance(values['content'], str) or not values['content'].strip() or not isinstance(values['conditions'], dict) or not isinstance(values['counterexamples'], (list, tuple)):
                    raise AppError('invalid_growth_revision')
                if not isinstance(values['purposes'], (list, tuple)) or not values['purposes'] or any(not isinstance(x, str) for x in values['purposes']):
                    raise AppError('invalid_growth_revision')
                until = values['valid_until']
                if isinstance(until, str):
                    try:
                        until = datetime.fromisoformat(until.replace('Z', '+00:00'))
                    except ValueError:
                        raise AppError('invalid_growth_revision') from None
                if until is not None and (not isinstance(until, datetime) or until.tzinfo is None):
                    raise AppError('invalid_growth_revision')
                values.update(valid_until=until, purposes=[canonical_purpose(p) for p in values['purposes']],
                              preference_kind='explicit', reason='explicit_user_correction')
            updated = await (await connection.execute(
                'UPDATE companion.style_rules SET title=%s,content=%s,conditions=%s,counterexamples=%s,'
                'purposes=%s,valid_until=%s,preference_kind=%s,state=%s,reason=%s,'
                'revision=revision+1,semantic_revision=semantic_revision+1,updated_at=now() WHERE id=%s RETURNING *',
                (values['title'], values['content'], document(values['conditions']), document(values['counterexamples']),
                 list(values['purposes']), values['valid_until'], values['preference_kind'], values['state'],
                 values['reason'], row['id']))).fetchone()
            await self.snapshot(connection, updated, state or 'explicit_revision')
            if self.on_changed:
                await self.on_changed(connection, context, updated, updated['kind'])
            return CommandResult(body['client_request_id'], {'id': row['id'], 'state': updated['state'],
                'semantic_revision': updated['semantic_revision']}, updated['revision'])
        return await self.companion.commands.execute(context, 'companion.growth.'+(state or 'revise'),
            body['client_request_id'], signature(body), apply)

    async def history(self, context, identifier):
        await self.companion.identity.check(context)
        async with self.companion.database.transaction(read_only=True) as connection:
            current = await (await connection.execute(
                'SELECT * FROM companion.style_rules WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[] '
                'AND (%s OR person_id IS NULL OR person_id=%s) AND (%s OR conversation_id IS NULL OR conversation_id=%s)',
                (identifier, context.actor_id, list(context.scope_ids), management_scope(context), context.person_id,
                 management_scope(context), context.conversation_id))).fetchone()
            if not current:
                raise AppError('growth_item_not_found', status=404)
            history = await (await connection.execute(
                'SELECT * FROM companion.habit_revisions WHERE rule_id=%s ORDER BY revision', (identifier,))).fetchall()
        permitted = await self.source_reader(context,
            tuple(source_refs(row['source_refs']) for row in history))
        return {'id': identifier, 'current_state': current['state'], 'current_reason': current['reason'],
                'revisions': [row for row, allowed in zip(history, permitted, strict=True) if allowed]}

    async def dismiss_unresolved(self, context, body):
        await self.companion.identity.check(context)
        async def apply(connection):
            row = await (await connection.execute(
                'SELECT * FROM companion.habit_unresolved WHERE proposal_id=%s AND actor_id=%s',
                (body.get('proposal_id'), context.actor_id))).fetchone()
            if (not row or not set(row['scope_ids']).issubset(context.scope_ids)
                    or (not management_scope(context) and (row['person_id'] not in (None, context.person_id)
                        or row['conversation_id'] not in (None, context.conversation_id)))):
                raise AppError('growth_item_not_found', status=404)
            await self.companion.validate_persistence(connection, context, source_refs(row['source_refs']))
            before = row['source_refs']
            row = await (await connection.execute(
                'SELECT * FROM companion.habit_unresolved WHERE proposal_id=%s FOR UPDATE',
                (body['proposal_id'],))).fetchone()
            if not row or row['source_refs'] != before:
                raise AppError('growth_item_changed', status=409)
            revision(row, body.get('expected_revision'))
            updated = await (await connection.execute(
                "UPDATE companion.habit_unresolved SET state='dismissed',revision=revision+1 "
                "WHERE proposal_id=%s RETURNING revision", (row['proposal_id'],))).fetchone()
            return CommandResult(body['client_request_id'], {'proposal_id': row['proposal_id'],
                                 'state': 'dismissed'}, updated['revision'])
        return await self.companion.commands.execute(context, 'companion.growth.unresolved.dismiss',
            body['client_request_id'], signature(body), apply)
