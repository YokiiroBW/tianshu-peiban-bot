from tianshu.conversation.codec import document,signature,context_from_record
from tianshu.sources.dependencies import changes_for,intersects
from tianshu.runtime.errors import AppError


class LifeDependencies:
    def __init__(self,life=None):
        self.life=life

    async def actors_for_source(self, connection, source_refs, kind):
        """Enumerate only Life-owned rows; callers isolate each actor's transaction."""
        changes, selector = changes_for(source_refs)
        if not selector:
            return ()
        specs = [
            (f"life.{name} r", "r.actor_id", "r.source_refs")
            for name in ("activities", "goals", "commitments", "contact_candidates", "day_plans", "day_slots", "diaries", "outfits")
        ]
        specs.extend([
            ("life.generation_recipes r", "r.context->>'actor_id'", "r.source_refs"),
            ("life.diary_revisions r JOIN life.diaries d ON d.id=r.diary_id", "d.actor_id", "r.recipe->'source_refs'"),
            ("life.chapter_revisions r JOIN life.chapters c ON c.id=r.chapter_id JOIN life.works w ON w.id=c.work_id", "w.actor_id", "r.recipe->'source_refs'"),
            ("life.chapters r JOIN life.works w ON w.id=r.work_id", "w.actor_id", "r.generation_recipe->'source_refs'"),
            ("life.goal_revisions r JOIN life.goals g ON g.id=r.goal_id", "g.actor_id", "r.source_refs"),
            ("life.goal_progress_receipts r JOIN life.goals g ON g.id=r.goal_id", "g.actor_id", "r.source_refs"),
            ("life.goal_milestones r JOIN life.goals g ON g.id=r.goal_id", "g.actor_id", "r.source_refs"),
            ("life.contact_preferences r", "r.actor_id", "r.availability_source_refs"),
            ("life.readings r", "r.actor_id", "jsonb_build_array(r.source_ref)"),
        ])
        actors = set()
        for table, owner, refs in specs:
            rows = await (await connection.execute(
                f"SELECT {owner} AS actor_id,COALESCE({refs},'[]'::jsonb) AS refs FROM {table} "
                f"WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE({refs},'[]'::jsonb)) item "
                "WHERE item->>'source_id'=ANY(%s))", (selector,))).fetchall()
            actors.update(row["actor_id"] for row in rows if row["actor_id"] and intersects(row["refs"], changes, kind))
        return tuple(sorted(actors))

    async def invalidate(self, connection, source_refs, kind):
        for actor_id in await self.actors_for_source(connection, source_refs, kind):
            await self.invalidate_actor(connection, source_refs, kind, actor_id=actor_id)

    async def invalidate_actor(self,connection,source_refs,kind,*,actor_id):
        changes,selector=changes_for(source_refs)
        affected=set()
        rows=await (await connection.execute("SELECT generation_id,source_refs FROM life.generation_recipes "
            "WHERE context->>'actor_id'=%s AND recipe IS NOT NULL AND EXISTS(SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects(row['source_refs'],changes,kind):
                affected.add(actor_id)
                await connection.execute("UPDATE life.generation_recipes SET recipe=NULL WHERE generation_id=%s",(row['generation_id'],))
        for table in ("life.diary_revisions","life.chapter_revisions"):
            key="diary_id" if table.endswith("diary_revisions") else "chapter_id"
            join = "JOIN life.diaries d ON d.id=x.diary_id" if key=="diary_id" else "JOIN life.chapters c ON c.id=x.chapter_id JOIN life.works d ON d.id=c.work_id"
            rows=await (await connection.execute(f"SELECT x.{key},x.revision,x.recipe FROM {table} x {join} WHERE d.actor_id=%s AND EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(x.recipe->'source_refs','[]'::jsonb)) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
            for row in rows:
                if intersects(row["recipe"]["source_refs"],changes,kind):
                    affected.add(actor_id)
                    await connection.execute(f"UPDATE {table} SET content='',recipe=%s,disclosure_ref=NULL,publication_metadata=NULL WHERE {key}=%s AND revision=%s",(document({"source_refs":row["recipe"]["source_refs"],"invalidated":True}),row[key],row["revision"]))
        rows=await (await connection.execute("SELECT id,source_refs FROM life.diaries WHERE actor_id=%s AND (state<>'failed' OR reason IS DISTINCT FROM 'source_unavailable' OR generation_recipe IS NOT NULL) AND EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                affected.add(actor_id)
                await connection.execute("UPDATE life.diaries SET state='failed',generation_recipe=NULL,episode_ids='{}',generation_retry_due_at=NULL,reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
        rows=await (await connection.execute("SELECT c.id,c.generation_recipe FROM life.chapters c JOIN life.works w ON w.id=c.work_id WHERE w.actor_id=%s AND EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(c.generation_recipe->'source_refs','[]'::jsonb)) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects(row["generation_recipe"]["source_refs"],changes,kind):
                affected.add(actor_id)
                await connection.execute("UPDATE life.chapters SET state='failed',generation_recipe=NULL,generation_retry_due_at=NULL,reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
        for table in ("life.activities","life.goals","life.commitments","life.contact_candidates"):
            rows=await (await connection.execute(f"SELECT * FROM {table} WHERE actor_id=%s AND EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
            for row in rows:
                if not intersects(row["source_refs"],changes,kind):
                    continue
                affected.add(row["actor_id"])
                if table=="life.activities":
                    if row["waiting_reason"] == "source_unavailable" and not row["checkpoint_data"]:
                        continue
                    await connection.execute("UPDATE life.activities SET title='材料已失效',checkpoint_data='{}',state=CASE WHEN state='completed' THEN state ELSE 'dormant' END,waiting_reason='source_unavailable',resume_condition=NULL,next_due_at=NULL,revision=revision+1 WHERE id=%s",(row["id"],))
                    await connection.execute("UPDATE life.actor_state SET activity_id=NULL,revision=revision+1 WHERE activity_id=%s",(row["id"],))
                elif table=="life.goals":
                    if intersects(row["intention_source_refs"],changes,kind):
                        if row["reason"] == "source_unavailable" and row["state"] == "paused" and not row["title"]:
                            continue
                        await connection.execute("UPDATE life.goals SET title='',motivation='',state='paused',reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
                    else:
                        remaining=[ref for ref in row["source_refs"] if not intersects((ref,),changes,kind)]
                        await connection.execute("UPDATE life.goals SET source_refs=%s,reason='milestone_source_unavailable',revision=revision+1 WHERE id=%s",(document(remaining),row["id"]))
                elif table=="life.commitments":
                    if row["reason"] == "source_unavailable" and not row["title"]:
                        continue
                    await connection.execute("UPDATE life.commitments SET title='',arguments='{}',state=CASE WHEN state IN ('completed','unknown','dispatching') THEN state ELSE 'cancelled' END,reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))
                else:
                    if row["reason"] == "source_unavailable" and row["state"] == "cancelled":
                        continue
                    await connection.execute("UPDATE life.contact_candidates SET summary='',payload='{}',state='cancelled',reason='source_unavailable',revision=revision+1 WHERE id=%s",(row["id"],))

        for table in ("life.day_plans","life.day_slots"):
            rows=await (await connection.execute(f"SELECT * FROM {table} WHERE actor_id=%s AND reason<>'source_unavailable' AND EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
            for row in rows:
                if not intersects(row["source_refs"],changes,kind):
                    continue
                affected.add(row["actor_id"])
                if table.endswith('day_slots'):
                    await connection.execute("UPDATE life.day_slots SET reason='source_unavailable',state=CASE WHEN state='planned' THEN 'cancelled' ELSE state END,revision=revision+1 WHERE id=%s",(row["id"],))
                else:
                    await connection.execute("UPDATE life.day_plans SET reason='source_unavailable',revision=revision+1 WHERE actor_id=%s AND day=%s",(row["actor_id"],row["day"]))
        rows=await (await connection.execute("SELECT r.* FROM life.goal_revisions r JOIN life.goals g ON g.id=r.goal_id WHERE g.actor_id=%s AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.source_refs) x WHERE x->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                affected.add(actor_id)
                await connection.execute("UPDATE life.goal_revisions SET snapshot='{}',reason='source_unavailable' WHERE goal_id=%s AND revision=%s",(row["goal_id"],row["revision"]))
        rows=await (await connection.execute("SELECT r.* FROM life.goal_progress_receipts r JOIN life.goals g ON g.id=r.goal_id WHERE g.actor_id=%s AND r.satisfied AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.source_refs) x WHERE x->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                affected.add(actor_id)
                await connection.execute("UPDATE life.goal_progress_receipts SET satisfied=false WHERE goal_id=%s AND action_id=%s",(row["goal_id"],row["action_id"]))
                await connection.execute("UPDATE life.goals SET progress=LEAST(1.0,(SELECT count(*)::float FROM life.goal_progress_receipts WHERE goal_id=life.goals.id AND satisfied)/NULLIF((criteria->>'target_count')::float,0)),state=CASE WHEN state='completed' THEN 'active' ELSE state END,reason='milestone_source_unavailable',revision=revision+1 WHERE id=%s",(row["goal_id"],))
        rows=await (await connection.execute("SELECT r.* FROM life.goal_milestones r JOIN life.goals g ON g.id=r.goal_id WHERE g.actor_id=%s AND r.satisfied AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.source_refs) x WHERE x->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                affected.add(actor_id)
                await connection.execute("UPDATE life.goal_milestones SET satisfied=false WHERE goal_id=%s AND milestone_id=%s",(row["goal_id"],row["milestone_id"]))
                await connection.execute("UPDATE life.goals SET progress=LEAST(1.0,(SELECT count(*)::float FROM life.goal_milestones WHERE goal_id=life.goals.id AND satisfied)/NULLIF((criteria->>'target_count')::float,0)),state=CASE WHEN state='completed' THEN 'active' ELSE state END,reason='milestone_source_unavailable',revision=revision+1 WHERE id=%s",(row["goal_id"],))
        rows=await (await connection.execute("SELECT actor_id,person_id,availability_source_refs FROM life.contact_preferences WHERE actor_id=%s AND (availability<>'unknown' OR available_until IS NOT NULL) AND EXISTS (SELECT 1 FROM jsonb_array_elements(availability_source_refs) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects(row["availability_source_refs"],changes,kind):
                affected.add(actor_id)
                await connection.execute("UPDATE life.contact_preferences SET availability='unknown',available_until=NULL,revision=revision+1 WHERE actor_id=%s AND person_id=%s",(row["actor_id"],row["person_id"]))

        rows=await (await connection.execute("SELECT id,source_refs FROM life.outfits WHERE actor_id=%s AND (description<>'' OR appearance<>'' OR reference IS NOT NULL) AND EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                affected.add(actor_id)
                await connection.execute("UPDATE life.outfits SET description='',appearance='',reference=NULL,revision=revision+1 WHERE id=%s",(row["id"],))
                await connection.execute("UPDATE life.actor_state SET outfit_id=NULL,revision=revision+1 WHERE outfit_id=%s",(row["id"],))
        rows=await (await connection.execute("SELECT id,source_ref FROM life.readings WHERE actor_id=%s AND (state<>'closed' OR covered<>'[]'::jsonb OR next_position IS NOT NULL OR requested<>'{}'::jsonb) AND source_ref->>'source_id'=ANY(%s)",(actor_id,selector,))).fetchall()
        for row in rows:
            if intersects((row["source_ref"],),changes,kind):
                affected.add(actor_id)
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
