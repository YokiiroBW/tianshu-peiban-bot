from tianshu.conversation.codec import document
from tianshu.sources.dependencies import changes_for,intersects


class LifeDependencies:
    async def invalidate(self,connection,source_refs,kind):
        changes,selector=changes_for(source_refs)
        for table in ("life.diary_revisions","life.chapter_revisions"):
            key="diary_id" if table.endswith("diary_revisions") else "chapter_id"
            rows=await (await connection.execute(f"SELECT {key},revision,recipe FROM {table} WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(recipe->'source_refs','[]'::jsonb)) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
            for row in rows:
                if intersects(row["recipe"]["source_refs"],changes,kind):
                    await connection.execute(f"UPDATE {table} SET content='',recipe=%s,disclosure_ref=NULL,publication_metadata=NULL WHERE {key}=%s AND revision=%s",(document({"source_refs":row["recipe"]["source_refs"],"invalidated":True}),row[key],row["revision"]))
        rows=await (await connection.execute("SELECT id,source_refs FROM life.diaries WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE life.diaries SET state='failed',generation_recipe=NULL,episode_ids='{}',reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
        rows=await (await connection.execute("SELECT id,generation_recipe FROM life.chapters WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(generation_recipe->'source_refs','[]'::jsonb)) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["generation_recipe"]["source_refs"],changes,kind):
                await connection.execute("UPDATE life.chapters SET state='failed',generation_recipe=NULL,reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
        for table in ("life.activities","life.goals","life.commitments","life.contact_candidates"):
            rows=await (await connection.execute(f"SELECT id,source_refs FROM {table} WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
            for row in rows:
                if not intersects(row["source_refs"],changes,kind):
                    continue
                if table=="life.activities":
                    await connection.execute("UPDATE life.activities SET title='材料已失效',checkpoint_data='{}',state=CASE WHEN state='completed' THEN state ELSE 'dormant' END,waiting_reason='source_unavailable',resume_condition=NULL,next_due_at=NULL,revision=revision+1 WHERE id=%s",(row["id"],))
                    await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE activity_id=%s",(row["id"],))
                elif table=="life.goals":
                    await connection.execute("UPDATE life.goals SET title='',motivation='',state='paused',revision=revision+1 WHERE id=%s",(row["id"],))
                elif table=="life.commitments":
                    await connection.execute("UPDATE life.commitments SET title='',arguments='{}',state=CASE WHEN state IN ('completed','unknown','dispatching') THEN state ELSE 'cancelled' END,reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
                else:
                    await connection.execute("UPDATE life.contact_candidates SET summary='',payload='{}',state='cancelled',reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))

        rows=await (await connection.execute("SELECT id,source_refs FROM life.outfits WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE life.outfits SET description='',appearance='',reference=NULL,revision=revision+1 WHERE id=%s",(row["id"],))
                await connection.execute("UPDATE life.actor_state SET outfit_id=NULL,revision=revision+1 WHERE outfit_id=%s",(row["id"],))
        rows=await (await connection.execute("SELECT id,source_ref FROM life.readings WHERE source_ref->>'source_id'=ANY(%s)",(selector,))).fetchall()
        for row in rows:
            if intersects((row["source_ref"],),changes,kind):
                await connection.execute("UPDATE life.readings SET state='closed',covered='[]',next_position=NULL,requested='{}',revision=revision+1,updated_at=now() WHERE id=%s",(row["id"],))
