"""Action results join the existing transactional queue, then reach both B consumers."""
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
    def __init__(self, database, identity, actions, memory, growth, conversation, life_worker):
        self.database, self.identity = database, identity
        self.actions, self.conversation, self.life_worker = actions, conversation, life_worker
        self.memory, self.growth = memory, growth

    async def consume(self, event):
        if event.get("type") != "capability.result" or event.get("version") != 1:
            raise AppError("unsupported_result_event", status=500)
        context = context_from_record(event["context"])
        await self.identity.check(context, source_refs(event["source_refs"]))
        current = await self.actions.get(event["action_id"], context)
        result = receipt(current["receipt"])
        if result is None:
            return
        refs = tuple({(ref.source_id, ref.revision): ref for ref in (
            *source_refs(current["action"]["source_refs"]), *source_refs(result.result.get("source_refs", ())),
            *(ref for item in result.artifacts for ref in item.source_refs))}.values())
        await self.identity.check(context, refs)
        async with self.database.transaction(read_only=True) as connection:
            previous = await (await connection.execute("SELECT receipt_id FROM conversation.result_consumption WHERE receipt_id=%s", (result.receipt_id,))).fetchone()
        if previous:
            return
        if current["action"]["capability_id"]=="channels.deliver":
            await self.conversation.expression.channel_result(context,current["action"]["expression_id"],result)
        else:
            await self.conversation.on_result(context, result, result_revision=current["result_revision"])
        owned_by_life = await self.life_worker.capability_result(context, result, result_revision=current["result_revision"], observed_at=current["result_observed_at"])
        async with self.database.transaction() as connection:
            if not owned_by_life and result.execution in {"succeeded", "failed", "cancelled", "unknown"}:
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
