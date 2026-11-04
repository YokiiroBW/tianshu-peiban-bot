from tianshu.conversation.codec import document
from tianshu.sources.dependencies import changes_for,intersects


class CompanionDependencies:
    TABLES = ('companion.affect_events', 'companion.style_rules', 'companion.learning_reflections',
              'companion.expression_examples', 'companion.habit_unresolved', 'companion.habit_supports',
              'companion.habit_revisions')

    async def actors_for_source(self, connection, source_refs, kind):
        changes, selector = changes_for(source_refs)
        actors = set()
        for table in self.TABLES:
            joined = table in {'companion.habit_supports', 'companion.habit_revisions'}
            actor = 'r.actor_id' if joined else 't.actor_id'
            join = ' JOIN companion.style_rules r ON r.id=t.rule_id' if joined else ''
            rows = await (await connection.execute(
                f"SELECT {actor} AS actor_id,t.source_refs FROM {table} t{join} "
                "WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(t.source_refs) s WHERE s->>'source_id'=ANY(%s))",
                (selector,))).fetchall()
            actors.update(row['actor_id'] for row in rows if intersects(row['source_refs'], changes, kind))
        return tuple(sorted(actors))

    async def invalidate(self,connection,source_refs,kind):
        for actor_id in await self.actors_for_source(connection, source_refs, kind):
            await self.invalidate_actor(connection, source_refs, kind, actor_id=actor_id)

    async def invalidate_actor(self,connection,source_refs,kind,*,actor_id):
        changes,selector=changes_for(source_refs)
        for table in self.TABLES:
            joined = table in {'companion.habit_supports', 'companion.habit_revisions'}
            owner = 'r.actor_id' if joined else 't.actor_id'
            join = ' JOIN companion.style_rules r ON r.id=t.rule_id' if joined else ''
            rows=await (await connection.execute(f"SELECT t.* FROM {table} t{join} WHERE {owner}=%s "
                "AND EXISTS (SELECT 1 FROM jsonb_array_elements(t.source_refs) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector))).fetchall()
            for row in rows:
                if not intersects(row["source_refs"],changes,kind):
                    continue
                if table=="companion.affect_events":
                    await connection.execute("DELETE FROM companion.affect_events WHERE actor_id=%s AND event_id=%s",(row["actor_id"],row["event_id"]))
                    await connection.execute("DELETE FROM companion.affect_regulation WHERE actor_id=%s",(row["actor_id"],))
                elif table=="companion.style_rules":
                    updated = await (await connection.execute(
                        "UPDATE companion.style_rules SET state='invalidated',reason='source_unavailable',title='',content='',"
                        "conditions='{}',counterexamples='[]',revision=revision+1,semantic_revision=semantic_revision+1 "
                        "WHERE id=%s AND state<>'invalidated' RETURNING *", (row['id'],))).fetchone()
                    if updated:
                        await connection.execute(
                            "INSERT INTO companion.habit_revisions(rule_id,revision,semantic_revision,action,snapshot,source_refs) "
                            "VALUES(%s,%s,%s,'source_invalidated',%s,%s) ON CONFLICT DO NOTHING",
                            (updated['id'], updated['revision'], updated['semantic_revision'],
                             document({'id': updated['id'], 'state': 'invalidated', 'reason': 'source_unavailable'}),
                             document(updated['source_refs'])))
                elif table=="companion.learning_reflections":
                    await connection.execute("UPDATE companion.learning_reflections SET state='invalidated',reason='source_unavailable',"
                        "content='',conditions='{}',counterexamples='[]',revision=revision+1 WHERE proposal_id=%s AND state='active'", (row['proposal_id'],))
                elif table=="companion.expression_examples":
                    await connection.execute("UPDATE companion.expression_examples SET state='invalidated',reason='source_unavailable',"
                        "revision=revision+1,updated_at=now() WHERE id=%s AND state<>'invalidated'", (row['id'],))
                elif table=="companion.habit_unresolved":
                    await connection.execute("UPDATE companion.habit_unresolved SET proposal='{}',reason='source_unavailable' WHERE proposal_id=%s", (row['proposal_id'],))
                elif table=="companion.habit_supports":
                    await connection.execute('DELETE FROM companion.habit_supports WHERE rule_id=%s AND event_id=%s', (row['rule_id'], row['event_id']))
                else:
                    await connection.execute("UPDATE companion.habit_revisions SET snapshot='{}' WHERE rule_id=%s AND revision=%s", (row['rule_id'], row['revision']))
