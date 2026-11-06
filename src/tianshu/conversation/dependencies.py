"""Invalidate only actual conversation dependencies, optionally partitioned by actor."""
from tianshu.sources.dependencies import changes_for, intersects


class ConversationDependencies:
    def __init__(self, jobs=None):
        self.statistics = None
        self.jobs = jobs

    def bind_statistics(self, statistics, jobs=None):
        self.statistics = statistics
        if jobs is not None:
            self.jobs = jobs

    async def actors_for_source(self, connection, source_refs, kind):
        changes, selector = changes_for(source_refs)
        queries = (
            ("SELECT actor_id,source_refs FROM conversation.turns", "source_refs"),
            ("SELECT actor_id,source_refs FROM conversation.messages", "source_refs"),
            ("SELECT actor_id,source_refs FROM conversation.expressions", "source_refs"),
            ("SELECT actor_id,source_refs FROM conversation.expression_plans", "source_refs"),
            ("SELECT request->'context'->>'actor_id' AS actor_id,source_refs FROM conversation.generation_requests", "source_refs"),
            ("SELECT t.actor_id,s.source_refs FROM conversation.context_selections s JOIN conversation.turns t ON t.turn_id=s.turn_id", "s.source_refs"),
        )
        actors = set()
        for query, refs_column in queries:
            rows = await (await connection.execute(query + f" WHERE EXISTS (SELECT 1 FROM jsonb_array_elements({refs_column}) r "
                "WHERE r->>'source_id'=ANY(%s))", (selector,))).fetchall()
            actors.update(row["actor_id"] for row in rows if intersects(row["source_refs"], changes, kind))
        from .rbg_result_withdrawal import actors_for_source as result_actors
        actors.update(await result_actors(connection, changes, selector, kind))
        return tuple(sorted(actors, key=lambda value: "" if value is None else value))

    async def invalidate(self, connection, source_refs, kind):
        return await self._invalidate(connection, source_refs, kind, filter_actor=False)

    async def invalidate_actor(self, connection, source_refs, kind, *, actor_id):
        return await self._invalidate(connection, source_refs, kind, actor_id=actor_id, filter_actor=True)

    async def _invalidate(self, connection, source_refs, kind, *, actor_id=None, filter_actor=False):
        changes, selector = changes_for(source_refs)
        from .rbg_result_withdrawal import invalidate as invalidate_results
        await invalidate_results(connection, changes, selector, kind, actor_id=actor_id,
                                 filter_actor=filter_actor, jobs=self.jobs)
        actor_sql = " AND actor_id IS NOT DISTINCT FROM %s" if filter_actor else ""
        parameters = (selector, actor_id) if filter_actor else (selector,)
        selection_actor = " AND t.actor_id IS NOT DISTINCT FROM %s" if filter_actor else ""
        selections = await (await connection.execute(
            "SELECT s.attempt_id,s.source_refs FROM conversation.context_selections s "
            "JOIN conversation.turns t ON t.turn_id=s.turn_id WHERE EXISTS "
            "(SELECT 1 FROM jsonb_array_elements(s.source_refs) r WHERE r->>'source_id'=ANY(%s))" + selection_actor,
            parameters)).fetchall()
        for row in selections:
            if intersects(row["source_refs"], changes, kind):
                await connection.execute("UPDATE conversation.context_selections SET model_result=NULL WHERE attempt_id=%s", (row["attempt_id"],))
        request_actor = " AND request->'context'->>'actor_id' IS NOT DISTINCT FROM %s" if filter_actor else ""
        requests = await (await connection.execute(
            "SELECT material_key,source_refs FROM conversation.generation_requests WHERE EXISTS "
            "(SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))" + request_actor,
            parameters)).fetchall()
        for row in requests:
            if intersects(row["source_refs"], changes, kind):
                await connection.execute("UPDATE conversation.generation_requests SET request=NULL,state='invalidated' WHERE material_key=%s", (row["material_key"],))
        for table, fields in (("conversation.turns", "turn_id,actor_id,state,source_refs,original_source_refs"),
                              ("conversation.messages", "id,source_refs"),
                              ("conversation.expressions", "expression_id,source_refs")):
            rows = await (await connection.execute(f"SELECT {fields} FROM {table} WHERE EXISTS "
                "(SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))" + actor_sql,
                parameters)).fetchall()
            for row in rows:
                if not intersects(row["source_refs"], changes, kind):
                    continue
                if table == "conversation.turns":
                    original_invalidated = intersects(row["original_source_refs"], changes, kind)
                    merged = await (await connection.execute(
                        "SELECT original_source_refs FROM conversation.turns WHERE merged_into=%s", (row["turn_id"],))).fetchall()
                    input_invalidated = original_invalidated or any(intersects(item["original_source_refs"], changes, kind) for item in merged)
                    input_withdrawn = input_invalidated and kind in {"channel_recall", "retracted", "forgotten"}
                    original_withdrawn = original_invalidated and kind in {"channel_recall", "retracted", "forgotten"}
                    if original_withdrawn:
                        changed = await (await connection.execute(
                            "UPDATE conversation.turns SET withdrawn_at=COALESCE(withdrawn_at,now()) WHERE turn_id=%s RETURNING withdrawn_at",
                            (row["turn_id"],))).fetchone()
                        await connection.execute("UPDATE conversation.input_supplements SET notification_state='withdrawn' "
                            "WHERE input_id=%s AND notification_state='pending'", (row["turn_id"],))
                        from .observability import withdrawn_on
                        recorded = await withdrawn_on(self.statistics, connection, input_id=row["turn_id"],
                            actor_id=row["actor_id"], occurred_at=changed["withdrawn_at"])
                        if self.statistics is not None and not recorded and self.jobs is not None:
                            from .observability import schedule_repair_on
                            await schedule_repair_on(self.jobs, connection)
                    await connection.execute("UPDATE conversation.turns SET input_parts='[]',original_parts='[]',model_parts='[]',model_native=NULL,continuation='[]',ordered_parts='[]',artifacts='[]',"
                        "state=CASE WHEN state IN ('completed','cancelled','unknown') THEN state WHEN %s THEN 'cancelled' ELSE 'failed' END,"
                        "error_code=CASE WHEN state IN ('completed','cancelled','unknown') THEN error_code WHEN %s THEN 'input_withdrawn' ELSE 'source_unavailable' END,"
                        "participation_state=CASE WHEN participation_state IN ('waiting','evaluating','unknown') THEN 'invalidated' ELSE participation_state END,"
                        "participation_reason='source_unavailable',revision=revision+1 WHERE turn_id=%s",
                        (input_withdrawn, input_withdrawn, row["turn_id"]))
                    await connection.execute("UPDATE conversation.tool_links SET result=CASE WHEN result IS NULL THEN NULL "
                        "ELSE jsonb_set(jsonb_set(result,'{artifacts}','[]'::jsonb),'{result}','{}'::jsonb) END "
                        "WHERE turn_id=%s", (row["turn_id"],))
                    if input_withdrawn and row["state"] not in {"completed", "cancelled", "unknown", "merged", "observed"} and self.jobs is not None:
                        from tianshu.runtime.jobs import JobSpec
                        from tianshu.types import ServiceClass
                        await self.jobs.enqueue(connection, JobSpec("conversation.cancel", "turn-source-cancel:"+row["turn_id"],
                            {"turn_id": row["turn_id"]}, ServiceClass.INTERACTIVE, subject_id=row["actor_id"]))
                elif table == "conversation.messages":
                    await connection.execute("UPDATE conversation.messages SET parts='[]' WHERE id=%s", (row["id"],))
                else:
                    await connection.execute("UPDATE conversation.expressions SET public_parts='[]',artifacts='[]',revision=revision+1 WHERE expression_id=%s", (row["expression_id"],))
        rows = await (await connection.execute("SELECT expression_id,source_refs FROM conversation.expression_plans WHERE EXISTS "
            "(SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))" + actor_sql,
            parameters)).fetchall()
        for row in rows:
            if intersects(row["source_refs"], changes, kind):
                await connection.execute("UPDATE conversation.expression_plans SET plan='{}',rendered=NULL,steps=(SELECT COALESCE(jsonb_agg(jsonb_build_object('index',s->'index','action_id',s->'action_id','state',s->'state','result_revision',s->'result_revision','artifacts','[]'::jsonb,'receipt',CASE WHEN s->'receipt' IS NULL OR s->'receipt'='null'::jsonb THEN 'null'::jsonb ELSE (s->'receipt')-'artifacts'-'result'-'coverage' END)),'[]'::jsonb) FROM jsonb_array_elements(steps) s),state=CASE WHEN state IN ('delivered','delivery_unknown','render_unknown','cancelled') THEN state ELSE 'failed' END,reason=CASE WHEN state IN ('delivered','delivery_unknown','render_unknown','cancelled') THEN reason ELSE 'source_unavailable' END,revision=revision+1 WHERE expression_id=%s", (row["expression_id"],))
