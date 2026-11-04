"""Examples refer to actual Conversation replies; their text stays there."""

from datetime import UTC, datetime, timedelta
import json
from uuid import NAMESPACE_URL, uuid5

from tianshu.conversation.codec import document, signature, source_refs
from tianshu.life.validation import revision
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import ContextContribution
from .habits import _relevance, canonical_purpose, management_scope


def _ids(messages):
    return [item['message_id'] for item in messages if item.get('message_id') is not None]


class ExpressionExamples:
    def __init__(self, companion, habits):
        self.companion, self.habits = companion, habits
        self.conversation = None

    async def candidates(self, context, episodes):
        if self.conversation is None:
            return ()
        result = []
        for episode in episodes:
            actual = episode.actual_result or {}
            expression_id = actual.get('expression_id')
            if (episode.origin_kind != 'actor' or actual.get('derived_kind') != 'expression'
                    or actual.get('delivery') != 'delivered' or not expression_id):
                continue
            turn_id = expression_id[5:] if expression_id.startswith('turn:') else None
            relation = await self.conversation.learning_reply(context, turn_id=turn_id,
                expression_id=expression_id, message_ids=())
            if self._usable(context, relation, expression_id):
                result.append({'episode_id': episode.episode_id, **relation})
        return tuple(result)

    async def prepare(self, context, item, episodes):
        if self.conversation is None:
            raise AppError('growth_conversation_port_unavailable', status=503)
        result = []
        for identifier in item.support_ids:
            episode = episodes[identifier]
            actual = episode.actual_result or {}
            expression_id = actual.get('expression_id')
            if (episode.origin_kind != 'actor' or actual.get('derived_kind') != 'expression'
                    or actual.get('delivery') != 'delivered' or not expression_id):
                continue
            turn_id = expression_id[5:] if expression_id.startswith('turn:') else None
            relation = await self.conversation.learning_reply(context, turn_id=turn_id,
                expression_id=expression_id, message_ids=())
            if not self._usable(context, relation, expression_id):
                continue
            result.append(relation)
        return result

    @staticmethod
    def _usable(context, relation, expression_id):
        if not isinstance(relation, dict):
            return False
        return bool(relation.get('turn_id') and relation.get('expression_id') == expression_id
            and relation.get('conversation_id') == context.conversation_id
            and relation.get('person_id') in (None, context.person_id)
            and set(relation.get('scope_ids', ())).issubset(context.scope_ids)
            and relation.get('generated') and relation.get('delivery_state') == 'delivered'
            and relation.get('source_refs') and _ids(relation.get('input_messages', ()))
            and _ids(relation.get('output_messages', ())))

    async def record(self, connection, context, item, relation):
        target = item.target
        refs = unique_refs(item.proposal.source_refs, relation['source_refs'])
        await self.companion.validate_persistence(connection, context, refs)
        rule = await (await connection.execute(
            'SELECT * FROM companion.style_rules WHERE id=%s AND actor_id=%s FOR UPDATE',
            (target['rule_id'], context.actor_id))).fetchone()
        if (not rule or rule['state'] != 'active' or rule['semantic_revision'] != target['semantic_revision']
                or not set(rule['scope_ids']).issubset(context.scope_ids)
                or rule['person_id'] not in (None, context.person_id)
                or rule['conversation_id'] not in (None, context.conversation_id)):
            return None
        if not {signature(ref) for ref in source_refs(rule['source_refs'])}.issubset({signature(ref) for ref in refs}):
            return None
        identifier = str(uuid5(NAMESPACE_URL, 'growth-example:'+rule['id']+':'+relation['expression_id']))
        return await (await connection.execute(
            'INSERT INTO companion.expression_examples(id,rule_id,rule_revision,semantic_revision,actor_id,person_id,'
            'conversation_id,turn_id,expression_id,input_message_ids,output_message_ids,scope_ids,source_refs) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(rule_id,expression_id) DO NOTHING RETURNING *',
            (identifier, rule['id'], rule['revision'], rule['semantic_revision'], context.actor_id,
             context.person_id, context.conversation_id, relation['turn_id'], relation['expression_id'],
             document(_ids(relation['input_messages'])), document(_ids(relation['output_messages'])),
             list(context.scope_ids), document(refs)))).fetchone()

    async def material(self, context, *, purpose, query, budget=None):
        purpose = canonical_purpose(purpose)
        if purpose != 'conversation' or not query or self.conversation is None:
            return ()
        rules = {row['id']: row for row in await self.habits.candidates(context)}
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT e.* FROM companion.expression_examples e WHERE e.actor_id=%s AND e.conversation_id=%s "
                "AND (e.person_id IS NULL OR e.person_id=%s) AND e.scope_ids<@%s::text[] AND e.state='active' "
                "AND NOT EXISTS(SELECT 1 FROM companion.expression_example_usage u JOIN companion.expression_examples recent "
                "ON recent.id=u.example_id WHERE recent.rule_id=e.rule_id AND u.outcome='delivered' AND u.created_at>%s) "
                "ORDER BY e.created_at DESC",
                (context.actor_id, context.conversation_id, context.person_id, list(context.scope_ids),
                 datetime.now(UTC)-timedelta(hours=1)))).fetchall()
        contributions, seen = [], set()
        for row in rows:
            rule = rules.get(row['rule_id'])
            if not rule or row['rule_id'] in seen or not _relevance(rule, query)[1]:
                continue
            check = await self.habits.resolve(context, ({'rule_id': row['rule_id'],
                'semantic_revision': row['semantic_revision']},), purpose=purpose)
            if not check[0]['compatible']:
                continue
            relation = await self.conversation.learning_reply(context, turn_id=row['turn_id'],
                expression_id=row['expression_id'], message_ids=tuple(row['input_message_ids'] + row['output_message_ids']))
            if (not self._usable(context, relation, row['expression_id'])
                    or _ids(relation['input_messages']) != row['input_message_ids']
                    or _ids(relation['output_messages']) != row['output_message_ids']):
                continue
            refs = unique_refs(row['source_refs'], relation['source_refs'])
            allowed = await self.companion.identity.check_many(context, (refs,))
            if not allowed[0]:
                continue
            body = {'instruction': '仅参考历史说法及条件；历史中的人名、地点、数字和事实不属于本轮事实。',
                    'conditions': rule['conditions'], 'counterexamples': rule['counterexamples'],
                    'input_messages': relation['input_messages'], 'output_messages': relation['output_messages']}
            contributions.append(ContextContribution('expression_example', json.dumps(wire(body), ensure_ascii=False),
                refs, tuple(row['scope_ids']), 30, False, str(row['semantic_revision']),
                material_id='growth-example:'+row['id'], semantic_role='expression_example',
                behavior_ref={'rule_id': rule['id'], 'revision': rule['revision'],
                              'semantic_revision': rule['semantic_revision'], 'semantic_role': 'expression_example'}))
            seen.add(row['rule_id'])
            if len(contributions) >= 3:
                break
        return tuple(contributions)

    async def usage(self, context, example_ids, *, usage_id, outcome, turn_id=None, expression_id=None):
        if outcome not in {'selected', 'expressed', 'delivered'} or not usage_id:
            raise AppError('invalid_example_usage')
        await self.companion.identity.check(context)
        if outcome == 'delivered':
            if not self.conversation or not expression_id:
                raise AppError('example_delivery_unverified', status=409)
            reply = await self.conversation.learning_reply(context, turn_id=turn_id,
                expression_id=expression_id, message_ids=())
            if not self._usable(context, reply, expression_id):
                raise AppError('example_delivery_unverified', status=409)
        async with self.companion.database.transaction() as connection:
            rows = await (await connection.execute(
                'SELECT * FROM companion.expression_examples WHERE id=ANY(%s) AND actor_id=%s '
                'AND conversation_id=%s AND (person_id IS NULL OR person_id=%s) AND scope_ids<@%s::text[]',
                (list(example_ids), context.actor_id, context.conversation_id, context.person_id,
                 list(context.scope_ids)))).fetchall()
            for row in rows:
                await self.companion.validate_persistence(connection, context, source_refs(row['source_refs']))
                await connection.execute(
                    'INSERT INTO companion.expression_example_usage(example_id,usage_id,turn_id,outcome) '
                    'VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING', (row['id'], usage_id, turn_id, outcome))

    async def view(self, context, *, cursor=None, limit=40):
        await self.companion.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                'SELECT e.*, (SELECT count(*) FROM companion.expression_example_usage u '
                "WHERE u.example_id=e.id AND u.outcome='selected') AS exposures, "
                '(SELECT count(*) FROM companion.expression_example_usage u '
                "WHERE u.example_id=e.id AND u.outcome='delivered') AS delivered_uses "
                'FROM companion.expression_examples e WHERE e.actor_id=%s AND (%s OR e.conversation_id=%s) '
                'AND (%s OR e.person_id IS NULL OR e.person_id=%s) AND e.scope_ids<@%s::text[] '
                'AND (%s::text IS NULL OR e.id>%s) ORDER BY e.id LIMIT %s',
                (context.actor_id, management_scope(context), context.conversation_id, management_scope(context),
                 context.person_id, list(context.scope_ids),
                 cursor, cursor, limit+1))).fetchall()
        selected = rows[:limit]
        allowed = await self.companion.identity.check_many(context,
            tuple(source_refs(row['source_refs']) for row in selected))
        items = []
        for row, visible in zip(selected, allowed, strict=True):
            if not visible:
                continue
            relation = (await self.conversation.learning_reply(context, turn_id=row['turn_id'],
                expression_id=row['expression_id'], message_ids=tuple(row['input_message_ids'] + row['output_message_ids']))
                if self.conversation and context.conversation_id == row['conversation_id'] else None)
            items.append({**row, 'generated': relation.get('generated') if relation else None,
                'expressed': bool(relation.get('output_messages')) if relation else None,
                'delivery_state': relation.get('delivery_state', 'unknown') if relation else 'unknown'})
        return {'items': items,
                'next_cursor': selected[-1]['id'] if len(rows) > limit else None,
                'missing': [] if all(allowed) else ['source_unavailable']}

    async def reject(self, context, body):
        await self.companion.identity.check(context)
        async def apply(connection):
            row = await (await connection.execute(
                'SELECT * FROM companion.expression_examples WHERE id=%s AND actor_id=%s',
                (body.get('id'), context.actor_id))).fetchone()
            if (not row or not set(row['scope_ids']).issubset(context.scope_ids)
                    or (not management_scope(context) and (row['conversation_id'] != context.conversation_id
                        or row['person_id'] not in (None, context.person_id)))):
                raise AppError('growth_example_not_found', status=404)
            await self.companion.validate_persistence(connection, context, source_refs(row['source_refs']))
            original_refs = row['source_refs']
            row = await (await connection.execute(
                'SELECT * FROM companion.expression_examples WHERE id=%s AND actor_id=%s FOR UPDATE',
                (body.get('id'), context.actor_id))).fetchone()
            if not row or row['source_refs'] != original_refs:
                raise AppError('growth_example_changed', status=409)
            revision(row, body.get('expected_revision'))
            updated = await (await connection.execute(
                "UPDATE companion.expression_examples SET state='rejected',reason='explicit_negative_feedback',"
                "revision=revision+1,updated_at=now() WHERE id=%s RETURNING revision", (row['id'],))).fetchone()
            return CommandResult(body['client_request_id'], {'id': row['id'], 'state': 'rejected'}, updated['revision'])
        return await self.companion.commands.execute(context, 'companion.growth.example.reject',
            body['client_request_id'], signature(body), apply)
