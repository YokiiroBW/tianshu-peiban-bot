from tianshu.conversation.codec import document
from tianshu.sources.dependencies import changes_for,intersects


class ConversationDependencies:
    async def invalidate(self,connection,source_refs,kind):
        changes,selector=changes_for(source_refs)
        requests=await (await connection.execute("SELECT material_key,source_refs FROM conversation.generation_requests WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in requests:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE conversation.generation_requests SET request=NULL,state='invalidated' WHERE material_key=%s",(row["material_key"],))
        for table,fields in (("conversation.turns","turn_id,source_refs,original_source_refs"),("conversation.messages","id,source_refs"),("conversation.expressions","expression_id,source_refs")):
            rows=await (await connection.execute(f"SELECT {fields} FROM {table} WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
            for row in rows:
                if not intersects(row["source_refs"],changes,kind):
                    continue
                if table=="conversation.turns":
                    await connection.execute("UPDATE conversation.turns SET input_parts='[]',original_parts='[]',model_parts='[]',model_native=NULL,continuation='[]',ordered_parts='[]',artifacts='[]',state=CASE WHEN state IN ('completed','cancelled') THEN state ELSE 'failed' END,error_code='source_unavailable',revision=revision+1 WHERE turn_id=%s",(row["turn_id"],))
                    await connection.execute("UPDATE conversation.tool_links SET result=NULL WHERE turn_id=%s",(row["turn_id"],))
                elif table=="conversation.messages":
                    await connection.execute("UPDATE conversation.messages SET parts='[]' WHERE id=%s",(row["id"],))
                else:
                    await connection.execute("UPDATE conversation.expressions SET public_parts='[]',artifacts='[]',revision=revision+1 WHERE expression_id=%s",(row["expression_id"],))

        rows=await (await connection.execute("SELECT expression_id,source_refs FROM conversation.expression_plans WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(source_refs) r WHERE r->>'source_id'=ANY(%s))",(selector,))).fetchall()
        for row in rows:
            if intersects(row["source_refs"],changes,kind):
                await connection.execute("UPDATE conversation.expression_plans SET plan='{}',rendered=NULL,steps=(SELECT COALESCE(jsonb_agg(jsonb_build_object('index',s->'index','action_id',s->'action_id','state',s->'state','result_revision',s->'result_revision','artifacts','[]'::jsonb,'receipt',NULL)),'[]'::jsonb) FROM jsonb_array_elements(steps) s),state=CASE WHEN state='delivered' THEN state ELSE 'failed' END,reason='source_unavailable',revision=revision+1 WHERE expression_id=%s",(row["expression_id"],))
