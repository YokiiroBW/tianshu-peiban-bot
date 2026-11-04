"""Resolve per-input learning progress from Companion's existing ledgers."""

from tianshu.conversation.codec import document, signature, source_refs
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs


def _result(item, *, state='not_recorded', revision_state='not_recorded', reason=None,
            episode=None, refs=(), evidence=(), evidence_complete=None, coverage_missing=()):
    return {'input_id': item['input_id'], 'event_id': item['event_id'],
            'event_revision': item['event_revision'], 'state': state,
            'revision_state': revision_state, 'episode_id': episode.episode_id if episode else None,
            'episode_revision': episode.revision if episode else None,
            'source_refs': tuple(refs), 'revision_evidence': tuple(evidence),
            'revision_evidence_complete': evidence_complete,
            'coverage_missing': tuple(coverage_missing), 'reason': reason}


def _state(row, episode):
    if row['revision'] < episode.revision:
        return 'pending', 'episode_revision_pending'
    if row['revision'] > episode.revision:
        return 'unavailable', 'episode_revision_changed'
    if row['state'] == 'unavailable':
        return 'unavailable', 'input_unavailable'
    if row['state'] == 'reviewed' and row['reviewed_revision'] >= episode.revision:
        return 'reviewed', row['skip_reason']
    if row['failure_state'] == 'blocked':
        return 'failed', row['failure_error']
    if row['retry_state'] == 'blocked' and not row['waiting_on_original']:
        return 'failed', row['retry_error']
    if row['waiting_on_original']:
        return 'pending', 'original_attempt_unresolved'
    return 'pending', None


