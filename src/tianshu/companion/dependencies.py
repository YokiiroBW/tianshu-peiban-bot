from tianshu.conversation.codec import document
from tianshu.conversation.provenance import changes_for,intersects


class CompanionDependencies:
    async def invalidate(self,connection,source_refs,kind):
        changes,selector=changes_for(source_refs)
        for table in ("companion.affect_events","companion.style_rules"):
            rows=await (await connection.execute(f"SELECT * FROM {table} WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
            for row in rows:
                if not intersects(row["source_refs"],changes,kind):
                    continue
                if table=="companion.affect_events":
                    await connection.execute("DELETE FROM companion.affect_events WHERE actor_id=%s AND event_id=%s",(row["actor_id"],row["event_id"]))
                    await connection.execute("DELETE FROM companion.affect_regulation WHERE actor_id=%s",(row["actor_id"],))
                else:
                    await connection.execute("UPDATE companion.style_rules SET state='invalidated',reason='source_unavailable',title='',content='',conditions='{}',counterexamples='[]',revision=revision+1 WHERE id=%s",(row["id"],))
