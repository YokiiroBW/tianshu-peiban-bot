"""Actual web publication before the channel acknowledges visible delivery."""
from uuid import NAMESPACE_URL,uuid5
from tianshu.runtime.errors import AppError
from tianshu.types import Receipt
from .codec import document,signature,source_refs


class WebPublication:
    def __init__(self,database,sources):
        self.database,self.sources=database,sources

    async def get(self,context,expression_id):
        await self.sources.check(context)
        async with self.database.transaction(read_only=True) as connection:
            row=await (await connection.execute(
                "SELECT source_refs FROM conversation.messages WHERE expression_id=%s AND actor_id=%s "
                "AND conversation_id=%s AND speaker='assistant' AND scope_ids<@%s::text[]",
                (expression_id,context.actor_id,context.conversation_id,list(context.scope_ids)))).fetchone()
        if row is None:
            return None
        await self.sources.check(context,source_refs(row["source_refs"]))
        return Receipt(str(uuid5(NAMESPACE_URL,"web-publication:"+expression_id)),None,None,
            "succeeded","confirmed","delivered",result={"expression_id":expression_id,"transport":"web"})

    async def publish(self,context,plan):
        if not context.delivery_allowed or context.actor_id!=plan.actor_id or context.conversation_id!=plan.target_ref:
            raise AppError("delivery_forbidden",status=403)
        # ChannelDelivery has already normalized the public parts. This port owns the actual public row.
        from .expression import ExpressionService
        parts=ExpressionService._parts(plan.public_parts)
        async with self.database.transaction() as connection:
            if not await self.sources.validate_persistence(connection,context,plan.source_refs):
                raise AppError("source_unavailable",status=409)
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("web-publication:"+plan.expression_id,))
            old=await (await connection.execute("SELECT actor_id,conversation_id,parts,source_refs FROM conversation.messages WHERE expression_id=%s",(plan.expression_id,))).fetchone()
            if old and (old["actor_id"]!=context.actor_id or old["conversation_id"]!=context.conversation_id or signature({"parts":old["parts"],"refs":old["source_refs"]})!=signature({"parts":parts,"refs":plan.source_refs})):
                raise AppError("expression_identity_conflict",status=409)
            if not old:
                await connection.execute("INSERT INTO conversation.messages(actor_id,conversation_id,speaker,parts,source_refs,scope_ids,turn_id,expression_id) VALUES (%s,%s,'assistant',%s,%s,%s,%s,%s)",(context.actor_id,context.conversation_id,document(parts),document(plan.source_refs),list(context.scope_ids),plan.turn_id,plan.expression_id))
        return Receipt(str(uuid5(NAMESPACE_URL,"web-publication:"+plan.expression_id)),None,None,"succeeded","confirmed","delivered",result={"expression_id":plan.expression_id,"transport":"web"})