class GrowthInputStatus:
    def __init__(self, companion, jobs):
        self.companion, self.jobs = companion, jobs

    @staticmethod
    async def _input_on(connection, context, scope_key, episode_id):
        rows = await (await connection.execute(
            'SELECT i.scope_key,i.episode_id,i.revision,i.reviewed_revision,i.state,i.missing,i.skip_reason,'
            'b.state AS retry_state,b.waiting_on_original,b.last_error AS retry_error,'
            'f.state AS failure_state,f.error_code AS failure_error '
            'FROM companion.growth_inputs i JOIN companion.organize_state s '
            'ON s.actor_id=i.actor_id AND s.scope_key=i.scope_key '
            'LEFT JOIN companion.growth_retry_batches b ON b.actor_id=i.actor_id AND b.scope_key=i.scope_key '
            'AND b.retry_key=i.retry_key '
            'LEFT JOIN companion.growth_material_failures f ON f.actor_id=i.actor_id AND f.scope_key=i.scope_key '
            'AND f.failure_key=i.retry_key AND f.runtime_generation=s.runtime_generation '
            'WHERE i.actor_id=%s AND i.episode_id=%s AND i.context->>\'conversation_id\'=%s '
            'AND i.context->\'scope_ids\' <@ %s::jsonb '
            'AND s.runtime_generation=(SELECT id FROM runtime.installation_generation WHERE singleton=true) '
            'ORDER BY i.scope_key',
            (context.actor_id, episode_id, context.conversation_id, document(sorted(context.scope_ids))))).fetchall()
        exact = [row for row in rows if row['scope_key'] == scope_key]
        if exact:
            return exact[0]
        if len(rows) == 1:
            return rows[0]
        return {'ambiguous': True} if rows else None

    async def _revisions_on(self, connection, context, episode, original_pairs):
        # A successful batch is not proof: only the same proposal's support
        # event and authorized original source can establish a rule revision.
        rows = await (await connection.execute(
            "SELECT h.rule_id,h.revision,h.semantic_revision,h.action,h.proposal_id,h.attempt_id,"
            "h.source_refs AS revision_sources,h.snapshot->'scope_ids' AS snapshot_scopes,"
            "h.snapshot->>'person_id' AS snapshot_person,h.snapshot->>'conversation_id' AS snapshot_conversation,"
            "r.scope_ids AS rule_scopes,r.person_id AS rule_person,r.conversation_id AS rule_conversation,"
            "s.source_refs AS support_sources FROM companion.habit_revisions h "
            "JOIN companion.style_rules r ON r.id=h.rule_id "
            "JOIN companion.habit_supports s ON s.rule_id=h.rule_id AND s.proposal_id=h.proposal_id "
            "WHERE r.actor_id=%s AND s.event_id=%s AND h.action IN ('revise','retract') "
            "AND h.proposal_id IS NOT NULL AND h.attempt_id IS NOT NULL "
            "ORDER BY h.created_at DESC,h.rule_id,h.revision DESC LIMIT 129",
            (context.actor_id, episode.event_id))).fetchall()
        found = []
        for row in rows[:128]:
            snapshot_scopes = row['snapshot_scopes']
            if (not isinstance(snapshot_scopes, list) or not set(snapshot_scopes).issubset(context.scope_ids)
                    or not set(row['rule_scopes']).issubset(context.scope_ids)
                    or row['snapshot_conversation'] not in (None, context.conversation_id)
                    or row['rule_conversation'] not in (None, context.conversation_id)
                    or row['snapshot_person'] not in (None, episode.person_id)
                    or row['rule_person'] not in (None, episode.person_id)):
                continue
            support = source_refs(row['support_sources'])
            revision = source_refs(row['revision_sources'])
            if not support or not revision or not original_pairs.intersection(
                    (ref.source_id, ref.revision) for ref in support):
                continue
            if not await self.companion.sources.validate_persistence(
                    connection, context, unique_refs(support, revision)):
                continue
            found.append({key: row[key] for key in (
                'rule_id', 'revision', 'semantic_revision', 'action', 'proposal_id', 'attempt_id')})
        return tuple(found), len(rows) <= 128

    async def read(self, context, *, inputs):
        if (not isinstance(inputs, (tuple, list)) or len(inputs) > 128 or not context.actor_id
                or not context.conversation_id or any(
                    not isinstance(item, dict)
                    or any(not isinstance(item.get(key), str) or not 1 <= len(item[key]) <= 512
                           for key in ('input_id', 'event_id'))
                    or type(item.get('event_revision')) is not int or item['event_revision'] < 1
                    for item in inputs)):
            raise AppError('invalid_growth_input_status')
        await self.companion.identity.check(context)
        scope_key = signature(sorted(context.scope_ids))
        results = []
        for item in inputs:
            episode_id = 'input:' + item['input_id']
            async with self.companion.database.transaction(read_only=True) as connection:
                known = await self._input_on(connection, context, scope_key, episode_id)
            if not known:
                results.append(_result(item, reason='growth_input_not_recorded'))
                continue
            if known.get('ambiguous'):
                results.append(_result(item, state='unavailable', reason='input_scope_ambiguous'))
                continue
            try:
                episode = await self.companion.memory.episode_at(context, episode_id)
                if (episode.actor_id != context.actor_id or episode.episode_id != episode_id
                        or episode.event_id != item['event_id'] or episode.conversation_id != context.conversation_id
                        or (context.conversation_kind != 'group'
                            and episode.person_id not in (None, context.person_id))):
                    results.append(_result(item, state='unavailable', reason='input_scene_mismatch'))
                    continue
                original = tuple(episode.source_refs)
                if not original:
                    results.append(_result(item, state='unavailable', reason='input_source_unavailable'))
                    continue
                async with self.companion.database.transaction() as connection:
                    generation = await self.jobs.current_generation_on(connection)
                    await self.jobs.assert_generation_on(connection, generation)
                    locked = await self.companion.memory.lock_episodes(
                        context, ((episode_id, episode.revision),), connection=connection)
                    if len(locked.items) != 1:
                        results.append(_result(item, state='unavailable', reason='episode_revised_or_unavailable'))
                        continue
                    episode = locked.items[0]
                    if (episode.actor_id != context.actor_id or episode.episode_id != episode_id
                            or episode.event_id != item['event_id'] or episode.conversation_id != context.conversation_id
                            or tuple(episode.source_refs) != original
                            or (context.conversation_kind != 'group'
                                and episode.person_id not in (None, context.person_id))):
                        results.append(_result(item, state='unavailable', reason='episode_revised_or_unavailable'))
                        continue
                    descriptions = await self.companion.sources.describe_for_write_on(connection, context, original)
                    original_pairs = {(value['source_id'], value['revision']) for value in descriptions
                                      if value['revision'] == item['event_revision']
                                      and (value.get('locator') or {}).get('event_id') == item['event_id']}
                    if not original_pairs:
                        results.append(_result(item, state='unavailable', reason='input_event_revision_mismatch'))
                        continue
                    row = await self._input_on(connection, context, scope_key, episode_id)
                    if not row:
                        results.append(_result(item, reason='growth_input_not_recorded'))
                        continue
                    if row.get('ambiguous'):
                        results.append(_result(item, state='unavailable', reason='input_scope_ambiguous'))
                        continue
                    state, reason = _state(row, episode)
                    evidence, evidence_complete = await self._revisions_on(connection, context, episode, original_pairs)
                    revision_state = ('applied' if evidence else 'unresolved'
                        if not evidence_complete or 'learning_identity_unresolved' in (row['missing'] or ()) else 'not_recorded')
                    if not evidence_complete and reason is None:
                        reason = 'revision_evidence_partial'
                    refs = unique_refs(episode.source_refs, getattr(episode, 'supplement_refs', ()))
                    results.append(_result(item, state=state, revision_state=revision_state,
                        reason=reason, episode=episode, refs=refs, evidence=evidence,
                        evidence_complete=evidence_complete, coverage_missing=row['missing'] or ()))
            except AppError as error:
                if error.status not in {401, 403, 404} and error.code not in {
                        'access_changed', 'delivery_access_changed', 'source_unavailable',
                        'episode_unavailable', 'episode_history_unavailable'}:
                    raise
                results.append(_result(item, state='unavailable', reason='input_source_unavailable'))
        return tuple(results)
