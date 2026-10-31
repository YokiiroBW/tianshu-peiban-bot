"""Action results join the existing transactional queue, then reach both B consumers."""
from tianshu.sources.dependencies import unique_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass
from .codec import context_from_record, receipt, source_refs


class ActionResultEvents:
    def __init__(self, jobs):
        self.jobs = jobs

    async def publish(self, connection, event):
        if event.get("type") != "capability.result" or event.get("version") != 1:
            raise AppError("unsupported_result_event", status=500)
        await self.jobs.enqueue(connection, JobSpec("conversation.result", "capability.result:"+event["event_id"],
            {"event": event}, ServiceClass.INTERACTIVE, subject_id=event["context"]["actor_id"]))


class ResultConsumer:
    def __init__(self, database, identity, actions, memory, growth, conversation, life_worker, renderer):
        self.database, self.identity = database, identity
        self.actions, self.conversation, self.life_worker = actions, conversation, life_worker
        self.memory, self.growth,self.renderer = memory, growth,renderer

    async def consume(self, event):
        if event.get("type") != "capability.result" or event.get("version") != 1:
            raise AppError("unsupported_result_event", status=500)
        context = context_from_record(event["context"])
        await self.identity.check(context, source_refs(event["source_refs"]))
        current = await self.actions.get(event["action_id"], context)
        result = receipt(current["receipt"])
        if result is None:
            return
        refs = unique_refs(tuple(ref for ref in (
            *source_refs(current["action"]["source_refs"]), *source_refs(result.result.get("source_refs", ())),
            *(ref for item in result.artifacts for ref in item.source_refs))))
        await self.identity.check(context, refs)
        async with self.database.transaction(read_only=True) as connection:
            previous = await (await connection.execute("SELECT receipt_id FROM conversation.result_consumption WHERE receipt_id=%s", (result.receipt_id,))).fetchone()
        if previous:
            return
        rendered=await self.renderer.on_action_result(context,result,current["result_revision"])
        channel_result=current["action"]["capability_id"]=="channels.deliver"
        if channel_result:
            await self.conversation.expression.channel_result(context,current["action"]["expression_id"],result)
        elif not rendered:
            await self.conversation.on_result(context, result, result_revision=current["result_revision"])
        technical = current["action"]["capability_id"] in {"channels.read","channels.probe","channels.configure",
            "capabilities.catalog.settings.save","capabilities.catalog.sources.save","capabilities.catalog.sources.refresh"}
        owned_by_life = await self.life_worker.capability_result(context, result, result_revision=current["result_revision"], observed_at=current["result_observed_at"])
        if not owned_by_life and not rendered and not channel_result and not technical:
            await self.life_worker.detached_result(context,result,current["action"])
        async with self.database.transaction() as connection:
            meaningful=context.actor_id is not None and context.origin_kind.value!="observation" and not technical
            if meaningful and not owned_by_life and not rendered and not channel_result and result.execution in {"succeeded", "failed", "cancelled", "unknown"}:
                await self.memory.observe_episode(context, {"episode_id": "action:"+result.action_id,
                    "event_id": "action:"+result.action_id, "revision": current["result_revision"],
                    "origin_kind": "tool_result", "actor_id": context.actor_id,
                    "occurred_at": current["result_observed_at"], "summary": {"succeeded": "原任务已执行",
                    "failed": "原任务未完成", "cancelled": "原任务已取消", "unknown": "原任务结果待确认"}[result.execution],
                    "source_refs": refs, "coverage": result.coverage, "action_id": result.action_id,
                    "receipt_id": result.receipt_id, "actual_result": {**result.result,
                        "capability_id": current["action"]["capability_id"], "execution": result.execution,
                        "effect": result.effect, "delivery": result.delivery}}, connection=connection)
                await self.growth.changed(context, f"action:{result.action_id}:{current['result_revision']}", connection=connection)
            await connection.execute("INSERT INTO conversation.result_consumption(receipt_id,action_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (result.receipt_id, result.action_id))


    async def expression_progress(self,expression_id,context):
        context=context_from_record(context)
        current=await self.renderer.get(context,expression_id)
        await self.conversation.expression_result(context,expression_id,current)
        await self.life_worker.expression_result(context,expression_id,current)

        actual=receipt(current["receipt"])
        if actual and current["state"] in {"delivered","delivery_unknown"}:
            refs=source_refs(current.get("source_refs",()))
            async with self.database.transaction() as connection:
                if await self.conversation.sources.validate_persistence(connection,context,refs):
                    await self.memory.observe_episode(context,{"episode_id":"expression:"+expression_id,"event_id":"expression:"+expression_id,
                        "revision":current["revision"],"origin_kind":"actor","actor_id":context.actor_id,"occurred_at":current["observed_at"],
                        "summary":"公开表达已投递" if actual.delivery=="delivered" else "公开表达投递待确认","source_refs":refs,
                        "actual_result":{"derived_kind":"expression","expression_id":expression_id,"delivery":actual.delivery,"receipt_id":actual.receipt_id}},connection=connection)
                    await self.growth.changed(context,f"expression:{expression_id}:{current['revision']}",connection=connection)
