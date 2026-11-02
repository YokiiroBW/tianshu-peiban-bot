from tianshu.conversation.codec import document,signature,context_from_record
from tianshu.sources.dependencies import changes_for,intersects
from tianshu.runtime.errors import AppError


class LifeDependencies:
    def __init__(self,life=None):
        self.life=life

    async def invalidate(self,connection,source_refs,kind):
        changes,selector=changes_for(source_refs)
        affected=set()
        rows=await (await connection.execute("SELECT generation_id,source_refs FROM life.generation_recipes "
            "WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row['source_refs'],changes,kind):
                await connection.execute("UPDATE life.generation_recipes SET recipe=NULL WHERE generation_id=%s",(row['generation_id'],))
        for table in ("life.diary_revisions","life.chapter_revisions"):
            key="diary_id" if table.endswith("diary_revisions") else "chapter_id"
            rows=await (await connection.execute(f"SELECT {key},revision,recipe FROM {table} WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(recipe->'source_refs','[]'::jsonb)) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
            for row in rows:
                if intersects(row["recipe"]["source_refs"],changes,kind):
                    await connection.execute(f"UPDATE {table} SET content='',recipe=%s,disclosure_ref=NULL,publication_metadata=NULL WHERE {key}=%s AND revision=%s",(document({"source_refs":row["recipe"]["source_refs"],"invalidated":True}),row[key],row["revision"]))
        rows=await (await connection.execute("SELECT id,source_refs FROM life.diaries WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE life.diaries SET state='failed',generation_recipe=NULL,episode_ids='{}',generation_retry_due_at=NULL,reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
        rows=await (await connection.execute("SELECT id,generation_recipe FROM life.chapters WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(generation_recipe->'source_refs','[]'::jsonb)) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["generation_recipe"]["source_refs"],changes,kind):
                await connection.execute("UPDATE life.chapters SET state='failed',generation_recipe=NULL,generation_retry_due_at=NULL,reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
        for table in ("life.activities","life.goals","life.commitments","life.contact_candidates"):
            rows=await (await connection.execute(f"SELECT * FROM {table} WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
            for row in rows:
                if not intersects(row["source_refs"],changes,kind):
                    continue
                affected.add(row["actor_id"])
                if table=="life.activities":
                    await connection.execute("UPDATE life.activities SET title='材料已失效',checkpoint_data='{}',state=CASE WHEN state='completed' THEN state ELSE 'dormant' END,waiting_reason='source_unavailable',resume_condition=NULL,next_due_at=NULL,revision=revision+1 WHERE id=%s",(row["id"],))
                    await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE activity_id=%s",(row["id"],))
                elif table=="life.goals":
                    if intersects(row["intention_source_refs"],changes,kind):
                        await connection.execute("UPDATE life.goals SET title='',motivation='',state='paused',reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
                    else:
                        remaining=[ref for ref in row["source_refs"] if not intersects((ref,),changes,kind)]
                        await connection.execute("UPDATE life.goals SET source_refs=%s,reason='milestone_source_unavailable',revision=revision+1 WHERE id=%s",(document(remaining),row["id"]))
                elif table=="life.commitments":
                    await connection.execute("UPDATE life.commitments SET title='',arguments='{}',state=CASE WHEN state IN ('completed','unknown','dispatching') THEN state ELSE 'cancelled' END,reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
                else:
                    await connection.execute("UPDATE life.contact_candidates SET summary='',payload='{}',state='cancelled',reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))

        for table in ("life.day_plans","life.day_slots"):
            rows=await (await connection.execute(f"SELECT * FROM {table} WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
            for row in rows:
                if not intersects(row["source_refs"],changes,kind):
                    continue
                affected.add(row["actor_id"])
                if table.endswith('day_slots'):
                    await connection.execute("UPDATE life.day_slots SET reason='source_unavailable',state=CASE WHEN state='planned' THEN 'cancelled' ELSE state END,revision=revision+1 WHERE id=%s",(row["id"],))
                else:
                    await connection.execute("UPDATE life.day_plans SET reason='source_unavailable',revision=revision+1 WHERE actor_id=%s AND day=%s",(row["actor_id"],row["day"]))
        rows=await (await connection.execute("SELECT * FROM life.goal_revisions WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE life.goal_revisions SET snapshot='{}',reason='source_unavailable' WHERE goal_id=%s AND revision=%s",(row["goal_id"],row["revision"]))
        rows=await (await connection.execute("SELECT * FROM life.goal_progress_receipts WHERE satisfied AND EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE life.goal_progress_receipts SET satisfied=false WHERE goal_id=%s AND action_id=%s",(row["goal_id"],row["action_id"]))
                await connection.execute("UPDATE life.goals SET progress=LEAST(1.0,(SELECT count(*)::float FROM life.goal_progress_receipts WHERE goal_id=life.goals.id AND satisfied)/NULLIF((criteria->>'target_count')::float,0)),state=CASE WHEN state='completed' THEN 'active' ELSE state END,reason='milestone_source_unavailable',revision=revision+1 WHERE id=%s",(row["goal_id"],))
        rows=await (await connection.execute("SELECT * FROM life.goal_milestones WHERE satisfied AND EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE life.goal_milestones SET satisfied=false WHERE goal_id=%s AND milestone_id=%s",(row["goal_id"],row["milestone_id"]))
                await connection.execute("UPDATE life.goals SET progress=LEAST(1.0,(SELECT count(*)::float FROM life.goal_milestones WHERE goal_id=life.goals.id AND satisfied)/NULLIF((criteria->>'target_count')::float,0)),state=CASE WHEN state='completed' THEN 'active' ELSE state END,reason='milestone_source_unavailable',revision=revision+1 WHERE id=%s",(row["goal_id"],))
        rows=await (await connection.execute("SELECT actor_id,person_id,availability_source_refs FROM life.contact_preferences WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(availability_source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["availability_source_refs"],changes,kind):
                await connection.execute("UPDATE life.contact_preferences SET availability='unknown',available_until=NULL,revision=revision+1 WHERE actor_id=%s AND person_id=%s",(row["actor_id"],row["person_id"]))

        rows=await (await connection.execute("SELECT id,source_refs FROM life.outfits WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE life.outfits SET description='',appearance='',reference=NULL,revision=revision+1 WHERE id=%s",(row["id"],))
                await connection.execute("UPDATE life.actor_state SET outfit_id=NULL,revision=revision+1 WHERE outfit_id=%s",(row["id"],))
        rows=await (await connection.execute("SELECT id,source_ref FROM life.readings WHERE source_ref->>'source_id'=ANY(%s)",(selector,))).fetchall()
        for row in rows:
            if intersects((row["source_ref"],),changes,kind):
                await connection.execute("UPDATE life.readings SET state='closed',covered='[]',next_position=NULL,requested='{}',revision=revision+1,updated_at=now() WHERE id=%s",(row["id"],))
        if self.life and affected:
            rows=await (await connection.execute("SELECT * FROM life.routines WHERE actor_id=ANY(%s) AND enabled",(list(affected),))).fetchall()
            for row in rows:
                try:
                    context=await self.life.identity.refresh_scheduled_context(context_from_record(row["context"]),permission="life.manage",connection=connection)
                    await self.life.signal(context,"source:"+signature({"refs":source_refs,"kind":kind}),connection=connection)
                except AppError as error:
                    if error.status not in {401,403,404,409,410}:
                        raise
